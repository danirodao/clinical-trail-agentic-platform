"""
Agent node — invokes the LLM with bound tools.

Token efficiency additions:
  - Prompt caching: static instructions in SystemMessage[0] (byte-identical
    every call) + per-user access profile in SystemMessage[1].
  - Mid-run compression: at `summarise_at_iteration` (complex queries only),
    past ToolMessage content is replaced with a cheap-model bullet summary.
  - Dynamic max_tokens: sized to query complexity and iteration phase.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import structlog
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
from ..prompts import STATIC_SYSTEM_PROMPT, build_dynamic_prompt, build_system_prompt

logger = structlog.get_logger(__name__)
tracer = get_tracer()

MAX_PROMPT_LOG_CHARS = 2500


def _preview_text(value: str, max_chars: int = MAX_PROMPT_LOG_CHARS) -> str:
    """Return bounded text for readable logs."""
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "... [truncated]"


def _extract_key_content(text: str, max_chars: int = 800) -> str:
    """
    Retain lines that contain numerics, UUIDs, or percentages first;
    append prose lines after. Hard-clip to max_chars.
    This ensures critical clinical values survive truncation.
    """
    lines = text.splitlines()
    key_lines, prose_lines = [], []
    for line in lines:
        if re.search(r'\d+\.?\d*|[a-f0-9]{8}-[a-f0-9]{4}|%', line):
            key_lines.append(line)
        else:
            prose_lines.append(line)
    combined = "\n".join(key_lines + prose_lines)
    return combined[:max_chars]


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
        max_tokens = _compute_max_tokens(
            complexity=state.get("query_complexity", "simple"),
            iteration=iteration,
            max_iter=max_iter,
        )
        llm = ChatOpenAI(
            model=model_name,
            temperature=agent_config.temperature,
            max_tokens=max_tokens,
            streaming=True,
            max_retries=6,
        )
        llm_with_tools = llm.bind_tools(tools)
        span.set_attribute("llm.max_tokens", max_tokens)

        logger.debug(
            f"agent_node: model={model_name}, iteration={iteration}, "
            f"tools_bound={len(tools)}, messages={len(state['messages'])}"
        )

        # ── Inject system prompts if not already present ───────────────────
        # Two SystemMessages injected locally (never written back to state):
        #   [0] STATIC  — byte-identical every call → OpenAI caches it
        #   [1] DYNAMIC — per-user access profile + active filters
        messages = list(state["messages"])
        already_compressed: bool = state.get("context_compressed", False)
        did_compress: bool = False
        if not messages or not isinstance(messages[0], SystemMessage):
            dynamic_prompt = build_dynamic_prompt(
                access_profile=state.get("access_profile_dict", {}),
                query_complexity=state.get("query_complexity", "simple"),
            )
            messages = [
                SystemMessage(content=STATIC_SYSTEM_PROMPT),
                SystemMessage(content=dynamic_prompt),
            ] + messages
            logger.debug("agent_node: dual system prompts injected (static+dynamic).")
            logger.info(
                "agent_prompt",
                model=model_name,
                iteration=iteration,
                static_chars=len(STATIC_SYSTEM_PROMPT),
                dynamic_chars=len(dynamic_prompt),
            )

        # ── Mid-run history compression (complex queries, once per query) ──
        messages, did_compress = await _maybe_compress_history(
            messages=messages,
            complexity=state.get("query_complexity", "simple"),
            iteration=iteration,
            already_compressed=already_compressed,
        )

        # ── Token Optimizer: Sliding Window & Tool Pruning ──────────────────
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
            logger.info(
                "llm_tool_plan",
                model=model_name,
                iteration=iteration,
                tools=tool_names,
                tool_args=[tc.get("args", {}) for tc in tool_calls],
            )
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
        "context_compressed": already_compressed or did_compress,
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
                        content=_extract_key_content(m.content, max_chars=max_tool_chars) + "\n... [Rest of historical output pruned]",
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
        "context_pruned",
        original_count=len(messages),
        final_count=len(result),
        turns_kept=len(turns),
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Token-efficiency helpers
# ─────────────────────────────────────────────────────────────────────────────

def _compute_max_tokens(complexity: str, iteration: int, max_iter: int) -> int:
    """
    Return a context-aware max_tokens budget for the LLM call.
    
    To prioritize response quality and prevent truncated answers, we always
    provide the full output budget. The model naturally stops when done, 
    so a higher cap does not waste tokens unless required for a better answer.
    """
    return agent_config.max_tokens


async def _maybe_compress_history(
    messages: list,
    complexity: str,
    iteration: int,
    already_compressed: bool,
) -> tuple[list, bool]:
    """
    At the configured checkpoint, call the cheap model to compress accumulated
    past ToolMessage content into a concise bullet summary.

    Only fires when ALL of:
      - complexity == "complex"
      - iteration == agent_config.summarise_at_iteration
      - not already compressed this query (one pass per query)
      - there are past ToolMessages worth compressing

    The summary replaces the FIRST past ToolMessage's content; subsequent ones
    get a placeholder. tool_call_ids are preserved so OpenAI's function-call
    pairing remains valid.

    Returns (possibly-modified messages, did_compress_bool).
    """
    if (
        complexity != "complex"
        or already_compressed
        or iteration != agent_config.summarise_at_iteration
    ):
        return messages, False

    # Find the last AIMessage — everything before it is "past" context
    last_ai_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], AIMessage):
            last_ai_idx = i
            break

    if last_ai_idx <= 0:
        return messages, False

    # Collect indices and text of ToolMessages before the last AIMessage
    past_tool_indices: list[int] = []
    past_tool_texts: list[str] = []
    for i, m in enumerate(messages):
        if i < last_ai_idx and isinstance(m, ToolMessage):
            text = m.content if isinstance(m.content, str) else str(m.content)
            if len(text) > 150:          # only bother if there's real content
                past_tool_indices.append(i)
                past_tool_texts.append(f"[{m.name}]: {text[:500]}")

    if not past_tool_texts:
        return messages, False

    # Call the cheap model to produce the summary
    try:
        mini_llm = ChatOpenAI(
            model=agent_config.compress_model,
            temperature=0.0,
            max_tokens=350,
        )
        compress_prompt = (
            "Compress these clinical trial tool results into ≤12 bullet points. "
            "Preserve ALL numeric values, patient counts, UUIDs, statistical "
            "results, and key findings. Be extremely terse — no prose.\n\n"
            + "\n\n".join(past_tool_texts)
        )
        resp = await mini_llm.ainvoke([HumanMessage(content=compress_prompt)])
        summary = resp.content.strip()
    except Exception as exc:
        logger.warning("history_compression_failed", error=str(exc))
        return messages, False

    # Replace ToolMessages in-place (keep tool_call_id intact for API compat)
    new_messages = list(messages)
    for idx_pos, msg_idx in enumerate(past_tool_indices):
        orig = new_messages[msg_idx]
        new_content = (
            f"[HISTORY COMPRESSED at iter {iteration} — "
            f"{len(past_tool_indices)} tool results]\n{summary}"
            if idx_pos == 0
            else "[Included in compressed history above]"
        )
        new_messages[msg_idx] = ToolMessage(
            content=new_content,
            tool_call_id=orig.tool_call_id,
            name=orig.name,
        )

    logger.info(
        "history_compressed",
        tool_messages_compressed=len(past_tool_indices),
        iteration=iteration,
        compress_model=agent_config.compress_model,
    )
    return new_messages, True