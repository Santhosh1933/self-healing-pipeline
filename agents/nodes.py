"""LangGraph node implementations for triage and repair."""

from __future__ import annotations

import json
import re
from typing import Any
from langchain_google_genai import ChatGoogleGenerativeAI
from config.settings import Settings
from core.exceptions import LLMResponseParsingError
from core.logger import get_logger, set_context
from agents.state import ClassificationResult, PipelineTriageState, RCAResult
from utils.github_client import GitHubClient
from utils.git_tools import run_docker_validation, validation_workspace
from utils.prompt_loader import PromptLoader

logger = get_logger(__name__)
prompts = PromptLoader()


def _context(state: PipelineTriageState) -> str:
    """Serialize failure fields for prompt rendering."""
    keys = ("run_id", "job_id", "task_key", "error_type", "error_message", "stack_trace", "commit_sha")
    return json.dumps({key: state.get(key, "") for key in keys}, indent=2)


def _model(name: str, settings: Settings) -> ChatGoogleGenerativeAI:
    """Create a deterministic Gemini chat model."""
    return ChatGoogleGenerativeAI(model=name, google_api_key=settings.gemini_api_key.get_secret_value(), temperature=0)


def _diff(response: Any) -> str:
    """Extract a raw unified diff from a model response."""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
    text = str(content).strip()
    fenced = re.search(r"```(?:diff|patch)?\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    patch = (fenced.group(1) if fenced else text).strip()
    if not patch.startswith(("diff ", "--- ")):
        raise LLMResponseParsingError("Fix Generator returned an invalid unified diff")
    return patch


async def classify_error_node(state: PipelineTriageState, settings: Settings) -> PipelineTriageState:
    """Classify a failure as transient infrastructure or code defect."""
    set_context(run_id=state.get("run_id"), job_id=state.get("job_id"), task_key=state.get("task_key"), step="classification")
    system, human = prompts.render("classification", failure_context=_context(state))
    result = await _model(settings.classifier_model, settings).with_structured_output(ClassificationResult).ainvoke([("system", system), ("human", human)])
    logger.info("Failure classified", extra={"extra_data": {"classification": result.classification}})
    return {**state, "classification": result.classification, "status": "classified"}


async def rca_discovery_node(state: PipelineTriageState, settings: Settings) -> PipelineTriageState:
    """Identify the root cause and relevant repository files."""
    set_context(step="rca")
    system, human = prompts.render("rca_discovery", failure_context=_context(state))
    result = await _model(settings.reasoning_model, settings).with_structured_output(RCAResult).ainvoke([("system", system), ("human", human)])
    logger.info("Root cause analysis completed", extra={"extra_data": {"target_files": result.target_files}})
    return {**state, "root_cause": result.root_cause, "target_files": result.target_files, "status": "rca_complete"}


async def fix_generator_node(state: PipelineTriageState, settings: Settings) -> PipelineTriageState:
    """Generate a minimal patch using RCA and previous validation feedback."""
    attempt = state.get("retry_count", 0) + 1
    set_context(step="fix_generation", status=f"attempt_{attempt}")
    system, human = prompts.render("fix_generator", root_cause=state.get("root_cause", ""), target_files=", ".join(state.get("target_files", [])), validation_output=state.get("validation_output", ""), failure_context=_context(state))
    response = await _model(settings.reasoning_model, settings).ainvoke([("system", system), ("human", human)])
    return {**state, "patch_diff": _diff(response), "retry_count": attempt, "status": "patch_generated"}


async def validator_node(state: PipelineTriageState, settings: Settings) -> PipelineTriageState:
    """Apply the generated patch in an ephemeral workspace and run pytest."""
    set_context(step="validation")
    try:
        with validation_workspace(state["repository_url"], state["commit_sha"]) as workspace:
            output, passed = run_docker_validation(workspace, state.get("patch_diff", ""), settings.pytest_timeout_seconds)
            logger.info("Patch validation completed", extra={"extra_data": {"passed": passed}})
            return {**state, "validation_output": output, "status": "validated" if passed else "validation_failed"}
    except Exception as exc:
        logger.error("Patch validation failed", exc_info=True)
        return {**state, "validation_output": str(exc), "status": "validation_failed"}


async def pr_creator_node(state: PipelineTriageState, settings: Settings) -> PipelineTriageState:
    """Create the GitHub Issue and linked pull request for a validated patch."""
    set_context(step="release")
    issue_url, pull_request_url = GitHubClient(settings).create_repair_pr(state)
    logger.info("Pull request created", extra={"extra_data": {"issue_url": issue_url, "pull_request_url": pull_request_url}})
    return {**state, "issue_url": issue_url, "pull_request_url": pull_request_url, "status": "pr_created"}


async def alert_devops_node(state: PipelineTriageState) -> PipelineTriageState:
    """Record a transient failure for the configured DevOps alert integration."""
    set_context(step="devops_alert", status="alerted")
    logger.warning("Transient pipeline failure requires retry or alert", extra={"extra_data": {"error_message": state.get("error_message")}})
    return {**state, "status": "devops_alerted"}


async def escalate_human_node(state: PipelineTriageState) -> PipelineTriageState:
    """Record a repair that exhausted its validation attempts."""
    set_context(step="human_escalation", status="escalated")
    logger.error("Repair escalated after validation retries", extra={"extra_data": {"validation_output": state.get("validation_output")}})
    return {**state, "status": "human_escalation"}
