"""HTTP request and response schemas."""

from pydantic import BaseModel, Field
from agents.state import FailureWebhookPayload


class AcceptedResponse(BaseModel):
    """Response returned after a failure is queued."""
    status: str
    run_id: str


__all__ = ["AcceptedResponse", "FailureWebhookPayload"]
