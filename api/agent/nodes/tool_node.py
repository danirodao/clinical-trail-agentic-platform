"""
Tool execution node — runs all tool calls from the last AIMessage.

Key behaviors:
  - Executes tool calls sequentially (asyncpg single-connection safety rule)
  - Records timing per tool call in ToolCallRecord format
  - Updates Prometheus tool_call counters and histograms
  - Creates an OpenTelemetry child span per tool call
  - Handles tool errors gracefully (returns error dict, lets LLM retry/explain)
  - Appends ToolMessages to the message list for LangGraph's expected format

Phase 5 fixes applied:
  - `span` is now defined inside a `with tracer.start_as_current_span()` block
  - Removed reference to undefined `TOOL_CALL_COUNT` (uses AGENT_TOOL_CALL_TOTAL)
  - Fixed `duration` → `duration_ms` variable name in Prometheus observe() call
  - span.set_attribute("tool.ceiling_applied") moved inside the span context
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from opentelemetry import trace

from ..observability import (
    AGENT_CEILING_APPLIED_TOTAL,
    AGENT_TOOL_CALL_DURATION,
    AGENT_TOOL_CALL_TOTAL,
    AGENT_TOOL_TIMEOUT_TOTAL,
    get_tracer,
)
from ..models import AgentState, Timer, ToolCallRecord

logger = logging.getLogger(__name__)
tracer = get_tracer()

# Prevent semantic-only planning loops from exhausting max_iterations.
# If too many semantic calls happen before any data tool call, force a pivot.
MAX_SEMANTIC_PREFLIGHT_CALLS = 8

SEMANTIC_TOOL_NAMES = {
    "get_semantic_cognitive_frame",
    "resolve_semantic_term",
    "get_concept_definition",
    "list_ontology_concepts",
    "get_field_concept_map",
    "map_code_to_concept",
    "map_concept_to_codes",
    "semantic_compatibility_check",
    "explain_metric_semantics",
}

# ── Per-tool timeout budgets (seconds) ─────────────────────────────────────────────────
# Semantic/ontology tools hit Neo4j read-only paths and should return quickly.
# Heavy analytical tools join Postgres + Qdrant and get a larger budget.
TOOL_TIMEOUTS: dict[str, float] = {
    # Semantic / ontology (Neo4j read-only)
    "resolve_semantic_term":              5.0,
    "get_semantic_cognitive_frame":       5.0,
    "get_concept_definition":             5.0,
    "list_ontology_concepts":             5.0,
    "get_field_concept_map":              5.0,
    "map_code_to_concept":                5.0,
    "map_concept_to_codes":               5.0,
    "semantic_compatibility_check":       5.0,
    "explain_metric_semantics":           5.0,
    # Light Postgres reads
    "get_trial_metadata":                10.0,
    "search_trials":                     10.0,
    "medication_interaction_check":      15.0,
    # Moderate analytics
    "get_patient_demographics":          15.0,
    "get_adverse_events":                20.0,
    "lab_result_trends":                 20.0,
    "data_quality_overview":             20.0,
    "cohort_outcome_snapshot":           20.0,
    # Heavy cross-trial analytics (Postgres + Qdrant)
    "cross_trial_safety_summary":        25.0,
    "comparative_effectiveness_analysis": 30.0,
    # Default for any unregistered tool
    "__default__":                       15.0,
}

# ── Per-category concurrency semaphores ────────────────────────────────────────────
# Caps the number of in-flight heavy DB calls per process to protect the
# asyncpg connection pool.  Lightweight semantic tools share a higher limit.
TOOL_SEMAPHORE_CLASS: dict[str, str] = {
    "cross_trial_safety_summary":           "analytical",
    "comparative_effectiveness_analysis":   "analytical",
    "lab_result_trends":                    "analytical",
    "get_adverse_events":                   "analytical",
    "cohort_outcome_snapshot":              "analytical",
    "data_quality_overview":                "analytical",
    # everything else → "semantic" (lighter, higher concurrency allowed)
}
_SEMAPHORE_LIMITS: dict[str, int] = {"analytical": 4, "semantic": 8}
# Lazily initialised inside the running event loop (safe for Python 3.10+).
_SEMAPHORES: dict[str, asyncio.Semaphore | None] = {"analytical": None, "semantic": None}


def _get_semaphore(class_name: str) -> asyncio.Semaphore:
    """Return (lazily creating) the semaphore for the given tool class."""
    if _SEMAPHORES.get(class_name) is None:
        _SEMAPHORES[class_name] = asyncio.Semaphore(
            _SEMAPHORE_LIMITS.get(class_name, 8)
        )
    return _SEMAPHORES[class_name]


async def tool_node(state: AgentState, tools_by_name: dict[str, BaseTool]) -> dict:
    """
    Execute all tool calls present in the last AIMessage.

    Runs sequentially (not in parallel) to respect asyncpg connection
    constraints — never use asyncio.gather() here.

    Each tool's result is wrapped in a ToolMessage for LangGraph compatibility.
    """
    last_message: AIMessage = state["messages"][-1]

    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        logger.warning("tool_node called with no tool calls in last message")
        return {}

    tool_messages: list[ToolMessage] = []
    new_records: list[dict] = []

    prior_records = state.get("tool_call_records", [])
    semantic_calls_so_far = sum(
        1 for rec in prior_records if _is_semantic_tool(str(rec.get("tool", "")))
    )
    data_calls_so_far = sum(
        1 for rec in prior_records if not _is_semantic_tool(str(rec.get("tool", "")))
    )

    for tool_call in last_message.tool_calls:
        tool_name: str = tool_call["name"]
        tool_args: dict = tool_call.get("args", {})
        tool_call_id: str = tool_call["id"]

        if (
            _is_semantic_tool(tool_name)
            and data_calls_so_far == 0
            and semantic_calls_so_far >= MAX_SEMANTIC_PREFLIGHT_CALLS
        ):
            result_content = json.dumps({
                "warning": "semantic_preflight_budget_reached",
                "message": (
                    "Too many ontology/semantic calls before querying clinical data. "
                    "Proceed with a domain data tool now (for example: "
                    "get_adverse_events, cross_trial_safety_summary, or cohort_outcome_snapshot)."
                ),
                "semantic_calls_so_far": semantic_calls_so_far,
            })

            tool_messages.append(
                ToolMessage(
                    content=result_content,
                    tool_call_id=tool_call_id,
                    name=tool_name,
                )
            )
            new_records.append(
                ToolCallRecord(
                    tool=tool_name,
                    args=_sanitize_args(tool_args),
                    result_summary="Semantic preflight budget reached; pivot requested",
                    duration_ms=0,
                    status="success",
                    error_message=None,
                ).model_dump()
            )
            semantic_calls_so_far += 1
            continue

        # ── Wrap the entire tool execution in an OTel span ────────────────
        with tracer.start_as_current_span(f"tool.{tool_name}") as span:
            span.set_attribute("tool.name", tool_name)
            span.set_attribute("tool.call_id", tool_call_id)
            # Truncate large arg payloads to keep span size reasonable
            span.set_attribute(
                "tool.args_preview",
                json.dumps(_sanitize_args(tool_args))[:500],
            )

            tool = tools_by_name.get(tool_name)
            if tool is None:
                error_content = json.dumps({
                    "error": f"Unknown tool: {tool_name}",
                    "available_tools": list(tools_by_name.keys()),
                })
                tool_messages.append(
                    ToolMessage(content=error_content, tool_call_id=tool_call_id)
                )
                AGENT_TOOL_CALL_TOTAL.labels(
                    tool_name=tool_name, status="unknown_tool"
                ).inc()
                span.set_status(trace.StatusCode.ERROR, f"Unknown tool: {tool_name}")
                continue

            # ── Type coercion: handle LLM sending wrong trial_ids type ────
            if "trial_ids" in tool_args:
                tool_args = _coerce_trial_ids(tool, tool_args)

            sem_class = TOOL_SEMAPHORE_CLASS.get(tool_name, "semantic")
            timeout   = TOOL_TIMEOUTS.get(tool_name, TOOL_TIMEOUTS["__default__"])
            span.set_attribute("tool.timeout_budget_s", timeout)
            span.set_attribute("tool.semaphore_class", sem_class)

            timer = Timer().start()
            status = "success"
            result_content = ""
            error_msg: str | None = None
            result_summary = ""

            try:
                logger.info(
                    f"Executing tool: {tool_name} "
                    f"args_keys={list(tool_args.keys())}"
                )

                # Sequential (asyncpg constraint) — semaphore caps heavy-DB
                # calls per process; wait_for enforces a hard time budget.
                async with _get_semaphore(sem_class):
                    raw_result = await asyncio.wait_for(
                        tool.ainvoke(tool_args), timeout=timeout
                    )

                # ── Distill result to save tokens before sending to LLM ────────
                distilled_result = _distill_result(raw_result, tool_name)

                # ── Normalise result to string for ToolMessage ─────────────
                if isinstance(distilled_result, dict):
                    result_content = json.dumps(distilled_result, default=str)

                    if isinstance(raw_result, dict):
                        if raw_result.get("status") == "empty":
                            status = "empty"
                        if raw_result.get("ceiling_applied"):
                            AGENT_CEILING_APPLIED_TOTAL.inc()
                            span.set_attribute("tool.ceiling_applied", True)

                elif isinstance(distilled_result, str):
                    result_content = distilled_result
                else:
                    result_content = json.dumps(distilled_result, default=str)

                duration_ms = timer.elapsed_ms()
                result_summary = _summarize_result(raw_result, tool_name)
                logger.info(
                    f"Tool {tool_name} completed in {duration_ms}ms "
                    f"— {result_summary}"
                )

            except asyncio.TimeoutError:
                duration_ms = timer.elapsed_ms()
                status = "error"
                error_msg = f"Tool '{tool_name}' timed out after {timeout:.0f}s"
                result_content = json.dumps({
                    "error": "tool_timeout",
                    "tool": tool_name,
                    "timeout_s": timeout,
                    "message": (
                        f"'{tool_name}' did not respond within {timeout:.0f}s. "
                        "The data service may be under load — please try again."
                    ),
                })
                result_summary = f"Timeout after {timeout:.0f}s"
                AGENT_TOOL_TIMEOUT_TOTAL.labels(tool_name=tool_name).inc()
                span.set_status(trace.StatusCode.ERROR, f"Timeout: {tool_name}")
                logger.warning(
                    f"Tool {tool_name} timed out after {timeout}s "
                    f"(budget={timeout}s, elapsed={duration_ms}ms)"
                )

            except Exception as exc:
                duration_ms = timer.elapsed_ms()
                status = "error"
                error_msg = str(exc)
                result_content = json.dumps({
                    "error": error_msg,
                    "tool": tool_name,
                    "message": (
                        "Tool execution failed. "
                        "Consider rephrasing or trying a different approach."
                    ),
                })
                result_summary = f"Error: {str(exc)[:100]}"
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                logger.error(
                    f"Tool {tool_name} failed after {duration_ms}ms: {exc}",
                    exc_info=True,
                )

            # ── Prometheus metrics ─────────────────────────────────────────
            # duration_ms is always defined here (set in both try and except)
            AGENT_TOOL_CALL_TOTAL.labels(
                tool_name=tool_name, status=status
            ).inc()
            AGENT_TOOL_CALL_DURATION.labels(tool_name=tool_name).observe(
                duration_ms / 1000.0  # histogram expects seconds
            )

            span.set_attribute("tool.status", status)
            span.set_attribute("tool.duration_ms", duration_ms)

            # ── Append ToolMessage ─────────────────────────────────────────
            tool_messages.append(
                ToolMessage(
                    content=result_content,
                    tool_call_id=tool_call_id,
                    name=tool_name,
                )
            )

            # ── Record for audit trail / frontend display ──────────────────
            new_records.append(
                ToolCallRecord(
                    tool=tool_name,
                    args=_sanitize_args(tool_args),
                    result_summary=result_summary,
                    duration_ms=duration_ms,
                    status=status,
                    error_message=error_msg,
                ).model_dump()
            )

            if _is_semantic_tool(tool_name):
                semantic_calls_so_far += 1
            else:
                data_calls_so_far += 1

    return {
        "messages": tool_messages,
        "tool_call_records": state.get("tool_call_records", []) + new_records,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _coerce_trial_ids(tool: BaseTool, tool_args: dict) -> dict:
    """
    Correct type mismatches between what the LLM sends and what the tool
    schema declares for the `trial_ids` field.

    The LLM sometimes sends a list when the tool expects a JSON string,
    or a string when the tool expects a list. This normalises both cases
    before the tool is invoked so we never surface a Pydantic validation
    error to the LLM.
    """
    try:
        schema = tool.args_schema.model_json_schema()
        trial_ids_schema = schema.get("properties", {}).get("trial_ids", {})
        expected_type = trial_ids_schema.get("type")
        any_of = trial_ids_schema.get("anyOf", [])
        supports_array = expected_type == "array" or any(
            opt.get("type") == "array" for opt in any_of if isinstance(opt, dict)
        )

        value = tool_args["trial_ids"]

        if isinstance(value, list) and not supports_array:
            # Tool schema is string-like; convert list into CSV string.
            tool_args = dict(tool_args)
            tool_args["trial_ids"] = ",".join(str(v) for v in value)

        elif supports_array and isinstance(value, str):
            # Tool wants a list, LLM gave a bare string
            tool_args = dict(tool_args)
            try:
                parsed = json.loads(value)
                tool_args["trial_ids"] = parsed if isinstance(parsed, list) else [value]
            except json.JSONDecodeError:
                tool_args["trial_ids"] = [value]

    except Exception as exc:
        # Never fail a tool call just because of coercion logic
        logger.warning(f"trial_ids coercion skipped for {tool.name}: {exc}")

    return tool_args


def _summarize_result(result: Any, tool_name: str) -> str:
    """Generate a short human-readable summary of a tool result for logs."""
    if isinstance(result, dict):
        if "error" in result:
            return f"Error: {result['error'][:80]}"
        if "count" in result:
            return f"count={result['count']}"
        if "total" in result:
            return f"total={result['total']}"
        if "patients" in result:
            n = (
                len(result["patients"])
                if isinstance(result["patients"], list)
                else result["patients"]
            )
            return f"{n} patients"
        if "results" in result:
            n = len(result["results"]) if isinstance(result["results"], list) else "?"
            return f"{n} results"
        if "trials" in result:
            n = len(result["trials"]) if isinstance(result["trials"], list) else "?"
            return f"{n} trials found"
        first_key = next(iter(result), None)
        if first_key:
            return f"{first_key}={str(result[first_key])[:60]}"
    return str(result)[:100]


def _sanitize_args(args: dict) -> dict:
    """
    Remove sensitive fields from tool args before storing in audit trail
    or sending to the frontend.

    access_context should never appear here (injected by tool wrappers, not
    by the LLM), but this is defense-in-depth.
    """
    return {k: v for k, v in args.items() if k != "access_context"}


def _is_semantic_tool(tool_name: str) -> bool:
    if tool_name in SEMANTIC_TOOL_NAMES:
        return True
    return tool_name.startswith("semantic_")


def _distill_result(result: Any, tool_name: str) -> Any:
    """
    Remove verbose, LLM-irrelevant metadata from tool results to save tokens.
    Keeps only the core clinical or descriptive data.
    """
    if not isinstance(result, dict):
        return result
    
    distilled = dict(result)
    
    # Strip common bulky metadata fields
    for key in ["relevance_score", "internal_id", "created_at", "updated_at", "schema_version", "metadata"]:
        distilled.pop(key, None)
        
    # Trim large lists (optional safety net, though tools should limit themselves)
    for k, v in list(distilled.items()):
        if isinstance(v, list) and len(v) > 50:
            distilled[k] = v[:50]
            distilled[f"{k}_truncated"] = f"List truncated. Showing 50 of {len(v)} items."
            
    # Remove nulls/empties to save further tokens
    distilled = {k: v for k, v in distilled.items() if v is not None and v != ""}
    
    return distilled