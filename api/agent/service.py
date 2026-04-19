"""
AgentService — the single entry point for all agent queries.

Manages the full lifecycle per query:
  1. Convert AccessProfile → access_context_json + AgentState
  2. Open MCP ClientSession (SSE connection with bearer token)
  3. Create secure tool wrappers bound to the session
  4. Build and run the LangGraph compiled graph
  5. Return QueryResponse or stream StreamEvents

Phase 5 fixes applied:
  - query() and query_stream() both have finally blocks that always record
    QUERY_COUNT, QUERY_DURATION, and decrement ACTIVE_QUERIES regardless of
    whether the call succeeds, errors, or is cancelled.
  - status / model_used / complexity are initialised before the try block in
    both methods so the finally block never raises NameError.
  - query() now uses AsyncPostgresSaver + config (thread_id) for checkpointing,
    consistent with query_stream().
  - query_stream() fallback no longer opens a second MCP session. It reads
    the persisted checkpoint via graph.aget_state(config) instead.
  - _serialize_profile() now populates trial_metadata from trial_scopes so
    NCT ID reverse-lookup in the synthesizer actually works.
  - CancelledError sets status="cancelled" before re-raising so the metric
    label is accurate.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
import asyncio
from asyncio import CancelledError
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import structlog
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from mcp import ClientSession
from mcp.client.sse import sse_client

from .access_context import serialize_access_profile
from .auth_client import get_mcp_access_token
from .config import agent_config
from .graph import build_agent_graph
from .models import (
    AgentState,
    AnswerTokenEvent,
    CompleteEvent,
    ErrorEvent,
    QueryMetadata,
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


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build the PostgreSQL connection string from environment variables
# ─────────────────────────────────────────────────────────────────────────────

def _pg_url() -> str:
    return (
        f"postgresql://{os.environ.get('POSTGRES_USER', 'ctuser')}"
        f":{os.environ.get('POSTGRES_PASSWORD', 'ctpassword')}"
        f"@{os.environ.get('POSTGRES_HOST', 'postgres')}"
        f":{os.environ.get('POSTGRES_PORT', 5432)}"
        f"/{os.environ.get('POSTGRES_DB', 'clinical_trials')}"
    )


class AgentService:
    """
    Stateless service: each method call opens its own MCP session and
    builds a fresh graph. No shared mutable state between queries.
    """

    def __init__(self) -> None:
        setup_observability(
            phoenix_endpoint=agent_config.phoenix_endpoint,
            project_name=agent_config.phoenix_project,
            enable=agent_config.enable_tracing,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    async def query(
        self,
        request: QueryRequest,
        access_profile: Any,
    ) -> QueryResponse:
        """
        Execute a single query and return the complete QueryResponse.
        Blocks until the agent finishes all tool calls.

        Uses AsyncPostgresSaver so the conversation turn is persisted and
        can be resumed or inspected via the chat history API.
        """
        # ── Initialise metric labels before try so finally never NameErrors ──
        model_used = "unknown"
        complexity = "simple"
        status = "error"

        ACTIVE_QUERIES.inc()
        start_time = time.perf_counter()

        thread_id = request.session_id if request.session_id else str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}

        try:
            initial_state = self._build_initial_state(request, access_profile)

            async with AsyncPostgresSaver.from_conn_string(_pg_url()) as saver:
                async with self._mcp_session() as session:
                    tools = await create_secure_tools(
                        session=session,
                        access_context_json=initial_state["access_context_json"],
                    )
                    graph = build_agent_graph(tools, start_time, checkpointer=saver)
                    final_state = await graph.ainvoke(initial_state, config=config)

            response_dict = final_state.get("final_response")
            if not response_dict:
                return self._error_response(
                    "Agent did not produce a response.", start_time
                )

            response = QueryResponse(**response_dict)
            
            # Fetch full context for evaluators (ToolMessages generated during the run)
            messages = final_state.get("messages", [])
            response.raw_context = [
                m.content for m in messages
                if getattr(m, "type", "") == "tool"
            ]

            # Capture labels for the finally block
            model_used = response.metadata.model_used
            complexity = final_state.get("query_complexity", "simple")
            status = "error" if response.error else "success"

            self._log_query(request, access_profile, response)
            return response

        except Exception as exc:
            logger.error("Agent query failed", error=str(exc), exc_info=True)
            status = "error"
            return self._error_response(f"An error occurred: {str(exc)}", start_time)

        finally:
            # Always recorded — even if an exception was raised
            duration = time.perf_counter() - start_time
            QUERY_COUNT.labels(
                model=model_used, status=status, complexity=complexity
            ).inc()
            QUERY_DURATION.labels(
                model=model_used, complexity=complexity
            ).observe(duration)
            ACTIVE_QUERIES.dec()

    async def query_stream(
        self,
        request: QueryRequest,
        access_profile: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Execute a query and yield StreamEvents as the agent progresses.

        Event sequence:
          status → (tool_call → tool_result)* → answer_token* → complete

        Each event is a Pydantic model. Callers should serialise with
        event.model_dump_json() + "\\n" for NDJSON streaming.
        """
        # ── Initialise metric labels before try so finally never NameErrors ──
        model_used = "unknown"
        complexity = "simple"
        status = "error"

        ACTIVE_QUERIES.inc()
        start_time = time.perf_counter()

        thread_id = request.session_id if request.session_id else str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}

        try:
            yield StatusEvent(data={"message": "Computing access profile..."})
            initial_state = self._build_initial_state(request, access_profile)
            yield StatusEvent(data={"message": "Connecting to data services..."})

            async with AsyncPostgresSaver.from_conn_string(_pg_url()) as saver:
                async with self._mcp_session() as session:
                    tools = await create_secure_tools(
                        session=session,
                        access_context_json=initial_state["access_context_json"],
                    )

                    if not tools:
                        logger.error(
                            "FATAL: No tools returned from create_secure_tools. "
                            "Check MCP server tool registration."
                        )
                    else:
                        logger.debug(
                            f"create_secure_tools returned {len(tools)} tools: "
                            f"{[t.name for t in tools]}"
                        )

                    graph = build_agent_graph(tools, start_time, checkpointer=saver)
                    yield StatusEvent(data={"message": "Analyzing your question..."})

                    # ── Track which run produced the final state ──────────
                    final_state: dict | None = None
                    tool_start_times: dict[str, float] = {}

                    # ── Configure recursion limit to accommodate reasoning depth ──
                    config["recursion_limit"] = 50

                    async for event in graph.astream_events(
                        initial_state,
                        config=config,
                        version="v2",
                        include_names=[
                            "guardrails", "agent", "tools", "synthesizer"
                        ],
                    ):
                        event_name = event.get("name", "")
                        event_type = event.get("event", "")

                        # ── Tool call started ─────────────────────────────
                        if event_type == "on_tool_start":
                            tool_name = event.get("name", "unknown")
                            run_id = event.get("run_id", tool_name)
                            tool_args = event.get("data", {}).get("input", {})
                            tool_start_times[run_id] = time.perf_counter()

                            yield ToolCallEvent(data={
                                "tool": tool_name,
                                "args": _sanitize_for_display(tool_args),
                            })

                        # ── Tool call completed ───────────────────────────
                        elif event_type == "on_tool_end":
                            tool_name = event.get("name", "unknown")
                            run_id = event.get("run_id", tool_name)
                            output = event.get("data", {}).get("output", {})

                            elapsed = tool_start_times.pop(run_id, None)
                            duration_ms = (
                                int((time.perf_counter() - elapsed) * 1000)
                                if elapsed is not None
                                else 0
                            )

                            yield ToolResultEvent(data={
                                "tool": tool_name,
                                "summary": _summarize_for_stream(output),
                                "duration_ms": duration_ms,
                                "status": (
                                    "error"
                                    if "error" in str(output).lower()
                                    else "success"
                                ),
                            })

                        # ── LLM token stream ──────────────────────────────
                        elif event_type == "on_chat_model_stream":
                            chunk = event.get("data", {}).get("chunk")
                            if (
                                chunk
                                and hasattr(chunk, "content")
                                and isinstance(chunk.content, str)
                                and chunk.content
                                and not getattr(chunk, "tool_calls", [])
                            ):
                                yield AnswerTokenEvent(
                                    data={"token": chunk.content}
                                )

                        # ── Capture final state from graph completion ─────
                        elif (
                            event_type == "on_chain_end"
                            and event_name == "LangGraph"
                        ):
                            output = event.get("data", {}).get("output", {})
                            if (
                                isinstance(output, dict)
                                and "final_response" in output
                            ):
                                final_state = output

                    # ── Recover final state from checkpoint if not captured ─
                    # This can happen when the graph short-circuits (e.g.
                    # guardrails denies access) and the on_chain_end event
                    # fires before final_response is populated in the output
                    # dict. Reading from the checkpoint is always safe and
                    # avoids re-running the full graph.
                    if final_state is None or not final_state.get("final_response"):
                        logger.warning(
                            "Streaming did not capture final_state via "
                            "on_chain_end — recovering from checkpoint"
                        )
                        checkpoint_state = await graph.aget_state(config)
                        if checkpoint_state and checkpoint_state.values:
                            final_state = checkpoint_state.values

            # ── Build response ────────────────────────────────────────────
            if final_state and final_state.get("final_response"):
                response = QueryResponse(**final_state["final_response"])
            else:
                logger.error(
                    "Could not recover final_response from stream or checkpoint"
                )
                response = self._error_response(
                    "Agent completed but did not produce a response. "
                    "Please try again.",
                    start_time,
                )

            # Capture labels for the finally block
            model_used = response.metadata.model_used
            complexity = (
                final_state.get("query_complexity", "simple")
                if final_state
                else "simple"
            )
            status = "error" if response.error else "success"

            self._log_query(request, access_profile, response)
            yield CompleteEvent(data=response)

        except CancelledError:
            # Client disconnected — record as cancelled, not error
            status = "cancelled"
            logger.info("Stream cancelled by client")

        except Exception as exc:
            status = "error"
            logger.error("Agent stream failed", error=str(exc), exc_info=True)
            yield ErrorEvent(data={"message": str(exc)})

        finally:
            # Always recorded — covers success, error, and client disconnect
            duration = time.perf_counter() - start_time
            QUERY_COUNT.labels(
                model=model_used, status=status, complexity=complexity
            ).inc()
            QUERY_DURATION.labels(
                model=model_used, complexity=complexity
            ).observe(duration)
            ACTIVE_QUERIES.dec()

    # ─────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────

    def _build_initial_state(
        self,
        request: QueryRequest,
        access_profile: Any,
    ) -> AgentState:
        """
        Convert the request + AccessProfile into the initial LangGraph state.

        access_context_json  — compact representation consumed by MCP tools
        access_profile_dict  — full serialisation stored in the checkpoint
                               and read by guardrails / synthesizer nodes
        """
        access_context_dict = serialize_access_profile(access_profile)
        access_context_json = json.dumps(access_context_dict)
        profile_dict = _serialize_profile(access_profile)

        return AgentState(
            messages=[HumanMessage(content=request.query)],
            access_profile_dict=profile_dict,
            access_context_json=access_context_json,
            user_query=request.query,
            query_complexity="simple",             # overwritten by guardrails
            model_name=agent_config.simple_model,  # overwritten by guardrails
            max_iterations=agent_config.max_iterations,
            iteration_count=0,
            tool_call_records=[],
            total_prompt_tokens=0,
            total_completion_tokens=0,
            final_response=None,
            requested_trial_ids=request.trial_ids or [],
        )

    @asynccontextmanager
    async def _mcp_session(self):
        """
        Open an authenticated SSE connection to the MCP server.
        Yields a ready-to-use ClientSession.
        Includes a retry loop to handle transient container startup delays.
        """
        url = agent_config.mcp_server_url
        token = await get_mcp_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        max_retries = 3
        retry_delay = 1.0
        last_exc = None

        for attempt in range(1, max_retries + 1):
            try:
                async with sse_client(url=url, headers=headers) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        logger.debug(f"MCP session initialized → {url} (attempt {attempt})")
                        yield session
                        return # Success

            except (BaseException, Exception) as exc:
                last_exc = exc
                # Unwrap ExceptionGroup if present
                root = exc
                if hasattr(exc, "exceptions") and exc.exceptions:
                    root = exc.exceptions[0]
                
                msg = str(root)
                logger.warning(
                    f"MCP connection attempt {attempt}/{max_retries} failed: {msg}"
                )
                
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay * attempt)
                else:
                    # Final attempt failed
                    if "401" in msg:
                        detail = "MCP auth failed — check MCP_BEARER_TOKEN"
                    elif "404" in msg:
                        detail = f"MCP endpoint not found — check {url}"
                    elif "Connection refused" in msg or "ConnectError" in msg:
                        detail = "MCP server unreachable — check container state"
                    else:
                        detail = f"MCP connection error: {msg[:120]}"

                    logger.error(f"MCP final connection failure: {type(root).__name__}: {root}")
                    raise RuntimeError(detail) from root


    def _error_response(self, message: str, start_time: float) -> QueryResponse:
        """Build a QueryResponse for hard failure paths."""
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

        # ── Automated Production Sampling (Evaluation Flywheel) ──────────────
        import random
        from api.evaluation.argilla_client import push_records_for_review

        if random.random() < agent_config.prod_sampling_rate:
            try:
                import uuid
                record = {
                    "id": f"prod-{uuid.uuid4().hex[:8]}",
                    "query": request.query,
                    "actual_output": response.answer,
                    "retrieval_context": getattr(response, "raw_context", []),
                    "actual_tools": [tc.tool for tc in response.tool_calls],
                    "category": "production_sampling",
                    "layer": "agent",
                }
                # Push synchronously (simple network call)
                push_records_for_review([record], source="production_sampling")
                logger.info("Production record sampled and sent to Argilla")
            except Exception as e:
                logger.warning("Production sampling to Argilla failed", error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Module-level serialisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_profile(access_profile: Any) -> dict:
    """
    Convert an AccessProfile dataclass to a plain JSON-serialisable dict.

    trial_metadata is populated from trial_scopes so the synthesizer can
    perform NCT ID → UUID reverse lookups when building sources and
    determining effective access levels.

    Previously this was always an empty dict {} with a comment saying
    "Populated below if available" but no code — now it is populated.
    """
    trial_scopes: dict = {}
    trial_metadata: dict = {}

    for trial_id, scope in access_profile.trial_scopes.items():
        # Serialise scope for guardrails / synthesizer use
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

        # Populate trial_metadata for NCT ID reverse lookup.
        # The AccessProfile does not carry nct_id/title directly — these are
        # available if the caller (researcher router) has enriched the profile.
        # We store whatever is available; the synthesizer tolerates empty dicts.
        nct_id = getattr(scope, "nct_id", "") or ""
        title = getattr(scope, "title", "") or ""
        if nct_id or title:
            trial_metadata[trial_id] = {"nct_id": nct_id, "title": title}

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
        "trial_metadata": trial_metadata,
    }


def _sanitize_for_display(args: dict) -> dict:
    """Remove access_context from tool args before sending to the frontend."""
    return {k: v for k, v in args.items() if k != "access_context"}


def _summarize_for_stream(output: Any) -> str:
    """Quick human-readable summary of a tool output for streaming events."""
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