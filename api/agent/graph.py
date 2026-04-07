"""
LangGraph workflow graph builder.
FIXED: Ensures tools are passed correctly via functools.partial.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Literal

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .models import AgentState
from .nodes.agent_node import agent_node
from .nodes.guardrails import guardrails_node
from .nodes.synthesizer import synthesizer_node
from .nodes.tool_node import tool_node

logger = logging.getLogger(__name__)


def _route_after_guardrails(state: AgentState) -> Literal["agent", "synthesizer"]:
    """Skip to synthesizer if guardrails denied access."""
    if state.get("final_response") is not None:
        return "synthesizer"
    return "agent"


def _route_after_agent(state: AgentState) -> Literal["tools", "synthesizer"]:
    """
    ReAct routing: check for NATIVE tool_calls on the AIMessage.
    If the LLM used function calling correctly, tool_calls will be a
    non-empty list of dicts. If it output plain text, tool_calls is empty.
    """
    last_message = state["messages"][-1]

    if isinstance(last_message, AIMessage):
        tool_calls = getattr(last_message, "tool_calls", [])
        if tool_calls:
            logger.debug(f"Routing to tools: {[tc.get('name') for tc in tool_calls]}")
            return "tools"

    logger.debug("Routing to synthesizer: no tool calls in last message")
    return "synthesizer"


def build_agent_graph(
    tools: list[BaseTool],
    start_time: float,
    checkpointer=None,
) -> CompiledStateGraph:
    """
    Construct and compile the LangGraph StateGraph for a single query.

    Args:
        tools:       15 secure MCP tool wrappers (fresh per query session)
        start_time:  perf_counter timestamp for duration tracking
        checkpointer: Optional LangGraph Postgres checkpointer for history
    """
    tools_by_name: dict[str, BaseTool] = {t.name: t for t in tools}

    logger.debug(f"Building agent graph with {len(tools)} tools: {list(tools_by_name.keys())}")

    # Bind per-query dependencies into node functions via partial
    # This avoids global state and ensures each query gets its own tools
    bound_agent = functools.partial(agent_node, tools=tools)
    bound_tool = functools.partial(tool_node, tools_by_name=tools_by_name)
    bound_synthesizer = functools.partial(synthesizer_node, start_time=start_time)

    # Build graph
    builder = StateGraph(AgentState)

    builder.add_node("guardrails", guardrails_node)
    builder.add_node("agent", bound_agent)
    builder.add_node("tools", bound_tool)
    builder.add_node("synthesizer", bound_synthesizer)

    # Edges
    builder.add_edge(START, "guardrails")

    builder.add_conditional_edges(
        "guardrails",
        _route_after_guardrails,
        {"agent": "agent", "synthesizer": "synthesizer"},
    )

    builder.add_conditional_edges(
        "agent",
        _route_after_agent,
        {"tools": "tools", "synthesizer": "synthesizer"},
    )

    # After tools run, always return to agent for next reasoning step (ReAct loop)
    builder.add_edge("tools", "agent")
    builder.add_edge("synthesizer", END)

    compiled = builder.compile(checkpointer=checkpointer)
    logger.debug("Agent graph compiled successfully")
    return compiled