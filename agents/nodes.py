"""LangGraph node implementations for triage and repair."""

from __future__ import annotations

import json
import re
from typing import Any
from github import GithubException
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
    keys = ("run_id", "job_id", "task_key", "error_type", "error_message", "stack_trace")
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
    return _normalize_hunks(patch)


def _normalize_hunks(patch: str) -> str:
    """Recalculate unified-diff hunk counts without changing file content."""
    lines = patch.splitlines()
    normalized = []
    hunk_index = 0
    while hunk_index < len(lines):
        line = lines[hunk_index]
        if line.startswith("--- ") and not line.startswith("--- a/") and not line.startswith("--- /dev/null"):
            normalized.append(f"--- a/{line[4:]}")
            hunk_index += 1
            continue
        if line.startswith("+++ ") and not line.startswith("+++ b/") and not line.startswith("+++ /dev/null"):
            normalized.append(f"+++ b/{line[4:]}")
            hunk_index += 1
            continue
        match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$", line)
        if not match:
            normalized.append(line)
            hunk_index += 1
            continue
        body_start = hunk_index + 1
        body_end = body_start
        while body_end < len(lines) and not lines[body_end].startswith("@@ ") and not lines[body_end].startswith("diff "):
            body_end += 1
        old_count = sum(not body_line.startswith("+") for body_line in lines[body_start:body_end] if not body_line.startswith("\\"))
        new_count = sum(not body_line.startswith("-") for body_line in lines[body_start:body_end] if not body_line.startswith("\\"))
        normalized.append(f"@@ -{match.group(1)},{old_count} +{match.group(3)},{new_count} @@{match.group(5)}")
        normalized.extend(lines[body_start:body_end])
        hunk_index = body_end
    return "\n".join(normalized) + "\n"


def _target_contents(state: PipelineTriageState, settings: Settings) -> str:
    """Load target files from the configured GitHub branch for line-accurate fixes."""
    repository = GitHubClient(settings).repository
    sections = []
    for relative_path in state.get("target_files", []):
        content = _repository_file(repository, relative_path, settings.github_branch)
        if isinstance(content, list):
            continue
        source = content.decoded_content.decode("utf-8")
        numbered_source = "\n".join(f"{line_number:4} | {line}" for line_number, line in enumerate(source.splitlines(), 1))
        sections.append(f"--- {relative_path} ---\n{numbered_source}")
    return "\n\n".join(sections)


def _repository_file(repository: Any, relative_path: str, branch: str) -> Any:
    """Resolve package paths in repositories that use a ``src`` layout."""
    candidates = [relative_path]
    if not relative_path.startswith("src/"):
        candidates.append(f"src/{relative_path}")
    for candidate in candidates:
        try:
            content = repository.get_contents(candidate, ref=branch)
        except GithubException as exc:
            if exc.status == 404:
                continue
            raise
        if not isinstance(content, list):
            return content
    return []


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
    repository = GitHubClient(settings).repository
    target_files = []
    for relative_path in result.target_files:
        content = _repository_file(repository, relative_path, settings.github_branch)
        target_files.append(content.path if content else relative_path)
    logger.info("Root cause analysis completed", extra={"extra_data": {"target_files": target_files}})
    return {**state, "root_cause": result.root_cause, "target_files": target_files, "status": "rca_complete"}


async def fix_generator_node(state: PipelineTriageState, settings: Settings) -> PipelineTriageState:
    """Generate a minimal patch using RCA and previous validation feedback."""
    attempt = state.get("retry_count", 0) + 1
    set_context(step="fix_generation", status=f"attempt_{attempt}")
    system, human = prompts.render("fix_generator", root_cause=state.get("root_cause", ""), target_files=", ".join(state.get("target_files", [])), target_contents=_target_contents(state, settings), validation_output=state.get("validation_output", ""), failure_context=_context(state))
    response = await _model(settings.reasoning_model, settings).ainvoke([("system", system), ("human", human)])
    return {**state, "patch_diff": _diff(response), "retry_count": attempt, "status": "patch_generated"}


async def validator_node(state: PipelineTriageState, settings: Settings) -> PipelineTriageState:
    """Apply the generated patch in an ephemeral workspace and run pytest."""
    set_context(step="validation")
    try:
        with validation_workspace(state["repository_url"], settings.github_branch) as workspace:
            output, passed = run_docker_validation(workspace, state.get("patch_diff", ""), settings.pytest_timeout_seconds)
            if not passed and state.get("patch_diff", "").strip():
                output = f"{output}\nGENERATED_PATCH:\n{state['patch_diff']}"
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
