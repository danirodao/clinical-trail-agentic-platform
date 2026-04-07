"""
Domain Owner endpoints.
Filter-based collection publishing, access request review, grant management.
"""

from uuid import UUID
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth.dependencies import CurrentUser, require_role
from auth.asset_service import AssetService
from api.main import get_db_pool
from auth.reconciliation_service import ReconciliationService

router = APIRouter()


# ─── Models ───────────────────────────────────────────────────

class TrialFilter(BaseModel):
    therapeutic_areas: Optional[list[str]] = None
    phases: Optional[list[str]] = None
    study_types: Optional[list[str]] = None
    regions: Optional[list[str]] = None
    countries: Optional[list[str]] = None
    overall_statuses: Optional[list[str]] = None
    min_enrollment: Optional[int] = None
    lead_sponsors: Optional[list[str]] = None


class PublishCollectionRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=500)
    description: str = Field(..., min_length=10)
    filter_criteria: TrialFilter
    sensitivity_level: str = "standard"
    is_dynamic: bool = True


class ReviewAction(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")
    notes: Optional[str] = None
    grant_duration_days: int = Field(365, ge=1, le=3650)


class RevokeAction(BaseModel):
    reason: str = Field(..., min_length=5)


# ─── Filter Options (for publish wizard dropdowns) ────────────

@router.get("/assets/filter-options", dependencies=[Depends(require_role("domain_owner"))])
async def get_filter_options(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Get available filter values for the publishing wizard."""
    service = AssetService(db_pool)
    return await service.get_filter_options()


# ─── Discovery & Preview ─────────────────────────────────────

@router.post("/assets/discover", dependencies=[Depends(require_role("domain_owner"))])
async def discover_trials(
    body: TrialFilter,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Preview which trials match the filter before publishing."""
    service = AssetService(db_pool)
    trials = await service.discover_trials(body.dict(exclude_none=True))

    published = sum(1 for t in trials if t["already_published"])

    return {
        "total_matching": len(trials),
        "already_published": published,
        "available_to_publish": len(trials) - published,
        "trials": trials,
        "summary": {
            "therapeutic_areas": sorted({t["therapeutic_area"] for t in trials if t["therapeutic_area"]}),
            "phases": sorted({t["phase"] for t in trials if t["phase"]}),
            "total_patients": sum(t["patient_count"] or 0 for t in trials),
            "total_enrollment": sum(t["enrollment_count"] or 0 for t in trials),
            "drugs": sorted({d for t in trials for d in (t["drug_names"] or [])}),
            "conditions": sorted({c for t in trials for c in (t["condition_names"] or [])}),
        },
    }


# ─── Publish Collection ──────────────────────────────────────

@router.post("/assets/publish-collection", dependencies=[Depends(require_role("domain_owner"))])
async def publish_collection(
    body: PublishCollectionRequest,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Publish a filter-based collection of trials."""
    service = AssetService(db_pool)
    try:
        return await service.publish_collection(
            user=user,
            name=body.name,
            description=body.description,
            filter_criteria=body.filter_criteria.dict(exclude_none=True),
            sensitivity_level=body.sensitivity_level,
            is_dynamic=body.is_dynamic,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── Refresh Dynamic Collections ─────────────────────────────

@router.post("/assets/refresh-collections", dependencies=[Depends(require_role("domain_owner"))])
async def refresh_dynamic_collections(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Re-evaluate dynamic collection filters and add new matching trials."""
    service = AssetService(db_pool)
    results = await service.refresh_dynamic_collections(owner_id=user.user_id)
    return {"refreshed": results}


# ─── List My Assets & Collections ─────────────────────────────

@router.get("/assets/", dependencies=[Depends(require_role("domain_owner"))])
async def list_my_assets(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    collections = await db_pool.fetch(
        """SELECT
              dac.collection_id, dac.name, dac.description,
              dac.sensitivity_level, dac.is_dynamic,
              dac.trial_count, dac.total_patients, dac.total_enrollment,
              dac.therapeutic_areas, dac.phases, dac.study_types,
              dac.regions, dac.countries,
              dac.filter_criteria, dac.created_at, dac.is_active,
              (SELECT COUNT(DISTINCT ag.organization_id)
               FROM access_grant ag
               JOIN collection_asset ca ON ag.asset_id = ca.asset_id
               WHERE ca.collection_id = dac.collection_id
               AND ag.is_active = TRUE) as organizations_with_access,
              (SELECT COUNT(*)
               FROM access_request ar
               WHERE ar.collection_id = dac.collection_id
               AND ar.status = 'pending') as pending_requests
           FROM data_asset_collection dac
           WHERE dac.owner_id = $1
           ORDER BY dac.created_at DESC""",
        user.user_id,
    )
    return {"collections": [dict(c) for c in collections]}


# ─── Access Requests ──────────────────────────────────────────

@router.get("/access-requests/pending", dependencies=[Depends(require_role("domain_owner"))])
async def list_pending_requests(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    rows = await db_pool.fetch(
        """SELECT ar.request_id, ar.asset_id, ar.collection_id,
                  ar.requesting_user_id, ar.requesting_org_id,
                  ar.justification, ar.scope, ar.expires_at, ar.created_at,
                  dac.name as collection_name, dac.trial_count,
                  dac.therapeutic_areas, dac.phases,
                  dac.sensitivity_level, dac.total_patients
           FROM access_request ar
           JOIN data_asset_collection dac ON ar.collection_id = dac.collection_id
           WHERE dac.owner_id = $1 AND ar.status = 'pending'
           ORDER BY ar.created_at ASC""",
        user.user_id,
    )
    return {"requests": [dict(r) for r in rows]}


@router.get("/access-requests/history", dependencies=[Depends(require_role("domain_owner"))])
async def list_request_history(
    user: CurrentUser,
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    db_pool=Depends(get_db_pool),
):
    query = """
        SELECT ar.*, dac.name as collection_name, dac.trial_count
        FROM access_request ar
        JOIN data_asset_collection dac ON ar.collection_id = dac.collection_id
        WHERE dac.owner_id = $1
    """
    params: list = [user.user_id]
    if status:
        query += f" AND ar.status = ${len(params)+1}"
        params.append(status)
    query += f" ORDER BY ar.created_at DESC LIMIT ${len(params)+1}"
    params.append(limit)

    rows = await db_pool.fetch(query, *params)
    return {"requests": [dict(r) for r in rows]}


@router.post("/access-requests/{request_id}/review",
             dependencies=[Depends(require_role("domain_owner"))])
async def review_request(
    request_id: UUID,
    body: ReviewAction,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    request = await db_pool.fetchrow(
        "SELECT * FROM access_request WHERE request_id = $1 AND status = 'pending'",
        request_id,
    )
    if not request:
        raise HTTPException(404, "Request not found or not pending")

    if body.action == "reject":
        await db_pool.execute(
            """UPDATE access_request
               SET status = 'rejected', reviewed_by = $1,
                   reviewed_at = NOW(), review_notes = $2
               WHERE request_id = $3""",
            user.user_id, body.notes, request_id,
        )
        return {"status": "rejected", "request_id": str(request_id)}

    # Approve: expand collection to per-trial grants
    expires_at = datetime.now(timezone.utc) + timedelta(days=body.grant_duration_days)

    service = AssetService(db_pool)
    result = await service.grant_collection_access(
        collection_id=request["collection_id"],
        org_id=request["requesting_org_id"],
        granted_by=user.user_id,
        request_id=request_id,
        expires_at=expires_at,
    )

    await db_pool.execute(
        """UPDATE access_request
           SET status = 'approved', reviewed_by = $1,
               reviewed_at = NOW(), review_notes = $2
           WHERE request_id = $3""",
        user.user_id, body.notes, request_id,
    )

    return {
        "status": "approved",
        "request_id": str(request_id),
        **result,
    }


# ─── Active Grants ────────────────────────────────────────────

@router.get("/grants/", dependencies=[Depends(require_role("domain_owner"))])
async def list_grants(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """List grants grouped by collection + org."""
    rows = await db_pool.fetch(
        """SELECT
              dac.collection_id, dac.name as collection_name,
              ag.organization_id,
              COUNT(ag.grant_id) as trial_count,
              MIN(ag.granted_at) as first_granted,
              MIN(ag.expires_at) as earliest_expiry,
              bool_and(ag.is_active) as all_active
           FROM access_grant ag
           JOIN data_asset da ON ag.asset_id = da.asset_id
           JOIN collection_asset ca ON da.asset_id = ca.asset_id
           JOIN data_asset_collection dac ON ca.collection_id = dac.collection_id
           WHERE dac.owner_id = $1
           GROUP BY dac.collection_id, dac.name, ag.organization_id
           ORDER BY dac.name, ag.organization_id""",
        user.user_id,
    )
    return {"grants": [dict(r) for r in rows]}


@router.post("/grants/{collection_id}/revoke/{org_id}",
             dependencies=[Depends(require_role("domain_owner"))])
async def revoke_collection_grant(
    collection_id: UUID,
    org_id: str,
    body: RevokeAction,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Revoke all grants for an org on a collection."""
    service = AssetService(db_pool)
    result = await service.revoke_collection_access(
        collection_id, org_id, user.user_id, body.reason
    )
    return result


# ─── Dashboard Stats ──────────────────────────────────────────

@router.get("/dashboard/stats", dependencies=[Depends(require_role("domain_owner"))])
async def get_dashboard_stats(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    total_trials = await db_pool.fetchval("SELECT COUNT(*) FROM clinical_trial")
    collections = await db_pool.fetchval(
        "SELECT COUNT(*) FROM data_asset_collection WHERE owner_id = $1 AND is_active = TRUE",
        user.user_id,
    )
    published_trials = await db_pool.fetchval(
        "SELECT COUNT(DISTINCT reference_id) FROM data_asset WHERE owner_id = $1 AND is_active = TRUE",
        user.user_id,
    )
    pending = await db_pool.fetchval(
        """SELECT COUNT(*) FROM access_request ar
           JOIN data_asset_collection dac ON ar.collection_id = dac.collection_id
           WHERE dac.owner_id = $1 AND ar.status = 'pending'""",
        user.user_id,
    )
    active_grants = await db_pool.fetchval(
        """SELECT COUNT(DISTINCT ag.organization_id)
           FROM access_grant ag
           JOIN data_asset da ON ag.asset_id = da.asset_id
           WHERE da.owner_id = $1 AND ag.is_active = TRUE""",
        user.user_id,
    )

    return {
        "total_trials_in_system": total_trials,
        "published_trials": published_trials,
        "unpublished_trials": total_trials - published_trials,
        "collections": collections,
        "pending_requests": pending,
        "organizations_with_access": active_grants,
    }


# ─── Audit Log ────────────────────────────────────────────────

@router.get("/audit-log/", dependencies=[Depends(require_role("domain_owner"))])
async def get_audit_log(
    user: CurrentUser,
    limit: int = Query(50, le=500),
    offset: int = Query(0),
    db_pool=Depends(get_db_pool),
):
    rows = await db_pool.fetch(
        """SELECT * FROM auth_audit_log
           WHERE actor_id = $1
           ORDER BY created_at DESC
           LIMIT $2 OFFSET $3""",
        user.user_id, limit, offset,
    )
    total = await db_pool.fetchval(
        "SELECT COUNT(*) FROM auth_audit_log WHERE actor_id = $1", user.user_id
    )
    return {"entries": [dict(r) for r in rows], "total": total}
    
@router.post("/admin/reconcile", dependencies=[Depends(require_role("domain_owner"))])
async def reconcile_authorization(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """
    Reconcile OpenFGA tuples with PostgreSQL state.
    Fixes drift from partial failures. Safe to run repeatedly.
    """
    service = ReconciliationService(db_pool)
    results = await service.reconcile_all()

    await db_pool.execute(
        """INSERT INTO auth_audit_log
           (action, actor_id, actor_role, target_type, target_id, details)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        "reconciliation_executed",
        user.user_id,
        user.role,
        "system",
        "openfga",
        results,
    )

    return results