"""Backward-compatible import for the AutoHeal FastAPI application."""

from main import app, create_app

__all__ = ["app", "create_app"]
