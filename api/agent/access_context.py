"""
Serializes an AccessProfile (from auth/authorization_service.py) into a compact
JSON string that is injected into every MCP tool call.

The MCP server deserializes this string and uses it to enforce:
  - Allowed trial IDs (Layer 1: OpenFGA)
  - Per-trial patient filters (Layer 2: PostgreSQL cohort criteria)
  - Access level per trial (individual vs aggregate)

This module also provides helpers used by the synthesizer to describe
active filters in human-readable form.
"""

from __future__ import annotations

import json

from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    # This avoids circular imports if AccessProfile is in another module
    from ..auth.authorization_service import AccessProfile 




def serialize_access_profile(access_profile: "AccessProfile") -> Dict[str, Any]:
    """
    Correctly serializes the researcher's AccessProfile.
    FIXED: Now populates trial_metadata with NCT IDs for reverse lookup.
    """
    if not hasattr(access_profile, 'has_any_access') or not access_profile.has_any_access:
        return {}

    access_levels: Dict[str, str] = {}
    patient_filters: Dict[str, List[Dict]] = {}
    trial_metadata: Dict[str, Dict] = dict(getattr(access_profile, 'trial_metadata', {}) or {})

    if hasattr(access_profile, 'trial_scopes'):
        for tid, scope in access_profile.trial_scopes.items():
            access_levels[tid] = scope.access_level

            if scope.cohort_scopes:
                patient_filters[tid] = [
                    {
                        "cohort_id":   cs.cohort_id,
                        "cohort_name": cs.cohort_name,
                        "criteria":    cs.filter_criteria,
                    }
                    for cs in scope.cohort_scopes
                ]

    return {
        "user_id":          getattr(access_profile, 'user_id', 'unknown'),
        "role":             getattr(access_profile, 'role', 'researcher'),
        "organization_id":  getattr(access_profile, 'organization_id', ''),
        "allowed_trial_ids": getattr(access_profile, 'allowed_trial_ids', []),
        "access_levels":    access_levels,
        "patient_filters":  patient_filters,
        "trial_metadata":   trial_metadata,
    }


def describe_filters(access_profile: Any) -> list[str]:
    """
    Produce a human-readable list of active access restrictions.
    Used in the API response's `filters_applied` field and in the system prompt.

    Example output:
      ["ethnicity: Hispanic or Latino", "age: 10–100", "conditions: Type 2 Diabetes"]
    """
    descriptions: list[str] = []
    seen: set[str] = set()

    for trial_id, scope in access_profile.trial_scopes.items():
        for cs in scope.cohort_scopes:
            criteria = cs.filter_criteria
            for key, value in criteria.items():
                if key == "age_min":
                    desc = f"age ≥ {value}"
                elif key == "age_max":
                    desc = f"age ≤ {value}"
                elif isinstance(value, list):
                    desc = f"{key.replace('_', ' ')}: {', '.join(str(v) for v in value)}"
                else:
                    desc = f"{key.replace('_', ' ')}: {value}"

                if desc not in seen:
                    descriptions.append(desc)
                    seen.add(desc)

    return descriptions


def determine_access_level_applied(access_profile: Any, scoped_trial_ids: list[str]) -> str:
    """
    Given the trials actually queried, determine the effective access level.
    Ceiling principle: if ANY queried trial is aggregate → result is "aggregate".
    """
    if not scoped_trial_ids:
        # No specific scope = all authorized trials were queried
        # Apply ceiling across everything
        all_levels = set()
        for tid, scope in access_profile.trial_scopes.items():
            all_levels.add(scope.access_level)
        if not all_levels:
            return "none"
        return "aggregate" if "aggregate" in all_levels else "individual"

    levels = set()
    for tid in scoped_trial_ids:
        # Try direct UUID lookup first
        scope = access_profile.trial_scopes.get(tid)
        
        if not scope:
            # Try matching by NCT ID (in case LLM passed NCT ID instead of UUID)
            for trial_id, trial_scope in access_profile.trial_scopes.items():
                # Check the trial_metadata dict for NCT ID mapping
                trial_meta = getattr(access_profile, 'trial_metadata', {})
                nct = trial_meta.get(trial_id, {}).get("nct_id", "")
                if nct == tid:
                    scope = trial_scope
                    break
        
        if scope:
            levels.add(scope.access_level)
        else:
            # Unknown trial = no access = treat as most restrictive
            levels.add("aggregate")

    if not levels:
        return "none"
    if "aggregate" in levels and "individual" in levels:
        return "mixed → aggregate (ceiling applied)"
    if "aggregate" in levels:
        return "aggregate"
    return "individual"


def build_access_summary_for_prompt(access_profile: Any) -> str:
    """
    Shows UUIDs clearly so the LLM uses them, not index numbers.
    """
    lines = []
    individual_trials = []
    aggregate_trials = []
    trial_metadata = getattr(access_profile, "trial_metadata", {}) or {}

    def trial_label(trial_id: str) -> str:
        meta = trial_metadata.get(trial_id, {}) if isinstance(trial_metadata, dict) else {}
        nct = (meta.get("nct_id") or "").strip() if isinstance(meta, dict) else ""
        title = (meta.get("title") or "").strip() if isinstance(meta, dict) else ""

        parts = []
        if nct:
            parts.append(f"NCT={nct}")
        if title:
            parts.append(f"title={title}")

        return f" ({'; '.join(parts)})" if parts else ""

    for tid, scope in access_profile.trial_scopes.items():
        if scope.access_level == "individual":
            filter_note = ""
            if scope.cohort_scopes:
                names = ", ".join(cs.cohort_name for cs in scope.cohort_scopes)
                filter_note = f" [cohort: {names}]"
            # Show UUID explicitly
            individual_trials.append(f"  • UUID={tid}{trial_label(tid)}{filter_note}")
        else:
            aggregate_trials.append(f"  • UUID={tid}{trial_label(tid)}")

    if individual_trials:
        lines.append(f"INDIVIDUAL ACCESS ({len(individual_trials)} trial(s)):")
        lines.extend(individual_trials)

    if aggregate_trials:
        lines.append(f"AGGREGATE ACCESS ({len(aggregate_trials)} trial(s)) — statistics only:")
        lines.extend(aggregate_trials)

    if not individual_trials and not aggregate_trials:
        lines.append("NO ACCESS — all queries will be rejected.")

    lines.append("")
    lines.append("⚠️  IMPORTANT: When calling tools, ALWAYS use the full UUID shown above.")
    lines.append("    NEVER use an index number (1, 2, 3), NCT ID, or partial ID.")

    return "\n".join(lines)