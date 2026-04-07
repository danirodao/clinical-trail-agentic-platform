"""
Tool execution node — runs all tool calls from the last AIMessage.

Key behaviors:
  - Executes tool calls sequentially (asyncpg single-connection safety rule)
  - Records timing per tool call in ToolCallRecord format
  - Updates Prometheus tool_call counters and histograms
  - Handles tool errors gracefully (returns error dict, lets LLM retry or explain)
  - Appends ToolMessages to the message list for LangGraph's expected format
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool

from ..models import AgentState, Timer, ToolCallRecord
from ..observability import TOOL_CALL_COUNT, TOOL_CALL_DURATION

logger = logging.getLogger(__name__)


async def tool_node(state: AgentState, tools_by_name: dict[str, BaseTool]) -> dict:
    """
    Execute all tool calls present in the last AIMessage.

    Runs sequentially (not parallel) to respect asyncpg connection constraints.
    Each tool's result is wrapped in a ToolMessage for LangGraph compatibility.
    """
    last_message: AIMessage = state["messages"][-1]

    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
        # Nothing to execute — should not happen (routing prevents this)
        logger.warning("tool_node called with no tool calls in last message")
        return {}

    tool_messages: list[ToolMessage] = []
    new_records: list[dict] = []

    for tool_call in last_message.tool_calls:
        tool_name: str = tool_call["name"]
        tool_args: dict = tool_call.get("args", {})
        tool_call_id: str = tool_call["id"]

        tool = tools_by_name.get(tool_name)
        if tool is None:
            error_content = json.dumps({
                "error": f"Unknown tool: {tool_name}",
                "available_tools": list(tools_by_name.keys()),
            })
            tool_messages.append(
                ToolMessage(content=error_content, tool_call_id=tool_call_id)
            )
            TOOL_CALL_COUNT.labels(tool_name=tool_name, status="unknown_tool").inc()
            continue

        timer = Timer().start()
        status = "success"
        result_content = ""
        error_msg = None
        try:
            logger.info(f"Executing tool: {tool_name} args_keys={list(tool_args.keys())}")

            # NOTE: Sequential execution — do NOT use asyncio.gather here
            # (asyncpg does not support concurrent operations on the same connection)
            if "trial_ids" in tool_args:
                # Inspect the Pydantic JSON schema to see what the tool ACTUALLY wants
                expected_schema = tool.args_schema.model_json_schema()
                expected_type = expected_schema.get("properties", {}).get("trial_ids", {}).get("type")
                
                # If the tool expects a STRING, but the LLM gave a LIST
                if expected_type == "string" and isinstance(tool_args["trial_ids"], list):
                    # Extract the first item from the list
                    tool_args["trial_ids"] = tool_args["trial_ids"][0] if tool_args["trial_ids"] else ""
                    
                # If the tool expects an ARRAY (list), but the LLM gave a STRING
                elif expected_type == "array" and isinstance(tool_args["trial_ids"], str):
                    # Wrap the string in a list
                    tool_args["trial_ids"] = [tool_args["trial_ids"]]
            raw_result = await tool.ainvoke(tool_args)

            # Normalize result to string for ToolMessage
            if isinstance(raw_result, dict):
                result_content = json.dumps(raw_result, default=str)
            elif isinstance(raw_result, str):
                result_content = raw_result
            else:
                result_content = json.dumps(raw_result, default=str)

            duration_ms = timer.elapsed_ms()
            result_summary = _summarize_result(raw_result, tool_name)

            logger.info(f"Tool {tool_name} completed in {duration_ms}ms — {result_summary}")

        except Exception as exc:
            duration_ms = timer.elapsed_ms()
            status = "error"
            error_msg = str(exc)  # ✅ FIX: Assign it here
            result_content = json.dumps({
                "error": error_msg,
                "tool": tool_name,
                "message": "Tool execution failed. Consider rephrasing or trying a different approach.",
            })
            result_summary = f"Error: {str(exc)[:100]}"
            logger.error(f"Tool {tool_name} failed after {duration_ms}ms: {exc}", exc_info=True)

        # ── Prometheus metrics ────────────────────────────────────────────────
        TOOL_CALL_COUNT.labels(tool_name=tool_name, status=status).inc()
        TOOL_CALL_DURATION.labels(tool_name=tool_name).observe(duration_ms / 1000.0)

        # ── Append ToolMessage ────────────────────────────────────────────────
        tool_messages.append(
            ToolMessage(
                content=result_content,
                tool_call_id=tool_call_id,
                name=tool_name,
            )
        )

        # ── Record for audit trail / frontend display ─────────────────────────
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


def _summarize_result(result: Any, tool_name: str) -> str:
    """Generate a short human-readable summary of a tool result for logs and frontend."""
    if isinstance(result, dict):
        if "error" in result:
            return f"Error: {result['error'][:80]}"
        if "count" in result:
            return f"count={result['count']}"
        if "total" in result:
            return f"total={result['total']}"
        if "patients" in result:
            n = len(result["patients"]) if isinstance(result["patients"], list) else result["patients"]
            return f"{n} patients"
        if "results" in result:
            n = len(result["results"]) if isinstance(result["results"], list) else "?"
            return f"{n} results"
        if "trials" in result:
            n = len(result["trials"]) if isinstance(result["trials"], list) else "?"
            return f"{n} trials found"
        # Generic: show first key-value pair
        first_key = next(iter(result), None)
        if first_key:
            return f"{first_key}={str(result[first_key])[:60]}"
    return str(result)[:100]


def _sanitize_args(args: dict) -> dict:
    """Remove sensitive fields from tool args before storing in audit trail."""
    # access_context should never be in args (we inject it separately in tool wrappers)
    # but add defense-in-depth here
    return {k: v for k, v in args.items() if k not in ("access_context",)}