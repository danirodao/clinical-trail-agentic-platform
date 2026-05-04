"""
Marketplace — Managers browse and request access to collections.
"""

from uuid import UUID
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone

from auth.dependencies import CurrentUser, require_role
from auth.asset_service import AssetService
from api.main import get_db_pool

router = APIRouter()


class CollectionAccessRequest(BaseModel):
    collection_id: UUID
    justification: str = Field(..., min_length=20)
    requested_duration_days: int = Field(365, ge=30, le=3650)
    scope: Optional[dict] = None


@router.get("/marketplace/{collection_id}/request-options", dependencies=[Depends(require_role("manager"))])
async def get_collection_request_options(
    collection_id: UUID,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    coll = await db_pool.fetchrow(
        """
        SELECT collection_id, owner_id
        FROM data_asset_collection
        WHERE collection_id = $1 AND is_active = TRUE
        """,
        collection_id,
    )
    if not coll:
        raise HTTPException(404, "Collection not found")

    rows = await db_pool.fetch(
        """
        SELECT purpose_key, label, description
        FROM governance_purpose
        WHERE is_active = TRUE
          AND (owner_id IS NULL OR owner_id = $1)
        ORDER BY owner_id NULLS FIRST, purpose_key
        """,
        coll["owner_id"],
    )
    return {
        "collection_id": str(collection_id),
        "purposes": [dict(r) for r in rows],
    }


@router.get("/marketplace/", dependencies=[Depends(require_role("manager"))])
async def browse_marketplace(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Browse available trial collections."""
    service = AssetService(db_pool)
    return await service.get_marketplace_view(user)


@router.get("/marketplace/{collection_id}", dependencies=[Depends(require_role("manager"))])
async def get_collection_detail(
    collection_id: UUID,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Get detailed view of a collection including trial list."""
    service = AssetService(db_pool)
    collection = await db_pool.fetchrow(
        """SELECT * FROM data_asset_collection WHERE collection_id = $1 AND is_active = TRUE""",
        collection_id,
    )
    if not collection:
        raise HTTPException(404, "Collection not found")

    patient_count_sql, patient_count_params, _ = service._build_patient_count_subquery(
        collection["filter_criteria"] or {},
        "ct.trial_id",
        start_idx=2,
    )

    trials = await db_pool.fetch(
        f"""SELECT ct.trial_id, ct.nct_id, ct.title, ct.phase,
                  ct.therapeutic_area, ct.overall_status,
                  ct.enrollment_count, ct.regions, ct.countries,
                  (SELECT array_agg(DISTINCT i.name) FROM intervention i
                   WHERE i.trial_id = ct.trial_id) AS drugs,
                  ({patient_count_sql}) AS patient_count
           FROM clinical_trial ct
           JOIN collection_asset ca ON ct.trial_id = ca.trial_id
           WHERE ca.collection_id = $1
           ORDER BY ct.therapeutic_area, ct.phase""",
        collection_id,
        *patient_count_params,
    )

    # Check org's access status
    granted_count = await db_pool.fetchval(
        """SELECT COUNT(DISTINCT ca.trial_id)
           FROM collection_asset ca
           JOIN access_grant ag ON ag.asset_id = ca.asset_id
           WHERE ca.collection_id = $1
           AND ag.organization_id = $2
           AND ag.is_active = TRUE""",
        collection_id, user.organization_id,
    )

    pending_request = await db_pool.fetchrow(
        """SELECT request_id, created_at FROM access_request
           WHERE collection_id = $1 AND requesting_org_id = $2 AND status = 'pending'""",
        collection_id, user.organization_id,
    )

    return {
        "collection": dict(collection),
        "trials": [dict(t) for t in trials],
        "access": {
            "granted_count": granted_count,
            "total_count": collection["trial_count"],
            "has_full_access": granted_count >= collection["trial_count"],
            "pending_request": dict(pending_request) if pending_request else None,
        },
    }


@router.post("/marketplace/request-access", dependencies=[Depends(require_role("manager"))])
async def request_collection_access(
    body: CollectionAccessRequest,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Request access to a trial collection for your organization."""
    # Verify collection exists
    coll = await db_pool.fetchrow(
        "SELECT collection_id, name, owner_id FROM data_asset_collection WHERE collection_id = $1 AND is_active = TRUE",
        body.collection_id,
    )
    if not coll:
        raise HTTPException(404, "Collection not found")

    # Check for duplicate pending request
    existing = await db_pool.fetchrow(
        """SELECT request_id FROM access_request
           WHERE collection_id = $1 AND requesting_org_id = $2 AND status = 'pending'""",
        body.collection_id, user.organization_id,
    )
    if existing:
        raise HTTPException(409, f"Pending request already exists: {existing['request_id']}")

    # Check if already fully granted
    trial_count = await db_pool.fetchval(
        "SELECT COUNT(*) FROM collection_asset WHERE collection_id = $1", body.collection_id
    )
    granted = await db_pool.fetchval(
        """SELECT COUNT(DISTINCT ca.trial_id) FROM collection_asset ca
           JOIN access_grant ag ON ag.asset_id = ca.asset_id
           WHERE ca.collection_id = $1 AND ag.organization_id = $2 AND ag.is_active = TRUE""",
        body.collection_id, user.organization_id,
    )
    if granted >= trial_count:
        raise HTTPException(409, "Organization already has full access to this collection")

    scope = body.scope or {}
    requested_purposes = [
        str(v).strip() for v in (
            scope.get("approved_purposes")
            or scope.get("purposes")
            or ([] if not scope.get("purpose") else [scope.get("purpose")])
        )
        if str(v).strip()
    ]
    if requested_purposes:
        rows = await db_pool.fetch(
            """
            SELECT purpose_key
            FROM governance_purpose
            WHERE is_active = TRUE
              AND purpose_key = ANY($1::text[])
              AND (owner_id IS NULL OR owner_id = $2)
            """,
            requested_purposes,
            coll["owner_id"],
        )
        allowed = {str(r["purpose_key"]) for r in rows}
        invalid = sorted(set(requested_purposes) - allowed)
        if invalid:
            raise HTTPException(
                422,
                f"Invalid purpose(s) for this collection: {invalid}",
            )

    expires_at = datetime.now(timezone.utc) + timedelta(days=body.requested_duration_days)

    row = await db_pool.fetchrow(
        """INSERT INTO access_request
           (collection_id, requesting_user_id, requesting_org_id,
            justification, scope, expires_at)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING request_id, created_at""",
        body.collection_id, user.user_id, user.organization_id,
        body.justification, scope, expires_at,
    )

    return {
        "status": "pending",
        "request_id": str(row["request_id"]),
        "collection_name": coll["name"],
    }