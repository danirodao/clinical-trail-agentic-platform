"""
Agent node — invokes the LLM with bound tools.

Phase 5 fixes applied:
  - Fixed `total_tokens` reference in the else-branch where `usage` is None
  - Added AGENT_ITERATION_COUNT histogram recording
  - Added OTel span wrapping the LLM call with token count attributes
  - Guard against `tools` being empty with a clear error log
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, SystemMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from opentelemetry import trace

from ..observability import (
    AGENT_ITERATION_COUNT,
    AGENT_LLM_TOKEN_TOTAL,
    AGENT_MAX_ITERATIONS_REACHED_TOTAL,
    get_tracer,
)
from ..config import agent_config
from ..models import AgentState
from ..prompts import build_system_prompt

logger = logging.getLogger(__name__)
tracer = get_tracer()


async def agent_node(state: AgentState, tools: list) -> dict:
    """
    Invoke the LLM with the full message history and bound tools.

    Creates a fresh ChatOpenAI instance with tools bound on every call.
    This is required because tools are closures over a per-session MCP
    connection — caching would bind the LLM to a dead session.
    """
    iteration: int = state["iteration_count"]
    max_iter: int = state.get("max_iterations", agent_config.max_iterations)
    model_name: str = state.get("model_name", agent_config.simple_model)

    # ── Iteration guard ───────────────────────────────────────────────────
    if iteration >= max_iter:
        logger.warning(f"Max iterations ({max_iter}) reached — forcing synthesizer")
        AGENT_MAX_ITERATIONS_REACHED_TOTAL.labels(model=model_name).inc()
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"I have reached the maximum number of analysis steps "
                        f"({max_iter}). Here is a summary based on the tool "
                        "results collected so far."
                    )
                )
            ],
            "iteration_count": iteration + 1,
        }

    # ── Guard: warn loudly if no tools are available ──────────────────────
    if not tools:
        logger.error(
            "agent_node: No tools provided! The LLM cannot call any tools. "
            "Check MCP server registration."
        )

    with tracer.start_as_current_span("agent_node.llm_call") as span:
        span.set_attribute("llm.model", model_name)
        span.set_attribute("llm.iteration", iteration)
        span.set_attribute("llm.tools_available", len(tools))
        span.set_attribute("llm.message_count", len(state["messages"]))

        # ── Build fresh model with tools bound ────────────────────────────
        llm = ChatOpenAI(
            model=model_name,
            temperature=agent_config.temperature,
            max_tokens=agent_config.max_tokens,
            streaming=True,
            max_retries=6,
        )
        llm_with_tools = llm.bind_tools(tools)

        logger.debug(
            f"agent_node: model={model_name}, iteration={iteration}, "
            f"tools_bound={len(tools)}, messages={len(state['messages'])}"
        )

        # ── Inject system prompt if not already present ───────────────────
        messages = list(state["messages"])
        if not messages or not isinstance(messages[0], SystemMessage):
            system_prompt = build_system_prompt(
                access_profile=state.get("access_profile_dict", {}),
                query_complexity=state.get("query_complexity", "simple"),
            )
            messages = [SystemMessage(content=system_prompt)] + messages
            logger.debug("agent_node: System prompt injected into messages.")

        # ── Token Optimizer: Sliding Window & Tool Pruning ────────────────────
        messages = _prune_context(
            messages,
            max_turns=agent_config.max_history_turns,
            max_tool_chars=agent_config.max_tool_output_chars,
            current_iteration=iteration,
        )

        # ── LLM invocation ────────────────────────────────────────────────
        response: AIMessage = await llm_with_tools.ainvoke(messages)

        # ── Token tracking ────────────────────────────────────────────────
        prompt_tokens: int = 0
        completion_tokens: int = 0
        total_tokens: int = 0

        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            if isinstance(usage, dict):
                prompt_tokens = usage.get("input_tokens", 0) or 0
                completion_tokens = usage.get("output_tokens", 0) or 0
                total_tokens = usage.get("total_tokens", 0) or (
                    prompt_tokens + completion_tokens
                )
            else:
                prompt_tokens = getattr(usage, "input_tokens", 0) or 0
                completion_tokens = getattr(usage, "output_tokens", 0) or 0
                total_tokens = getattr(usage, "total_tokens", 0) or (
                    prompt_tokens + completion_tokens
                )
        else:
            resp_meta = getattr(response, "response_metadata", {}) or {}
            token_usage = resp_meta.get("token_usage", {}) or {}
            prompt_tokens = token_usage.get("prompt_tokens", 0)
            completion_tokens = token_usage.get("completion_tokens", 0)
            total_tokens = token_usage.get(
                "total_tokens", prompt_tokens + completion_tokens
            )

        if prompt_tokens or completion_tokens:
            AGENT_LLM_TOKEN_TOTAL.labels(
                model=model_name, token_type="prompt"
            ).inc(prompt_tokens)
            AGENT_LLM_TOKEN_TOTAL.labels(
                model=model_name, token_type="completion"
            ).inc(completion_tokens)
            AGENT_LLM_TOKEN_TOTAL.labels(
                model=model_name, token_type="total"
            ).inc(total_tokens)

        span.set_attribute("llm.prompt_tokens", prompt_tokens)
        span.set_attribute("llm.completion_tokens", completion_tokens)
        span.set_attribute("llm.total_tokens", total_tokens)

        # ── Log tool call decision ────────────────────────────────────────
        tool_calls = getattr(response, "tool_calls", [])
        has_tool_calls = bool(tool_calls)
        span.set_attribute("llm.has_tool_calls", has_tool_calls)

        if has_tool_calls:
            tool_names = [tc.get("name", "?") for tc in tool_calls]
            logger.debug(f"agent_node: LLM triggered tool calls → {tool_names}")
        else:
            logger.debug(
                "agent_node: LLM returned direct text response (no tool calls)"
            )

        # ── Record iteration for histogram ────────────────────────────────
        if not has_tool_calls:
            AGENT_ITERATION_COUNT.labels(model=model_name).observe(iteration + 1)

    return {
        "messages": [response],
        "iteration_count": iteration + 1,
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + prompt_tokens,
        "total_completion_tokens": (
            state.get("total_completion_tokens", 0) + completion_tokens
        ),
    }


def _prune_context(
    messages: list,
    max_turns: int,
    max_tool_chars: int,
    current_iteration: int,
) -> list:
    """
    Sliding window for history + aggressive pruning of old tool outputs.

    1. Keeps SystemMessage at index 0.
    2. Groups remaining messages into Turns (starting with HumanMessage).
    3. Keeps only the last `max_turns` turns.
    4. For turns *before* the current turn, truncates ToolMessage content.
    """
    if not messages:
        return []

    sys_msg = None
    other_messages = messages
    if isinstance(messages[0], SystemMessage):
        sys_msg = messages[0]
        other_messages = messages[1:]

    # ── Group into turns ──────────────────────────────────────────────────
    turns: list[list] = []
    current_turn_msgs: list = []

    for m in other_messages:
        if isinstance(m, HumanMessage) and current_turn_msgs:
            turns.append(current_turn_msgs)
            current_turn_msgs = []
        current_turn_msgs.append(m)
    if current_turn_msgs:
        turns.append(current_turn_msgs)

    # ── Apply window ──────────────────────────────────────────────────────
    if len(turns) > max_turns:
        logger.debug(f"History window exceeded ({len(turns)} > {max_turns}) — dropping oldest turns")
        turns = turns[-max_turns:]

    # ── Prune ToolMessages in non-current questions ───────────────────────
    # We only keep full tool output for the VERY LAST turn (the active question)
    # AND only for the current reasoning steps in that turn.
    pruned_turns: list[list] = []
    for i, turn_msgs in enumerate(turns):
        is_current_turn = (i == len(turns) - 1)
        pruned_turn = []
        for m in turn_msgs:
            if isinstance(m, ToolMessage) and not is_current_turn:
                # This was a tool call from a PREVIOUS question in the chat.
                # We only need a summary of it, not the raw JSON.
                if isinstance(m.content, str) and len(m.content) > max_tool_chars:
                    m = ToolMessage(
                        content=m.content[:max_tool_chars] + "... [Rest of historical output pruned]",
                        name=m.name,
                        tool_call_id=m.tool_call_id,
                    )
            pruned_turn.append(m)
        pruned_turns.append(pruned_turn)

    # ── Reassemble ────────────────────────────────────────────────────────
    result = [sys_msg] if sys_msg else []
    for turn_msgs in pruned_turns:
        result.extend(turn_msgs)

    logger.debug(
        "context_pruned original_count=%s final_count=%s turns_kept=%s",
        len(messages),
        len(result),
        len(turns),
    )
    return result