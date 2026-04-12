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
    get_tracer,
)
from ..models import AgentState, Timer, ToolCallRecord

logger = logging.getLogger(__name__)
tracer = get_tracer()


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

    for tool_call in last_message.tool_calls:
        tool_name: str = tool_call["name"]
        tool_args: dict = tool_call.get("args", {})
        tool_call_id: str = tool_call["id"]

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

                # NOTE: Sequential — do NOT use asyncio.gather() here
                raw_result = await tool.ainvoke(tool_args)

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
        expected_type = (
            schema.get("properties", {})
            .get("trial_ids", {})
            .get("type")
        )

        value = tool_args["trial_ids"]

        if expected_type == "string" and isinstance(value, list):
            # Tool wants a JSON-encoded list string, LLM gave a plain list
            tool_args = dict(tool_args)
            tool_args["trial_ids"] = json.dumps(value)

        elif expected_type == "array" and isinstance(value, str):
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