"""
Dynamically generates LangChain StructuredTools from the MCP server's exposed tools.

KEY SECURITY DESIGN:
  - Fetches the tool list and JSON schemas directly from the running MCP server.
  - Strips the `access_context` parameter out of the schema dynamically.
  - Creates a Pydantic model on the fly so the LLM never sees the context parameter.
  - Injects `access_context` invisibly when the tool is actually called.
"""

import asyncio
import hashlib
import json
import logging
import time
from enum import Enum
from typing import Any

import structlog
from langchain_core.tools import StructuredTool
from mcp import ClientSession
from pydantic import create_model, Field

logger = structlog.get_logger(__name__)

MAX_ARG_LOG_CHARS = 2000
MAX_RESULT_LOG_CHARS = 2000

# ─────────────────────────────────────────────────────────────────────────────
# Tool result TTL cache
# ─────────────────────────────────────────────────────────────────────────────

# Tools whose results are deterministic for identical (access_context, args).
# These are safe to cache; all patient-query and data-mutation tools are NOT.
CACHEABLE_TOOLS: frozenset[str] = frozenset({
    "get_trial_metadata",
    "list_ontology_concepts",
    "get_concept_definition",
    "get_field_concept_map",
    "explain_metric_semantics",
    "map_code_to_concept",
    "map_concept_to_codes",
    "normalize_clinical_term",
})


class _ToolCache:
    """
    In-process TTL cache for deterministic MCP tool calls.

    Key: SHA-256( tool_name + access_context_json + sorted_args_json )
    Value: (result_dict, expiry_timestamp)

    Thread/async safety: Python's GIL protects dict reads/writes.  No lock
    needed because asyncio tasks are cooperative and never preempt during a
    single dict lookup or assignment.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def _key(self, tool_name: str, access_context_json: str, kwargs: dict) -> str:
        payload = json.dumps(
            {"tool": tool_name, "ctx": access_context_json, "args": kwargs},
            sort_keys=True,
            default=str,
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    def get(self, tool_name: str, access_context_json: str, kwargs: dict) -> Any | None:
        key = self._key(tool_name, access_context_json, kwargs)
        entry = self._store.get(key)
        if entry is None:
            return None
        result, expiry = entry
        if time.monotonic() > expiry:
            del self._store[key]
            return None
        return result

    def set(
        self,
        tool_name: str,
        access_context_json: str,
        kwargs: dict,
        result: Any,
        ttl_seconds: int,
    ) -> None:
        key = self._key(tool_name, access_context_json, kwargs)
        self._store[key] = (result, time.monotonic() + ttl_seconds)

    def size(self) -> int:
        return len(self._store)


# Module-level singleton — shared across all queries in the same process
_tool_cache = _ToolCache()


# ───────────────────────────────────────────────────────────────────────────────
# Async Circuit Breaker
# ───────────────────────────────────────────────────────────────────────────────

class _CBState(Enum):
    CLOSED    = 0  # normal operation
    OPEN      = 1  # fast-fail all calls
    HALF_OPEN = 2  # one probe allowed; success → CLOSED, failure → OPEN


class _CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit breaker is OPEN."""


