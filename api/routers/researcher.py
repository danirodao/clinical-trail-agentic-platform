"""
Researcher endpoints — query data with two-layer authorization.

Phase 6 hardened:
  • Input validation + prompt-injection detection on every query
  • Response scrubbing (patient UUID redaction) on every answer
  • Circuit-breaker check before hitting the agent
  • Audit trail via AuditLogMiddleware (automatic, no code needed here)
  • Rate limiting via RateLimitMiddleware (automatic, no code needed here)
  • Suggested-questions endpoint
  • Conversation history endpoint (LangGraph checkpoint reader)
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from auth.dependencies import CurrentUser, require_role, get_current_user
from auth.middleware import UserContext
from auth.authorization_service import AuthorizationService
from auth.openfga_client import get_openfga_client
from api.database import get_db_pool

from api.agent.models import QueryRequest, QueryResponse
from api.agent.service import AgentService
from api.agent.input_validator import validate_query
from api.agent.response_scrubber import scrub_patient_ids, build_allowed_uuid_set
from api.agent.suggested_questions import generate_suggested_questions
from api.agent.error_handler import (
    AgentError,
    AgentErrorCode,
    mcp_circuit_breaker,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def get_agent_service() -> AgentService:
    """Dependency: returns a singleton-like AgentService instance."""
    return AgentService()


async def _build_auth_service(
    db_pool=Depends(get_db_pool),
    fga=Depends(get_openfga_client),
) -> AuthorizationService:
    """Dependency: constructs AuthorizationService with pool + FGA client."""
    return AuthorizationService(db_pool=db_pool, fga_client=fga)


# ---------------------------------------------------------------------------
# GET /research/my-access
# ---------------------------------------------------------------------------

@router.get(
    "/research/my-access",
    dependencies=[Depends(require_role("researcher", "manager"))],
)
async def get_my_access(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
    fga=Depends(get_openfga_client),
):
    """
    Returns the current user's full access profile including cohort scopes,
    per-trial access levels, and patient counts.
    """
    auth_service = AuthorizationService(db_pool=db_pool, fga_client=fga)
    profile = await auth_service.compute_access_profile(user)

    # Batch-fetch metadata for all accessible trials
    trial_metadata: dict[str, dict] = {}
    if profile.allowed_trial_ids:
        rows = await db_pool.fetch(
            """
            SELECT
                ct.trial_id,
                ct.nct_id,
                ct.title,
                ct.phase,
                ct.therapeutic_area,
                ct.overall_status,
                ct.study_type,
                ct.enrollment_count,
                (
                    SELECT COUNT(*)
                    FROM patient_trial_enrollment pte
                    WHERE pte.trial_id = ct.trial_id
                ) AS patient_count
            FROM clinical_trial ct
            WHERE ct.trial_id = ANY($1::uuid[])
            """,
            profile.allowed_trial_ids,
        )
        trial_metadata = {str(r["trial_id"]): dict(r) for r in rows}

    # Build trial_access list from trial_scopes
    trial_details = []
    for trial_id, scope in profile.trial_scopes.items():
        meta = trial_metadata.get(trial_id, {})
        trial_details.append({
            "trial_id":         trial_id,
            "nct_id":           meta.get("nct_id"),
            "title":            meta.get("title"),
            "phase":            meta.get("phase", ""),
            "therapeutic_area": meta.get("therapeutic_area", ""),
            "overall_status":   meta.get("overall_status", ""),
            "enrollment_count": meta.get("enrollment_count", 0),
            "patient_count":    meta.get("patient_count", 0),
            "access_level":     scope.access_level,
            "is_unrestricted":  scope.is_unrestricted,
            "cohort_filters": [
                {
                    "cohort_id":       cs.cohort_id,
                    "cohort_name":     cs.cohort_name,
                    "filter_criteria": cs.filter_criteria,
                }
                for cs in scope.cohort_scopes
            ],
        })

    # Sort: individual first, then by nct_id
    trial_details.sort(key=lambda t: (
        0 if t["access_level"] == "individual" else 1,
        t.get("nct_id") or "",
    ))

    return {
        "user_id":         user.user_id,
        "username":        user.username,
        "role":            user.role,
        "organization_id": user.organization_id,
        "access_summary": {
            "has_any_access":        profile.has_any_access,
            "aggregate_only":        profile.aggregate_only,
            "aggregate_trial_count": len(profile.aggregate_trial_ids),
            "individual_trial_count": len(profile.individual_trial_ids),
            "aggregate_trial_ids":   profile.aggregate_trial_ids,
            "individual_trial_ids":  profile.individual_trial_ids,
        },
        "trial_access": trial_details,
    }


# ---------------------------------------------------------------------------
# GET /research/suggested-questions
# ---------------------------------------------------------------------------

@router.get(
    "/research/suggested-questions",
    dependencies=[Depends(require_role("researcher", "manager"))],
)
async def get_suggested_questions(
    user: UserContext = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
    fga=Depends(get_openfga_client),
):
    """
    Returns personalized suggested question chips for the frontend query UI,
    based on the researcher's current access profile.
    """
    auth_service = AuthorizationService(db_pool=db_pool, fga_client=fga)
    profile = await auth_service.compute_access_profile(user)

    if not profile.has_any_access or not profile.allowed_trial_ids:
        return {"suggestions": []}

    # Build a parameterized placeholder list: $1, $2, ...
    placeholders = ", ".join(
        f"${i + 1}" for i in range(len(profile.allowed_trial_ids))
    )
    rows = await db_pool.fetch(
        f"""
        SELECT trial_id::text, nct_id, title, phase, therapeutic_area
        FROM clinical_trial
        WHERE trial_id::text IN ({placeholders})
        """,
        *profile.allowed_trial_ids,
    )
    trial_metadata = [dict(r) for r in rows]

    suggestions = generate_suggested_questions(profile, trial_metadata)

    return {
        "suggestions": [
            {
                "text":         s.text,
                "category":     s.category,
                "trial_ids":    s.trial_ids,
                "access_level": s.access_level,
            }
            for s in suggestions
        ]
    }


# ---------------------------------------------------------------------------
# POST /research/query  (synchronous JSON response)
# ---------------------------------------------------------------------------

@router.post(
    "/research/query",
    response_model=QueryResponse,
    dependencies=[Depends(require_role("researcher", "manager", "domain_owner"))],
)
async def execute_query(
    body: QueryRequest,
    user: UserContext = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
    fga=Depends(get_openfga_client),
    agent_service: AgentService = Depends(get_agent_service),
) -> QueryResponse:
    """
    Synchronous semantic query endpoint.

    Phase 6 hardening applied:
      1. Input validation + sanitization
      2. Circuit-breaker check
      3. Access profile computation
      4. Agent execution
      5. Response scrubbing (patient UUID redaction)
    """
    # ── Step 1: validate + sanitize input ─────────────────────────────────
    try:
        validation = validate_query(body.query)
        body = body.model_copy(update={"query": validation.sanitized_query})
    except AgentError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())

    # ── Step 2: circuit-breaker check ────────────────────────────────────
    try:
        mcp_circuit_breaker.check()
    except AgentError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict())

    # ── Step 3: compute access profile ────────────────────────────────────
    auth_service = AuthorizationService(db_pool=db_pool, fga_client=fga)
    profile = await auth_service.compute_access_profile(user)

    if not profile.has_any_access:
        raise HTTPException(
            status_code=403,
            detail={
                "error": True,
                "code":    AgentErrorCode.ACCESS_DENIED.value,
                "message": "You do not have access to any clinical trial data.",
            },
        )

    # Narrow scope if the caller provided explicit trial_ids
    if body.trial_ids:
        unauthorized = [t for t in body.trial_ids if t not in profile.allowed_trial_ids]
        if unauthorized:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": True,
                    "code":    AgentErrorCode.ACCESS_DENIED.value,
                    "message": f"You are not authorized to query trial(s): {unauthorized}",
                },
            )

    # ── Step 4: run the agent ─────────────────────────────────────────────
    try:
        response: QueryResponse = await agent_service.query(body, profile)
    except AgentError as exc:
        logger.error(
            "AgentError during query for user=%s code=%s detail=%s",
            user.username, exc.code, exc.detail,
        )
        status = 503 if exc.retryable else 422
        raise HTTPException(status_code=status, detail=exc.to_dict())
    except Exception as exc:
        logger.exception("Unexpected agent error for user=%s", user.username)
        raise HTTPException(
            status_code=500,
            detail={
                "error":   True,
                "code":    AgentErrorCode.UNEXPECTED.value,
                "message": "An unexpected error occurred. Please try again.",
            },
        )

    # ── Step 5: scrub patient UUIDs from LLM output ───────────────────────
    allowed_uuids = build_allowed_uuid_set(profile)
    scrub_result  = scrub_patient_ids(response.answer, allowed_uuids)

    if scrub_result.was_modified:
        logger.warning(
            "Response scrubber redacted %d UUID(s) for user=%s",
            scrub_result.redaction_count,
            user.username,
        )
        response = response.model_copy(update={"answer": scrub_result.scrubbed_text})

    return response


# ---------------------------------------------------------------------------
# POST /research/query/stream  (NDJSON streaming response)
# ---------------------------------------------------------------------------

@router.post(
    "/research/query/stream",
    dependencies=[Depends(require_role("researcher", "manager", "domain_owner"))],
)
async def execute_query_stream(
    body: QueryRequest,
    user: UserContext = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
    fga=Depends(get_openfga_client),
    agent_service: AgentService = Depends(get_agent_service),
) -> StreamingResponse:
    """
    Streaming semantic query endpoint.
    Yields newline-delimited JSON events:
      status | tool_call | tool_result | thinking | answer_token | complete | error

    Phase 6 hardening: same validation + circuit-breaker + scrubbing as
    the synchronous endpoint, but scrubbing is applied to the final
    'complete' event answer before it is flushed to the client.
    """
    # ── Step 1: validate + sanitize ───────────────────────────────────────
    try:
        validation = validate_query(body.query)
        body = body.model_copy(update={"query": validation.sanitized_query})
    except AgentError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())

    # ── Step 2: circuit-breaker check ─────────────────────────────────────
    try:
        mcp_circuit_breaker.check()
    except AgentError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict())

    # ── Step 3: compute access profile ────────────────────────────────────
    auth_service = AuthorizationService(db_pool=db_pool, fga_client=fga)
    profile = await auth_service.compute_access_profile(user)

    if not profile.has_any_access:
        raise HTTPException(
            status_code=403,
            detail={
                "error":   True,
                "code":    AgentErrorCode.ACCESS_DENIED.value,
                "message": "You do not have access to any clinical trial data.",
            },
        )

    if body.trial_ids:
        unauthorized = [t for t in body.trial_ids if t not in profile.allowed_trial_ids]
        if unauthorized:
            raise HTTPException(
                status_code=403,
                detail={
                    "error":   True,
                    "code":    AgentErrorCode.ACCESS_DENIED.value,
                    "message": f"You are not authorized to query trial(s): {unauthorized}",
                },
            )

    # ── Step 4: build allowed UUID set for scrubbing ───────────────────────
    allowed_uuids = build_allowed_uuid_set(profile)

    # ── Step 5: stream events ─────────────────────────────────────────────
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            async for event in agent_service.query_stream(body, profile):
                # Intercept the final 'complete' event and scrub its answer
                if event.event == "complete" and hasattr(event, "data"):
                    answer = getattr(event.data, "answer", "")
                    scrub_result = scrub_patient_ids(answer, allowed_uuids)
                    if scrub_result.was_modified:
                        logger.warning(
                            "Stream scrubber redacted %d UUID(s) for user=%s",
                            scrub_result.redaction_count,
                            user.username,
                        )
                        event.data.answer = scrub_result.scrubbed_text

                yield event.model_dump_json() + "\n"

        except AgentError as exc:
            logger.error(
                "AgentError during stream for user=%s code=%s",
                user.username, exc.code,
            )
            from api.agent.models import ErrorEvent
            error_event = ErrorEvent(
                event="error",
                data={
                    "code":    exc.code.value,
                    "message": exc.message,
                },
            )
            yield error_event.model_dump_json() + "\n"

        except Exception as exc:
            logger.exception("Unexpected stream error for user=%s", user.username)
            from api.agent.models import ErrorEvent
            error_event = ErrorEvent(
                event="error",
                data={
                    "code":    AgentErrorCode.UNEXPECTED.value,
                    "message": "An unexpected error occurred. Please try again.",
                },
            )
            yield error_event.model_dump_json() + "\n"

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control":    "no-cache",
            "Connection":       "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /research/conversations/{session_id}
# ---------------------------------------------------------------------------

@router.get(
    "/research/conversations/{session_id}",
    dependencies=[Depends(require_role("researcher", "manager"))],
)
async def get_conversation_history(
    session_id: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
):
    """
    Reads LangGraph checkpoint state for a given session_id and returns
    the conversation messages (human + AI turns only).
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    try:
        async with AsyncPostgresSaver.from_conn_string(
            request.app.state.checkpointer_url
        ) as saver:
            config = {"configurable": {"thread_id": session_id}}
            checkpoint_tuple = await saver.aget_tuple(config)

            if not checkpoint_tuple:
                return {"messages": []}

            state = checkpoint_tuple.checkpoint.get("channel_values", {})
            messages = state.get("messages", [])

            formatted: list[dict] = []
            for msg in messages:
                if not (hasattr(msg, "type") and hasattr(msg, "content")):
                    continue
                if msg.type not in ("human", "ai"):
                    continue
                # Skip empty AI messages (mid-loop tool-call frames)
                if not msg.content:
                    continue
                formatted.append({
                    "role":    "user" if msg.type == "human" else "agent",
                    "content": msg.content,
                })

            return {"messages": formatted}

    except Exception as exc:
        logger.warning(
            "Failed to fetch conversation history session=%s user=%s error=%s",
            session_id, user.username, exc,
        )
        return {"messages": []}