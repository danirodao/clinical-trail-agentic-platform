"""
Authorization Service — Two-layer access control.

Layer 1 (OpenFGA): Which trials can the user access? (binary gate)
Layer 2 (PostgreSQL): Which patients within those trials? (cohort filters)

The AccessProfile carries both layers through the entire query pipeline.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import asyncpg

from auth.middleware import UserContext
from auth.openfga_client import OpenFGAClient, get_openfga_client

logger = logging.getLogger(__name__)

K_ANONYMITY_MIN = 5


@dataclass
class CohortScope:
    """One cohort's filter criteria for a specific trial."""
    cohort_id: str
    cohort_name: str
    filter_criteria: dict


@dataclass
class TrialAccessScope:
    """Access scope for a single trial — may have multiple cohort filters."""
    trial_id: str
    access_level: str  # 'individual' or 'aggregate'

    # List of cohort filters that grant access to this trial.
    # If empty → direct trial assignment → ALL patients accessible.
    # If multiple → UNION of all cohort filters.
    cohort_scopes: list[CohortScope] = field(default_factory=list)

    @property
    def has_patient_filter(self) -> bool:
        """True if access is restricted to a patient subset."""
        return len(self.cohort_scopes) > 0

    @property
    def is_unrestricted(self) -> bool:
        """True if user can see ALL patients (direct trial assignment)."""
        return len(self.cohort_scopes) == 0