class _AsyncCircuitBreaker:
    """
    Lightweight async circuit breaker — no external dependencies.

    CLOSED  → fail_max consecutive failures  → OPEN
    OPEN    → reset_timeout elapsed           → HALF_OPEN (one probe)
    HALF_OPEN → probe success                 → CLOSED
    HALF_OPEN → probe failure                 → OPEN
    """

    def __init__(
        self,
        name: str,
        fail_max: int = 5,
        reset_timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._state = _CBState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> _CBState:
        return self._state

    async def call(self, coro):
        """Await *coro* under circuit-breaker control."""
        async with self._lock:
            if self._state == _CBState.OPEN:
                elapsed = time.monotonic() - (self._opened_at or 0.0)
                if elapsed >= self.reset_timeout:
                    self._state = _CBState.HALF_OPEN
                    logger.info("circuit_breaker_half_open", extra={"server": self.name})
                    self._update_metrics()
                else:
                    self._update_metrics()
                    raise _CircuitOpenError(self.name)

        try:
            result = await coro
        except _CircuitOpenError:
            raise
        except Exception:
            async with self._lock:
                self._failures += 1
                if self._state in (_CBState.CLOSED, _CBState.HALF_OPEN):
                    if (
                        self._failures >= self.fail_max
                        or self._state == _CBState.HALF_OPEN
                    ):
                        self._state = _CBState.OPEN
                        self._opened_at = time.monotonic()
                        logger.warning(
                            "circuit_breaker_opened",
                            extra={"server": self.name, "failures": self._failures},
                        )
                        self._update_metrics()
            raise
        else:
            async with self._lock:
                if self._state == _CBState.HALF_OPEN:
                    self._state = _CBState.CLOSED
                    self._failures = 0
                    logger.info("circuit_breaker_closed", extra={"server": self.name})
                    self._update_metrics()
                elif self._state == _CBState.CLOSED:
                    # Reset consecutive failure count on any success.
                    self._failures = 0
            return result

    def _update_metrics(self) -> None:
        """Sync Prometheus gauges / counters to current state (best-effort)."""
        try:
            from .observability import (
                AGENT_MCP_CIRCUIT_BREAKER_OPEN_TOTAL,
                AGENT_MCP_CIRCUIT_BREAKER_STATE,
            )
            AGENT_MCP_CIRCUIT_BREAKER_STATE.labels(server=self.name).set(
                self._state.value
            )
            if self._state == _CBState.OPEN:
                AGENT_MCP_CIRCUIT_BREAKER_OPEN_TOTAL.labels(server=self.name).inc()
        except Exception:
            pass  # Never fail because of metrics


# One breaker per MCP server — module-level singletons shared across all sessions.
_DATA_MCP_BREAKER     = _AsyncCircuitBreaker("data",     fail_max=5, reset_timeout=30.0)
_SEMANTIC_MCP_BREAKER = _AsyncCircuitBreaker("semantic", fail_max=5, reset_timeout=30.0)
_BREAKERS: dict[str, _AsyncCircuitBreaker] = {
    "data":     _DATA_MCP_BREAKER,
    "semantic": _SEMANTIC_MCP_BREAKER,
}


def _preview_text(value: Any, max_chars: int) -> str:
    """Render a bounded string representation suitable for logs."""
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "... [truncated]"



def _parse_tool_result(result: Any) -> dict:
    """Parse the MCP tool call result into a Python dict."""
    if hasattr(result, "content"):
        content = result.content
        if isinstance(content, list) and content:
            text = content[0].text if hasattr(content[0], "text") else str(content[0])
        else:
            text = str(content)
    else:
        text = str(result)

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"raw": text}


