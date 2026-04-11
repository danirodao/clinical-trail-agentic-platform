"""
Structured logging configuration using structlog.

Features:
  - JSON output in production (ENVIRONMENT != development)
  - Coloured console output in development
  - Context variables: bind per-request fields (user_id, query_id, org_id)
    using structlog.contextvars so they auto-appear in every log line
    within an async request without manual passing.

Usage:
    from api.logging_config import configure_logging, get_logger, bind_request_context

    # In main.py lifespan (call before anything else logs):
    configure_logging()

    # In a FastAPI dependency or route:
    bind_request_context(user_id="jane", query_id="abc", org_id="org-pharma")

    # Anywhere in the call stack:
    log = get_logger(__name__)
    log.info("tool_called", tool="count_patients", duration_ms=120)

Why stdlib.LoggerFactory instead of PrintLoggerFactory:
    structlog.stdlib.add_logger_name reads the .name attribute from the
    underlying logger object.  PrintLogger (from PrintLoggerFactory) does
    not have a .name attribute, so add_logger_name raises AttributeError.
    stdlib.LoggerFactory wraps Python's standard logging.Logger, which
    always has a .name, fixing the crash while also meaning that uvicorn,
    LangChain, httpx, etc. all flow through the same structured pipeline.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars


def configure_logging() -> None:
    """
    Call once at application startup before anything else logs.
    Sets up structlog backed by Python's stdlib logging so that:
      - structlog.get_logger() loggers are structured
      - stdlib loggers (uvicorn, LangChain, httpx…) also emit structured output
    """
    is_dev = os.getenv("ENVIRONMENT", "development") == "development"
    log_level = logging.DEBUG if is_dev else logging.INFO

    # ── Processors shared by both structlog and stdlib renderers ──────────────
    # These run on every log call, in order.
    shared_processors: list[Any] = [
        merge_contextvars,                        # inject per-request context vars
        structlog.stdlib.add_log_level,           # adds "level": "info"
        structlog.stdlib.add_logger_name,         # adds "logger": "api.agent.service"
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),  # replaces format_exc_info (py3.12+)
    ]

    if is_dev:
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    # ── Configure structlog ───────────────────────────────────────────────────
    structlog.configure(
        processors=shared_processors + [renderer],
        # make_filtering_bound_logger wraps stdlib's BoundLogger
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        # stdlib.LoggerFactory is required for add_logger_name (has .name attr)
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ── Configure root stdlib logger ──────────────────────────────────────────
    # This ensures third-party libraries (uvicorn, langchain, httpx, etc.)
    # that use logging.getLogger() will also emit structured output.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Quiet noisy libraries in production
    if not is_dev:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str = "api") -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to the given name."""
    return structlog.get_logger(name)


def bind_request_context(**kwargs: Any) -> None:
    """
    Bind key/value pairs to the current async context.
    All subsequent log calls in the same async task will include these fields.

    Example:
        bind_request_context(user_id="jane", query_id="uuid", org_id="org-pharma")
    """
    bind_contextvars(**kwargs)


def clear_request_context() -> None:
    """Clear all context vars at the end of a request."""
    clear_contextvars()