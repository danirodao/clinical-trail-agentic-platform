"""
All Pydantic models, TypedDicts, and event types for the agent system.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal, Optional, Union, List
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# API Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="Natural language query")
    trial_ids: Optional[List[str]] = Field(default=None, description="Optional subset of trial IDs")
    session_id: Optional[str] = Field(default=None, description="Conversation session ID")


class ToolCallRecord(BaseModel):
    """Records a single tool invocation for audit trail and frontend display."""
    tool: str
    args: dict[str, Any]
    result_summary: str
    duration_ms: int
    status: Literal["success", "error"] = "success"
    error_message: Optional[str] = None


class QuerySource(BaseModel):
    trial_id: str
    nct_id: str
    title: str


class QueryMetadata(BaseModel):
    model_used: str
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int
    iteration_count: int


class QueryResponse(BaseModel):
    answer: str
    sources: list[QuerySource] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    access_level_applied: Literal["individual", "aggregate", "mixed", "none"]
    filters_applied: list[str] = Field(default_factory=list)
    metadata: QueryMetadata
    error: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Streaming Event types (NDJSON lines sent to frontend)
# ─────────────────────────────────────────────────────────────────────────────

class StatusEvent(BaseModel):
    event: Literal["status"] = "status"
    data: dict[str, str]


class ToolCallEvent(BaseModel):
    event: Literal["tool_call"] = "tool_call"
    data: dict[str, Any]   # {tool, args}


class ToolResultEvent(BaseModel):
    event: Literal["tool_result"] = "tool_result"
    data: dict[str, Any]   # {tool, summary, duration_ms, status}


class ThinkingEvent(BaseModel):
    event: Literal["thinking"] = "thinking"
    data: dict[str, str]   # {content}


class AnswerTokenEvent(BaseModel):
    event: Literal["answer_token"] = "answer_token"
    data: dict[str, str]   # {token}


class CompleteEvent(BaseModel):
    event: Literal["complete"] = "complete"
    data: QueryResponse


class ErrorEvent(BaseModel):
    event: Literal["error"] = "error"
    data: dict[str, str]   # {message}


StreamEvent = Union[
    StatusEvent,
    ToolCallEvent,
    ToolResultEvent,
    ThinkingEvent,
    AnswerTokenEvent,
    CompleteEvent,
    ErrorEvent,
]


# ─────────────────────────────────────────────────────────────────────────────
# LangGraph State (TypedDict)
# ─────────────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    """
    Mutable state threaded through every LangGraph node.

    `messages` uses the built-in add_messages reducer so appends are safe.
    All other fields are replaced on each update.
    """
    # Conversation history (add_messages reducer handles appends safely)
    messages: Annotated[list[BaseMessage], add_messages]

    # Authorization (set in guardrails, read-only in all other nodes)
    access_profile_dict: dict[str, Any]   # Serialized AccessProfile
    access_context_json: str              # JSON string injected into every tool call

    # Query metadata (set in guardrails)
    user_query: str
    query_complexity: Literal["simple", "complex"]
    model_name: str
    max_iterations: int

    # Execution tracking (mutated in agent_node / tool_node)
    iteration_count: int
    tool_call_records: list[dict]         # Accumulated ToolCallRecord dicts
    total_prompt_tokens: int
    total_completion_tokens: int

    # Output (set in synthesizer)
    final_response: Optional[dict]        # QueryResponse as dict
    requested_trial_ids: list[str]
    tools: List[Any] = []  # ADD THIS FIELD to carry tools through the graph


# ─────────────────────────────────────────────────────────────────────────────
# Internal helper: timing utility
# ─────────────────────────────────────────────────────────────────────────────

class Timer:
    """Simple wall-clock timer for tool call duration measurement."""

    def __init__(self):
        self._start: float = 0.0

    def start(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)