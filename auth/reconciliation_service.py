"""
Reconciliation Service.

Ensures OpenFGA tuples match PostgreSQL state.
Fixes drift caused by partial failures during assignment/revocation.
"""

import logging
from typing import Optional

import asyncpg

from auth.openfga.condition_payload import (
    build_condition_context_from_scope,
    build_delegation_context_from_ceiling,
)
from auth.openfga_client import OpenFGAClient, get_openfga_client

logger = logging.getLogger(__name__)


class ReconciliationService:

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        fga_client: Optional[OpenFGAClient] = None,
    ):
        self.db = db_pool
        self.fga = fga_client or get_openfga_client()

    async def reconcile_all(self) -> dict:
        """Run full reconciliation. Returns summary of fixes applied."""
        results = {
            "org_grants": await self._reconcile_org_grants(),
            "researcher_assignments": await self._reconcile_researcher_assignments(),
            "cohort_assignments": await self._reconcile_cohort_assignments(),
        }
        return results

    async def _load_trial_ceiling_context(
        self,
        organization_id: str,
        trial_id,
    ) -> Optional[dict]:
        row = await self.db.fetchrow(
            """
            SELECT ag.scope, ag.expires_at
            FROM access_grant ag
            JOIN data_asset da ON ag.asset_id = da.asset_id
            WHERE ag.organization_id = $1
              AND da.reference_id = $2
              AND ag.revoked_at IS NULL
              AND ag.expires_at > NOW()
            ORDER BY ag.expires_at DESC
            LIMIT 1
            """,
            organization_id,
            trial_id,
        )
        if not row:
            return None

        return build_condition_context_from_scope(
            row["scope"] or {},
            valid_until=row["expires_at"],
        )

    async def _load_cohort_ceiling_context(
        self,
        organization_id: str,
        cohort_id,
    ) -> Optional[dict]:
        row = await self.db.fetchrow(
            """
            SELECT ag.scope, ag.expires_at
            FROM cohort_trial ct
            JOIN data_asset da ON da.reference_id = ct.trial_id
            JOIN access_grant ag ON ag.asset_id = da.asset_id
            WHERE ct.cohort_id = $1
              AND ag.organization_id = $2
              AND ag.revoked_at IS NULL
              AND ag.expires_at > NOW()
            ORDER BY ag.expires_at DESC
            LIMIT 1
            """,
            cohort_id,
            organization_id,
        )
        if not row:
            return None

        return build_condition_context_from_scope(
            row["scope"] or {},
            valid_until=row["expires_at"],
        )

    async def _reconcile_org_grants(self) -> dict:
        """Ensure every active access_grant has an OpenFGA tuple."""
        added = 0
        removed = 0

        # Find active grants that should have tuples
        active_grants = await self.db.fetch(
             """SELECT DISTINCT ON (ag.organization_id, da.reference_id)
                 ag.organization_id,
                 da.reference_id as trial_id,
                 ag.scope,
                 ag.expires_at
               FROM access_grant ag
               JOIN data_asset da ON ag.asset_id = da.asset_id
             WHERE ag.revoked_at IS NULL
             AND ag.expires_at > NOW()
             ORDER BY ag.organization_id, da.reference_id, ag.expires_at DESC"""
        )

        for grant in active_grants:
            exists = await self.fga.tuple_exists(
                user=f"organization:{grant['organization_id']}",
                relation="granted_org",
                object=f"clinical_trial:{grant['trial_id']}",
            )
            if not exists:
                logger.warning(
                    f"RECONCILE: Missing tuple org:{grant['organization_id']} "
                    f"→ granted_org → trial:{grant['trial_id']}"
                )
                success = await self.fga.write_conditional_tuples([{
                    "user": f"organization:{grant['organization_id']}",
                    "relation": "granted_org",
                    "object": f"clinical_trial:{grant['trial_id']}",
                    "condition_name": "check_fine_grained_access",
                    "condition_context": build_condition_context_from_scope(
                        grant["scope"] or {},
                        valid_until=grant["expires_at"],
                    ),
                }])
                if success:
                    added += 1

        # Find revoked/expired grants that might still have tuples
        revoked_grants = await self.db.fetch(
            """SELECT DISTINCT ag.organization_id, da.reference_id as trial_id
               FROM access_grant ag
               JOIN data_asset da ON ag.asset_id = da.asset_id
               WHERE (ag.revoked_at IS NOT NULL OR ag.expires_at <= NOW())
               AND NOT EXISTS (
                   SELECT 1 FROM access_grant ag2
                   JOIN data_asset da2 ON ag2.asset_id = da2.asset_id
                   WHERE da2.reference_id = da.reference_id
                   AND ag2.organization_id = ag.organization_id
                   AND ag2.revoked_at IS NULL
                   AND ag2.expires_at > NOW()
               )"""
        )

        for grant in revoked_grants:
            exists = await self.fga.tuple_exists(
                user=f"organization:{grant['organization_id']}",
                relation="granted_org",
                object=f"clinical_trial:{grant['trial_id']}",
            )
            if exists:
                logger.warning(
                    f"RECONCILE: Stale tuple org:{grant['organization_id']} "
                    f"→ granted_org → trial:{grant['trial_id']}"
                )
                success = await self.fga.delete_tuples([{
                    "user": f"organization:{grant['organization_id']}",
                    "relation": "granted_org",
                    "object": f"clinical_trial:{grant['trial_id']}",
                }])
                if success:
                    removed += 1

        return {"tuples_added": added, "stale_tuples_removed": removed}

    async def _reconcile_cohort_assignments(self) -> dict:
        """Ensure active cohort assignments have OpenFGA cohort tuples."""
        added = 0
        removed = 0

        active = await self.db.fetch(
            """SELECT DISTINCT researcher_id, organization_id, cohort_id, expires_at
               FROM researcher_assignment
               WHERE cohort_id IS NOT NULL
               AND revoked_at IS NULL
               AND expires_at > NOW()"""
        )

        for a in active:
            exists = await self.fga.tuple_exists(
                user=f"user:{a['researcher_id']}",
                relation="assigned_researcher",
                object=f"cohort:{a['cohort_id']}",
            )
            if not exists:
                ceiling_context = await self._load_cohort_ceiling_context(
                    a["organization_id"],
                    a["cohort_id"],
                )
                if not ceiling_context:
                    logger.warning(
                        "RECONCILE: Skipping cohort tuple rebuild without active org ceiling: "
                        "user:%s cohort:%s org:%s",
                        a["researcher_id"],
                        a["cohort_id"],
                        a["organization_id"],
                    )
                    continue
                success = await self.fga.write_conditional_tuples([{
                    "user": f"user:{a['researcher_id']}",
                    "relation": "assigned_researcher",
                    "object": f"cohort:{a['cohort_id']}",
                    "condition_name": "check_fine_grained_access",
                    "condition_context": build_delegation_context_from_ceiling(
                        ceiling_context,
                        delegated_valid_until=a["expires_at"],
                    ),
                }])
                if success:
                    added += 1

        stale = await self.db.fetch(
            """SELECT DISTINCT ra.researcher_id, ra.cohort_id
               FROM researcher_assignment ra
               WHERE ra.cohort_id IS NOT NULL
               AND (ra.revoked_at IS NOT NULL OR ra.expires_at <= NOW())
               AND NOT EXISTS (
                   SELECT 1 FROM researcher_assignment ra2
                   WHERE ra2.researcher_id = ra.researcher_id
                   AND ra2.cohort_id = ra.cohort_id
                   AND ra2.revoked_at IS NULL
                   AND ra2.expires_at > NOW()
               )"""
        )

        for a in stale:
            exists = await self.fga.tuple_exists(
                user=f"user:{a['researcher_id']}",
                relation="assigned_researcher",
                object=f"cohort:{a['cohort_id']}",
            )
            if exists:
                success = await self.fga.delete_tuples([{
                    "user": f"user:{a['researcher_id']}",
                    "relation": "assigned_researcher",
                    "object": f"cohort:{a['cohort_id']}",
                }])
                if success:
                    removed += 1

        return {"tuples_added": added, "stale_tuples_removed": removed}

    async def _reconcile_researcher_assignments(self) -> dict:
        """Ensure active individual assignments have OpenFGA tuples."""
        added = 0
        removed = 0

        # ── Active individual assignments → should have tuples ─

        # Direct trial assignments
        direct_assigns = await self.db.fetch(
            """SELECT researcher_id, organization_id, trial_id, expires_at
               FROM researcher_assignment
               WHERE access_level = 'individual'
               AND trial_id IS NOT NULL
               AND revoked_at IS NULL
               AND expires_at > NOW()"""
        )

        for a in direct_assigns:
            exists = await self.fga.tuple_exists(
                user=f"user:{a['researcher_id']}",
                relation="assigned_researcher",
                object=f"clinical_trial:{a['trial_id']}",
            )
            if not exists:
                logger.warning(
                    f"RECONCILE: Missing tuple user:{a['researcher_id']} "
                    f"→ assigned_researcher → trial:{a['trial_id']}"
                )
                ceiling_context = await self._load_trial_ceiling_context(
                    a["organization_id"],
                    a["trial_id"],
                )
                if not ceiling_context:
                    logger.warning(
                        "RECONCILE: Skipping direct assignment rebuild without active org ceiling: "
                        "user:%s trial:%s org:%s",
                        a["researcher_id"],
                        a["trial_id"],
                        a["organization_id"],
                    )
                    continue
                success = await self.fga.write_conditional_tuples([{
                    "user": f"user:{a['researcher_id']}",
                    "relation": "assigned_researcher",
                    "object": f"clinical_trial:{a['trial_id']}",
                    "condition_name": "check_fine_grained_access",
                    "condition_context": build_delegation_context_from_ceiling(
                        ceiling_context,
                        delegated_valid_until=a["expires_at"],
                    ),
                }])
                if success:
                    added += 1

        # Cohort assignments (expand to per-trial)
        cohort_assigns = await self.db.fetch(
            """SELECT ra.researcher_id, ra.organization_id, ra.expires_at, ct.trial_id
               FROM researcher_assignment ra
               JOIN cohort_trial ct ON ra.cohort_id = ct.cohort_id
               WHERE ra.access_level = 'individual'
               AND ra.cohort_id IS NOT NULL
               AND ra.revoked_at IS NULL
               AND ra.expires_at > NOW()"""
        )

        for a in cohort_assigns:
            exists = await self.fga.tuple_exists(
                user=f"user:{a['researcher_id']}",
                relation="assigned_researcher",
                object=f"clinical_trial:{a['trial_id']}",
            )
            if not exists:
                logger.warning(
                    f"RECONCILE: Missing cohort tuple user:{a['researcher_id']} "
                    f"→ assigned_researcher → trial:{a['trial_id']}"
                )
                ceiling_context = await self._load_trial_ceiling_context(
                    a["organization_id"],
                    a["trial_id"],
                )
                if not ceiling_context:
                    logger.warning(
                        "RECONCILE: Skipping cohort trial rebuild without active org ceiling: "
                        "user:%s trial:%s org:%s",
                        a["researcher_id"],
                        a["trial_id"],
                        a["organization_id"],
                    )
                    continue
                success = await self.fga.write_conditional_tuples([{
                    "user": f"user:{a['researcher_id']}",
                    "relation": "assigned_researcher",
                    "object": f"clinical_trial:{a['trial_id']}",
                    "condition_name": "check_fine_grained_access",
                    "condition_context": build_delegation_context_from_ceiling(
                        ceiling_context,
                        delegated_valid_until=a["expires_at"],
                    ),
                }])
                if success:
                    added += 1

        # ── Revoked/expired assignments → should NOT have tuples ─
        # Only remove if no OTHER active assignment covers the same trial

        revoked = await self.db.fetch(
            """SELECT DISTINCT ra.researcher_id, 
                   COALESCE(ra.trial_id, ct.trial_id) as trial_id
               FROM researcher_assignment ra
               LEFT JOIN cohort_trial ct ON ra.cohort_id = ct.cohort_id
               WHERE ra.access_level = 'individual'
               AND (ra.revoked_at IS NOT NULL OR ra.expires_at <= NOW())
               AND COALESCE(ra.trial_id, ct.trial_id) IS NOT NULL"""
        )

        for a in revoked:
            # Check if another active assignment covers this trial
            still_active = await self.db.fetchval(
                """SELECT COUNT(*) FROM researcher_assignment ra
                   LEFT JOIN cohort_trial ct ON ra.cohort_id = ct.cohort_id
                   WHERE ra.researcher_id = $1
                   AND ra.access_level = 'individual'
                   AND ra.revoked_at IS NULL
                   AND ra.expires_at > NOW()
                   AND (ra.trial_id = $2 OR ct.trial_id = $2)""",
                a["researcher_id"], a["trial_id"],
            )

            if still_active == 0:
                exists = await self.fga.tuple_exists(
                    user=f"user:{a['researcher_id']}",
                    relation="assigned_researcher",
                    object=f"clinical_trial:{a['trial_id']}",
                )
                if exists:
                    logger.warning(
                        f"RECONCILE: Stale tuple user:{a['researcher_id']} "
                        f"→ assigned_researcher → trial:{a['trial_id']}"
                    )
                    success = await self.fga.delete_tuples([{
                        "user": f"user:{a['researcher_id']}",
                        "relation": "assigned_researcher",
                        "object": f"clinical_trial:{a['trial_id']}",
                    }])
                    if success:
                        removed += 1

        return {"tuples_added": added, "stale_tuples_removed": removed}