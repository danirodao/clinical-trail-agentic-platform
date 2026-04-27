"""
Access Request Service — Manages the workflow:
  Manager requests access → Domain owner approves → OpenFGA tuples written
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import asyncpg

from auth.middleware import UserContext
from auth.openfga_client import OpenFGAClient, get_openfga_client
from auth.openfga_outbox import enqueue_delete_tuples, enqueue_write_tuples

logger = logging.getLogger(__name__)

DEFAULT_GRANT_DAYS = 365


class AccessRequestService:
    """Manages access request lifecycle and tuple synchronization."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        fga_client: Optional[OpenFGAClient] = None,
    ):
        self.db = db_pool
        self.fga = fga_client or get_openfga_client()

    # ─── Manager: Create Request ──────────────────────────────

    async def create_request(
        self,
        user: UserContext,
        asset_id: UUID,
        justification: str,
        scope: Optional[dict] = None,
        requested_duration_days: int = DEFAULT_GRANT_DAYS,
    ) -> dict:
        """Manager creates an access request for a data asset."""
        if user.role != "manager":
            raise PermissionError("Only managers can create access requests")

        # Verify asset exists and is active
        asset = await self.db.fetchrow(
            "SELECT * FROM data_asset WHERE asset_id = $1 AND is_active = TRUE",
            asset_id,
        )
        if not asset:
            raise ValueError(f"Data asset {asset_id} not found or inactive")

        # Check for duplicate pending request
        existing = await self.db.fetchrow(
            """SELECT request_id FROM access_request
               WHERE asset_id = $1 AND requesting_org_id = $2 AND status = 'pending'""",
            asset_id, user.organization_id,
        )
        if existing:
            raise ValueError(f"Pending request already exists: {existing['request_id']}")

        expires_at = datetime.now(timezone.utc) + timedelta(days=requested_duration_days)

        row = await self.db.fetchrow(
            """INSERT INTO access_request
               (asset_id, requesting_user_id, requesting_org_id, justification, scope, expires_at)
               VALUES ($1, $2, $3, $4, $5, $6)
               RETURNING request_id, status, created_at""",
            asset_id, user.user_id, user.organization_id,
            justification, scope or {}, expires_at,
        )

        # Audit log
        await self._audit_log(
            action="access_requested",
            actor_id=user.user_id,
            actor_role=user.role,
            target_type="data_asset",
            target_id=str(asset_id),
            details={
                "request_id": str(row["request_id"]),
                "organization_id": user.organization_id,
                "justification": justification,
            },
        )

        return dict(row)

    # ─── Domain Owner: Approve Request ────────────────────────

    async def approve_request(
        self,
        user: UserContext,
        request_id: UUID,
        scope: Optional[dict] = None,
        grant_duration_days: int = DEFAULT_GRANT_DAYS,
        notes: Optional[str] = None,
    ) -> dict:
        """Domain owner approves an access request → writes OpenFGA tuples."""
        if user.role != "domain_owner":
            raise PermissionError("Only domain owners can approve access requests")

        # Get the request
        request = await self.db.fetchrow(
            """SELECT ar.*, da.reference_id, da.asset_type, da.owner_id
               FROM access_request ar
               JOIN data_asset da ON ar.asset_id = da.asset_id
               WHERE ar.request_id = $1 AND ar.status = 'pending'""",
            request_id,
        )
        if not request:
            raise ValueError(f"Request {request_id} not found or not pending")

        # Verify the approver owns the asset
        if request["owner_id"] != user.user_id:
            raise PermissionError("You do not own this data asset")

        expires_at = datetime.now(timezone.utc) + timedelta(days=grant_duration_days)

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Update request status
                await conn.execute(
                    """UPDATE access_request
                       SET status = 'approved', reviewed_by = $1,
                           reviewed_at = NOW(), review_notes = $2, updated_at = NOW()
                       WHERE request_id = $3""",
                    user.user_id, notes, request_id,
                )

                # Create access grant
                grant = await conn.fetchrow(
                    """INSERT INTO access_grant
                       (request_id, asset_id, organization_id, scope,
                        granted_by, expires_at)
                       VALUES ($1, $2, $3, $4, $5, $6)
                       RETURNING grant_id""",
                    request_id, request["asset_id"],
                    request["requesting_org_id"],
                    scope or request.get("scope", {}),
                    user.user_id, expires_at,
                )

                # Queue OpenFGA tuple sync via transactional outbox.
                if request["asset_type"] == "clinical_trial":
                    trial_id = str(request["reference_id"])
                    org_id = request["requesting_org_id"]
                    await enqueue_write_tuples(
                        conn,
                        [{
                            "user": f"organization:{org_id}",
                            "relation": "granted_org",
                            "object": f"clinical_trial:{trial_id}",
                        }],
                        source="access_request.approve",
                        correlation_id=str(grant["grant_id"]),
                    )

        # Audit log
        await self._audit_log(
            action="access_approved",
            actor_id=user.user_id,
            actor_role=user.role,
            target_type="access_request",
            target_id=str(request_id),
            details={
                "grant_id": str(grant["grant_id"]),
                "organization_id": request["requesting_org_id"],
                "trial_id": str(request["reference_id"]),
            },
        )

        return {"grant_id": grant["grant_id"], "status": "approved"}

    # ─── Domain Owner: Reject Request ─────────────────────────

    async def reject_request(
        self,
        user: UserContext,
        request_id: UUID,
        reason: str,
    ) -> dict:
        """Domain owner rejects an access request."""
        if user.role != "domain_owner":
            raise PermissionError("Only domain owners can reject access requests")

        await self.db.execute(
            """UPDATE access_request
               SET status = 'rejected', reviewed_by = $1,
                   reviewed_at = NOW(), review_notes = $2, updated_at = NOW()
               WHERE request_id = $3 AND status = 'pending'""",
            user.user_id, reason, request_id,
        )

        await self._audit_log(
            action="access_rejected",
            actor_id=user.user_id,
            actor_role=user.role,
            target_type="access_request",
            target_id=str(request_id),
            details={"reason": reason},
        )

        return {"request_id": str(request_id), "status": "rejected"}

    # ─── Revocation ───────────────────────────────────────────

    async def revoke_grant(
        self,
        user: UserContext,
        grant_id: UUID,
        reason: str,
    ) -> dict:
        """Domain owner revokes an active access grant → removes OpenFGA tuples."""
        async with self.db.acquire() as conn:
            async with conn.transaction():
                grant = await conn.fetchrow(
                    """SELECT ag.*, da.reference_id, da.asset_type
                       FROM access_grant ag
                       JOIN data_asset da ON ag.asset_id = da.asset_id
                       WHERE ag.grant_id = $1 AND ag.revoked_at IS NULL""",
                    grant_id,
                )
                if not grant:
                    raise ValueError(f"Grant {grant_id} not found or already revoked")

                await conn.execute(
                    """UPDATE access_grant
                       SET revoked_at = NOW(), revoked_by = $1, revoke_reason = $2
                       WHERE grant_id = $3""",
                    user.user_id,
                    reason,
                    grant_id,
                )

                # Only remove the OpenFGA tuple if no other active grant exists
                # for the same org/trial combination.
                if grant["asset_type"] == "clinical_trial":
                    still_active = await conn.fetchval(
                        """SELECT COUNT(*) FROM access_grant ag2
                           JOIN data_asset da2 ON ag2.asset_id = da2.asset_id
                           WHERE ag2.organization_id = $1
                           AND da2.reference_id = $2
                           AND ag2.revoked_at IS NULL
                           AND ag2.expires_at > NOW()
                           AND ag2.grant_id != $3""",
                        grant["organization_id"],
                        grant["reference_id"],
                        grant_id,
                    )
                    if still_active == 0:
                        await enqueue_delete_tuples(
                            conn,
                            [{
                                "user": f"organization:{grant['organization_id']}",
                                "relation": "granted_org",
                                "object": f"clinical_trial:{grant['reference_id']}",
                            }],
                            source="access_request.revoke",
                            correlation_id=str(grant_id),
                        )

        await self._audit_log(
            action="access_revoked",
            actor_id=user.user_id,
            actor_role=user.role,
            target_type="access_grant",
            target_id=str(grant_id),
            details={"reason": reason, "organization_id": grant["organization_id"]},
        )

        return {"grant_id": str(grant_id), "status": "revoked"}

    # ─── Helpers ──────────────────────────────────────────────

    async def _audit_log(self, **kwargs):
        """Write to auth audit log."""
        try:
            await self.db.execute(
                """INSERT INTO auth_audit_log
                   (action, actor_id, actor_role, target_type, target_id, details)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                kwargs.get("action"),
                kwargs.get("actor_id"),
                kwargs.get("actor_role"),
                kwargs.get("target_type"),
                kwargs.get("target_id"),
                kwargs.get("details", {}),
            )
        except Exception as e:
            logger.error(f"Audit log write failed: {e}")