"""
Prometheus metrics for the Semantic MCP server process.
"""

from __future__ import annotations

import time
import functools
import logging
from typing import Any, Callable, Coroutine

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

logger = logging.getLogger(__name__)

SEMANTIC_TOOL_CALL_TOTAL = Counter(
    "semantic_mcp_tool_call_total",
    "Total Semantic MCP tool invocations",
    ["tool_name", "status"],
)

SEMANTIC_TOOL_CALL_DURATION = Histogram(
    "semantic_mcp_tool_call_duration_seconds",
    "Duration of Semantic MCP tool execution",
    ["tool_name"],
    buckets=[0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
)


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def instrument_tool(tool_name: str):
    """Decorator that wraps an async semantic MCP tool with timing and counting."""
    def decorator(fn: Callable[..., Coroutine[Any, Any, Any]]):
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            status = "success"
            try:
                result = await fn(*args, **kwargs)
                return result
            except PermissionError:
                status = "unauthorized"
                raise
            except Exception:
                status = "error"
                raise
            finally:
                elapsed = time.perf_counter() - start
                SEMANTIC_TOOL_CALL_TOTAL.labels(tool_name=tool_name, status=status).inc()
                SEMANTIC_TOOL_CALL_DURATION.labels(tool_name=tool_name).observe(elapsed)
        return wrapper
    return decorator
