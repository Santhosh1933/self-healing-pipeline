"""Structured JSON logging with workflow context."""

import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

_context: ContextVar[dict[str, Any]] = ContextVar("autoheal_context", default={})


class JSONFormatter(logging.Formatter):
    """Format log records as machine-readable JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log record and contextual fields."""
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": record.levelname, "logger": record.name, "message": record.getMessage(), **_context.get(), "extra": getattr(record, "extra_data", {})}
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def get_logger(name: str = "autoheal") -> logging.Logger:
    """Return a configured JSON logger."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def set_context(**fields: Any) -> None:
    """Set fields included in subsequent logs for the current execution context."""
    _context.set({key: value for key, value in fields.items() if value is not None})
