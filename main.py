"""Application factory and Uvicorn entrypoint."""

import os
from fastapi import FastAPI
from pydantic import ValidationError
from agents.graph import build_graph
from api.routes import create_router
from config.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the AutoHeal FastAPI application."""
    app = FastAPI(title="AutoHeal-DataEngine", version="1.0.0")
    app.state.settings = settings
    graph = build_graph(settings) if settings else None
    app.include_router(create_router(graph, settings.repo_name if settings else None))
    return app


try:
    app = create_app(Settings.from_environment())
except (RuntimeError, ValidationError):
    app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
