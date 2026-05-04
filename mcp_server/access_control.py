"""
Access control module for MCP tool authorization.
Fixed: Added ID resolution (NCT vs UUID) and corrected enforcement logic.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple, Dict

logger = logging.getLogger(__name__)

# Toggle detailed authorization filter traces in MCP logs.
LOG_AUTH_FILTERS = os.getenv("LOG_AUTH_FILTERS", "true").lower() == "true"

@dataclass
class CohortFilter:
    cohort_id: str
    cohort_name: str
    criteria: dict[str, Any]

@dataclass
class TrialAccess:
    trial_id: str
    nct_id: str # Added to help with ID resolution
    access_level: str  # "individual" or "aggregate"
    therapeutic_area: str = ""
    phase: str = ""
    cohort_filters: list[CohortFilter] = field(default_factory=list)

    @property
    def is_individual(self) -> bool:
        return self.access_level == "individual"

    @property
    def is_aggregate(self) -> bool:
        return self.access_level == "aggregate"

    @property
    def has_patient_filter(self) -> bool:
        return len(self.cohort_filters) > 0
    @property
    def is_unrestricted(self) -> bool:
        """True if individual access with no cohort filters (sees all patients)."""
        return self.is_individual and not self.has_patient_filter

@dataclass
class AccessContext:
    user_id: str
    role: str
    organization_id: str
    allowed_trial_ids: list[str]
    trial_access: dict[str, TrialAccess] = field(default_factory=dict)

    # ABAC dynamic context forwarded from OpenFGAContextBuilder.
    # Present only when the caller provided scope_params in the query request.
    # MCP tools use this to call OpenFGA check_with_context() for fine-grained
    # enforcement of region / area / phase / purpose / clearance conditions.
    # None = no conditional tuples need checking for this request.
    abac_context: Optional[dict] = None

    @classmethod
    def from_json(cls, json_str: str) -> "AccessContext":
        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Invalid access_context JSON: {e}")

        allowed_ids = data.get("allowed_trial_ids", [])
        access_levels = data.get("access_levels", {})
        patient_filters_raw = data.get("patient_filters", {})
        
        # New: metadata mapping if provided by the agent (optional but helpful)
        trial_metadata = data.get("trial_metadata", {}) 

        trial_access: dict[str, TrialAccess] = {}
        for tid in allowed_ids:
            level = access_levels.get(tid, "aggregate")
            
            # Extract NCT ID from metadata if available
            nct_id = trial_metadata.get(tid, {}).get("nct_id", "")
            therapeutic_area = trial_metadata.get(tid, {}).get("therapeutic_area", "")
            phase = trial_metadata.get(tid, {}).get("phase", "")

            cohort_filters = []
            for cf_data in patient_filters_raw.get(tid, []):
                cohort_filters.append(CohortFilter(
                    cohort_id=cf_data.get("cohort_id", ""),
                    cohort_name=cf_data.get("cohort_name", ""),
                    criteria=cf_data.get("criteria", {}),
                ))

            trial_access[tid] = TrialAccess(
                trial_id=tid,
                nct_id=nct_id,
                therapeutic_area=therapeutic_area,
                phase=phase,
                access_level=level,
                cohort_filters=cohort_filters,
            )

        return cls(
            user_id=data.get("user_id", "unknown"),
            role=data.get("role", "researcher"),
            organization_id=data.get("organization_id", ""),
            allowed_trial_ids=allowed_ids,
            trial_access=trial_access,
            # Deserialize ABAC context if the agent forwarded it.
            # This was built by OpenFGAContextBuilder in the router and
            # embedded by serialize_access_profile() — it is safe to trust here.
            abac_context=data.get("abac_context"),
        )

    def _resolve_trial_info(self, tid_or_nct: str) -> Optional[TrialAccess]:
        """More forgiving trial ID resolution."""
        if not tid_or_nct or tid_or_nct.strip() == "":
            return None

        tid_or_nct = str(tid_or_nct).strip()

        # Direct UUID match
        if tid_or_nct in self.trial_access:
            return self.trial_access[tid_or_nct]

        # NCT ID match
        for info in self.trial_access.values():
            if info.nct_id == tid_or_nct or info.nct_id.endswith(tid_or_nct):
                return info

        # Partial UUID match (first 8 chars)
        for tid, info in self.trial_access.items():
            if tid.startswith(tid_or_nct) or tid_or_nct.startswith(tid[:8]):
                return info

        return None

    def validate_trial_access(self, requested_ids: Optional[List[str]]) -> List[str]:
        """
        Returns authorized UUIDs for the requested IDs.
        If the LLM passes garbage (like '3', 'all', 'NCT...'),
        it gracefully falls back to ALL authorized trials.
        """
        if not requested_ids:
            return self.allowed_trial_ids

        resolved_uuids = []
        for rid in requested_ids:
            rid = str(rid).strip()

            # Detect clearly invalid inputs from LLM hallucination
            if len(rid) < 8:
                # Too short to be a UUID or NCT ID — LLM passed an index number
                logger.warning(
                    f"Ignoring invalid trial_id '{rid}' (too short — likely an index). "
                    f"Falling back to all {len(self.allowed_trial_ids)} authorized trials."
                )
                return self.allowed_trial_ids  # Fall back immediately

            info = self._resolve_trial_info(rid)
            if info:
                resolved_uuids.append(info.trial_id)

        # If nothing resolved, fall back to all authorized trials
        if not resolved_uuids:
            logger.warning(
                f"None of the requested trial_ids {requested_ids} could be resolved. "
                f"Falling back to all {len(self.allowed_trial_ids)} authorized trials."
            )
            return self.allowed_trial_ids

        return resolved_uuids
    def get_effective_access_level(self, trial_ids: List[str]) -> str:
        """
        Ceiling principle: if ANY of the requested trials is aggregate-only,
        the entire query becomes aggregate-only.
        """
        if not trial_ids:
            return "aggregate"  # safe default

        for raw_tid in trial_ids:
            info = self._resolve_trial_info(raw_tid)
            if not info or info.is_aggregate:
                return "aggregate"
        
        return "individual"
    def enforce_individual_access_only(self, trial_ids: List[str]):
        """Strict check to prevent aggregate-only trials from accessing patient-level tools."""
        for tid in trial_ids:
            info = self._resolve_trial_info(tid)
            if not info:
                raise PermissionError(f"No access to trial {tid}")
            if info.is_aggregate:
                raise PermissionError(f"Trial {tid} is AGGREGATE ONLY. Patient-level data is blocked.")

    def build_authorized_patient_filter(
        self,
        trial_ids: List[str],
        param_offset: int = 1,
        patient_alias: str = "p",
        enrollment_alias: str = "pte",
    ) -> Tuple[str, List[Any], int]:
        """
        Builds the SQL filter. 
        Fixed: Now correctly resolves IDs and prevents invalid UUID casting in SQL.

          Permission invariants:
          1) Access is evaluated PER TRIAL. Filters from trial A never apply to trial B.
          2) Direct assignment (no cohort filters) => unrestricted patient scope for
              that trial only.
          3) Cohort assignment => patient scope restricted by that trial's cohort
              criteria only.
          4) If a trial has mixed cohort criteria (patient-level + trial-level-only),
              patient-level criteria remain authoritative.
        """
        if not trial_ids:
            if LOG_AUTH_FILTERS:
                logger.info(
                    "AUTH_FILTER_BUILT user=%s org=%s reason=no_trial_ids where=1=0 params=[]",
                    self.user_id,
                    self.organization_id,
                )
            return "1=0", [], param_offset

        trial_clauses: list[str] = []
        all_params: list[Any] = []
        idx = param_offset
        trace_rows: list[dict[str, Any]] = []

        for raw_id in trial_ids:
            info = self._resolve_trial_info(raw_id)
            if not info:
                continue # Skip trials user isn't authorized for

            real_uuid = info.trial_id

            # Reserve parameter positions, but only commit them if we actually
            # emit a SQL clause for this trial (prevents unused placeholders).
            trial_param_idx = idx
            next_idx = trial_param_idx + 1
            trial_cond = f"{enrollment_alias}.trial_id = ${trial_param_idx}::uuid"
            trial_region_sql, trial_region_params, next_idx = self._build_patient_region_guard(
                param_offset=next_idx,
                patient_alias=patient_alias,
                trial_id=info.trial_id,
            )

            base_trial_clause = trial_cond
            if trial_region_sql:
                base_trial_clause = f"({trial_cond} AND ({trial_region_sql}))"

            if info.has_patient_filter:
                cohort_clauses: list[str] = []
                cohort_params: list[Any] = []
                cohort_idx = next_idx
                saw_trial_only_applicable_cohort = False
                patient_level_cohort_count = 0
                trial_only_cohort_count = 0

                for cohort in info.cohort_filters:
                    if not self._cohort_applies_to_trial(cohort.criteria, info):
                        continue

                    cohort_sql, single_cohort_params, cohort_idx = self._build_single_cohort_filter(
                        cohort.criteria, cohort_idx, patient_alias
                    )
                    if cohort_sql:
                        cohort_clauses.append(f"({cohort_sql})")
                        cohort_params.extend(single_cohort_params)
                        patient_level_cohort_count += 1
                    elif cohort.criteria:
                        # Criteria contains only trial-level constraints (e.g.
                        # trial_ids/therapeutic_areas/phases). Track this and
                        # apply a neutral fallback ONLY if no patient-level
                        # cohort filters exist for this trial.
                        saw_trial_only_applicable_cohort = True
                        trial_only_cohort_count += 1

                if not cohort_clauses and saw_trial_only_applicable_cohort:
                    cohort_clauses.append("(1=1)")

                if cohort_clauses:
                    cohort_combined = " OR ".join(cohort_clauses)
                    trial_clauses.append(f"({base_trial_clause} AND ({cohort_combined}))")
                    all_params.append(real_uuid)
                    all_params.extend(trial_region_params)
                    all_params.extend(cohort_params)
                    idx = cohort_idx
                    trace_rows.append({
                        "trial_id": info.trial_id,
                        "access_level": info.access_level,
                        "mode": "cohort",
                        "patient_level_cohort_count": patient_level_cohort_count,
                        "trial_only_cohort_count": trial_only_cohort_count,
                        "cohort_clause_applied": True,
                    })
                else:
                    logger.warning(f"User '{self.user_id}' has no valid cohort filters for trial '{info.trial_id}'. Access denied for this cohort path.")
                    # No valid cohort clauses => do not consume parameter indexes.
                    trace_rows.append({
                        "trial_id": info.trial_id,
                        "access_level": info.access_level,
                        "mode": "cohort",
                        "patient_level_cohort_count": patient_level_cohort_count,
                        "trial_only_cohort_count": trial_only_cohort_count,
                        "cohort_clause_applied": False,
                    })
            else:
                # Direct assignment path (or assignment with no patient criteria)
                # for THIS trial only.
                trial_clauses.append(f"({base_trial_clause})")
                all_params.append(real_uuid)
                all_params.extend(trial_region_params)
                idx = next_idx
                trace_rows.append({
                    "trial_id": info.trial_id,
                    "access_level": info.access_level,
                    "mode": "direct_or_unrestricted",
                })

        if not trial_clauses:
            if LOG_AUTH_FILTERS:
                logger.info(
                    "AUTH_FILTER_BUILT user=%s org=%s where=1=0 params=[] requested_trials=%s trace=%s",
                    self.user_id,
                    self.organization_id,
                    trial_ids,
                    trace_rows,
                )
            return "1=0", [], param_offset

        where_clause = " OR ".join(trial_clauses)

        if LOG_AUTH_FILTERS:
            logger.info(
                "AUTH_FILTER_BUILT user=%s org=%s requested_trials=%s where=%s params=%s trace=%s",
                self.user_id,
                self.organization_id,
                trial_ids,
                where_clause,
                all_params,
                trace_rows,
            )

        return f"({where_clause})", all_params, idx

    def _build_patient_region_guard(
        self,
        param_offset: int,
        patient_alias: str,
        trial_id: str | None = None,
    ) -> Tuple[str, List[Any], int]:
        """
        Build a patient-level region guard from ABAC context.

        Region source precedence:
          1) requested_region
          2) allowed_regions

        Patient region is resolved from patient.region when present; otherwise
        country_region lookup by patient.country is used.
        """
        if not self.abac_context:
            return "", [], param_offset

        _REGION_SYNONYMS: Dict[str, list[str]] = {
            "EU": ["EU", "Europe", "European Union", "EEA"],
            "NA": ["NA", "North America", "United States", "US", "Canada"],
            "APAC": ["APAC", "Asia Pacific", "Asia-Pacific", "Asia"],
            "LATAM": ["LATAM", "Latin America", "South America"],
            "MEA": ["MEA", "Middle East", "Africa", "Middle East and Africa"],
        }
        _CANONICAL_REGIONS = set(_REGION_SYNONYMS.keys())

        def _canonical_region(raw: str) -> str | None:
            token = str(raw or "").strip()
            if not token:
                return None
            upper = token.upper()
            if upper in _CANONICAL_REGIONS:
                return upper
            low = token.lower()
            for canonical, synonyms in _REGION_SYNONYMS.items():
                if low in {s.lower() for s in synonyms}:
                    return canonical
            return None

        requested_region = str(self.abac_context.get("requested_region") or "").strip()
        if requested_region:
            raw_regions = [requested_region]
        else:
            per_trial = self.abac_context.get("per_trial_allowed_regions") or {}
            per_trial_regions = []
            if trial_id and isinstance(per_trial, dict):
                per_trial_regions = per_trial.get(trial_id) or []

            if per_trial_regions:
                raw_regions = [
                    str(v).strip()
                    for v in per_trial_regions
                    if str(v).strip()
                ]
            else:
                raw_regions = [
                    str(v).strip()
                    for v in (self.abac_context.get("allowed_regions") or [])
                    if str(v).strip()
                ]

        if not raw_regions:
            return "", [], param_offset

        # If scope covers all canonical regions (and no explicit requested_region
        # was provided), region guard is effectively unbounded and should not be
        # applied as a SQL filter.
        if not requested_region:
            canonical_regions = {
                c for c in (_canonical_region(r) for r in raw_regions) if c
            }
            if canonical_regions == _CANONICAL_REGIONS:
                return "", [], param_offset

        synonyms: list[str] = []
        for reg in raw_regions:
            synonyms.extend(_REGION_SYNONYMS.get(reg, [reg]))

        normalized_regions = sorted({s.strip().upper() for s in synonyms if s.strip()})
        if not normalized_regions:
            return "", [], param_offset

        idx = param_offset
        placeholders = ", ".join(f"${idx + i}" for i in range(len(normalized_regions)))
        region_expr = (
            f"COALESCE(NULLIF(TRIM({patient_alias}.region), ''), "
            f"(SELECT cr.region FROM country_region cr "
            f"WHERE LOWER(cr.country) = LOWER({patient_alias}.country) LIMIT 1))"
        )
        sql = f"UPPER({region_expr}) = ANY(ARRAY[{placeholders}]::text[])"

        return sql, normalized_regions, idx + len(normalized_regions)

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return str(value or "").strip().lower()

    @classmethod
    def _normalize_phase(cls, value: Any) -> str:
        phase = cls._normalize_text(value)
        if phase.startswith("phase "):
            phase = phase[6:].strip()
        return phase

    def _cohort_applies_to_trial(self, criteria: dict[str, Any], info: TrialAccess) -> bool:
        """
        Return True when trial-level criteria in a cohort allow this trial.

        These checks prevent accidental trial exclusion when criteria include
        non-patient dimensions (trial_ids, therapeutic_areas, phases).
        """
        if not criteria:
            return True

        trial_ids = criteria.get("trial_ids")
        if isinstance(trial_ids, list) and trial_ids:
            normalized_trial_ids = {self._normalize_text(v) for v in trial_ids}
            if self._normalize_text(info.trial_id) not in normalized_trial_ids:
                return False

        therapeutic_areas = criteria.get("therapeutic_areas")
        if isinstance(therapeutic_areas, list) and therapeutic_areas:
            normalized_areas = {self._normalize_text(v) for v in therapeutic_areas}
            if self._normalize_text(info.therapeutic_area) not in normalized_areas:
                return False

        phases = criteria.get("phases")
        if isinstance(phases, list) and phases:
            normalized_phases = {self._normalize_phase(v) for v in phases}
            if self._normalize_phase(info.phase) not in normalized_phases:
                return False

        return True

    def _build_single_cohort_filter(
        self, criteria: dict[str, Any], param_offset: int, patient_alias: str
    ) -> Tuple[str, List[Any], int]:
        conditions: list[str] = []
        params: list[Any] = []
        idx = param_offset
        pa = patient_alias

        # Build placeholder SQL dynamically so each filter uses its own param index.
        # Map criteria keys to actual patient table columns.
        mapping = [
            ("age_min", "age", ">=", False),
            ("age_max", "age", "<=", False),
            ("sex", "sex", "= ANY", True),
            ("ethnicity", "ethnicity", "= ANY", True),
            ("country", "country", "= ANY", True),
            ("disposition_status", "disposition_status", "= ANY", True),
            ("arm_assigned", "arm_assigned", "= ANY", True),
        ]

        for key, column, op, is_array in mapping:
            val = criteria.get(key)
            if val is not None and val != []:
                if is_array:
                    conditions.append(f"{pa}.{column} {op}(${idx}::text[])")
                else:
                    conditions.append(f"{pa}.{column} {op} ${idx}")
                params.append(val)
                idx += 1

        if criteria.get("conditions"):
            conditions.append(f"""EXISTS (
                SELECT 1 FROM patient_condition pc
                WHERE pc.patient_id = {pa}.patient_id
                AND pc.condition_name = ANY(${idx}::text[])
            )""")
            params.append(criteria["conditions"])
            idx += 1

        return " AND ".join(conditions), params, idx

    def build_trial_id_filter(
        self,
        trial_ids: List[str],
        param_offset: int = 1,
        column: str = "trial_id",
    ) -> Tuple[str, List[Any], int]:
        """
        Simple trial_id IN (...) filter for trial-metadata queries
        that do NOT involve patient data (no cohort filters needed).

        Use this for: search_trials, get_trial_details, get_eligibility_criteria.
        Use build_authorized_patient_filter for any query joining the patient table.
        """
        if not trial_ids:
            return "1=0", [], param_offset

        if len(trial_ids) == 1:
            clause = f"{column} = ${param_offset}::uuid"
            return clause, [trial_ids[0]], param_offset + 1

        clause = f"{column} = ANY(${param_offset}::uuid[])"
        return clause, [trial_ids], param_offset + 1

    def get_filter_descriptions(self) -> List[str]:
        """
        Human-readable list of active cohort filters.
        Used in tool metadata responses so the LLM can describe
        what restrictions are active in its answer.

        Example output:
          ["cohort 'Hispanic CT' | age 10-100 | ethnicity=['Hispanic or Latino']"]
        """
        descriptions: List[str] = []

        for tid, info in self.trial_access.items():
            for cohort in info.cohort_filters:
                parts: List[str] = [f"cohort '{cohort.cohort_name}'"]
                c = cohort.criteria

                if c.get("age_min") is not None or c.get("age_max") is not None:
                    age_min = c.get("age_min", "?")
                    age_max = c.get("age_max", "?")
                    parts.append(f"age {age_min}-{age_max}")

                if c.get("sex"):
                    parts.append(f"sex={c['sex']}")

                if c.get("ethnicity"):
                    parts.append(f"ethnicity={c['ethnicity']}")

                if c.get("country"):
                    parts.append(f"country={c['country']}")

                if c.get("conditions"):
                    parts.append(f"conditions={c['conditions']}")

                if c.get("disposition_status"):
                    parts.append(f"disposition={c['disposition_status']}")

                if c.get("arm_assigned"):
                    parts.append(f"arm={c['arm_assigned']}")

                descriptions.append(" | ".join(parts))

        return descriptions

# ====================== COMPATIBILITY SHIM (FINAL) ======================
def enforce_individual_access_only(access_context_input: Any, trial_ids: List[str]):
    """Compatibility layer for older tool modules."""
    try:
        if isinstance(access_context_input, AccessContext):
            ctx = access_context_input
        elif isinstance(access_context_input, str):
            ctx = AccessContext.from_json(access_context_input)
        elif isinstance(access_context_input, dict):
            ctx = AccessContext.from_json(json.dumps(access_context_input))
        else:
            raise TypeError(f"Unsupported access_context type: {type(access_context_input)}")

        ctx.enforce_individual_access_only(trial_ids)

    except PermissionError:
        raise
    except Exception as e:
        raise PermissionError(f"Access control validation failed: {str(e)}") from e


# Also expose the method that tools are calling
def get_effective_access_level(access_context_input: Any, trial_ids: List[str]) -> str:
    """Compatibility function for tools that call get_effective_access_level."""
    try:
        if isinstance(access_context_input, AccessContext):
            ctx = access_context_input
        elif isinstance(access_context_input, str):
            ctx = AccessContext.from_json(access_context_input)
        elif isinstance(access_context_input, dict):
            ctx = AccessContext.from_json(json.dumps(access_context_input))
        else:
            return "aggregate"  # safe fallback

        return ctx.get_effective_access_level(trial_ids)
    except Exception:
        return "aggregate"

