"""
Asset Publishing Service.

Domain owners publish collections of trials using filter criteria.
Each trial remains the atomic authorization unit in OpenFGA.
Collections are the marketplace entity managers browse and request.

Dynamic collections auto-include new trials matching the filter on ingestion.
When a dynamic collection has active grants, new matching trials automatically
get OpenFGA tuples for all granted organizations.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import asyncpg

from auth.middleware import UserContext
from auth.openfga_client import OpenFGAClient, get_openfga_client
from auth.openfga_outbox import enqueue_delete_tuples, enqueue_write_tuples
from auth.openfga.condition_payload import (
    build_condition_context_from_scope,
    build_delegation_context_from_ceiling,
)

logger = logging.getLogger(__name__)


def _as_non_empty_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _merge_scope_with_collection_filter(
    scope: Optional[dict[str, Any]],
    filter_criteria: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Enforce collection-level constraints onto the grant scope (fail-closed narrowing)."""
    merged: dict[str, Any] = dict(scope or {})
    criteria = filter_criteria or {}

    areas = _as_non_empty_list(criteria.get("therapeutic_areas"))
    regions = _as_non_empty_list(criteria.get("regions"))
    phases = _as_non_empty_list(criteria.get("phases"))

    if areas:
        merged["permitted_areas"] = areas
    if regions:
        merged["permitted_regions"] = regions
    if phases:
        merged["permitted_phases"] = phases

    return merged


