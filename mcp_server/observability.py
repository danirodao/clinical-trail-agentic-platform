"""
Prometheus metrics for the MCP server process.

The MCP server runs in a separate container.
This module defines tool-level metrics and exposes a /metrics endpoint
that Prometheus scrapes independently of the API container.
"""

from __future__ import annotations

import inspect
import time
import functools
import logging
from typing import Any, Callable, Coroutine

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

logger = logging.getLogger(__name__)

# ── Metric definitions ────────────────────────────────────────────────────────

MCP_TOOL_CALL_TOTAL = Counter(
    "mcp_tool_call_total",
    "Total MCP tool invocations received by the server",
    ["tool_name", "status"],  # status: success | error | unauthorized | empty
)

MCP_TOOL_CALL_DURATION = Histogram(
    "mcp_tool_call_duration_seconds",
    "Duration of MCP tool execution including DB queries",
    ["tool_name"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
)

MCP_DB_QUERY_DURATION = Histogram(
    "mcp_db_query_duration_seconds",
    "Duration of individual database queries within tools",
    ["db", "operation"],  # db: postgres | qdrant | neo4j
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

MCP_AUTH_FAILURES_TOTAL = Counter(
    "mcp_auth_failures_total",
    "Bearer token validation failures on the MCP server",
)

MCP_ACTIVE_TOOL_CALLS = Counter(
    "mcp_active_tool_calls_total",
    "Running total of active (in-flight) tool executions",
    ["tool_name"],
)


# ── Decorator: automatically instrument any async tool function ───────────────

def instrument_tool(tool_name: str):
    """
    Decorator that wraps an async MCP tool function with:
      - Duration timing → MCP_TOOL_CALL_DURATION
      - Success/error counting → MCP_TOOL_CALL_TOTAL
      - Structured logging

    Usage:
        @instrument_tool("count_patients")
        async def count_patients(access_context: str, trial_ids: str, ...) -> dict:
            ...
    """
    def decorator(fn: Callable[..., Coroutine[Any, Any, Any]]):
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            status = "success"
            try:
                result = await fn(*args, **kwargs)
                # Treat empty results as a distinct status for alerting
                if isinstance(result, dict) and result.get("status") == "empty":
                    status = "empty"
                return result
            except PermissionError:
                status = "unauthorized"
                raise
            except Exception as exc:
                status = "error"
                logger.exception("Tool %s failed: %s", tool_name, exc)
                raise
            finally:
                duration = time.perf_counter() - start
                MCP_TOOL_CALL_TOTAL.labels(tool_name=tool_name, status=status).inc()
                MCP_TOOL_CALL_DURATION.labels(tool_name=tool_name).observe(duration)
                logger.info(
                    "mcp_tool_executed: %s (status=%s, duration=%sms)",
                    tool_name,
                    status,
                    round(duration * 1000, 1),
                    extra={
                        "tool": tool_name,
                        "status": status,
                        "duration_ms": round(duration * 1000, 1),
                    },
                )
        
        # CRITICAL: Preserve the original signature for FastMCP inspection
        wrapper.__signature__ = inspect.signature(fn)
        return wrapper
    return decorator


def metrics_response() -> tuple[bytes, str]:
    """Return (body, content_type) for a Prometheus scrape response."""
    return generate_latest(), CONTENT_TYPE_LATEST