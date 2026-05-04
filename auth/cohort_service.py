"""
Cohort Service — CRUD for cohorts with ceiling enforcement.
Cohorts are owned by organizations (created by managers).
All trial_ids in a cohort must be within the org's access ceiling.
"""

import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg

from auth.middleware import UserContext
from auth.openfga_client import OpenFGAClient, get_openfga_client
from auth.openfga.context_builder import (
    ALLOWED_AREAS,
    ALLOWED_PHASES,
    ALLOWED_REGIONS,
    DEFAULT_ALLOWED_PURPOSES,
)
from auth.openfga_outbox import enqueue_write_tuples

logger = logging.getLogger(__name__)


class CeilingViolationError(Exception):
    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__(f"Ceiling violations: {violations}")


class CohortService:
    """Manages cohorts within the org's access ceiling."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        fga_client: Optional[OpenFGAClient] = None,
    ):
        self.db = db_pool
        self.fga = fga_client or get_openfga_client()

    async def _verify_ceiling(self, org_id: str, trial_ids: list[str]) -> tuple[bool, list[str]]:
        """
        Verify all trial_ids are within the org's access ceiling.

        For cohort creation/preview, PostgreSQL active grants are the source of
        truth. This avoids false denies when OpenFGA conditional checks require
        runtime context not provided in this endpoint.
        """
        if not trial_ids:
            return True, []

        rows = await self.db.fetch(
            """
            SELECT DISTINCT da.reference_id::text AS trial_id
            FROM access_grant ag
            JOIN data_asset da ON da.asset_id = ag.asset_id
            WHERE ag.organization_id = $1
              AND da.asset_type = 'clinical_trial'
              AND da.reference_id::text = ANY($2::text[])
              AND ag.revoked_at IS NULL
              AND ag.expires_at > NOW()
            """,
            org_id,
            trial_ids,
        )

        allowed = {r["trial_id"] for r in rows}
        violations = [
            f"Organization {org_id} does not have access to trial {trial_id}"
            for trial_id in trial_ids
            if trial_id not in allowed
        ]

        return len(violations) == 0, violations

    def _coerce_scope_list(self, scope: dict[str, Any], *keys: str) -> list[str]:
        for key in keys:
            value = scope.get(key)
            if isinstance(value, str) and value.strip():
                return [value.strip()]
            if isinstance(value, list):
                cleaned = [str(v).strip() for v in value if str(v).strip()]
                if cleaned:
                    return cleaned
        return []

    async def _load_inherited_trial_constraints(
        self,
        org_id: str,
        trial_ids: list[str],
    ) -> dict[str, dict[str, list[str]]]:
        """
        Load inherited restrictions (from active grants) for each selected trial.

        These become the mandatory base-layer constraints. Manager-selected
        patient filters are applied on top (logical AND).
        """
        if not trial_ids:
            return {}

        rows = await self.db.fetch(
            """
            SELECT DISTINCT ON (da.reference_id)
                   da.reference_id::text AS trial_id,
                   ag.scope
            FROM access_grant ag
            JOIN data_asset da ON da.asset_id = ag.asset_id
            WHERE ag.organization_id = $1
              AND da.asset_type = 'clinical_trial'
              AND da.reference_id::text = ANY($2::text[])
              AND ag.revoked_at IS NULL
              AND ag.expires_at > NOW()
            ORDER BY da.reference_id, ag.expires_at DESC
            """,
            org_id,
            trial_ids,
        )

        constraints: dict[str, dict[str, list[str]]] = {}
        for row in rows:
            scope = dict(row.get("scope") or {})
            constraints[row["trial_id"]] = {
                "regions": self._coerce_scope_list(scope, "permitted_regions", "regions", "region"),
                "countries": self._coerce_scope_list(scope, "permitted_countries", "countries", "country"),
            }
        return constraints

    def _build_inherited_filter_query(
        self,
        trial_constraints: dict[str, dict[str, list[str]]],
        param_start_idx: int,
    ) -> tuple[str, list[Any], int]:
        """
        Build SQL enforcing inherited restrictions per trial.

        Output shape:
          (trial=A AND region in A_regions AND country in A_countries)
          OR
          (trial=B AND region in B_regions)
        """
        if not trial_constraints:
            return "1=1", [], param_start_idx

        params: list[Any] = []
        idx = param_start_idx
        per_trial_clauses: list[str] = []

        for trial_id, c in trial_constraints.items():
            parts: list[str] = [f"pte.trial_id = ${idx}::uuid"]
            params.append(trial_id)
            idx += 1

            if c.get("regions"):
                parts.append(f"p.region = ANY(${idx}::text[])")
                params.append(c["regions"])
                idx += 1

            if c.get("countries"):
                parts.append(f"p.country = ANY(${idx}::text[])")
                params.append(c["countries"])
                idx += 1

            per_trial_clauses.append("(" + " AND ".join(parts) + ")")

        return "(" + " OR ".join(per_trial_clauses) + ")", params, idx

    def _build_patient_filter_query(
        self,
        filter_criteria: dict,
        param_start_idx: int = 1,
        inherited_trial_constraints: Optional[dict[str, dict[str, list[str]]]] = None,
    ) -> tuple[str, list]:
        """Build dynamic WHERE clause and params for filtering patients."""
        conditions = []
        params = []
        param_idx = param_start_idx

        inherited_clause, inherited_params, param_idx = self._build_inherited_filter_query(
            inherited_trial_constraints or {},
            param_idx,
        )
        if inherited_clause:
            conditions.append(inherited_clause)
            params.extend(inherited_params)

        trial_ids = filter_criteria.get("trial_ids", [])
        if trial_ids:
            conditions.append(f"pte.trial_id = ANY(${param_idx}::uuid[])")
            params.append(trial_ids)
            param_idx += 1

        if filter_criteria.get("age_min") is not None:
            conditions.append(f"p.age >= ${param_idx}")
            params.append(filter_criteria["age_min"])
            param_idx += 1

        if filter_criteria.get("age_max") is not None:
            conditions.append(f"p.age <= ${param_idx}")
            params.append(filter_criteria["age_max"])
            param_idx += 1

        if filter_criteria.get("sex"):
            conditions.append(f"p.sex = ANY(${param_idx}::text[])")
            params.append(filter_criteria["sex"])
            param_idx += 1

        if filter_criteria.get("country"):
            conditions.append(f"p.country = ANY(${param_idx}::text[])")
            params.append(filter_criteria["country"])
            param_idx += 1

        if filter_criteria.get("ethnicity"):
            conditions.append(f"p.ethnicity = ANY(${param_idx}::text[])")
            params.append(filter_criteria["ethnicity"])
            param_idx += 1

        if filter_criteria.get("disposition_status"):
            conditions.append(f"p.disposition_status = ANY(${param_idx}::text[])")
            params.append(filter_criteria["disposition_status"])
            param_idx += 1

        if filter_criteria.get("arm_assigned"):
            conditions.append(f"p.arm_assigned = ANY(${param_idx}::text[])")
            params.append(filter_criteria["arm_assigned"])
            param_idx += 1

        if filter_criteria.get("conditions"):
            conditions.append(f"EXISTS (SELECT 1 FROM patient_condition pc WHERE pc.patient_id = p.patient_id AND pc.condition_name = ANY(${param_idx}::text[]))")
            params.append(filter_criteria["conditions"])
            param_idx += 1

        if filter_criteria.get("phases"):
            # phases usually apply to the trial rather than the patient, but if they want to filter
            # we should join it in. However, the existing logic didn't filter patients directly by phases.
            # I will skip phases here since the UI already filters trials by phases, unless they meant trial phases.
            pass

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        return where_clause, params

    async def get_filter_options(self) -> dict:
        """Fetch distinct patient filter options from the database sequentially."""
        async with self.db.acquire() as conn:
            conds = await conn.fetch("SELECT DISTINCT condition_name FROM patient_condition WHERE condition_name IS NOT NULL")
            countries = await conn.fetch("SELECT DISTINCT country FROM patient WHERE country IS NOT NULL")
            ethnicities = await conn.fetch("SELECT DISTINCT ethnicity FROM patient WHERE ethnicity IS NOT NULL")
            statuses = await conn.fetch("SELECT DISTINCT disposition_status FROM patient WHERE disposition_status IS NOT NULL")
            arms = await conn.fetch("SELECT DISTINCT arm_assigned FROM patient WHERE arm_assigned IS NOT NULL")
            
        return {
            "conditions": [r["condition_name"] for r in conds],
            "country": [r["country"] for r in countries],
            "ethnicity": [r["ethnicity"] for r in ethnicities],
            "disposition_status": [r["disposition_status"] for r in statuses],
            "arm_assigned": [r["arm_assigned"] for r in arms],
            "regions": sorted(ALLOWED_REGIONS),
            "therapeutic_areas": sorted(ALLOWED_AREAS),
            "phases": sorted(ALLOWED_PHASES),
            "purposes": sorted(DEFAULT_ALLOWED_PURPOSES),
        }

    async def preview_cohort(
        self,
        user: UserContext,
        filter_criteria: dict,
    ) -> dict:
        """
        Preview a cohort without saving it.
        Returns patient count, demographics, and ceiling validation.
        """
        trial_ids = filter_criteria.get("trial_ids", [])

        # Ceiling check
        within_ceiling, violations = await self._verify_ceiling(
            user.organization_id, trial_ids
        )

        if not trial_ids:
            return {
                "patient_count": 0,
                "trial_count": 0,
                "trials": [],
                "demographics": {"sex": {}, "age_distribution": {}},
                "within_ceiling": True,
                "ceiling_violations": [],
            }

        inherited_constraints = await self._load_inherited_trial_constraints(
            user.organization_id,
            trial_ids,
        )

        # Build dynamic WHERE clause from filter_criteria
        where_clause, params = self._build_patient_filter_query(
            filter_criteria,
            param_start_idx=1,
            inherited_trial_constraints=inherited_constraints,
        )

        # Count patients
        patient_count = await self.db.fetchval(
            f"""
            SELECT COUNT(DISTINCT p.patient_id)
            FROM patient p
            JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
            WHERE {where_clause}
            """,
            *params,
        )

        # Demographics
        sex_dist = await self.db.fetch(
            f"""
            SELECT p.sex, COUNT(DISTINCT p.patient_id) as cnt
            FROM patient p
            JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
            WHERE {where_clause}
            GROUP BY p.sex
            """,
            *params,
        )

        # Per-trial breakdown
        trial_breakdown = await self.db.fetch(
            f"""
            SELECT pte.trial_id, ct.title, COUNT(DISTINCT p.patient_id) as patient_count
            FROM patient p
            JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
            JOIN clinical_trial ct ON pte.trial_id = ct.trial_id
            WHERE {where_clause}
            GROUP BY pte.trial_id, ct.title
            """,
            *params,
        )

        return {
            "patient_count": patient_count or 0,
            "trial_count": len(trial_breakdown),
            "trials": [
                {
                    "trial_id": str(r["trial_id"]),
                    "title": r["title"],
                    "patient_count": r["patient_count"],
                }
                for r in trial_breakdown
            ],
            "demographics": {
                "sex": {r["sex"]: r["cnt"] for r in sex_dist},
                "age_distribution": {},
            },
            "within_ceiling": within_ceiling,
            "ceiling_violations": violations,
        }

    async def create_cohort(
        self,
        user: UserContext,
        name: str,
        description: str,
        filter_criteria: dict,
        is_dynamic: bool = True,
    ) -> dict:
        """
        Create a cohort. Enforces ceiling — all trial_ids must be accessible.
        """
        if user.role != "manager":
            raise PermissionError("Only managers can create cohorts")

        trial_ids = filter_criteria.get("trial_ids", [])
        if not trial_ids:
            raise ValueError("At least one trial must be selected")

        # Ceiling enforcement
        within_ceiling, violations = await self._verify_ceiling(
            user.organization_id, trial_ids
        )
        if not within_ceiling:
            raise CeilingViolationError(violations)

        inherited_constraints = await self._load_inherited_trial_constraints(
            user.organization_id,
            trial_ids,
        )

        async with self.db.acquire() as conn:
            async with conn.transaction():
                # Create cohort
                cohort = await conn.fetchrow(
                    """
                    INSERT INTO cohort
                    (name, filter_criteria, created_by, organization_id, is_dynamic)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING cohort_id
                    """,
                    name, filter_criteria, user.user_id,
                    user.organization_id, is_dynamic,
                )
                cohort_id = cohort["cohort_id"]

                # Link trials
                for trial_id in trial_ids:
                    await conn.execute(
                        """
                        INSERT INTO cohort_trial (cohort_id, trial_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                        """,
                        cohort_id, UUID(trial_id),
                    )

                # If static, snapshot current patients
                if not is_dynamic:
                    where_clause, params = self._build_patient_filter_query(
                        filter_criteria,
                        param_start_idx=2,
                        inherited_trial_constraints=inherited_constraints,
                    )
                 
                    await conn.execute(
                        f"""
                        INSERT INTO cohort_patient (cohort_id, patient_id)
                        SELECT DISTINCT $1::uuid,  p.patient_id
                        FROM patient p
                        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                        WHERE {where_clause}
                        """,
                        cohort_id, *params,
                    )

                # Queue OpenFGA tuple sync via transactional outbox.
                await enqueue_write_tuples(
                    conn,
                    [
                        {
                            "user": f"user:{user.username}",
                            "relation": "creator",
                            "object": f"cohort:{cohort_id}",
                        },
                        {
                            "user": f"organization:{user.organization_id}",
                            "relation": "owning_org",
                            "object": f"cohort:{cohort_id}",
                        },
                    ],
                    source="cohort.create",
                    correlation_id=str(cohort_id),
                )

                await enqueue_write_tuples(
                    conn,
                    [
                        {
                            "user": f"clinical_trial:{trial_id}",
                            "relation": "includes_trial",
                            "object": f"cohort:{cohort_id}",
                        }
                        for trial_id in trial_ids
                    ],
                    source="cohort.create",
                    correlation_id=str(cohort_id),
                )

        return {"cohort_id": str(cohort_id), "status": "created"}

    async def list_org_cohorts(self, user: UserContext) -> list[dict]:
        """List all cohorts for the user's organization."""
        rows = await self.db.fetch(
            """
            SELECT c.cohort_id, c.name, c.filter_criteria, c.is_dynamic,
                   c.created_at,
                   (SELECT COUNT(*) FROM cohort_trial ct WHERE ct.cohort_id = c.cohort_id) as trial_count,
                   COALESCE(
                       json_agg(json_build_object(
                           'researcher_id', ra.researcher_id,
                           'access_level', ra.access_level,
                           'expires_at', ra.expires_at,
                           'is_active', ra.is_active
                       )) FILTER (WHERE ra.assignment_id IS NOT NULL),
                       '[]'
                   ) as assignments
            FROM cohort c
            LEFT JOIN researcher_assignment ra
                ON ra.cohort_id = c.cohort_id
            WHERE c.organization_id = $1
            GROUP BY c.cohort_id
            ORDER BY c.created_at DESC
            """,
            user.organization_id,
        )

        results = []
        for r in rows:
            trial_ids_rows = await self.db.fetch(
                "SELECT trial_id FROM cohort_trial WHERE cohort_id = $1",
                r["cohort_id"],
            )
            # For dynamic cohorts, compute patient count live
            trial_ids = [str(row["trial_id"]) for row in trial_ids_rows]
            patient_count = 0
            if trial_ids:
                # Merge trial_ids into filter criteria to use the common method
                f_crit = dict(r["filter_criteria"])
                f_crit["trial_ids"] = trial_ids
                inherited_constraints = await self._load_inherited_trial_constraints(
                    user.organization_id,
                    trial_ids,
                )
                where_clause, params = self._build_patient_filter_query(
                    f_crit,
                    param_start_idx=1,
                    inherited_trial_constraints=inherited_constraints,
                )
                
                patient_count = await self.db.fetchval(
                    f"""
                    SELECT COUNT(DISTINCT p.patient_id)
                    FROM patient p
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    WHERE {where_clause}
                    """,
                    *params,
                ) or 0

            results.append({
                "cohort_id": str(r["cohort_id"]),
                "name": r["name"],
                "filter_criteria": r["filter_criteria"],
                "trial_ids": trial_ids,
                "patient_count": patient_count,
                "is_dynamic": r["is_dynamic"],
                "created_at": r["created_at"].isoformat(),
                "assignments": r["assignments"],
            })

        return results