class AssetService:

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        fga_client: Optional[OpenFGAClient] = None,
    ):
        self.db = db_pool
        self.fga = fga_client or get_openfga_client()

    # ═══════════════════════════════════════════════════════════
    # FILTER-BASED TRIAL DISCOVERY
    # ═══════════════════════════════════════════════════════════

    def _build_trial_query(self, filters: dict) -> tuple[str, list]:
        """Build a WHERE clause from filter criteria. Returns (where_sql, params)."""
        conditions = ["1=1"]
        params: list = []
        idx = 1

        if filters.get("therapeutic_areas"):
            conditions.append(f"ct.therapeutic_area = ANY(${idx}::text[])")
            params.append(filters["therapeutic_areas"])
            idx += 1

        if filters.get("phases"):
            conditions.append(f"ct.phase = ANY(${idx}::text[])")
            params.append(filters["phases"])
            idx += 1

        if filters.get("study_types"):
            conditions.append(f"ct.study_type = ANY(${idx}::text[])")
            params.append(filters["study_types"])
            idx += 1

        if filters.get("regions"):
            # Array overlap: trial.regions && ['US', 'EU']
            conditions.append(f"ct.regions && ${idx}::text[]")
            params.append(filters["regions"])
            idx += 1

        if filters.get("countries"):
            conditions.append(f"ct.countries && ${idx}::text[]")
            params.append(filters["countries"])
            idx += 1

        if filters.get("overall_statuses"):
            conditions.append(f"ct.overall_status = ANY(${idx}::text[])")
            params.append(filters["overall_statuses"])
            idx += 1

        if filters.get("min_enrollment") is not None:
            conditions.append(f"ct.enrollment_count >= ${idx}")
            params.append(filters["min_enrollment"])
            idx += 1

        if filters.get("lead_sponsors"):
            conditions.append(f"ct.lead_sponsor = ANY(${idx}::text[])")
            params.append(filters["lead_sponsors"])
            idx += 1

        return " AND ".join(conditions), params

    def _build_patient_count_subquery(
        self,
        filters: dict,
        trial_ref: str,
        start_idx: int = 1,
    ) -> tuple[str, list, int]:
        """Build a correlated subquery that counts only patients visible to the filter."""
        conditions = [f"pte.trial_id = {trial_ref}"]
        params: list = []
        idx = start_idx

        if filters.get("regions"):
            conditions.append(f"p.region = ANY(${idx}::text[])")
            params.append(filters["regions"])
            idx += 1

        if filters.get("countries"):
            conditions.append(f"p.country = ANY(${idx}::text[])")
            params.append(filters["countries"])
            idx += 1

        sql = f"""
            SELECT COUNT(DISTINCT pte.patient_id)
            FROM patient_trial_enrollment pte
            JOIN patient p ON p.patient_id = pte.patient_id
            WHERE {' AND '.join(conditions)}
        """
        return sql, params, idx

    async def discover_trials(self, filters: dict) -> list[dict]:
        """
        Find trials matching filter criteria.
        Returns full metadata for preview before publishing.
        """
        where_clause, params = self._build_trial_query(filters)
        patient_count_sql, patient_count_params, _ = self._build_patient_count_subquery(
            filters,
            "ct.trial_id",
            start_idx=len(params) + 1,
        )

        rows = await self.db.fetch(
            f"""
            SELECT
                ct.trial_id, ct.nct_id, ct.title, ct.phase,
                ct.therapeutic_area, ct.overall_status, ct.study_type,
                ct.enrollment_count, ct.lead_sponsor,
                ct.regions, ct.countries,
                ct.start_date, ct.completion_date,
                EXISTS(
                    SELECT 1 FROM data_asset da
                    WHERE da.reference_id = ct.trial_id
                    AND da.asset_type = 'clinical_trial'
                ) AS already_published,
                ({patient_count_sql}) AS patient_count,
                (SELECT array_agg(DISTINCT i.name)
                 FROM intervention i
                 WHERE i.trial_id = ct.trial_id) AS drug_names,
                (SELECT array_agg(DISTINCT pc.condition_name)
                 FROM patient_condition pc
                 JOIN patient_trial_enrollment pte2 ON pc.patient_id = pte2.patient_id
                 WHERE pte2.trial_id = ct.trial_id) AS condition_names
            FROM clinical_trial ct
            WHERE {where_clause}
            ORDER BY ct.therapeutic_area, ct.phase, ct.title
            """,
            *params,
            *patient_count_params,
        )

        return [dict(r) for r in rows]

    async def get_filter_options(self) -> dict:
        """Get all available filter values from existing trial data."""
        results = {}

        for col in ["therapeutic_area", "phase", "study_type", "overall_status", "lead_sponsor"]:
            rows = await self.db.fetch(
                f"SELECT DISTINCT {col} as val FROM clinical_trial WHERE {col} IS NOT NULL ORDER BY 1"
            )
            results[f"{col}s" if not col.endswith("s") else col] = [r["val"] for r in rows]

        # Array columns need unnest
        for col in ["regions", "countries"]:
            rows = await self.db.fetch(
                f"SELECT DISTINCT unnest({col}) as val FROM clinical_trial WHERE {col} IS NOT NULL ORDER BY 1"
            )
            results[col] = [r["val"] for r in rows]

        # Stats
        total = await self.db.fetchval("SELECT COUNT(*) FROM clinical_trial")
        published = await self.db.fetchval(
            "SELECT COUNT(DISTINCT reference_id) FROM data_asset WHERE asset_type = 'clinical_trial' AND is_active = TRUE"
        )

        results["stats"] = {
            "total_trials": total,
            "published_trials": published,
            "unpublished_trials": total - published,
        }

        return results

    # ═══════════════════════════════════════════════════════════
    # COLLECTION PUBLISHING
    # ═══════════════════════════════════════════════════════════

    async def publish_collection(
        self,
        user: UserContext,
        name: str,
        description: str,
        filter_criteria: dict,
        sensitivity_level: str = "standard",
        is_dynamic: bool = True,
    ) -> dict:
        """
        Publish a filter-based collection of trials.

        1. Evaluates filter → finds matching trials
        2. Creates data_asset per trial (idempotent)
        3. Creates collection + links
        4. Writes OpenFGA ownership tuples per trial
        5. Computes denormalized summary metadata

        Dynamic collections will auto-include new matching trials on ingestion.
        """
        # Discover matching trials
        all_trials = await self.discover_trials(filter_criteria)
        if not all_trials:
            raise ValueError("No trials match the specified filter criteria")

        unpublished = [t for t in all_trials if not t["already_published"]]
        already_published = [t for t in all_trials if t["already_published"]]

        # Compute summary metadata
        all_therapeutic_areas = list({t["therapeutic_area"] for t in all_trials if t["therapeutic_area"]})
        all_phases = list({t["phase"] for t in all_trials if t["phase"]})
        all_study_types = list({t["study_type"] for t in all_trials if t["study_type"]})
        all_regions = list({r for t in all_trials for r in (t["regions"] or [])})
        all_countries = list({c for t in all_trials for c in (t["countries"] or [])})
        total_patients = sum(t["patient_count"] or 0 for t in all_trials)
        total_enrollment = sum(t["enrollment_count"] or 0 for t in all_trials)

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # 1. Create collection
                collection = await conn.fetchrow(
                    """INSERT INTO data_asset_collection
                       (name, description, owner_id, filter_criteria,
                        sensitivity_level, is_dynamic,
                        trial_count, total_patients, total_enrollment,
                        therapeutic_areas, phases, study_types, regions, countries)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                       RETURNING collection_id""",
                    name, description, user.user_id, filter_criteria,
                    sensitivity_level, is_dynamic,
                    len(all_trials), total_patients, total_enrollment,
                    all_therapeutic_areas, all_phases, all_study_types,
                    all_regions, all_countries,
                )
                collection_id = collection["collection_id"]

                # 2. Create data_asset per trial + link to collection
                fga_tuples = []
                published_count = 0

                for trial in all_trials:
                    # Upsert data_asset (handles already-published trials)
                    asset = await conn.fetchrow(
                        """INSERT INTO data_asset
                           (asset_type, reference_id, owner_id, title, description,
                            sensitivity_level, therapeutic_area)
                           VALUES ('clinical_trial', $1, $2, $3, $4, $5, $6)
                           ON CONFLICT (reference_id, asset_type)
                           DO UPDATE SET updated_at = NOW()
                           RETURNING asset_id""",
                        trial["trial_id"], user.user_id,
                        trial["title"] or trial["nct_id"],
                        description,
                        sensitivity_level,
                        trial["therapeutic_area"],
                    )

                    # Link to collection
                    await conn.execute(
                        """INSERT INTO collection_asset (collection_id, asset_id, trial_id)
                           VALUES ($1, $2, $3)
                           ON CONFLICT DO NOTHING""",
                        collection_id, asset["asset_id"], trial["trial_id"],
                    )

                    # OpenFGA ownership (only for newly published)
                    if not trial["already_published"]:
                        fga_tuples.append({
                            "user": f"user:{user.username}",
                            "relation": "owner",
                            "object": f"clinical_trial:{trial['trial_id']}",
                        })
                        published_count += 1

                await enqueue_write_tuples(
                    conn,
                    fga_tuples,
                    source="asset.publish_collection",
                    correlation_id=str(collection_id),
                )

        # 4. Audit
        await self._audit(
            action="collection_published",
            actor_id=user.user_id,
            actor_role=user.role,
            target_type="collection",
            target_id=str(collection_id),
            details={
                "name": name,
                "total_trials": len(all_trials),
                "newly_published": published_count,
                "already_published": len(already_published),
                "is_dynamic": is_dynamic,
                "filter_criteria": filter_criteria,
            },
        )

        return {
            "collection_id": str(collection_id),
            "total_trials": len(all_trials),
            "newly_published": published_count,
            "already_published": len(already_published),
            "summary": {
                "therapeutic_areas": all_therapeutic_areas,
                "phases": all_phases,
                "total_patients": total_patients,
                "total_enrollment": total_enrollment,
                "regions": all_regions,
            },
            "status": "published",
        }

    # ═══════════════════════════════════════════════════════════
    # DYNAMIC COLLECTION REFRESH
    # ═══════════════════════════════════════════════════════════

    async def refresh_dynamic_collections(
        self, owner_id: Optional[str] = None
    ) -> list[dict]:
        """
        Re-evaluate all dynamic collections and add newly matching trials.
        If a collection has active grants, auto-write OpenFGA tuples for new trials.

        Called:
        - By processor after ingesting a new trial
        - By domain owner manually via API
        - By a scheduled job (future)
        """
        query = """
            SELECT collection_id, owner_id, filter_criteria, sensitivity_level
            FROM data_asset_collection
            WHERE is_dynamic = TRUE AND is_active = TRUE
        """
        params = []
        if owner_id:
            query += " AND owner_id = $1"
            params.append(owner_id)

        collections = await self.db.fetch(query, *params)
        results = []

        for coll in collections:
            coll_id = coll["collection_id"]
            filters = coll["filter_criteria"]

            # Find matching trials
            all_trials = await self.discover_trials(filters)
            trial_ids = {t["trial_id"] for t in all_trials}

            # Find which are already linked
            existing = await self.db.fetch(
                "SELECT trial_id FROM collection_asset WHERE collection_id = $1",
                coll_id,
            )
            existing_ids = {r["trial_id"] for r in existing}

            new_trial_ids = trial_ids - existing_ids
            if not new_trial_ids:
                continue

            new_trials = [t for t in all_trials if t["trial_id"] in new_trial_ids]

            # Publish new trials + link
            fga_owner_tuples = []
            fga_grant_tuples = []

            async with self.db.acquire() as conn:
                async with conn.transaction():
                    for trial in new_trials:
                        asset = await conn.fetchrow(
                            """INSERT INTO data_asset
                               (asset_type, reference_id, owner_id, title, description,
                                sensitivity_level, therapeutic_area)
                               VALUES ('clinical_trial', $1, $2, $3, NULL, $4, $5)
                               ON CONFLICT (reference_id, asset_type)
                               DO UPDATE SET updated_at = NOW()
                               RETURNING asset_id""",
                            trial["trial_id"], coll["owner_id"],
                            trial["title"] or trial.get("nct_id", ""),
                            coll["sensitivity_level"],
                            trial["therapeutic_area"],
                        )

                        await conn.execute(
                            """INSERT INTO collection_asset (collection_id, asset_id, trial_id)
                               VALUES ($1, $2, $3) ON CONFLICT DO NOTHING""",
                            coll_id, asset["asset_id"], trial["trial_id"],
                        )

                        fga_owner_tuples.append({
                            "user": f"user:{coll['owner_id']}",
                            "relation": "owner",
                            "object": f"clinical_trial:{trial['trial_id']}",
                        })

                        # Auto-grant: find orgs with active grants on this collection
                        active_grants = await conn.fetch(
                            """SELECT DISTINCT ON (ag.organization_id)
                                      ag.organization_id, ag.scope, ag.expires_at, ag.granted_by
                               FROM access_grant ag
                               JOIN collection_asset ca ON ag.asset_id = ca.asset_id
                               WHERE ca.collection_id = $1 AND ag.is_active = TRUE
                               ORDER BY ag.organization_id, ag.expires_at DESC""",
                            coll_id,
                        )

                        for grant in active_grants:
                            # Create grant for the new trial
                            await conn.execute(
                                """INSERT INTO access_grant
                                   (asset_id, collection_id, organization_id, scope,
                                    granted_by, expires_at)
                                   VALUES ($1, $2, $3, $4, $5, $6)
                                   ON CONFLICT DO NOTHING""",
                                asset["asset_id"], coll_id,
                                grant["organization_id"],
                                grant["scope"] or {},
                                grant["granted_by"],
                                grant["expires_at"],
                            )

                            org_condition_context = build_condition_context_from_scope(
                                grant["scope"] or {},
                                valid_until=grant["expires_at"],
                            )

                            fga_grant_tuples.append({
                                "user": f"organization:{grant['organization_id']}",
                                "relation": "granted_org",
                                "object": f"clinical_trial:{trial['trial_id']}",
                                "condition_name": "check_fine_grained_access",
                                "condition_context": org_condition_context,
                            })
                            researcher_tuples = await self._auto_assign_researchers_for_new_trial(
                                conn,
                                trial["trial_id"],
                                coll_id,
                                grant["organization_id"],
                                org_condition_context,
                            )
                            fga_grant_tuples.extend(researcher_tuples)

                    # Update collection summary
                    await self._update_collection_summary(conn, coll_id, filters)

                    all_tuples = fga_owner_tuples + fga_grant_tuples
                    await enqueue_write_tuples(
                        conn,
                        all_tuples,
                        source="asset.refresh_dynamic_collections",
                        correlation_id=str(coll_id),
                    )

            results.append({
                "collection_id": str(coll_id),
                "new_trials_added": len(new_trials),
                "auto_grants_written": len(fga_grant_tuples),
            })

            logger.info(
                f"Dynamic collection {coll_id}: added {len(new_trials)} trials, "
                f"wrote {len(fga_grant_tuples)} auto-grants"
            )

        return results
    async def _auto_assign_researchers_for_new_trial(
        self,
        conn: asyncpg.Connection,
        trial_id: UUID,
        collection_id: UUID,
        organization_id: str,
        ceiling_context: dict,
    ) -> list[dict]:
        """
        When a new trial is added to a dynamic collection, check if any
        researchers have active cohort assignments covering this collection.
        If so, write OpenFGA tuples for them.
        """
        fga_tuples = []

        # Find cohorts that include trials from this collection
        # AND have active researcher assignments with individual access
        researchers = await conn.fetch(
            """
            SELECT DISTINCT ra.researcher_id, ra.access_level, ra.expires_at
            FROM researcher_assignment ra
            JOIN cohort_trial ct_link ON ra.cohort_id = ct_link.cohort_id
            JOIN collection_asset ca ON ct_link.trial_id = ca.trial_id
            WHERE ca.collection_id = $1
            AND ra.organization_id = $2
            AND ra.access_level = 'individual'
            AND ra.revoked_at IS NULL
            AND ra.expires_at > NOW()
            """,
            collection_id,
            organization_id,
        )

        for r in researchers:
            fga_tuples.append({
                "user": f"user:{r['researcher_id']}",
                "relation": "assigned_researcher",
                "object": f"clinical_trial:{trial_id}",
                "condition_name": "check_fine_grained_access",
                "condition_context": build_delegation_context_from_ceiling(
                    ceiling_context,
                    delegated_valid_until=r["expires_at"],
                ),
            })

        return fga_tuples
    # ═══════════════════════════════════════════════════════════
    # GRANT MANAGEMENT
    # ═══════════════════════════════════════════════════════════

    async def grant_collection_access(
        self,
        collection_id: UUID,
        org_id: str,
        granted_by: str,
        request_id: UUID,
        expires_at: datetime,
        scope: Optional[dict] = None,
    ) -> dict:
        """
        Grant an organization access to all trials in a collection.
        Creates per-trial access_grant + OpenFGA tuples.
        """
        async with self.db.acquire() as conn:
            async with conn.transaction():
                collection = await conn.fetchrow(
                    "SELECT filter_criteria FROM data_asset_collection WHERE collection_id = $1",
                    collection_id,
                )
                if not collection:
                    raise ValueError("Collection not found")

                effective_scope = _merge_scope_with_collection_filter(
                    scope,
                    collection["filter_criteria"],
                )

                assets = await conn.fetch(
                    """SELECT da.asset_id, da.reference_id as trial_id
                       FROM data_asset da
                       JOIN collection_asset ca ON da.asset_id = ca.asset_id
                       WHERE ca.collection_id = $1""",
                    collection_id,
                )

                if not assets:
                    raise ValueError("Collection has no assets")

                grant_ids = []
                fga_tuples = []

                for asset in assets:
                    # Idempotent: reuse existing active grant, but ensure collection_id
                    # and scope are set so revocation can find it later.
                    existing = await conn.fetchrow(
                        """SELECT grant_id FROM access_grant
                           WHERE asset_id = $1 AND organization_id = $2
                           AND revoked_at IS NULL AND expires_at > NOW()""",
                        asset["asset_id"],
                        org_id,
                    )
                    if existing:
                        await conn.execute(
                            """UPDATE access_grant
                               SET collection_id = COALESCE(collection_id, $1),
                                   scope = CASE WHEN scope = '{}'::jsonb AND $2::jsonb != '{}'::jsonb
                                               THEN $2 ELSE scope END
                               WHERE grant_id = $3""",
                            collection_id,
                            effective_scope,
                            existing["grant_id"],
                        )
                        grant_ids.append(existing["grant_id"])
                        continue

                    row = await conn.fetchrow(
                        """INSERT INTO access_grant
                           (request_id, asset_id, collection_id, organization_id,
                            granted_by, expires_at, scope)
                           VALUES ($1, $2, $3, $4, $5, $6, $7)
                           RETURNING grant_id""",
                        request_id,
                        asset["asset_id"],
                        collection_id,
                        org_id,
                        granted_by,
                        expires_at,
                        effective_scope,
                    )
                    grant_ids.append(row["grant_id"])

                    condition_context = build_condition_context_from_scope(
                        effective_scope,
                        valid_until=expires_at,
                    )

                    fga_tuples.append({
                        "user": f"organization:{org_id}",
                        "relation": "granted_org",
                        "object": f"clinical_trial:{asset['trial_id']}",
                        "condition_name": "check_fine_grained_access",
                        "condition_context": condition_context,
                    })

                await enqueue_write_tuples(
                    conn,
                    fga_tuples,
                    source="asset.grant_collection_access",
                    correlation_id=str(collection_id),
                )

        return {
            "grants_created": len(fga_tuples),
            "grants_existing": len(grant_ids) - len(fga_tuples),
            "total_trials": len(assets),
        }

    async def revoke_collection_access(
        self,
        collection_id: UUID,
        org_id: str,
        revoked_by: str,
        reason: str,
    ) -> dict:
        """Revoke all grants for an org on a collection + cascade researcher assignments."""
        async with self.db.acquire() as conn:
            # Ownership check — only the collection owner may revoke grants
            collection = await conn.fetchrow(
                "SELECT owner_id FROM data_asset_collection WHERE collection_id = $1",
                collection_id,
            )
            if not collection:
                raise ValueError(f"Collection {collection_id} not found")
            if collection["owner_id"] != revoked_by:
                raise PermissionError("Only the collection owner can revoke grants")

            async with conn.transaction():
                # Use collection_asset JOIN so we find grants regardless of whether
                # collection_id was set on the access_grant row (idempotent-path grants
                # created from pre-existing rows may have collection_id = NULL).
                grants = await conn.fetch(
                    """SELECT DISTINCT ag.grant_id, da.reference_id as trial_id
                       FROM access_grant ag
                       JOIN data_asset da ON ag.asset_id = da.asset_id
                       JOIN collection_asset ca ON ca.asset_id = ag.asset_id
                       WHERE ca.collection_id = $1
                       AND ag.organization_id = $2
                       AND ag.revoked_at IS NULL
                       AND ag.expires_at > NOW()""",
                    collection_id,
                    org_id,
                )

                fga_deletes = []
                researcher_revoke_count = 0

                for g in grants:
                    await conn.execute(
                        """UPDATE access_grant
                           SET revoked_at = NOW(), revoked_by = $1, revoke_reason = $2
                           WHERE grant_id = $3""",
                        revoked_by,
                        reason,
                        g["grant_id"],
                    )
                    fga_deletes.append({
                        "user": f"organization:{org_id}",
                        "relation": "granted_org",
                        "object": f"clinical_trial:{g['trial_id']}",
                    })

                    # Cascade: revoke direct researcher assignments for this
                    # org on this trial and queue FGA tuple deletions.
                    ra_rows = await conn.fetch(
                        """SELECT assignment_id, researcher_id
                           FROM researcher_assignment
                           WHERE organization_id = $1
                           AND trial_id = $2
                           AND revoked_at IS NULL""",
                        org_id,
                        g["trial_id"],
                    )
                    for ra in ra_rows:
                        await conn.execute(
                            """UPDATE researcher_assignment
                               SET revoked_at = NOW(), revoked_by = $1
                               WHERE assignment_id = $2""",
                            revoked_by,
                            ra["assignment_id"],
                        )
                        fga_deletes.append({
                            "user": f"user:{ra['researcher_id']}",
                            "relation": "assigned_researcher",
                            "object": f"clinical_trial:{g['trial_id']}",
                        })
                        researcher_revoke_count += 1

                await enqueue_delete_tuples(
                    conn,
                    fga_deletes,
                    source="asset.revoke_collection_access",
                    correlation_id=str(collection_id),
                )

        return {
            "revoked_count": len(grants),
            "researcher_assignments_revoked": researcher_revoke_count,
        }

    # ═══════════════════════════════════════════════════════════
    # MARKETPLACE VIEW
    # ═══════════════════════════════════════════════════════════

    async def get_marketplace_view(self, user: UserContext) -> dict:
        """
        Marketplace for managers: shows collections with access status.
        Each collection shows its filter criteria, trial count, and whether
        the manager's org has access, a pending request, or no access.
        """
        collections = await self.db.fetch(
            """
            SELECT
                dac.collection_id, dac.name, dac.description,
                dac.sensitivity_level, dac.is_dynamic,
                dac.trial_count, dac.total_patients, dac.total_enrollment,
                dac.therapeutic_areas, dac.phases, dac.study_types,
                dac.regions, dac.countries,
                dac.filter_criteria, dac.created_at,
                -- How many trials in this collection does the org have access to?
                (SELECT COUNT(DISTINCT ca.trial_id)
                 FROM collection_asset ca
                 JOIN access_grant ag ON ag.asset_id = ca.asset_id
                 WHERE ca.collection_id = dac.collection_id
                 AND ag.organization_id = $1
                 AND ag.is_active = TRUE) AS granted_count,
                -- Pending request?
                (SELECT ar.request_id
                 FROM access_request ar
                 WHERE ar.collection_id = dac.collection_id
                 AND ar.requesting_org_id = $1
                 AND ar.status = 'pending'
                 LIMIT 1) AS pending_request_id,
                -- Drug names across all trials in collection
                (SELECT array_agg(DISTINCT i.name)
                 FROM intervention i
                 JOIN collection_asset ca2 ON i.trial_id = ca2.trial_id
                 WHERE ca2.collection_id = dac.collection_id) AS drug_names,
                -- Condition names
                (SELECT array_agg(DISTINCT pc.condition_name)
                 FROM patient_condition pc
                 JOIN patient_trial_enrollment pte ON pc.patient_id = pte.patient_id
                 JOIN collection_asset ca3 ON pte.trial_id = ca3.trial_id
                 WHERE ca3.collection_id = dac.collection_id
                 LIMIT 20) AS condition_names
            FROM data_asset_collection dac
            WHERE dac.is_active = TRUE
            ORDER BY dac.created_at DESC
            """,
            user.organization_id,
        )

        return {
            "collections": [
                {
                    **dict(c),
                    "drug_names": c["drug_names"] or [],
                    "condition_names": c["condition_names"] or [],
                    "access_status": (
                        "full_access" if c["granted_count"] >= c["trial_count"]
                        else "partial_access" if c["granted_count"] > 0
                        else "pending" if c["pending_request_id"]
                        else "no_access"
                    ),
                }
                for c in collections
            ],
        }

    # ═══════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════

    async def _update_collection_summary(
        self,
        conn: asyncpg.Connection,
        collection_id: UUID,
        filter_criteria: dict,
    ):
        """Recompute denormalized summary fields for a collection."""
        patient_count_sql, patient_count_params, _ = self._build_patient_count_subquery(
            filter_criteria or {},
            "ca.trial_id",
            start_idx=2,
        )
        await conn.execute(
            f"""
            UPDATE data_asset_collection dac SET
                trial_count = sub.trial_count,
                total_patients = sub.total_patients,
                total_enrollment = sub.total_enrollment,
                therapeutic_areas = sub.therapeutic_areas,
                phases = sub.phases,
                updated_at = NOW()
            FROM (
                SELECT
                    ca.collection_id,
                    COUNT(DISTINCT ca.trial_id) as trial_count,
                    COALESCE(SUM(({patient_count_sql})), 0) as total_patients,
                    COALESCE(SUM(ct.enrollment_count), 0) as total_enrollment,
                    array_agg(DISTINCT ct.therapeutic_area)
                        FILTER (WHERE ct.therapeutic_area IS NOT NULL) as therapeutic_areas,
                    array_agg(DISTINCT ct.phase)
                        FILTER (WHERE ct.phase IS NOT NULL) as phases
                FROM collection_asset ca
                JOIN clinical_trial ct ON ca.trial_id = ct.trial_id
                WHERE ca.collection_id = $1
                GROUP BY ca.collection_id
            ) sub
            WHERE dac.collection_id = sub.collection_id
            AND dac.collection_id = $1
            """,
            collection_id,
            *patient_count_params,
        )

    async def _audit(self, **kwargs):
        try:
            await self.db.execute(
                """INSERT INTO auth_audit_log
                   (action, actor_id, actor_role, target_type, target_id, details)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                kwargs.get("action"), kwargs.get("actor_id"),
                kwargs.get("actor_role"), kwargs.get("target_type"),
                kwargs.get("target_id"), kwargs.get("details", {}),
            )
        except Exception as e:
            logger.error(f"Audit log failed: {e}")