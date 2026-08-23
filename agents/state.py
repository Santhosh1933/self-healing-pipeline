"""Workflow state and structured LLM result models."""

from typing import Literal, TypedDict
from pydantic import BaseModel, Field

Classification = Literal["TRANSIENT", "CODE_DEFECT"]


class PipelineTriageState(TypedDict, total=False):
    """State passed between pipeline triage graph nodes."""
    run_id: str
    job_id: str
    task_key: str
    error_type: str
    error_message: str
    stack_trace: str
    commit_sha: str
    classification: Classification
    root_cause: str
    target_files: list[str]
    patch_diff: str
    validation_output: str
    retry_count: int
    status: str
    issue_url: str
    pull_request_url: str
    repository_url: str


class FailureWebhookPayload(BaseModel):
    """Validated payload accepted from a pipeline failure webhook."""
    run_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    task_key: str = Field(min_length=1)
    error_type: str = Field(min_length=1)
    error_message: str = Field(min_length=1)
    stack_trace: str = ""
    commit_sha: str = ""
    repository_url: str = "https://github.com/Santhosh1933/brazilian-data-etl-pipeline.git"


class ClassificationResult(BaseModel):
    """Structured classifier response."""
    classification: Classification
    reason: str


class RCAResult(BaseModel):
    """Structured root-cause analysis response."""
    root_cause: str
    target_files: list[str]
