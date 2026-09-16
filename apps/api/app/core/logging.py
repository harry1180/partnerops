"""Structured logging with correlation IDs (structlog, JSON in prod, console locally)."""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog

from app.core.config import get_settings

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")

_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    level = getattr(logging, settings.access_log_level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stdout, level=logging.WARNING, format="%(message)s")
    logging.getLogger("uvicorn.error").setLevel(level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _add_correlation_id,  # type: ignore[list-item]
            structlog.dev.ConsoleRenderer() if settings.is_local else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
    _configured = True


def _add_correlation_id(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    cid = correlation_id_var.get()
    if cid:
        event_dict["correlation_id"] = cid
    return event_dict


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
