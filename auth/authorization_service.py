"""
Authorization Service — Two-layer access control.

Layer 1 (OpenFGA): Which trials can the user access? (binary gate)
Layer 2 (PostgreSQL): Which patients within those trials? (cohort filters)

The AccessProfile carries both layers through the entire query pipeline.

ABAC note: OpenFGA's /list-objects endpoint does NOT evaluate conditions —
it returns all tuples regardless of whether CEL expressions pass. ABAC
conditions are enforced only by /check. When an abac_context is provided
(from OpenFGAContextBuilder in the router), compute_access_profile_with_abac()
performs per-trial /check calls to filter down the allowed list post-hoc.
"""

import json
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Optional

import asyncpg

from auth.middleware import UserContext
from auth.openfga_client import OpenFGAClient, get_openfga_client

logger = logging.getLogger(__name__)

K_ANONYMITY_MIN = 5


def _normalize_area_token(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_trial_id(tid: Any) -> str:
    """Canonical trial UUID string for set/dict lookups across FGA and PostgreSQL."""
    return str(tid).strip()


def _normalize_access_level(value: Any) -> str:
    level = str(value or "").strip().lower()
    return "individual" if level == "individual" else "aggregate"


# Active assignment predicate — evaluated at query time (not stale is_active boolean).
_ACTIVE_ASSIGNMENT_WHERE = """
    ra.revoked_at IS NULL
    AND ra.expires_at > NOW()
"""


def _parse_filter_criteria(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


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

    # Runtime region restriction set by the user's request scope.
    # When non-empty, only patients whose region matches one of these
    # values are visible — applied on top of cohort filters.
    requested_regions: list[str] = field(default_factory=list)

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
            requested_regions=snapshot.get("requested_regions", []),
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
            "requested_regions": self.requested_regions,
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

    @staticmethod
    def _list_objects_context(user: UserContext) -> dict[str, Any]:
        """
        Minimal runtime context for OpenFGA list-objects when model conditions
        require dynamic parameters. Uses conservative defaults.
        """
        return {
            "current_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "requested_region": "",
            "requested_area": "",
            "requested_phase": "",
            "stated_purpose": "",
            "user_clearance_level": int(getattr(user, "clearance_level", 1) or 1),
            "actual_cohort_size": 0,
        }

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
        list_ctx = self._list_objects_context(user)
        aggregate_ids = await self.fga.get_accessible_trial_ids(
            profile.user_id, access_level="aggregate", context=list_ctx
        )
        individual_ids = await self.fga.get_accessible_trial_ids(
            profile.user_id, access_level="individual", context=list_ctx
        )

        # Resilience fallback: some OpenFGA versions evaluate tuple conditions
        # during /list-objects and can return 400 when runtime context keys are
        # absent. In that case, rely on active PostgreSQL grants/assignments to
        # avoid false zero-access profiles.
        db_aggregate_ids = await self._db_aggregate_trial_ids(profile)
        db_individual_ids = await self._db_individual_trial_ids(profile)

        if (not aggregate_ids and db_aggregate_ids) or (not individual_ids and db_individual_ids):
            logger.warning(
                "OpenFGA list-objects returned empty but DB has active access; "
                "using DB fallback for user=%s org=%s",
                profile.user_id,
                profile.organization_id,
            )

        aggregate_ids = sorted(
            {_normalize_trial_id(t) for t in aggregate_ids}
            | {_normalize_trial_id(t) for t in db_aggregate_ids}
        )
        individual_ids = sorted(
            {_normalize_trial_id(t) for t in individual_ids}
            | {_normalize_trial_id(t) for t in db_individual_ids}
        )
        # Individual assignment always wins over org-level aggregate grant for the same trial.
        aggregate_ids = sorted(set(aggregate_ids) - set(individual_ids))

        profile.aggregate_trial_ids = aggregate_ids
        profile.individual_trial_ids = individual_ids
        profile.allowed_trial_ids = sorted(set(aggregate_ids) | set(individual_ids))
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
            await self._load_collection_filters(profile)
            await self._load_trial_metadata(profile)

        logger.info(
            f"AccessProfile for {user.username}: "
            f"aggregate={len(aggregate_ids)}, individual={len(individual_ids)}, "
            f"scopes={len(profile.trial_scopes)}"
        )

        return profile

    async def _fetch_direct_assignments(self, profile: AccessProfile) -> list[asyncpg.Record]:
        return await self.db.fetch(
            f"""
            SELECT ra.trial_id::text AS trial_id, ra.access_level
            FROM researcher_assignment ra
            WHERE ra.researcher_id = $1
              AND ra.organization_id = $2
              AND ra.trial_id IS NOT NULL
              AND ra.cohort_id IS NULL
              AND {_ACTIVE_ASSIGNMENT_WHERE}
            """,
            profile.user_id,
            profile.organization_id,
        )

    async def _fetch_expanded_cohort_assignments(
        self, profile: AccessProfile
    ) -> list[dict[str, Any]]:
        """
        Return one row per (cohort, trial) for active cohort-based assignments.

        Trial membership is resolved from cohort.filter_criteria.trial_ids first,
        then cohort_trial as a fallback — matching manager assignment semantics.
        """
        rows = await self.db.fetch(
            f"""
            SELECT
                ra.cohort_id::text AS cohort_id,
                ra.access_level,
                c.name AS cohort_name,
                c.filter_criteria
            FROM researcher_assignment ra
            JOIN cohort c ON c.cohort_id = ra.cohort_id
            WHERE ra.researcher_id = $1
              AND ra.organization_id = $2
              AND ra.cohort_id IS NOT NULL
              AND {_ACTIVE_ASSIGNMENT_WHERE}
            """,
            profile.user_id,
            profile.organization_id,
        )

        expanded: list[dict[str, Any]] = []
        for row in rows:
            criteria = _parse_filter_criteria(row["filter_criteria"])
            trial_ids = [
                str(tid).strip()
                for tid in (criteria.get("trial_ids") or [])
                if str(tid).strip()
            ]

            if not trial_ids:
                ct_rows = await self.db.fetch(
                    """
                    SELECT ct.trial_id::text AS trial_id
                    FROM cohort_trial ct
                    WHERE ct.cohort_id = $1::uuid
                    """,
                    row["cohort_id"],
                )
                trial_ids = [str(r["trial_id"]) for r in ct_rows]

            for trial_id in trial_ids:
                expanded.append({
                    "cohort_id": str(row["cohort_id"]),
                    "cohort_name": row["cohort_name"],
                    "access_level": row["access_level"],
                    "filter_criteria": criteria,
                    "trial_id": trial_id,
                })

        return expanded

    async def _trial_ids_from_cohort_assignments(
        self,
        profile: AccessProfile,
        access_level: str | None = None,
    ) -> list[str]:
        rows = await self._fetch_expanded_cohort_assignments(profile)
        if access_level:
            level = access_level.strip().lower()
            rows = [
                r for r in rows
                if str(r.get("access_level") or "").strip().lower() == level
            ]
        return sorted({str(r["trial_id"]) for r in rows})

    async def _db_aggregate_trial_ids(self, profile: AccessProfile) -> list[str]:
        """Fallback source for aggregate trial access from active grants/assignments."""
        rows = await self.db.fetch(
            """
            WITH granted_trials AS (
                SELECT DISTINCT da.reference_id::text AS trial_id
                FROM access_grant ag
                JOIN data_asset da ON da.asset_id = ag.asset_id
                WHERE ag.organization_id = $1
                  AND ag.is_active = TRUE
                  AND ag.revoked_at IS NULL
                  AND ag.expires_at > NOW()
                  AND da.asset_type = 'clinical_trial'
            ),
            direct_assignments AS (
                SELECT DISTINCT ra.trial_id::text AS trial_id
                FROM researcher_assignment ra
                WHERE ra.organization_id = $1
                  AND ra.researcher_id = $2
                  AND ra.access_level = 'aggregate'
                  AND ra.trial_id IS NOT NULL
                  AND ra.cohort_id IS NULL
                  AND ra.revoked_at IS NULL
                  AND ra.expires_at > NOW()
            )
            SELECT trial_id FROM granted_trials
            UNION
            SELECT trial_id FROM direct_assignments
            """,
            profile.organization_id,
            profile.user_id,
        )
        cohort_ids = await self._trial_ids_from_cohort_assignments(
            profile, access_level="aggregate"
        )
        return sorted(
            {_normalize_trial_id(r["trial_id"]) for r in rows}
            | {_normalize_trial_id(t) for t in cohort_ids}
        )

    async def _db_individual_trial_ids(self, profile: AccessProfile) -> list[str]:
        """Fallback source for individual trial access from active assignments."""
        direct_rows = await self.db.fetch(
            f"""
            SELECT DISTINCT ra.trial_id::text AS trial_id
            FROM researcher_assignment ra
            WHERE ra.organization_id = $1
              AND ra.researcher_id = $2
              AND ra.access_level = 'individual'
              AND ra.trial_id IS NOT NULL
              AND ra.cohort_id IS NULL
              AND {_ACTIVE_ASSIGNMENT_WHERE}
            """,
            profile.organization_id,
            profile.user_id,
        )
        direct_ids = {_normalize_trial_id(r["trial_id"]) for r in direct_rows}
        cohort_ids = {
            _normalize_trial_id(t)
            for t in await self._trial_ids_from_cohort_assignments(
                profile, access_level="individual"
            )
        }
        return sorted(direct_ids | cohort_ids)

    async def compute_access_profile_with_abac(
        self,
        user: UserContext,
        abac_context: dict,
    ) -> AccessProfile:
        """
        Compute access profile AND enforce ABAC conditions per trial.

        Why this is needed:
          OpenFGA's /list-objects endpoint ignores conditions — it returns all
          tuples regardless of whether the CEL expressions would pass. The ABAC
          enforcement only happens at /check time. This method runs a /check
          call (with the provided abac_context) for each trial returned by the
          plain list_objects call, and removes any trial whose conditional check
          returns DENY or CONDITIONAL_RESULT.

        When to use:
          Call this instead of compute_access_profile() when an abac_context
          is available (i.e. the user provided scope_params in the query request
          and OpenFGAContextBuilder successfully assembled the runtime attributes).

        Falls back to compute_access_profile() if abac_context is empty.
        """
        if not abac_context:
            return await self.compute_access_profile(user)

        # Step 1: get the plain ReBAC profile (trial IDs without ABAC filtering)
        profile = await self.compute_access_profile(user)

        if not profile.allowed_trial_ids or user.role == "domain_owner":
            # domain_owner bypass and empty-access short-circuit
            return profile

        # Step 1b: fetch per-trial cohort sizes from real enrollment data.
        # This replaces placeholder cohort_size=0 at trial-gating time and
        # prevents false denies when minimum_cohort_size > 0.
        count_rows = await self.db.fetch(
            """
            SELECT trial_id::text AS trial_id, COUNT(*)::int AS cohort_size
            FROM patient_trial_enrollment
            WHERE trial_id::text = ANY($1::text[])
            GROUP BY trial_id
            """,
            profile.allowed_trial_ids,
        )
        cohort_sizes = {r["trial_id"]: int(r["cohort_size"]) for r in count_rows}

        # Step 2: run per-trial /check with context to enforce ABAC conditions.
        # We check the most permissive relation for each trial — if the
        # conditional check fails, the user can't access it under this context.
        import asyncio

        async def _check_trial(trial_id: str, relation: str) -> tuple[str, bool]:
            trial_context = dict(abac_context)
            trial_context["actual_cohort_size"] = cohort_sizes.get(trial_id, 0)

            # OpenFGA CEL requires requested_area in permitted_areas. An empty
            # string always fails. Derive the area from the trial when the query
            # declared multiple areas (requested_areas) or omitted area entirely.
            if not str(trial_context.get("requested_area") or "").strip():
                requested_areas = [
                    _normalize_area_token(v)
                    for v in (abac_context.get("requested_areas") or [])
                    if _normalize_area_token(v)
                ]
                meta = profile.trial_metadata.get(trial_id, {})
                trial_area = _normalize_area_token(meta.get("therapeutic_area", ""))
                if requested_areas:
                    if trial_area not in requested_areas:
                        return trial_id, False
                    trial_context["requested_area"] = trial_area
                elif trial_area:
                    trial_context["requested_area"] = trial_area

            result = await self.fga.check_with_context(
                user     = f"user:{user.username}",
                relation = relation,
                object   = f"clinical_trial:{trial_id}",
                context  = trial_context,
            )
            # CONDITIONAL_RESULT (context_incomplete) and errors → DENY
            return trial_id, result.allowed and not result.context_incomplete

        # Check individual access (stricter) and aggregate in parallel
        individual_checks = await asyncio.gather(*[
            _check_trial(tid, "can_view_individual")
            for tid in profile.individual_trial_ids
        ])
        aggregate_checks = await asyncio.gather(*[
            _check_trial(tid, "can_view_aggregate")
            for tid in profile.aggregate_trial_ids
        ])

        abac_individual = [_normalize_trial_id(tid) for tid, ok in individual_checks if ok]
        abac_aggregate  = [
            _normalize_trial_id(tid) for tid, ok in aggregate_checks if ok
        ]
        abac_aggregate = [
            tid for tid in abac_aggregate if tid not in set(abac_individual)
        ]
        abac_allowed = sorted(set(abac_individual) | set(abac_aggregate))

        removed = len(profile.allowed_trial_ids) - len(abac_allowed)
        if removed > 0:
            logger.info(
                "ABAC context filtered %d trial(s) for user=%s (region=%s area=%s phase=%s)",
                removed,
                user.username,
                abac_context.get("requested_region"),
                abac_context.get("requested_area"),
                abac_context.get("requested_phase"),
            )

        # Step 3: rebuild the profile using only ABAC-approved trial IDs
        profile.individual_trial_ids = abac_individual
        profile.aggregate_trial_ids  = abac_aggregate
        profile.allowed_trial_ids    = abac_allowed
        profile.has_any_access       = bool(abac_allowed)
        profile.has_individual_access = bool(abac_individual)
        profile.aggregate_only        = not bool(abac_individual)

        # Rebuild SQL filter with the filtered set
        if abac_allowed:
            ids = ", ".join(f"'{t}'" for t in abac_allowed)
            profile.sql_trial_filter = f"trial_id IN ({ids})"
        else:
            profile.sql_trial_filter = "1=0"

        abac_allowed_set = set(abac_allowed)
        abac_individual_set = set(abac_individual)
        abac_aggregate_set = set(abac_aggregate)

        normalized_scopes: dict[str, TrialAccessScope] = {}
        for tid, scope in profile.trial_scopes.items():
            ntid = _normalize_trial_id(tid)
            if ntid not in abac_allowed_set:
                continue
            if ntid in abac_individual_set:
                scope.access_level = "individual"
            elif ntid in abac_aggregate_set:
                scope.access_level = "aggregate"
            normalized_scopes[ntid] = scope
        profile.trial_scopes = normalized_scopes

        profile.trial_metadata = {
            _normalize_trial_id(tid): meta
            for tid, meta in profile.trial_metadata.items()
            if _normalize_trial_id(tid) in abac_allowed_set
        }

        # Capture runtime region restriction for row-level filtering.
        # The value is a single string from the context (e.g. "Europe"); store
        # as a list so the filter builder can use SQL IN (...).
        raw_region = (abac_context.get("requested_region") or "").strip()
        if raw_region:
            profile.requested_regions = [raw_region]

        return profile

    async def _load_trial_metadata(self, profile: AccessProfile):
        """Load lightweight trial labels (NCT ID + title) for authorized trials."""
        if not profile.allowed_trial_ids:
            profile.trial_metadata = {}
            return

        rows = await self.db.fetch(
            """
            SELECT
                trial_id::text AS trial_id,
                nct_id,
                title,
                phase,
                therapeutic_area,
                regions
            FROM clinical_trial
            WHERE trial_id::text = ANY($1::text[])
            """,
            profile.allowed_trial_ids,
        )

        profile.trial_metadata = {
            r["trial_id"]: {
                "nct_id": r.get("nct_id") or "",
                "title": r.get("title") or "",
                "phase": r.get("phase") or "",
                "therapeutic_area": r.get("therapeutic_area") or "",
                "regions": list(r.get("regions") or []),
            }
            for r in rows
        }
    async def _load_collection_filters(self, profile: AccessProfile):
        """
        Load and apply collection-level filters (regions, filter_criteria) to trial scopes.
        
        When a user accesses trials via an access_grant (e.g., a published collection),
        the collection may have:
        - regions: Applied as profile.requested_regions (org-wide restriction)
        - filter_criteria: Applied as cohort-level patient filters per trial
        
        Regions are handled separately from other filters to respect both restrictions.
        """
        rows = await self.db.fetch(
            """
            SELECT DISTINCT
                da.reference_id::text AS trial_id,
                dac.filter_criteria,
                dac.regions
            FROM access_grant ag
            JOIN data_asset da ON da.asset_id = ag.asset_id
            LEFT JOIN LATERAL (
                SELECT ca.collection_id
                FROM collection_asset ca
                WHERE ca.asset_id = ag.asset_id
                ORDER BY ca.collection_id
                LIMIT 1
            ) ca_fallback ON TRUE
            JOIN data_asset_collection dac
              ON dac.collection_id = COALESCE(ag.collection_id, ca_fallback.collection_id)
            WHERE ag.organization_id = $1
              AND ag.is_active = TRUE
              AND ag.revoked_at IS NULL
              AND ag.expires_at > NOW()
              AND da.asset_type = 'clinical_trial'
              AND da.reference_id::text = ANY($2::text[])
            """,
            profile.organization_id,
            profile.allowed_trial_ids,
        )
        
        # Collect all collection regions and non-region filter_criteria
        collection_regions: set[str] = set()
        collection_filters_by_trial: dict[str, dict] = {}
        
        for row in rows:
            trial_id = row["trial_id"]
            
            # 1. Accumulate regions from collection FILTER criteria (authoritative)
            filter_criteria = dict(row["filter_criteria"] or {})

            fc_regions: list[str] = []
            if isinstance(filter_criteria.get("region"), list):
                fc_regions.extend(str(v).strip() for v in filter_criteria.get("region", []) if str(v).strip())
            if isinstance(filter_criteria.get("regions"), list):
                fc_regions.extend(str(v).strip() for v in filter_criteria.get("regions", []) if str(v).strip())

            if fc_regions:
                collection_regions.update(fc_regions)
            elif row["regions"]:
                # Backward-compatible fallback when filter_criteria has no region key.
                collection_regions.update(str(v).strip() for v in row["regions"] if str(v).strip())

            # 2. Extract non-region filter_criteria for per-trial application
            # Remove region keys since region guard is handled globally.
            filter_criteria.pop("region", None)
            filter_criteria.pop("regions", None)
            
            if filter_criteria:  # Only store if there are criteria left
                collection_filters_by_trial[trial_id] = filter_criteria
        
        # 3. Apply collection regions to profile (restricts all patients across all trials)
        if collection_regions:
            profile.requested_regions = sorted(list(collection_regions))
        
        # 4. Apply per-trial collection filter_criteria as cohort-level restrictions
        for trial_id, coll_filters in collection_filters_by_trial.items():
            scope = profile.trial_scopes.get(trial_id)
            if scope:
                # If user has a direct assignment (unrestricted), the collection
                # filters still apply. Add them as a synthetic cohort scope.
                if scope.is_unrestricted:
                    # Convert direct access to cohort-filtered by applying collection filters
                    scope.cohort_scopes = [
                        CohortScope(
                            cohort_id="__collection__",
                            cohort_name="Collection Restrictions",
                            filter_criteria=coll_filters,
                        )
                    ]
                else:
                    # Already has cohort filters; add collection filters as additional scope
                    scope.cohort_scopes.append(
                        CohortScope(
                            cohort_id="__collection__",
                            cohort_name="Collection Restrictions",
                            filter_criteria=coll_filters,
                        )
                    )
    
    async def _load_trial_scopes(
        self, profile: AccessProfile, user: UserContext
    ):
        """
        Build trial scopes for ALL accessible trials (individual AND aggregate).
        """
        individual_set = {_normalize_trial_id(t) for t in profile.individual_trial_ids}
        assignment_level_by_trial: dict[str, str] = {}

        # Policy-first: Get cohorts the user can access in OpenFGA.
        # NOTE: Some tuples may still be in the outbox queue, so we also include
        # cohorts from the database where the user has active assignments.
        allowed_cohort_ids = set(
            await self.fga.list_objects(
                user=f"user:{profile.user_id}",
                relation="can_access",
                object_type="cohort",
                context=self._list_objects_context(user),
            )
        )

        # ── 1. Cohort assignments expanded to trial_ids (filter_criteria first) ──
        cohort_assignments = await self._fetch_expanded_cohort_assignments(profile)

        db_assigned_cohort_ids = {str(ca["cohort_id"]) for ca in cohort_assignments}
        allowed_cohort_ids = allowed_cohort_ids.union(db_assigned_cohort_ids)

        # ── 2. Direct trial assignments (trial_id set, no cohort) ───
        direct_assignments = await self._fetch_direct_assignments(profile)

        # ── 3. Index for fast lookup ─────────────────────────────────
        direct_trial_ids = {str(d["trial_id"]) for d in direct_assignments}

        # PostgreSQL assignments are the source of truth for access LEVEL.
        for d in direct_assignments:
            tid = _normalize_trial_id(d["trial_id"])
            level = _normalize_access_level(d.get("access_level"))
            assignment_level_by_trial[tid] = level
            if level == "individual":
                individual_set.add(tid)

        for ca in cohort_assignments:
            tid = _normalize_trial_id(ca["trial_id"])
            level = _normalize_access_level(ca.get("access_level"))
            prev = assignment_level_by_trial.get(tid)
            if prev != "individual":
                assignment_level_by_trial[tid] = level
            if level == "individual":
                individual_set.add(tid)

        profile.individual_trial_ids = sorted(individual_set)
        profile.has_individual_access = bool(individual_set)
        profile.aggregate_only = not bool(individual_set)

        allowed_set = {
            _normalize_trial_id(t) for t in profile.allowed_trial_ids
        } | individual_set
        profile.allowed_trial_ids = sorted(allowed_set)
        profile.aggregate_trial_ids = sorted(
            set(_normalize_trial_id(t) for t in profile.aggregate_trial_ids)
            - individual_set
        )

        direct_trial_ids = {_normalize_trial_id(t) for t in direct_trial_ids}

        cohort_by_trial: dict[str, list] = {}
        for ca in cohort_assignments:
            if str(ca["cohort_id"]) not in allowed_cohort_ids:
                continue
            tid = _normalize_trial_id(ca["trial_id"])
            cohort_by_trial.setdefault(tid, []).append(ca)

        # ── 4. Build scope for EVERY accessible trial ────────────────
        for trial_id in profile.allowed_trial_ids:
            tid = _normalize_trial_id(trial_id)
            assigned_level = assignment_level_by_trial.get(tid)
            is_individual = (
                tid in individual_set
                or assigned_level == "individual"
            )
            has_direct = tid in direct_trial_ids
            trial_cohorts = cohort_by_trial.get(tid, [])

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

            profile.trial_scopes[tid] = TrialAccessScope(
                trial_id=tid,
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
        cohort_assignments = [
            ca for ca in await self._fetch_expanded_cohort_assignments(profile)
            if str(ca.get("access_level") or "").strip().lower() == "individual"
        ]
        direct_assignments = [
            d for d in await self._fetch_direct_assignments(profile)
            if str(d.get("access_level") or "").strip().lower() == "individual"
        ]

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

    def _patient_region_sql_expr(self) -> str:
        """
        SQL expression resolving a patient's region from either:
          1) patient.region (preferred)
          2) country_region lookup via patient.country (fallback)
        """
        return (
            "COALESCE(NULLIF(TRIM(p.region), ''), "
            "(SELECT cr.region FROM country_region cr "
            "WHERE LOWER(cr.country) = LOWER(p.country) LIMIT 1))"
        )

    def _region_sql_guard(self, profile: AccessProfile) -> str | None:
        """Return SQL fragment `p.region IN (...)` if requested_regions is set."""
        if not profile.requested_regions:
            return None
        vals = ", ".join(f"'{r}'" for r in profile.requested_regions)
        return f"{self._patient_region_sql_expr()} IN ({vals})"

    def build_patient_sql_filter(
        self, profile: AccessProfile, trial_id: str
    ) -> str:
        """
        Build a SQL WHERE clause for patient-level filtering.

        Cohort filters restrict which patients are visible by demographic/clinical
        criteria. The runtime `requested_regions` guard (from ABAC scope) is AND-ed
        on top so that even unrestricted trial access is bounded by region.
        """
        scope = profile.trial_scopes.get(trial_id)
        region_guard = self._region_sql_guard(profile)

        if not scope or scope.is_unrestricted:
            return region_guard if region_guard else "1=1"

        if not scope.cohort_scopes:
            return region_guard if region_guard else "1=1"

        # Build OR clause for each cohort's filter
        cohort_clauses = []
        for cs in scope.cohort_scopes:
            clause = self._build_single_cohort_sql(cs.filter_criteria)
            if clause:
                cohort_clauses.append(f"({clause})")

        if not cohort_clauses:
            return region_guard if region_guard else "1=1"

        # UNION = OR for cohorts, then AND with region guard
        cohort_sql = " OR ".join(cohort_clauses)
        if region_guard:
            return f"({cohort_sql}) AND ({region_guard})"
        return cohort_sql

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

        if filter_criteria.get("region"):
            values = ", ".join(f"'{r}'" for r in filter_criteria["region"])
            conditions.append(f"{self._patient_region_sql_expr()} IN ({values})")

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
            # Domain owners bypass cohort filters but still respect runtime region.
            region_guard = self._region_sql_guard(profile)
            return region_guard if region_guard else "1=1"

        if not profile.trial_scopes:
            # No individual scopes — use simple trial filter + region guard.
            region_guard = self._region_sql_guard(profile)
            base = profile.sql_trial_filter
            if region_guard:
                return f"({base}) AND ({region_guard})"
            return base

        clauses = []
        for trial_id, scope in profile.trial_scopes.items():
            patient_filter = self.build_patient_sql_filter(profile, trial_id)
            clauses.append(
                f"(pte.trial_id = '{trial_id}' AND ({patient_filter}))"
            )

        if not clauses:
            region_guard = self._region_sql_guard(profile)
            if region_guard:
                return f"({profile.sql_trial_filter}) AND ({region_guard})"
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
        parts_outer: list[str] = []

        # ── Cohort property filters ───────────────────────────────────────
        scope = profile.trial_scopes.get(trial_id)
        if scope and not scope.is_unrestricted:
            neo4j_clauses = []
            for cs in scope.cohort_scopes:
                fc = cs.filter_criteria
                parts: list[str] = []

                if fc.get("age_min") is not None:
                    parts.append(f"{patient_var}.age >= {int(fc['age_min'])}")
                if fc.get("age_max") is not None:
                    parts.append(f"{patient_var}.age <= {int(fc['age_max'])}")
                if fc.get("sex"):
                    values = ", ".join(f"'{s}'" for s in fc["sex"])
                    parts.append(f"{patient_var}.sex IN [{values}]")
                if fc.get("region"):
                    values = ", ".join(f"'{r}'" for r in fc["region"])
                    parts.append(f"{patient_var}.region IN [{values}]")

                if parts:
                    neo4j_clauses.append("(" + " AND ".join(parts) + ")")

            if neo4j_clauses:
                parts_outer.append("(" + " OR ".join(neo4j_clauses) + ")")

        # ── Runtime region guard (from requested_regions in ABAC scope) ──
        if profile.requested_regions:
            values = ", ".join(f"'{r}'" for r in profile.requested_regions)
            parts_outer.append(f"{patient_var}.region IN [{values}]")

        if parts_outer:
            return "AND (" + " AND ".join(parts_outer) + ")"
        return ""