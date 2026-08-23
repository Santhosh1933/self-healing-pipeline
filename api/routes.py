"""FastAPI route definitions for health and failure webhooks."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from agents.state import FailureWebhookPayload
from api.schemas import AcceptedResponse


def create_router(graph: object | None, repository_name: str | None) -> APIRouter:
    """Create routes bound to the compiled triage graph."""
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        """Report service liveness."""
        return {"status": "ok"}

    @router.post("/webhook/pipeline-failure", response_model=AcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
    async def pipeline_failure(payload: FailureWebhookPayload, background_tasks: BackgroundTasks) -> AcceptedResponse:
        """Accept a failure event and start graph execution in the background."""
        if graph is None or not repository_name:
            raise HTTPException(status_code=503, detail="Service is not configured")
        state = {**payload.model_dump(), "retry_count": 0, "status": "received", "repository_url": f"https://github.com/{repository_name}.git"}
        background_tasks.add_task(graph.ainvoke, state)
        return AcceptedResponse(status="accepted", run_id=payload.run_id)

    return router