@dataclass
class AccessProfile:
    """
    Complete access profile for a user's request.
    Threaded through the entire retrieval pipeline.
    """
    user_id: str
    role: str
    organization_id: str

    # Per-trial access scopes (includes patient-level filters)
    trial_scopes: dict[str, TrialAccessScope] = field(default_factory=dict)

    # Computed convenience fields
    aggregate_trial_ids: list[str] = field(default_factory=list)
    individual_trial_ids: list[str] = field(default_factory=list)
    allowed_trial_ids: list[str] = field(default_factory=list)
    has_any_access: bool = False
    has_individual_access: bool = False
    aggregate_only: bool = True

    # Pre-built filter clauses
    sql_trial_filter: str = "1=0"

    # Optional trial labels for human-readable UX/prompts.
    # {trial_uuid: {"nct_id": "NCT...", "title": "..."}}
    trial_metadata: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "AccessProfile":
        """
        Reconstruct an AccessProfile from a previously serialised snapshot dict.

        Used exclusively by the offline evaluator to replay a request under the
        original user's exact access context without re-querying OpenFGA or
        PostgreSQL.  The snapshot format is produced by AccessProfile.to_snapshot().

        No live auth calls are made — the snapshot IS the authoritative profile.
        """
        trial_scopes: dict[str, TrialAccessScope] = {}
        for tid, scope_dict in snapshot.get("trial_scopes", {}).items():
            cohort_scopes = [
                CohortScope(
                    cohort_id=cs["cohort_id"],
                    cohort_name=cs["cohort_name"],
                    filter_criteria=cs.get("filter_criteria", {}),
                )
                for cs in scope_dict.get("cohort_scopes", [])
            ]
            trial_scopes[tid] = TrialAccessScope(
                trial_id=scope_dict["trial_id"],
                access_level=scope_dict["access_level"],
                cohort_scopes=cohort_scopes,
            )

        allowed = snapshot.get("allowed_trial_ids", [])
        individual = snapshot.get("individual_trial_ids", [])
        aggregate = snapshot.get("aggregate_trial_ids", [])

        sql_filter = (
            f"trial_id IN ({', '.join(repr(t) for t in allowed)})"
            if allowed
            else "1=0"
        )

        return cls(
            user_id=snapshot["user_id"],
            role=snapshot["role"],
            organization_id=snapshot["organization_id"],
            trial_scopes=trial_scopes,
            allowed_trial_ids=allowed,
            individual_trial_ids=individual,
            aggregate_trial_ids=aggregate,
            has_any_access=bool(allowed),
            has_individual_access=bool(individual),
            aggregate_only=not bool(individual),
            sql_trial_filter=sql_filter,
            trial_metadata=snapshot.get("trial_metadata", {}),
        )

    def to_snapshot(self) -> dict:
        """
        Serialise this AccessProfile to a plain dict suitable for storage in
        Argilla (or any other evaluation store) and later restoration via
        AccessProfile.from_snapshot().
        """
        return {
            "user_id": self.user_id,
            "role": self.role,
            "organization_id": self.organization_id,
            "allowed_trial_ids": self.allowed_trial_ids,
            "individual_trial_ids": self.individual_trial_ids,
            "aggregate_trial_ids": self.aggregate_trial_ids,
            "trial_scopes": {
                tid: {
                    "trial_id": scope.trial_id,
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
                for tid, scope in self.trial_scopes.items()
            },
            "trial_metadata": self.trial_metadata,
        }


class AuthorizationService:
    """Computes and validates two-layer access profiles."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        fga_client: Optional[OpenFGAClient] = None,
    ):
        self.db = db_pool
        self.fga = fga_client or get_openfga_client()

    async def compute_access_profile(self, user: UserContext) -> AccessProfile:
        """
        Compute the full two-layer access profile.

        Layer 1: Query OpenFGA for trial-level access.
        Layer 2: Query PostgreSQL for cohort-level patient filters.
        """
        profile = AccessProfile(
            user_id=user.username,
            role=user.role,
            organization_id=user.organization_id,
        )

        # ── Domain owners see everything ──────────────────────
        if user.role == "domain_owner":
            profile.has_any_access = True
            profile.has_individual_access = True
            profile.aggregate_only = False
            profile.sql_trial_filter = "1=1"
            return profile

        # ── Layer 1: OpenFGA — which trials? ──────────────────
        aggregate_ids = await self.fga.get_accessible_trial_ids(
            profile.user_id, access_level="aggregate"
        )
        individual_ids = await self.fga.get_accessible_trial_ids(
            profile.user_id, access_level="individual"
        )

        profile.aggregate_trial_ids = aggregate_ids
        profile.individual_trial_ids = individual_ids
        profile.allowed_trial_ids = list(set(aggregate_ids + individual_ids))
        profile.has_any_access = len(profile.allowed_trial_ids) > 0
        profile.has_individual_access = len(individual_ids) > 0
        profile.aggregate_only = len(individual_ids) == 0

        if profile.allowed_trial_ids:
            ids = ", ".join(f"'{t}'" for t in profile.allowed_trial_ids)
            profile.sql_trial_filter = f"trial_id IN ({ids})"

        # ── Layer 2: PostgreSQL — build scopes for ALL trials ─
        #    (not just individual — aggregate trials need metadata too)
        if profile.allowed_trial_ids:
            await self._load_trial_scopes(profile, user)
            await self._load_trial_metadata(profile)

        logger.info(
            f"AccessProfile for {user.username}: "
            f"aggregate={len(aggregate_ids)}, individual={len(individual_ids)}, "
            f"scopes={len(profile.trial_scopes)}"
        )

        return profile

    async def _load_trial_metadata(self, profile: AccessProfile):
        """Load lightweight trial labels (NCT ID + title) for authorized trials."""
        if not profile.allowed_trial_ids:
            profile.trial_metadata = {}
            return

        rows = await self.db.fetch(
            """
            SELECT trial_id::text AS trial_id, nct_id, title
            FROM clinical_trial
            WHERE trial_id::text = ANY($1::text[])
            """,
            profile.allowed_trial_ids,
        )

        profile.trial_metadata = {
            r["trial_id"]: {
                "nct_id": r.get("nct_id") or "",
                "title": r.get("title") or "",
            }
            for r in rows
        }
    async def _load_trial_scopes(
        self, profile: AccessProfile, user: UserContext
    ):
        """
        Build trial scopes for ALL accessible trials (individual AND aggregate).
        """
        individual_set = set(profile.individual_trial_ids)

        # Policy-first: Get cohorts the user can access in OpenFGA.
        # NOTE: Some tuples may still be in the outbox queue, so we also include
        # cohorts from the database where the user has active assignments.
        allowed_cohort_ids = set(
            await self.fga.list_objects(
                user=f"user:{profile.user_id}",
                relation="can_access",
                object_type="cohort",
            )
        )

        # ── 1. Get ALL active cohort assignments (any access level) ──
        cohort_assignments = await self.db.fetch(
            """
            SELECT ra.cohort_id, ra.access_level,
                c.name AS cohort_name, c.filter_criteria,
                ct.trial_id
            FROM researcher_assignment ra
            JOIN cohort c ON ra.cohort_id = c.cohort_id
            JOIN cohort_trial ct ON c.cohort_id = ct.cohort_id
            WHERE ra.researcher_id = $1
            AND ra.organization_id = $2
            AND ra.cohort_id IS NOT NULL
            AND ra.revoked_at IS NULL
            AND ra.expires_at > NOW()
            """,
            profile.user_id,
            profile.organization_id,
        )

        # ── 1b. Get assigned cohort IDs from DB (in case tuples still in outbox) ──
        db_assigned_cohort_ids = {str(ca["cohort_id"]) for ca in cohort_assignments}

        # Union OpenFGA + DB cohorts (defensive: if OpenFGA tuple hasn't synced yet,
        # the DB assignment still counts)
        allowed_cohort_ids = allowed_cohort_ids.union(db_assigned_cohort_ids)

        # ── 2. Get ALL direct trial assignments (any access level) ───
        direct_assignments = await self.db.fetch(
            """
            SELECT ra.trial_id, ra.access_level
            FROM researcher_assignment ra
            WHERE ra.researcher_id = $1
            AND ra.organization_id = $2
            AND ra.trial_id IS NOT NULL
            AND ra.cohort_id IS NULL
            AND ra.revoked_at IS NULL
            AND ra.expires_at > NOW()
            """,
            profile.user_id,
            profile.organization_id,
        )

        # ── 3. Index for fast lookup ─────────────────────────────────
        direct_trial_ids = {str(d["trial_id"]) for d in direct_assignments}

        cohort_by_trial: dict[str, list] = {}
        for ca in cohort_assignments:
            if str(ca["cohort_id"]) not in allowed_cohort_ids:
                continue
            tid = str(ca["trial_id"])
            if tid not in cohort_by_trial:
                cohort_by_trial[tid] = []
            cohort_by_trial[tid].append(ca)

        # ── 4. Build scope for EVERY accessible trial ────────────────
        for trial_id in profile.allowed_trial_ids:
            is_individual = trial_id in individual_set
            has_direct = trial_id in direct_trial_ids
            trial_cohorts = cohort_by_trial.get(trial_id, [])

            # Determine cohort_scopes list:
            #   - direct assignment  → empty list → is_unrestricted = True
            #   - cohort assignment  → populated  → is_unrestricted = False
            #   - org grant only     → empty list → is_unrestricted = True
            if has_direct:
                scopes = []
            elif trial_cohorts:
                scopes = [
                    CohortScope(
                        cohort_id=str(ca["cohort_id"]),
                        cohort_name=ca["cohort_name"],
                        filter_criteria=ca["filter_criteria"] or {},
                    )
                    for ca in trial_cohorts
                ]
            else:
                scopes = []

            profile.trial_scopes[trial_id] = TrialAccessScope(
                trial_id=trial_id,
                access_level="individual" if is_individual else "aggregate",
                cohort_scopes=scopes,
            )

    async def _load_cohort_scopes(
        self, profile: AccessProfile, user: UserContext
    ):
        """
        Load cohort filter criteria for each individually-accessible trial.
        
        For each trial, find ALL active cohort assignments and collect their
        filter criteria. Multiple cohorts = UNION of filters.
        """
        # Get all active cohort assignments for this researcher
        cohort_assignments = await self.db.fetch(
            """
            SELECT ra.cohort_id, c.name as cohort_name, c.filter_criteria,
                   ct.trial_id
            FROM researcher_assignment ra
            JOIN cohort c ON ra.cohort_id = c.cohort_id
            JOIN cohort_trial ct ON c.cohort_id = ct.cohort_id
            WHERE ra.researcher_id = $1
            AND ra.organization_id = $2
            AND ra.access_level = 'individual'
            AND ra.cohort_id IS NOT NULL
            AND ra.revoked_at IS NULL
            AND ra.expires_at > NOW()
            """,
            profile.user_id,
            profile.organization_id,
        )

        # Get direct trial assignments (no cohort = no patient filter)
        direct_assignments = await self.db.fetch(
            """
            SELECT ra.trial_id
            FROM researcher_assignment ra
            WHERE ra.researcher_id = $1
            AND ra.organization_id = $2
            AND ra.access_level = 'individual'
            AND ra.trial_id IS NOT NULL
            AND ra.cohort_id IS NULL
            AND ra.revoked_at IS NULL
            AND ra.expires_at > NOW()
            """,
            profile.user_id,
            profile.organization_id,
        )

        # Build trial scopes
        for trial_id in profile.individual_trial_ids:
            scope = TrialAccessScope(
                trial_id=trial_id,
                access_level="individual",
            )

            # Check if any direct assignment (unrestricted)
            has_direct = any(
                str(d["trial_id"]) == trial_id for d in direct_assignments
            )

            if has_direct:
                # Direct assignment = ALL patients, no cohort filter
                scope.cohort_scopes = []
            else:
                # Collect all cohort filters that cover this trial
                for ca in cohort_assignments:
                    if str(ca["trial_id"]) == trial_id:
                        scope.cohort_scopes.append(CohortScope(
                            cohort_id=str(ca["cohort_id"]),
                            cohort_name=ca["cohort_name"],
                            filter_criteria=ca["filter_criteria"] or {},
                        ))

            profile.trial_scopes[trial_id] = scope

    # ═══════════════════════════════════════════════════════════
    # FILTER BUILDERS
    # ═══════════════════════════════════════════════════════════

    def build_patient_sql_filter(
        self, profile: AccessProfile, trial_id: str
    ) -> str:
        """
        Build a SQL WHERE clause for patient-level filtering.
        
        For unrestricted access: returns "1=1"
        For single cohort: returns that cohort's filter
        For multiple cohorts: returns UNION (OR) of all filters
        """
        scope = profile.trial_scopes.get(trial_id)

        if not scope or scope.is_unrestricted:
            return "1=1"  # No patient filter

        if not scope.cohort_scopes:
            return "1=1"

        # Build OR clause for each cohort's filter
        cohort_clauses = []
        for cs in scope.cohort_scopes:
            clause = self._build_single_cohort_sql(cs.filter_criteria)
            if clause:
                cohort_clauses.append(f"({clause})")

        if not cohort_clauses:
            return "1=1"

        # UNION = OR
        return " OR ".join(cohort_clauses)

    def _build_single_cohort_sql(self, filter_criteria: dict) -> str:
        """Convert one cohort's filter_criteria JSONB to a SQL WHERE clause."""
        conditions = []

        if filter_criteria.get("age_min") is not None:
            conditions.append(f"p.age >= {int(filter_criteria['age_min'])}")

        if filter_criteria.get("age_max") is not None:
            conditions.append(f"p.age <= {int(filter_criteria['age_max'])}")

        if filter_criteria.get("sex"):
            values = ", ".join(f"'{s}'" for s in filter_criteria["sex"])
            conditions.append(f"p.sex IN ({values})")

        if filter_criteria.get("ethnicity"):
            values = ", ".join(f"'{e}'" for e in filter_criteria["ethnicity"])
            conditions.append(f"p.ethnicity IN ({values})")

        if filter_criteria.get("country"):
            values = ", ".join(f"'{c}'" for c in filter_criteria["country"])
            conditions.append(f"p.country IN ({values})")

        if filter_criteria.get("disposition_status"):
            values = ", ".join(f"'{d}'" for d in filter_criteria["disposition_status"])
            conditions.append(f"p.disposition_status IN ({values})")

        if filter_criteria.get("arm_assigned"):
            values = ", ".join(f"'{a}'" for a in filter_criteria["arm_assigned"])
            conditions.append(f"p.arm_assigned IN ({values})")

        if filter_criteria.get("conditions"):
            values = ", ".join(f"'{c}'" for c in filter_criteria["conditions"])
            conditions.append(
                f"""EXISTS (
                    SELECT 1 FROM patient_condition pc
                    WHERE pc.patient_id = p.patient_id
                    AND pc.condition_name IN ({values})
                )"""
            )

        if not conditions:
            return ""

        return " AND ".join(conditions)

    def build_full_patient_filter(self, profile: AccessProfile) -> str:
        """
        Build a complete SQL WHERE clause covering ALL trials with
        their respective patient filters.

        Result:
            (trial_id = 'T1' AND (cohortA_filter OR cohortB_filter))
            OR
            (trial_id = 'T2' AND (cohortB_filter))
            OR
            (trial_id = 'T3' AND 1=1)  -- unrestricted
        """
        if profile.role == "domain_owner":
            return "1=1"

        if not profile.trial_scopes:
            # No individual scopes — use simple trial filter
            return profile.sql_trial_filter

        clauses = []
        for trial_id, scope in profile.trial_scopes.items():
            patient_filter = self.build_patient_sql_filter(profile, trial_id)
            clauses.append(
                f"(pte.trial_id = '{trial_id}' AND ({patient_filter}))"
            )

        # Add aggregate-only trials (no patient-level data)
        for trial_id in profile.aggregate_trial_ids:
            if trial_id not in profile.trial_scopes:
                # Aggregate only — will be filtered separately
                pass

        if not clauses:
            return profile.sql_trial_filter

        return " OR ".join(clauses)

    def build_qdrant_filter(self, profile: AccessProfile) -> dict:
        """
        Build Qdrant pre-filter. Trial-level only.
        Patient-level filtering happens in post-processing.
        """
        if profile.role == "domain_owner":
            return {}

        if not profile.allowed_trial_ids:
            return {"must": [{"key": "trial_id", "match": {"value": "DENIED"}}]}

        return {
            "must": [{
                "key": "trial_id",
                "match": {"any": profile.allowed_trial_ids}
            }]
        }

    def build_neo4j_trial_clause(
        self, profile: AccessProfile, trial_var: str = "t"
    ) -> str:
        """Build Neo4j WHERE clause for trial-level filtering."""
        if profile.role == "domain_owner":
            return ""

        if not profile.allowed_trial_ids:
            return f"AND {trial_var}.trial_id = 'DENIED'"

        ids = ", ".join(f"'{t}'" for t in profile.allowed_trial_ids)
        return f"AND {trial_var}.trial_id IN [{ids}]"

    def build_neo4j_patient_clause(
        self, profile: AccessProfile, trial_id: str, patient_var: str = "p"
    ) -> str:
        """Build Neo4j WHERE clause for patient-level filtering within a trial."""
        scope = profile.trial_scopes.get(trial_id)
        if not scope or scope.is_unrestricted:
            return ""

        # For Neo4j, we can only apply simple property filters
        # Complex conditions (EXISTS subquery) must be post-filtered
        neo4j_clauses = []
        for cs in scope.cohort_scopes:
            fc = cs.filter_criteria
            parts = []

            if fc.get("age_min") is not None:
                parts.append(f"{patient_var}.age >= {int(fc['age_min'])}")
            if fc.get("age_max") is not None:
                parts.append(f"{patient_var}.age <= {int(fc['age_max'])}")
            if fc.get("sex"):
                values = ", ".join(f"'{s}'" for s in fc["sex"])
                parts.append(f"{patient_var}.sex IN [{values}]")

            if parts:
                neo4j_clauses.append("(" + " AND ".join(parts) + ")")

        if neo4j_clauses:
            return "AND (" + " OR ".join(neo4j_clauses) + ")"
        return ""