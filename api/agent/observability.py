"""
Observability setup: Prometheus metrics + Arize Phoenix OTEL tracing.

Call setup_observability() once at application startup (in FastAPI lifespan).
Metrics are registered globally and exported via /metrics endpoint.
Phoenix traces every LangChain LLM call and tool invocation automatically.
"""

from __future__ import annotations

import logging
import os

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, REGISTRY

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Prometheus Metrics
# ─────────────────────────────────────────────────────────────────────────────

# Guard against double-registration during hot reload in development
def _get_or_create(metric_class, name, description, labelnames=(), **kwargs):
    """Get existing metric or create new one — safe for repeated imports."""
    try:
        return metric_class(name, description, labelnames, **kwargs)
    except ValueError:
        # Already registered
        return REGISTRY._names_to_collectors.get(name)


QUERY_COUNT = _get_or_create(
    Counter,
    "agent_query_total",
    "Total agent queries processed",
    ["model", "status", "complexity"],
)

QUERY_DURATION = _get_or_create(
    Histogram,
    "agent_query_duration_seconds",
    "Agent query end-to-end latency",
    ["model", "complexity"],
    buckets=[0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0],
)

TOOL_CALL_COUNT = _get_or_create(
    Counter,
    "agent_tool_call_total",
    "Total tool invocations",
    ["tool_name", "status"],
)

TOOL_CALL_DURATION = _get_or_create(
    Histogram,
    "agent_tool_call_duration_seconds",
    "Tool invocation latency",
    ["tool_name"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

TOKEN_USAGE = _get_or_create(
    Counter,
    "agent_llm_tokens_total",
    "LLM token usage",
    ["model", "token_type"],  # token_type: prompt | completion
)

ACTIVE_QUERIES = _get_or_create(
    Gauge,
    "agent_active_queries",
    "Currently executing agent queries",
)

ACCESS_DENIED_COUNT = _get_or_create(
    Counter,
    "agent_access_denied_total",
    "Queries rejected due to insufficient access",
    ["reason"],
)

ITERATION_COUNT = _get_or_create(
    Histogram,
    "agent_iteration_count",
    "Number of ReAct iterations per query",
    buckets=[1, 2, 3, 4, 5, 7, 10],
)


# ─────────────────────────────────────────────────────────────────────────────
# Phoenix / OTEL Tracing Setup
# ─────────────────────────────────────────────────────────────────────────────

_tracing_initialized = False


def setup_observability(phoenix_endpoint: str, project_name: str, enable: bool = True) -> None:
    """
    Initialize Arize Phoenix tracing and LangChain auto-instrumentation.

    Call once during FastAPI application startup.
    If Phoenix is unavailable, logs a warning and continues — tracing is
    best-effort and should never block the application.
    """
    global _tracing_initialized

    if _tracing_initialized:
        return

    if not enable:
        logger.info("Observability tracing disabled (ENABLE_TRACING=false)")
        _tracing_initialized = True
        return

    try:
        from phoenix.otel import register
        from openinference.instrumentation.langchain import LangChainInstrumentor

        tracer_provider = register(
            project_name=project_name,
            endpoint=phoenix_endpoint,
            set_global_tracer_provider=True,
        )

        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)

        logger.info(
            f"Phoenix tracing initialized → {phoenix_endpoint} "
            f"(project: {project_name})"
        )
    except ImportError:
        logger.warning(
            "Phoenix tracing packages not installed. "
            "Install arize-phoenix-otel and openinference-instrumentation-langchain."
        )
    except Exception as exc:
        logger.warning(
            f"Phoenix tracing initialization failed (non-fatal): {exc}. "
            "Continuing without tracing."
        )

    _tracing_initialized = True