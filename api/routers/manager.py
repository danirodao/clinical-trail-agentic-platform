"""
Manager endpoints — request access, create cohorts, assign researchers.
"""

from uuid import UUID
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from auth.cohort_service import CohortService, CeilingViolationError
from auth.dependencies import CurrentUser, require_role
from auth.access_request_service import AccessRequestService
from auth.openfga_client import get_openfga_client
from api.database import get_db_pool
import json

router = APIRouter()


class AccessRequestBody(BaseModel):
    asset_id: UUID
    justification: str
    scope: Optional[dict] = None
    requested_duration_days: int = 365


class AssignResearcherBody(BaseModel):
    researcher_username: str  # Keycloak username
    trial_id: Optional[UUID] = None
    cohort_id: Optional[UUID] = None
    access_level: str = "individual"  # 'individual' or 'aggregate'
    duration_days: int = 180

class CohortCreateBody(BaseModel):
    name: str
    description: str = ""
    filter_criteria: dict
    is_dynamic: bool = True
    
class RevokeAssignmentBody(BaseModel):
    reason: str = Field(..., min_length=5)


@router.post("/access-requests/", dependencies=[Depends(require_role("manager"))])
async def request_access(
    body: AccessRequestBody,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Manager requests access to a data asset on behalf of their organization."""
    service = AccessRequestService(db_pool)
    return await service.create_request(
        user,
        body.asset_id,
        body.justification,
        body.scope,
        body.requested_duration_days,
    )


@router.get("/my-org/grants", dependencies=[Depends(require_role("manager"))])
async def list_org_grants(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """List all active access grants for my organization."""
    rows = await db_pool.fetch(
        """SELECT ag.grant_id, ag.asset_id, da.reference_id as trial_id, ag.scope, ag.granted_at,
                  ag.expires_at, ag.is_active,
                  da.title as asset_title, da.asset_type
           FROM access_grant ag
           JOIN data_asset da ON ag.asset_id = da.asset_id
           WHERE ag.organization_id = $1 AND ag.is_active = TRUE
           ORDER BY ag.granted_at DESC""",
        user.organization_id,
    )
    return {"grants": [dict(r) for r in rows]}


@router.post("/assignments/", dependencies=[Depends(require_role("manager"))])
async def assign_researcher(
    body: AssignResearcherBody,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """
    Assign a researcher to a trial or cohort.
    
    For individual access: writes OpenFGA tuples so the researcher
    can access patient-level data.
    
    For aggregate access: no OpenFGA tuple needed — aggregate access
    comes from org membership + granted_org automatically.
    
    For cohort assignments: expands to per-trial tuples for all trials
    in the cohort.
    """
    fga = get_openfga_client()

    if not body.trial_id and not body.cohort_id:
        raise HTTPException(400, "Must specify either trial_id or cohort_id")

    # ── Resolve trial IDs ─────────────────────────────────────
    # If assigning to a cohort, get all trial_ids from cohort_trial
    trial_ids: list[str] = []

    if body.cohort_id:
        rows = await db_pool.fetch(
            "SELECT trial_id FROM cohort_trial WHERE cohort_id = $1",
            to_uuid(body.cohort_id),
        )
        trial_ids = [str(r["trial_id"]) for r in rows]

        if not trial_ids:
            raise HTTPException(400, f"Cohort {body.cohort_id} has no trials linked")

    elif body.trial_id:
        trial_ids = [body.trial_id]

    # ── Ceiling check: verify org has access to ALL trials ────
    for tid in trial_ids:
        result = await fga.check(
            user=f"organization:{user.organization_id}",
            relation="granted_org",
            object=f"clinical_trial:{tid}",
        )
        if not result.allowed:
            raise HTTPException(
                403,
                f"Organization {user.organization_id} does not have access to "
                f"trial {tid}. Request access from the domain owner first."
            )

    # ── Check researcher belongs to same org ──────────────────
    # In production, query Keycloak Admin API to verify.
    # For now, we trust the manager is assigning within their org.

    expires_at = datetime.now(timezone.utc) + timedelta(days=body.duration_days)

    # ── Write PostgreSQL record ───────────────────────────────
    


    assignment = await db_pool.fetchrow(
        """INSERT INTO researcher_assignment
           (researcher_id, organization_id, trial_id, cohort_id,
            access_level, assigned_by, expires_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           RETURNING assignment_id""",
        body.researcher_username,
        user.organization_id,
        to_uuid(body.trial_id),
        to_uuid(body.cohort_id),
        body.access_level,
        user.user_id,
        expires_at,
    )

    # ── Write OpenFGA tuples (individual access only) ─────────
    fga_tuples_written = 0

    if body.access_level == "individual":
        tuples = [
            {
                "user": f"user:{body.researcher_username}",
                "relation": "assigned_researcher",
                "object": f"clinical_trial:{tid}",
            }
            for tid in trial_ids
        ]

        # Write in batches of 100 (OpenFGA limit)
        for i in range(0, len(tuples), 100):
            batch = tuples[i:i + 100]
            success = await fga.write_tuples(batch)
            if success:
                fga_tuples_written += len(batch)
            else:
                # Log but don't fail — PG record exists for reconciliation
                import logging
                logging.getLogger(__name__).error(
                    f"Failed to write OpenFGA tuples for assignment "
                    f"{assignment['assignment_id']}, batch starting at {i}"
                )

    # ── Audit log ─────────────────────────────────────────────
    
    await db_pool.execute(
        """INSERT INTO auth_audit_log
           (action, actor_id, actor_role, target_type, target_id, details)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
        "researcher_assigned",
        user.user_id,
        user.role,
        "researcher_assignment",
        str(assignment["assignment_id"]),
        json.dumps({
            "researcher": body.researcher_username,
            "trial_ids": [str(t) for t in trial_ids],
            "cohort_id": str(body.cohort_id) if body.cohort_id else None,
            "access_level": body.access_level,
            "fga_tuples_written": fga_tuples_written,
            "duration_days": body.duration_days,
        }),
    )

    return {
        "assignment_id": str(assignment["assignment_id"]),
        "researcher": body.researcher_username,
        "trial_ids": trial_ids,
        "access_level": body.access_level,
        "fga_tuples_written": fga_tuples_written,
        "expires_at": expires_at.isoformat(),
        "status": "assigned",
    }
# ─── Revoke Assignment ───────────────────────────────────────
 # ── Safe UUID conversion (handles str, UUID, or None) ─────
def to_uuid(val) -> UUID | None:
    if val is None:
        return None
    if isinstance(val, UUID):
        return val
    return UUID(str(val))
@router.post("/assignments/{assignment_id}/revoke",
             dependencies=[Depends(require_role("manager"))])
async def revoke_assignment(
    assignment_id: UUID,
    body: RevokeAssignmentBody,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """
    Revoke a researcher's assignment.
    Soft-deletes the PG record AND removes OpenFGA tuples.
    """
    fga = get_openfga_client()

    # Get the assignment
    assignment = await db_pool.fetchrow(
        """SELECT assignment_id, researcher_id, organization_id,
                  trial_id, cohort_id, access_level
           FROM researcher_assignment
           WHERE assignment_id = $1
           AND organization_id = $2
           AND revoked_at IS NULL""",
        assignment_id, user.organization_id,
    )

    if not assignment:
        raise HTTPException(404, "Assignment not found or already revoked")

    # Resolve trial IDs (same logic as assignment)
    trial_ids: list[str] = []
    if assignment["cohort_id"]:
        rows = await db_pool.fetch(
            "SELECT trial_id FROM cohort_trial WHERE cohort_id = $1",
            assignment["cohort_id"],
        )
        trial_ids = [str(r["trial_id"]) for r in rows]
    elif assignment["trial_id"]:
        trial_ids = [str(assignment["trial_id"])]

    # Soft-delete in PostgreSQL
    await db_pool.execute(
        """UPDATE researcher_assignment
           SET revoked_at = NOW(), revoked_by = $1
           WHERE assignment_id = $2""",
        user.user_id, assignment_id,
    )

    # Remove OpenFGA tuples (only if individual access was granted)
    fga_tuples_deleted = 0
    if assignment["access_level"] == "individual" and trial_ids:
        # Before deleting, check if the researcher has OTHER active assignments
        # for the same trial. If so, don't delete the OpenFGA tuple.
        for tid in trial_ids:
            other_active = await db_pool.fetchval(
                """SELECT COUNT(*) FROM researcher_assignment
                   WHERE researcher_id = $1
                   AND organization_id = $2
                   AND access_level = 'individual'
                   AND revoked_at IS NULL
                   AND expires_at > NOW()
                   AND assignment_id != $3
                   AND (
                       trial_id = $4::uuid
                       OR cohort_id IN (
                           SELECT cohort_id FROM cohort_trial WHERE trial_id = $4::uuid
                       )
                   )""",
                assignment["researcher_id"],
                assignment["organization_id"],
                assignment_id,
                UUID(tid),
            )

            if other_active == 0:
                # Safe to delete — no other assignment covers this trial
                success = await fga.delete_tuples([{
                    "user": f"user:{assignment['researcher_id']}",
                    "relation": "assigned_researcher",
                    "object": f"clinical_trial:{tid}",
                }])
                if success:
                    fga_tuples_deleted += 1

    # Audit
    await db_pool.execute(
        """INSERT INTO auth_audit_log
           (action, actor_id, actor_role, target_type, target_id, details)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
        "researcher_assignment_revoked",
        user.user_id,
        user.role,
        "researcher_assignment",
        str(assignment_id),
        json.dumps({
            "researcher": assignment["researcher_id"],
            "trial_ids": [str(t) for t in trial_ids],
            "access_level": assignment["access_level"],
            "fga_tuples_deleted": fga_tuples_deleted,
            "reason": body.reason,
        }),
    )

    return {
        "assignment_id": str(assignment_id),
        "status": "revoked",
        "fga_tuples_deleted": fga_tuples_deleted,
    }

@router.get("/assignments/", dependencies=[Depends(require_role("manager"))])
async def list_assignments(
    user: CurrentUser,
    include_revoked: bool = Query(False),
    db_pool=Depends(get_db_pool),
):
    """List all researcher assignments in my organization."""
    query = """
        SELECT ra.assignment_id, ra.researcher_id,
               ra.trial_id, ra.cohort_id,
               ra.access_level, ra.assigned_by,
               ra.assigned_at, ra.expires_at,
               ra.revoked_at, ra.revoked_by,
               ra.is_active,
               ct.nct_id as trial_nct_id, ct.title as trial_title,
               c.name as cohort_name
        FROM researcher_assignment ra
        LEFT JOIN clinical_trial ct ON ra.trial_id = ct.trial_id
        LEFT JOIN cohort c ON ra.cohort_id = c.cohort_id
        WHERE ra.organization_id = $1
    """
    if not include_revoked:
        query += " AND ra.revoked_at IS NULL"

    query += " ORDER BY ra.assigned_at DESC"

    rows = await db_pool.fetch(query, user.organization_id)
    return {"assignments": [dict(r) for r in rows]}
    
@router.get("/cohorts/filter-options", dependencies=[Depends(require_role("manager"))])
async def get_filter_options(
    db_pool=Depends(get_db_pool),
):
    """Get dynamic database-driven enumerations for cohort UI."""
    service = CohortService(db_pool)
    return await service.get_filter_options()

@router.post("/cohorts/preview", dependencies=[Depends(require_role("manager"))])
async def preview_cohort(
    body: CohortCreateBody,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Preview a cohort before saving — returns patient count and ceiling validation."""
    service = CohortService(db_pool)
    return await service.preview_cohort(user, body.filter_criteria)


@router.post("/cohorts/", dependencies=[Depends(require_role("manager"))])
async def create_cohort(
    body: CohortCreateBody,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Create a cohort within the organization's access ceiling."""
    service = CohortService(db_pool)
    try:
        return await service.create_cohort(
            user, body.name, body.description,
            body.filter_criteria, body.is_dynamic,
        )
    except CeilingViolationError as e:
        raise HTTPException(403, detail={
            "message": "Cohort contains trials outside your organization's access ceiling",
            "violations": e.violations,
        })


@router.get("/cohorts/", dependencies=[Depends(require_role("manager"))])
async def list_cohorts(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """List all cohorts for the manager's organization."""
    service = CohortService(db_pool)
    cohorts = await service.list_org_cohorts(user)
    return {"cohorts": cohorts}