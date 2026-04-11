"""
Central observability module for the Clinical Trial Agent.

Defines ALL Prometheus metrics used across the agent package so that
metric names are consistent and there is a single source of truth.

Name mapping (reconciles all existing import references):

  service.py imports:      ACTIVE_QUERIES, QUERY_COUNT, QUERY_DURATION, setup_observability
  tool_node.py imports:    AGENT_TOOL_CALL_TOTAL, AGENT_TOOL_CALL_DURATION,
                           AGENT_CEILING_APPLIED_TOTAL, get_tracer
  synthesizer.py imports:  ITERATION_COUNT, QUERY_COUNT, QUERY_DURATION
  guardrails.py imports:   AGENT_ACCESS_DENIED_TOTAL, get_tracer
  agent_node.py imports:   AGENT_LLM_TOKEN_TOTAL, AGENT_ITERATION_COUNT, get_tracer

All names are defined once here. Aliases are provided where two files
refer to the same metric by different names.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from prometheus_client import Counter, Gauge, Histogram

# ── OpenTelemetry ──────────────────────────────────────────────────────────────
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource

# ── Phoenix + LangChain instrumentation ───────────────────────────────────────
try:
    from openinference.instrumentation.langchain import LangChainInstrumentor
    _LANGCHAIN_INSTRUMENTATION_AVAILABLE = True
except ImportError:
    _LANGCHAIN_INSTRUMENTATION_AVAILABLE = False
    logging.warning(
        "openinference-instrumentation-langchain not installed; "
        "LangChain tracing disabled"
    )

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Prometheus Metric Definitions
# ══════════════════════════════════════════════════════════════════════════════

# ── Query-level ───────────────────────────────────────────────────────────────

QUERY_COUNT = Counter(
    "agent_query_total",
    "Total number of agent queries processed",
    ["model", "status", "complexity"],
    # status:     success | error | access_denied
    # complexity: simple  | complex
)
# Alias used by service.py
AGENT_QUERY_TOTAL = QUERY_COUNT

QUERY_DURATION = Histogram(
    "agent_query_duration_seconds",
    "End-to-end query duration including MCP calls and LLM reasoning",
    ["model", "complexity"],
    buckets=[0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0, 60.0],
)

ACTIVE_QUERIES = Gauge(
    "agent_active_queries",
    "Number of queries currently being processed",
)
# Alias for any future code that uses the longer name
AGENT_ACTIVE_QUERIES = ACTIVE_QUERIES

ITERATION_COUNT = Histogram(
    "agent_iteration_count",
    "Number of ReAct iterations per query",
    buckets=[1, 2, 3, 4, 5, 7, 10, 15],
)
# Alias used by agent_node.py
AGENT_ITERATION_COUNT = ITERATION_COUNT

# ── Tool-level ────────────────────────────────────────────────────────────────

AGENT_TOOL_CALL_TOTAL = Counter(
    "agent_tool_call_total",
    "Total tool invocations made by the agent",
    ["tool_name", "status"],
    # status: success | error | empty_result | unknown_tool
)

AGENT_TOOL_CALL_DURATION = Histogram(
    "agent_tool_call_duration_seconds",
    "Duration of individual MCP tool calls as observed by the agent",
    ["tool_name"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
)

# ── LLM token tracking ────────────────────────────────────────────────────────

AGENT_LLM_TOKEN_TOTAL = Counter(
    "agent_llm_token_total",
    "Total LLM tokens consumed",
    ["model", "token_type"],
    # token_type: prompt | completion | total
)

# ── Authorization ─────────────────────────────────────────────────────────────

AGENT_ACCESS_DENIED_TOTAL = Counter(
    "agent_access_denied_total",
    "Queries rejected at the authorization guardrail",
    ["reason"],
    # reason: no_access | unauthorized_trial_scope | role_mismatch
)

# ── Ceiling principle ─────────────────────────────────────────────────────────

AGENT_CEILING_APPLIED_TOTAL = Counter(
    "agent_ceiling_applied_total",
    "Times the aggregate ceiling was applied in multi-trial queries",
)


# ══════════════════════════════════════════════════════════════════════════════
# OpenTelemetry + Phoenix Tracing Setup
# ══════════════════════════════════════════════════════════════════════════════

_tracing_initialized: bool = False

def setup_observability() -> TracerProvider | None:
    phoenix_endpoint = os.getenv("PHOENIX_ENDPOINT", "http://phoenix:6006/v1/traces")
    project_name = os.getenv("PHOENIX_PROJECT_NAME", "clinical-trial-agent")
    return setup_observability(phoenix_endpoint, project_name)

def setup_observability(
    phoenix_endpoint: str | None = None,
    project_name: str = "clinical-trial-agent",
    enable: bool = True,
) -> TracerProvider | None:
    """
    Initialize OpenTelemetry with Phoenix as the OTLP backend.
    Auto-instruments LangChain so every LLM call and tool call gets a span.

    Called once at AgentService.__init__().
    Calling multiple times is safe — subsequent calls are no-ops.

    Args:
        phoenix_endpoint: Full OTLP HTTP endpoint, e.g.
                          "http://phoenix:6006/v1/traces"
        project_name:     Phoenix project label for grouping traces.
        enable:           Set False to skip tracing (e.g. in unit tests).

    Returns:
        The TracerProvider, or None if tracing is disabled.
    """
    global _tracing_initialized

    if _tracing_initialized:
        return trace.get_tracer_provider()  # type: ignore[return-value]

    if not enable:
        logger.info("Observability tracing disabled (enable=False)")
        return None

    endpoint = phoenix_endpoint or os.getenv(
        "PHOENIX_ENDPOINT", "http://phoenix:6006/v1/traces"
    )

    resource = Resource.create(
        {
            "service.name": project_name,
            "service.version": "1.0.0",
            "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        }
    )

    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    if _LANGCHAIN_INSTRUMENTATION_AVAILABLE:
        LangChainInstrumentor().instrument(tracer_provider=provider)
        logger.info(
            "LangChain auto-instrumentation active → Phoenix at %s", endpoint
        )
    else:
        logger.warning(
            "LangChain instrumentation unavailable; "
            "install openinference-instrumentation-langchain"
        )

    _tracing_initialized = True
    logger.info("OpenTelemetry tracing initialized → %s", endpoint)
    return provider


@lru_cache(maxsize=None)
def get_tracer(name: str = "clinical-trial-agent") -> trace.Tracer:
    """
    Return a named OpenTelemetry tracer for manual span creation.

    Cached so repeated calls with the same name return the same tracer.

    Usage:
        tracer = get_tracer()
        with tracer.start_as_current_span("my.operation") as span:
            span.set_attribute("key", "value")
    """
    return trace.get_tracer(name)