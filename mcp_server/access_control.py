"""
Access control module for MCP tool authorization.
Fixed: Added ID resolution (NCT vs UUID) and corrected enforcement logic.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple, Dict

logger = logging.getLogger(__name__)

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
                access_level=level,
                cohort_filters=cohort_filters,
            )

        return cls(
            user_id=data.get("user_id", "unknown"),
            role=data.get("role", "researcher"),
            organization_id=data.get("organization_id", ""),
            allowed_trial_ids=allowed_ids,
            trial_access=trial_access,
        )
    
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
                import logging
                logging.getLogger(__name__).warning(
                    f"Ignoring invalid trial_id '{rid}' (too short — likely an index). "
                    f"Falling back to all {len(self.allowed_trial_ids)} authorized trials."
                )
                return self.allowed_trial_ids  # Fall back immediately

            info = self._resolve_trial_info(rid)
            if info:
                resolved_uuids.append(info.trial_id)

        # If nothing resolved, fall back to all authorized trials
        if not resolved_uuids:
            logging.getLogger(__name__).warning(
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
        """
        if not trial_ids:
            return "1=0", [], param_offset

        trial_clauses: list[str] = []
        all_params: list[Any] = []
        idx = param_offset

        for raw_id in trial_ids:
            info = self._resolve_trial_info(raw_id)
            if not info:
                continue # Skip trials user isn't authorized for

            real_uuid = info.trial_id
            
            # Base condition: Must be enrolled in this specific trial
            trial_cond = f"{enrollment_alias}.trial_id = ${idx}::uuid"
            all_params.append(real_uuid)
            idx += 1

            if info.has_patient_filter:
                cohort_clauses: list[str] = []
                for cohort in info.cohort_filters:
                    cohort_sql, cohort_params, idx = self._build_single_cohort_filter(
                        cohort.criteria, idx, patient_alias
                    )
                    if cohort_sql:
                        cohort_clauses.append(f"({cohort_sql})")
                        all_params.extend(cohort_params)

                if cohort_clauses:
                    cohort_combined = " OR ".join(cohort_clauses)
                    trial_clauses.append(f"({trial_cond} AND ({cohort_combined}))")
                else:
                    logger.warning(f"User '{self.user_id}' has no valid cohort filters for trial '{info.trial_id}'. Access denied for this cohort path.")
                    # By doing nothing (pass), we prevent the data leak.
                    pass 
            else:
                # Unrestricted individual access
                trial_clauses.append(f"({trial_cond})")

        if not trial_clauses:
            return "1=0", [], param_offset

        where_clause = " OR ".join(trial_clauses)
        return f"({where_clause})", all_params, idx

    def _build_single_cohort_filter(
        self, criteria: dict[str, Any], param_offset: int, patient_alias: str
    ) -> Tuple[str, List[Any], int]:
        conditions: list[str] = []
        params: list[Any] = []
        idx = param_offset
        pa = patient_alias

        # Logic remains mostly same, but added better safety
        mapping = [
            ("age_min", f"{pa}.age >= ${idx}"),
            ("age_max", f"{pa}.age <= ${idx}"),
            ("sex", f"{pa}.sex = ANY(${idx}::text[])"),
            ("ethnicity", f"{pa}.ethnicity = ANY(${idx}::text[])"),
            ("country", f"{pa}.country = ANY(${idx}::text[])"),
            ("disposition_status", f"{pa}.disposition_status = ANY(${idx}::text[])"),
            ("arm_assigned", f"{pa}.arm_assigned = ANY(${idx}::text[])"),
        ]

        for key, sql in mapping:
            val = criteria.get(key)
            if val is not None and val != []:
                conditions.append(sql)
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

