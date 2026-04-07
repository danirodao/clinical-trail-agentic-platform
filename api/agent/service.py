"""
AgentService — the single entry point for all agent queries.

Manages the full lifecycle per query:
  1. Convert AccessProfile → access_context_json + AgentState
  2. Open MCP ClientSession (SSE connection with bearer token)
  3. Create 15 secure tool wrappers bound to the session
  4. Build and run the LangGraph compiled graph
  5. Return QueryResponse or stream StreamEvents

Usage (from FastAPI router):
    service = AgentService()
    response = await service.query(request, access_profile)

    # OR for streaming:
    async for event in service.query_stream(request, access_profile):
        yield event.model_dump_json() + "\\n"
"""

from __future__ import annotations

import json
import logging
import time
from asyncio import CancelledError
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator
import os
import uuid
import structlog
from langchain_core.messages import HumanMessage
from mcp import ClientSession
from mcp.client.sse import sse_client
from .auth_client import get_mcp_access_token
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from .access_context import serialize_access_profile, describe_filters
from .config import agent_config
from .graph import build_agent_graph
from .models import (
    AgentState,
    AnswerTokenEvent,
    CompleteEvent,
    ErrorEvent,
    QueryRequest,
    QueryResponse,
    StatusEvent,
    StreamEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from .observability import (
    ACTIVE_QUERIES,
    QUERY_COUNT,
    QUERY_DURATION,
    setup_observability,
)
from .tool_wrappers import create_secure_tools

logger = structlog.get_logger(__name__)


class AgentService:
    """
    Stateless service: each method call opens its own MCP session and
    builds a fresh graph. No shared mutable state between queries.
    """

    def __init__(self):
        setup_observability(
            phoenix_endpoint=agent_config.phoenix_endpoint,
            project_name=agent_config.phoenix_project,
            enable=agent_config.enable_tracing,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def query(
        self,
        request: QueryRequest,
        access_profile: Any,
    ) -> QueryResponse:
        """
        Execute a single query and return the complete QueryResponse.
        Blocks until the agent finishes all tool calls.
        """
        ACTIVE_QUERIES.inc()
        start_time = time.perf_counter()

        try:
            initial_state = self._build_initial_state(request, access_profile)

            async with self._mcp_session() as session:
                tools = await create_secure_tools(
                    session=session,
                    access_context_json=initial_state["access_context_json"],
                )
                graph = build_agent_graph(tools, start_time)                
                final_state = await graph.ainvoke(initial_state)

            response_dict = final_state.get("final_response")
            if not response_dict:
                return self._error_response("Agent did not produce a response.", start_time)

            response = QueryResponse(**response_dict)
            self._log_query(request, access_profile, response)
            return response

        except Exception as exc:
            logger.error("Agent query failed", error=str(exc), exc_info=True)
            QUERY_COUNT.labels(model="unknown", status="error", complexity="unknown").inc()
            return self._error_response(f"An error occurred: {str(exc)}", start_time)

        finally:
            ACTIVE_QUERIES.dec()

    async def query_stream(
        self,
        request: QueryRequest,
        access_profile: Any       
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Execute a query and yield StreamEvents as the agent progresses.

        Event sequence:
          status → (tool_call → tool_result)* → answer_token* → complete

        Each event is a Pydantic model. Callers should serialize with
        event.model_dump_json() + "\\n" for NDJSON streaming.
        """
        ACTIVE_QUERIES.inc()
        start_time = time.perf_counter()
        db_url= f"postgresql://{os.environ.get('POSTGRES_USER', 'ctuser')}:{os.environ.get('POSTGRES_PASSWORD', 'ctpassword')}@{os.environ.get('POSTGRES_HOST', 'postgres')}:{os.environ.get('POSTGRES_PORT', 5432)}/{os.environ.get('POSTGRES_DB', 'clinical_trials')}"
        thread_id = request.session_id if request.session_id else str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        try:
            yield StatusEvent(data={"message": "Computing access profile..."})
            initial_state = self._build_initial_state(request, access_profile)
            yield StatusEvent(data={"message": "Connecting to data services..."})

            # --- CHANGE 2: Wrap execution in the Saver context ---
            async with AsyncPostgresSaver.from_conn_string(db_url) as saver:
                async with self._mcp_session() as session:
                    tools = await create_secure_tools(
                        session=session,
                        access_context_json=initial_state["access_context_json"],
                    )
                    logger.debug(f"create_secure_tools returned {len(tools)} tools")
                    logger.debug(f"Tool names: {[t.name for t in tools]}")
                    if not tools:
                        logger.error("FATAL: No tools returned from create_secure_tools. Check MCP server tool registration.")
                    # Pass the saver to your graph builder
                    graph = build_agent_graph(tools, start_time, checkpointer=saver)

                    yield StatusEvent(data={"message": "Analyzing your question..."})

                    final_state = None
                    tool_start_times: dict[str, float] = {}

                    # --- CHANGE 3: Pass config to astream_events ---
                    async for event in graph.astream_events(
                        initial_state,
                        config=config,  # <--- CRITICAL: Tells LangGraph which session to use
                        version="v2",
                        include_names=["guardrails", "agent", "tools", "synthesizer"],
                    ):
                    # -----------------------------------------------
                        event_name = event.get("name", "")
                        event_type = event.get("event", "")

                        # ── Tool call started ─────────────────────────────────────
                        if event_type == "on_tool_start":
                            tool_name = event.get("name", "unknown")
                            run_id = event.get("run_id", tool_name)
                            tool_args = event.get("data", {}).get("input", {})
                            tool_start_times[run_id] = time.perf_counter()

                            yield ToolCallEvent(data={
                                "tool": tool_name,
                                "args": _sanitize_for_display(tool_args),
                            })

                        # ── Tool call completed ───────────────────────────────────
                        elif event_type == "on_tool_end":
                            tool_name = event.get("name", "unknown")
                            run_id = event.get("run_id", tool_name)
                            output = event.get("data", {}).get("output", {})

                            elapsed = tool_start_times.pop(run_id, None)
                            duration_ms = int((time.perf_counter() - elapsed) * 1000) if elapsed else 0

                            yield ToolResultEvent(data={
                                "tool": tool_name,
                                "summary": _summarize_for_stream(output),
                                "duration_ms": duration_ms,
                                "status": "error" if "error" in str(output).lower() else "success",
                            })

                        # ── LLM reasoning (non-tool AIMessage) ───────────────────
                        elif event_type == "on_chat_model_stream":
                            chunk = event.get("data", {}).get("chunk")
                            if chunk and hasattr(chunk, "content") and chunk.content:
                                content = chunk.content
                                if isinstance(content, str) and content:
                                    tool_calls = getattr(chunk, "tool_calls", [])
                                    if not tool_calls:
                                        yield AnswerTokenEvent(data={"token": content})

                        # ── Graph state update (capture final state) ──────────────
                        elif event_type == "on_chain_end" and event_name == "LangGraph":
                            output = event.get("data", {}).get("output", {})
                            if isinstance(output, dict) and "final_response" in output:
                                final_state = output

                if final_state and final_state.get("final_response"):
                    response = QueryResponse(**final_state["final_response"])
                else:
                    # Fallback: run non-streaming to get the final state
                    # Also must be wrapped in Saver and pass config!
                    async with AsyncPostgresSaver.from_conn_string(db_url) as fallback_saver:
                        async with self._mcp_session() as session:
                            tools = await create_secure_tools(
                                session=session,
                                access_context_json=initial_state["access_context_json"],
                            )
                            graph = build_agent_graph(tools, start_time, checkpointer=fallback_saver)
                            fallback_state = await graph.ainvoke(initial_state, config=config)
                            response = QueryResponse(**fallback_state["final_response"])

                self._log_query(request, access_profile, response)
                yield CompleteEvent(data=response)

        except CancelledError:
            logger.info("Stream cancelled by client")
        except Exception as exc:
            logger.error("Agent stream failed", error=str(exc), exc_info=True)
            yield ErrorEvent(data={"message": str(exc)})
        finally:
            ACTIVE_QUERIES.dec()

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _build_initial_state(
        self,
        request: QueryRequest,
        access_profile: Any,
    ) -> AgentState:
        """
        Convert the request + AccessProfile into the initial LangGraph state.

        The AccessProfile is serialized to a plain dict (JSON-safe) for storage
        in the graph state. The access_context_json is a separate compact
        representation used by MCP tool calls.
        """
        access_context_dict = serialize_access_profile(access_profile)
        # 2. Then, dump that dictionary to a JSON string for the tools
        access_context_json = json.dumps(access_context_dict)
        # ------------------------------

        profile_dict = _serialize_profile(access_profile) # This is for 

        # Add trial_ids scope from request (validated in guardrails node)
        requested_trial_ids = request.trial_ids or []

        return AgentState(
            messages=[HumanMessage(content=request.query)],
            access_profile_dict=profile_dict,
            access_context_json=access_context_json, # This is for MCP tools
            user_query=request.query,
            query_complexity="simple",    # overwritten by guardrails
            model_name=agent_config.simple_model,  # overwritten by guardrails
            max_iterations=agent_config.max_iterations,
            iteration_count=0,
            tool_call_records=[],
            total_prompt_tokens=0,
            total_completion_tokens=0,
            final_response=None,
            # Extra field for guardrails scope validation
            requested_trial_ids=requested_trial_ids,
        )
    @asynccontextmanager
    async def _mcp_session(self):
        """
        Open an authenticated SSE connection to the MCP server.
        Handles both mcp 1.x (headers kwarg) and older versions.
        """
        from mcp.client.sse import sse_client
        from mcp import ClientSession

        url   = agent_config.mcp_server_url   # must end in /sse
        #token = agent_config.mcp_bearer_token
        token = await get_mcp_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        try:
            # mcp >= 1.x passes headers directly to the underlying httpx client
            async with sse_client(url=url, headers=headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    logger.debug(f"MCP session initialized → {url}")
                    yield session

        except BaseException as exc:
            # Unwrap ExceptionGroup (anyio wraps errors this way)
            root = exc
            if hasattr(exc, "exceptions") and exc.exceptions:
                root = exc.exceptions[0]

            logger.error(f"MCP connection failed: {type(root).__name__}: {root}")

            # Surface the real HTTP error message
            msg = str(root)
            if "401" in msg:
                detail = f"MCP authentication failed — check MCP_BEARER_TOKEN matches the server"
            elif "404" in msg:
                detail = f"MCP endpoint not found — check MCP_SERVER_URL includes /sse path ({url})"
            elif "Connection refused" in msg or "ConnectError" in msg:
                detail = "MCP server is not reachable — check it is running"
            else:
                detail = f"MCP connection error: {msg[:120]}"

            raise RuntimeError(detail) from root

    def _error_response(self, message: str, start_time: float) -> QueryResponse:
        from .models import QueryMetadata
        return QueryResponse(
            answer=message,
            sources=[],
            tool_calls=[],
            access_level_applied="none",
            filters_applied=[],
            metadata=QueryMetadata(
                model_used="none",
                total_tokens=0,
                prompt_tokens=0,
                completion_tokens=0,
                duration_ms=int((time.perf_counter() - start_time) * 1000),
                iteration_count=0,
            ),
            error="agent_error",
        )

    def _log_query(
        self,
        request: QueryRequest,
        access_profile: Any,
        response: QueryResponse,
    ) -> None:
        """Structured audit log for every completed query."""
        logger.info(
            "agent_query_complete",
            user_id=getattr(access_profile, "user_id", "unknown"),
            organization_id=getattr(access_profile, "organization_id", "unknown"),
            query_length=len(request.query),
            trial_scope=request.trial_ids,
            access_level=response.access_level_applied,
            model_used=response.metadata.model_used,
            tools_called=[tc.tool for tc in response.tool_calls],
            tool_call_count=len(response.tool_calls),
            total_tokens=response.metadata.total_tokens,
            duration_ms=response.metadata.duration_ms,
            iteration_count=response.metadata.iteration_count,
            status="error" if response.error else "success",
        )

# ─────────────────────────────────────────────────────────────────────────────
# Serialization helpers
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_profile(access_profile: Any) -> dict:
    """
    Convert an AccessProfile dataclass to a plain JSON-serializable dict.
    Includes trial_scopes with cohort filter criteria for the synthesizer.
    """
    trial_scopes = {}
    trial_metadata = {}

    for trial_id, scope in access_profile.trial_scopes.items():
        trial_scopes[trial_id] = {
            "trial_id": trial_id,
            "access_level": scope.access_level,
            "cohort_scopes": [
                {
                    "cohort_id": cs.cohort_id,
                    "cohort_name": cs.cohort_name,
                    "filter_criteria": cs.filter_criteria,
                }
                for cs in scope.cohort_scopes
            ],
        }

    return {
        "user_id": access_profile.user_id,
        "role": access_profile.role,
        "organization_id": access_profile.organization_id,
        "allowed_trial_ids": access_profile.allowed_trial_ids,
        "individual_trial_ids": access_profile.individual_trial_ids,
        "aggregate_trial_ids": access_profile.aggregate_trial_ids,
        "has_any_access": access_profile.has_any_access,
        "has_individual_access": access_profile.has_individual_access,
        "aggregate_only": access_profile.aggregate_only,
        "trial_scopes": trial_scopes,
        "trial_metadata": trial_metadata,  # Populated below if available
    }

def _sanitize_for_display(args: dict) -> dict:
    """Remove access_context from tool args before sending to frontend."""
    return {k: v for k, v in args.items() if k != "access_context"}


def _summarize_for_stream(output: Any) -> str:
    """Quick summary of tool output for streaming ToolResultEvent."""
    if isinstance(output, dict):
        if "error" in output:
            return f"Error: {str(output['error'])[:80]}"
        if "count" in output:
            return f"{output['count']} records"
        if "total" in output:
            return f"Total: {output['total']}"
        keys = list(output.keys())
        return f"Result with fields: {', '.join(keys[:4])}"
    return str(output)[:100]