def _sanitize_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Remove hidden access context from logs and avoid oversized payloads."""
    return {
        k: v
        for k, v in kwargs.items()
        if k != "access_context"
    }


def _map_json_schema_to_python_type(prop_info: dict[str, Any]) -> Any:
    """Map JSON Schema property definitions to Python types for Pydantic models."""
    if "anyOf" in prop_info:
        mapped = []
        for option in prop_info.get("anyOf", []):
            option_type = option.get("type")
            if option_type == "string":
                mapped.append(str)
            elif option_type == "integer":
                mapped.append(int)
            elif option_type == "number":
                mapped.append(float)
            elif option_type == "boolean":
                mapped.append(bool)
            elif option_type == "array":
                mapped.append(list[str])
            elif option_type == "object":
                mapped.append(dict)
            elif option_type == "null":
                mapped.append(type(None))

        if mapped:
            py_type = mapped[0]
            for t in mapped[1:]:
                py_type = py_type | t
            return py_type

    json_type = prop_info.get("type", "string")
    if json_type == "string":
        return str
    if json_type == "integer":
        return int
    if json_type == "number":
        return float
    if json_type == "boolean":
        return bool
    if json_type == "array":
        return list[str]
    if json_type == "object":
        return dict
    return Any


async def create_secure_tools(
    session: ClientSession,
    access_context_json: str,
    server_name: str = "data",
) -> list[StructuredTool]:
    """
    Dynamically discover tools from the MCP server, hide the access_context parameter,
    and build LangChain StructuredTools.

    server_name: "data" | "semantic" — selects the circuit breaker instance.
    """
    logger.debug("Fetching tool list from MCP server...")

    # 1. Ask the MCP server for its available tools
    try:
        response = await session.list_tools()
        logger.debug("mcp_tool_catalog_received", count=len(response.tools))
        if not response.tools:
            logger.error("mcp_tool_catalog_empty")
            return []
            
    except Exception as e:
        logger.error("mcp_tool_catalog_failed", error=str(e), exc_info=True)
        return []
    tools = []

    for tool_def in response.tools:
        tool_name = tool_def.name
        description = tool_def.description
        schema = tool_def.inputSchema

        # 2. Build a dynamic Pydantic schema (excluding access_context)
        fields = {}
        for prop_name, prop_info in schema.get("properties", {}).items():
            if prop_name == "access_context":
                continue

            # Map JSON schema types to Python types (supports anyOf unions).
            py_type = _map_json_schema_to_python_type(prop_info)
            
            is_required = prop_name in schema.get("required", [])
            
            # Extract defaults provided by the server, or None if optional
            default_val = prop_info.get("default", ... if is_required else None)
            
            # Allow None types for optional fields
            if default_val is None and not is_required:
                py_type = py_type | None
            
            # Add to Pydantic fields dictionary
            fields[prop_name] = (
                py_type, 
                Field(default=default_val, description=prop_info.get("description", ""))
            )

        # Create the Pydantic class in memory
        DynamicSchema = create_model(f"{tool_name}Input", **fields)

        # 3. Create the execution closure
        def make_caller(t_name: str, s_name: str):
            async def _caller(**kwargs) -> dict:
                # Secretly inject the authorization context
                kwargs["access_context"] = access_context_json

                # Remove None values so MCP uses its native defaults
                clean_kwargs = {k: v for k, v in kwargs.items() if v is not None}
                public_kwargs = _sanitize_args(clean_kwargs)
                started = time.perf_counter()

                # ── TTL cache check for deterministic lookup tools ──────────
                if t_name in CACHEABLE_TOOLS:
                    cached = _tool_cache.get(t_name, access_context_json, public_kwargs)
                    if cached is not None:
                        logger.debug(
                            "tool_cache_hit",
                            tool=t_name,
                            cache_size=_tool_cache.size(),
                        )
                        return cached

                logger.info(
                    "tool_invocation",
                    tool=t_name,
                    arguments_preview=_preview_text(public_kwargs, MAX_ARG_LOG_CHARS),
                )

                try:
                    breaker = _BREAKERS.get(s_name, _DATA_MCP_BREAKER)
                    result = await breaker.call(
                        session.call_tool(t_name, arguments=clean_kwargs)
                    )
                    parsed = _parse_tool_result(result)
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    logger.info(
                        "tool_result",
                        tool=t_name,
                        duration_ms=duration_ms,
                        result_preview=_preview_text(parsed, MAX_RESULT_LOG_CHARS),
                        status=("error" if isinstance(parsed, dict) and parsed.get("error") else "success"),
                    )

                    # Store in cache if eligible and result is clean
                    if (
                        t_name in CACHEABLE_TOOLS
                        and isinstance(parsed, dict)
                        and not parsed.get("error")
                    ):
                        from .config import agent_config as _cfg  # avoid circular at module level
                        _tool_cache.set(
                            t_name, access_context_json, public_kwargs,
                            parsed, _cfg.tool_cache_ttl_seconds,
                        )

                    return parsed
                except _CircuitOpenError:
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    logger.warning(
                        "circuit_breaker_rejected",
                        extra={"tool": t_name, "server": s_name},
                    )
                    try:
                        from .observability import AGENT_MCP_CIRCUIT_BREAKER_REJECTED_TOTAL
                        AGENT_MCP_CIRCUIT_BREAKER_REJECTED_TOTAL.labels(server=s_name).inc()
                    except Exception:
                        pass
                    return {
                        "error": "service_unavailable",
                        "tool": t_name,
                        "message": (
                            "The clinical data service is temporarily unavailable. "
                            "Please try again in a moment."
                        ),
                    }
                except Exception as e:
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    logger.error(
                        "tool_failed",
                        tool=t_name,
                        duration_ms=duration_ms,
                        arguments_preview=_preview_text(public_kwargs, MAX_ARG_LOG_CHARS),
                        error=str(e),
                    )
                    return {"error": str(e), "tool": t_name}

            _caller.__name__ = t_name
            _caller.__doc__ = description
            return _caller

        # 4. Wrap it in a LangChain StructuredTool
        structured_tool = StructuredTool.from_function(
            coroutine=make_caller(tool_name, server_name),
            name=tool_name,
            description=description,
            args_schema=DynamicSchema,
            return_direct=False,
        )
        tools.append(structured_tool)

    logger.info("mcp_tools_loaded", count=len(tools))
    return tools