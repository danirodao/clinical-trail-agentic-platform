"""
Researcher endpoints — query data with two-layer authorization.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import AsyncGenerator
from pydantic import BaseModel

from auth.dependencies import CurrentUser, require_role
from auth.authorization_service import AuthorizationService
from auth.openfga_client import get_openfga_client
from api.database import get_db_pool

from auth.dependencies import get_current_user
from auth.middleware import UserContext
from api.agent.models import QueryRequest, QueryResponse
from api.agent.service import AgentService

router = APIRouter()





@router.get("/research/my-access", dependencies=[Depends(require_role("researcher", "manager"))])
async def get_my_access(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Get the current user's full access profile including cohort scopes."""
    auth_service = AuthorizationService(db_pool)
    profile = await auth_service.compute_access_profile(user)

    # ── Batch-fetch metadata for ALL accessible trials ────────
    trial_metadata = {}
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

    # ── Build trial_access from trial_scopes ──────────────────
    trial_details = []
    for trial_id, scope in profile.trial_scopes.items():
        meta = trial_metadata.get(trial_id, {})

        trial_details.append({
            "trial_id": trial_id,
            "nct_id": meta.get("nct_id"),
            "title": meta.get("title"),
            "phase": meta.get("phase", ""),
            "therapeutic_area": meta.get("therapeutic_area", ""),
            "overall_status": meta.get("overall_status", ""),
            "enrollment_count": meta.get("enrollment_count", 0),
            "patient_count": meta.get("patient_count", 0),
            "access_level": scope.access_level,
            "is_unrestricted": scope.is_unrestricted,
            "cohort_filters": [
                {
                    "cohort_id": cs.cohort_id,
                    "cohort_name": cs.cohort_name,
                    "filter_criteria": cs.filter_criteria,
                }
                for cs in scope.cohort_scopes
            ],
        })

    # ── Sort: individual first, then by nct_id ────────────────
    trial_details.sort(key=lambda t: (
        0 if t["access_level"] == "individual" else 1,
        t.get("nct_id") or "",
    ))

    return {
        "user_id": user.user_id,
        "username": user.username,
        "role": user.role,
        "organization_id": user.organization_id,
        "access_summary": {
            "has_any_access": profile.has_any_access,
            "aggregate_only": profile.aggregate_only,
            "aggregate_trial_count": len(profile.aggregate_trial_ids),
            "individual_trial_count": len(profile.individual_trial_ids),
            "aggregate_trial_ids": profile.aggregate_trial_ids,
            "individual_trial_ids": profile.individual_trial_ids,
        },
        "trial_access": trial_details,
    }


@router.post("/research/query",
             dependencies=[Depends(require_role("researcher", "manager", "domain_owner"))])
async def research_query(
    body: QueryRequest,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """
    Research query endpoint.
    Demonstrates two-layer authorization filtering.
    Will be replaced with LangGraph agent in Phase 3.
    """
    auth_service = AuthorizationService(db_pool)
    profile = await auth_service.compute_access_profile(user)

    if not profile.has_any_access:
        raise HTTPException(403, "You do not have access to any clinical trial data.")

    # Example: Count accessible patients per trial WITH cohort filters applied
    patient_counts = []
    for trial_id in profile.individual_trial_ids:
        patient_filter = auth_service.build_patient_sql_filter(profile, trial_id)

        count = await db_pool.fetchval(
            f"""
            SELECT COUNT(DISTINCT p.patient_id)
            FROM patient p
            JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
            WHERE pte.trial_id = $1::uuid
            AND ({patient_filter})
            """,
            trial_id,
        )

        scope = profile.trial_scopes.get(trial_id)
        patient_counts.append({
            "trial_id": trial_id,
            "accessible_patients": count,
            "is_unrestricted": scope.is_unrestricted if scope else True,
            "cohort_count": len(scope.cohort_scopes) if scope else 0,
        })

    return {
        "query": body.query,
        "access_profile": {
            "aggregate_only": profile.aggregate_only,
            "individual_trials": len(profile.individual_trial_ids),
            "aggregate_trials": len(profile.aggregate_trial_ids),
        },
        "patient_access": patient_counts,
        "filters_applied": {
            "qdrant": auth_service.build_qdrant_filter(profile),
            "full_patient_sql": auth_service.build_full_patient_filter(profile),
        },
        "message": "Phase 3 will replace this with LangGraph agent pipeline.",
    }

def get_agent_service() -> AgentService:
    """Dependency injection for the Agent Service."""
    return AgentService()

async def get_authorization_service(request: Request) -> AuthorizationService:
    """
    Dependency injection for AuthorizationService.
    Assumes your FastAPI app stores the DB pool and FGA client on app.state
    or you have a dedicated dependency for them. Adjust as needed.
    """
    # Example using app.state:
    db_pool = request.app.state.db_pool
    fga_client = request.app.state.fga_client
    return AuthorizationService(db=db_pool, fga=fga_client)

@router.post("/research/query", response_model=QueryResponse)
async def execute_query(
    request: QueryRequest,
    user: UserContext = Depends(require_role("researcher")),
    db = Depends(get_db_pool),      # <-- Use your real DB dependency
    fga = Depends(get_openfga_client), # <-- Use your real FGA dependency
    agent_service: AgentService = Depends(get_agent_service),
):
    """Synchronous Semantic Query Endpoint."""
    # Build the service exactly how /my-access builds it
    auth_service = AuthorizationService(db, fga)
    
    profile = await auth_service.compute_access_profile(user)
    
    if not profile.has_any_access:
        raise HTTPException(status_code=403, detail="No access")

    return await agent_service.query(request, profile)


@router.post("/research/query/stream")
async def execute_query_stream(
    request: QueryRequest,
    user: UserContext = Depends(require_role("researcher")),
    db = Depends(get_db_pool),      # <-- Use your real DB dependency
    fga = Depends(get_openfga_client), # <-- Use your real FGA dependency
    agent_service: AgentService = Depends(get_agent_service),
):

    print(f"DEBUG: Parsed request: {request}")
    """Streaming Semantic Query Endpoint (NDJSON)."""
    # Build the service exactly how /my-access builds it
    auth_service = AuthorizationService(db, fga)
    
    profile = await auth_service.compute_access_profile(user)
    
    if not profile.has_any_access:
        raise HTTPException(status_code=403, detail="No access")

    async def event_generator() -> AsyncGenerator[str, None]:
        async for event in agent_service.query_stream(request, profile):
            yield event.model_dump_json() + "\n"

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no", 
        }
    )
@router.get("/research/conversations/{session_id}")
async def get_conversation_history(
    session_id: str, 
    request: Request,
    user: UserContext = Depends(require_role("researcher"))
):
    """Fetches the message history for a given session."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    
    try:
        # Create a new checkpointer connection for this request
        async with AsyncPostgresSaver.from_conn_string(request.app.state.checkpointer_url) as saver:
            config = {"configurable": {"thread_id": session_id}}
            
            checkpoint_tuple = await saver.aget_tuple(config)
            if not checkpoint_tuple:
                return {"messages": []}
                
            state = checkpoint_tuple.checkpoint.get("channel_values", {})
            messages = state.get("messages", [])
            
            formatted_messages = []
            for msg in messages:
                if hasattr(msg, 'type') and msg.type in ["human", "ai"]:
                    if hasattr(msg, 'content') and msg.content:
                        formatted_messages.append({
                            "role": "user" if msg.type == "human" else "agent",
                            "content": msg.content
                        })
                        
            return {"messages": formatted_messages}
    except Exception as e:
        print(f"Failed to fetch history: {e}")
        return {"messages": []}