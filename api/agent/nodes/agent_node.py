"""
Agent node — invokes the LLM with bound tools.

FIXED:
- Removed broken model cache that reused stale tool closures across sessions
- Added system prompt injection into messages
- Fixed streaming=True for proper token streaming
- Tools are bound fresh on every invocation (they are cheap LangChain objects)
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, SystemMessage

from ..config import agent_config
from ..models import AgentState
from ..observability import TOKEN_USAGE
from ..prompts import build_system_prompt

logger = logging.getLogger(__name__)


async def agent_node(state: AgentState, tools: list) -> dict:
    """
    Invoke the LLM with the full message history and bound tools.

    Creates a fresh ChatOpenAI instance with tools bound on every call.
    This is required because tools are closures over a per-session MCP
    connection — they cannot be cached across requests.
    """
    iteration = state["iteration_count"]
    max_iter = state.get("max_iterations", agent_config.max_iterations)

    # ── Iteration guard ───────────────────────────────────────────────────────
    if iteration >= max_iter:
        logger.warning(f"Max iterations ({max_iter}) reached — forcing synthesizer")
        return {
            "messages": [AIMessage(
                content=(
                    f"I have reached the maximum number of analysis steps ({max_iter}). "
                    "Here is a summary based on the tool results collected so far."
                )
            )],
            "iteration_count": iteration + 1,
        }

    # ── Build fresh model with tools bound ───────────────────────────────────
    # CRITICAL: Do NOT cache this. Tools are closures over a live MCP session.
    # Caching would bind the LLM to a dead session from a previous request.
    if not tools:
        logger.error("agent_node: No tools provided! The LLM will not be able to call any tools.")

    model_name = state.get("model_name", agent_config.simple_model)

    llm = ChatOpenAI(
        model=model_name,
        temperature=agent_config.temperature,
        max_tokens=agent_config.max_tokens,
        streaming=True,  # Required for token-level streaming in astream_events
    )

    # This is the ONLY correct place to bind tools
    llm_with_tools = llm.bind_tools(tools)

    logger.debug(
        f"agent_node: model={model_name}, iteration={iteration}, "
        f"tools_bound={len(tools)}, messages={len(state['messages'])}"
    )

    # ── Inject system prompt if not already present ───────────────────────────
    messages = list(state["messages"])

    if not messages or not isinstance(messages[0], SystemMessage):
        # Build the system prompt from the access profile stored in state
        access_profile_dict = state.get("access_profile_dict", {})
        query_complexity = state.get("query_complexity", "simple")

        system_prompt = build_system_prompt(
            access_profile=access_profile_dict,
            query_complexity=query_complexity,
        )
        messages = [SystemMessage(content=system_prompt)] + messages
        logger.debug("agent_node: System prompt injected into messages.")

    # ── LLM invocation ────────────────────────────────────────────────────────
    response: AIMessage = await llm_with_tools.ainvoke(messages)

    # ── Token tracking ────────────────────────────────────────────────────────
    prompt_tokens = 0
    completion_tokens = 0

    usage = getattr(response, "usage_metadata", None)
    if usage:
        prompt_tokens = getattr(usage, "input_tokens", 0) or 0
        completion_tokens = getattr(usage, "output_tokens", 0) or 0
    else:
        # Fallback for older LangChain versions
        resp_meta = getattr(response, "response_metadata", {})
        token_usage = resp_meta.get("token_usage", {})
        prompt_tokens = token_usage.get("prompt_tokens", 0)
        completion_tokens = token_usage.get("completion_tokens", 0)

    if prompt_tokens or completion_tokens:
        TOKEN_USAGE.labels(model=model_name, token_type="prompt").inc(prompt_tokens)
        TOKEN_USAGE.labels(model=model_name, token_type="completion").inc(completion_tokens)

    # ── Log whether the LLM used a tool or responded directly ─────────────────
    tool_calls = getattr(response, "tool_calls", [])
    if tool_calls:
        tool_names = [tc.get("name", "?") for tc in tool_calls]
        logger.debug(f"agent_node: LLM triggered tool calls → {tool_names}")
    else:
        logger.debug(f"agent_node: LLM returned direct text response (no tool calls)")

    return {
        "messages": [response],
        "iteration_count": iteration + 1,
        "total_prompt_tokens": state.get("total_prompt_tokens", 0) + prompt_tokens,
        "total_completion_tokens": state.get("total_completion_tokens", 0) + completion_tokens,
    }