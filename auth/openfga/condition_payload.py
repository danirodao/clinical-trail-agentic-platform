"""
Helpers to build STATIC condition contexts for OpenFGA conditional tuples.

These contexts are persisted on tuples at write time and are evaluated with
runtime DYNAMIC attributes passed via /check context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from auth.openfga.context_builder import (
    ALLOWED_AREAS,
    ALLOWED_PHASES,
    DEFAULT_ALLOWED_PURPOSES,
    ALLOWED_REGIONS,
)


_RFC3339_UTC = "%Y-%m-%dT%H:%M:%SZ"

_REGION_ALIASES = {
    "eu": "EU",
    "europe": "EU",
    "na": "NA",
    "north_america": "NA",
    "north america": "NA",
    "apac": "APAC",
    "asia_pacific": "APAC",
    "asia pacific": "APAC",
    "latam": "LATAM",
    "latin_america": "LATAM",
    "latin america": "LATAM",
    "mea": "MEA",
    "middle_east_and_africa": "MEA",
    "middle east and africa": "MEA",
}


def _to_rfc3339_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_RFC3339_UTC)


def _coerce_list(scope: dict[str, Any], *keys: str) -> list[str]:
    for key in keys:
        val = scope.get(key)
        if isinstance(val, str) and val.strip():
            return [val.strip()]
        if isinstance(val, list):
            out = [str(v).strip() for v in val if str(v).strip()]
            if out:
                return out
    return []


def _has_any_key(scope: dict[str, Any], *keys: str) -> bool:
    return any(k in scope for k in keys)


def _normalize_region(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    canonical = _REGION_ALIASES.get(raw.lower().replace("-", " "), raw.upper())
    return canonical if canonical in ALLOWED_REGIONS else None


def _normalize_area(value: str) -> str | None:
    raw = value.strip().lower().replace(" ", "_").replace("-", "_")
    return raw if raw in ALLOWED_AREAS else None


def _normalize_phase(value: str) -> str | None:
    raw = value.strip().upper().replace("PHASE ", "")
    return raw if raw in ALLOWED_PHASES else None


def _normalize_values(values: list[str], normalizer) -> list[str]:
    out: list[str] = []
    for v in values:
        norm = normalizer(v)
        if norm and norm not in out:
            out.append(norm)
    return out


def build_condition_context_from_scope(
    scope: dict[str, Any] | None,
    valid_until: datetime,
    valid_from: datetime | None = None,
) -> dict[str, Any]:
    """
    Build static conditional tuple attributes from an optional grant scope.

    If scope is empty or partial, defaults are permissive allowlists so legacy
    workflows continue to function while still using conditional tuple format.
    """
    s = scope or {}
    start = valid_from or datetime.now(timezone.utc)

    region_keys = ("permitted_regions", "regions", "region")
    area_keys = ("permitted_areas", "therapeutic_areas", "areas", "area")
    phase_keys = ("permitted_phases", "phases", "phase")
    purpose_keys = ("approved_purposes", "purposes", "purpose")

    regions = _normalize_values(_coerce_list(s, *region_keys), _normalize_region)
    areas = _normalize_values(_coerce_list(s, *area_keys), _normalize_area)
    phases = _normalize_values(_coerce_list(s, *phase_keys), _normalize_phase)
    purposes = _coerce_list(s, "approved_purposes", "purposes", "purpose")

    if not regions and not _has_any_key(s, *region_keys):
        regions = sorted(ALLOWED_REGIONS)
    if not areas and not _has_any_key(s, *area_keys):
        areas = sorted(ALLOWED_AREAS)
    if not phases and not _has_any_key(s, *phase_keys):
        phases = sorted(ALLOWED_PHASES)
    if not purposes and not _has_any_key(s, *purpose_keys):
        purposes = sorted(DEFAULT_ALLOWED_PURPOSES)

    try:
        resource_classification = int(s.get("resource_classification", 1))
    except (TypeError, ValueError):
        resource_classification = 1
    resource_classification = max(1, min(5, resource_classification))

    try:
        minimum_cohort_size = int(s.get("minimum_cohort_size", 5))
    except (TypeError, ValueError):
        minimum_cohort_size = 5
    minimum_cohort_size = max(1, minimum_cohort_size)

    return {
        "valid_from": _to_rfc3339_utc(start),
        "valid_until": _to_rfc3339_utc(valid_until),
        "permitted_regions": regions,
        "permitted_areas": areas,
        "permitted_phases": phases,
        "approved_purposes": purposes,
        "resource_classification": resource_classification,
        "minimum_cohort_size": minimum_cohort_size,
    }


def build_delegation_context_from_ceiling(
    ceiling_context: dict[str, Any],
    delegated_valid_until: datetime,
) -> dict[str, Any]:
    """
    Build a Tier-2 delegated context from a Tier-1 ceiling context.

    The delegated window is narrowed to not exceed the ceiling window.
    Scope dimensions are inherited so the result is a subset/equal delegation.
    """
    ceiling_until_raw = str(ceiling_context.get("valid_until", "")).strip()
    try:
        ceiling_until = datetime.strptime(ceiling_until_raw, _RFC3339_UTC).replace(tzinfo=timezone.utc)
    except ValueError:
        ceiling_until = delegated_valid_until

    narrowed_until = delegated_valid_until if delegated_valid_until <= ceiling_until else ceiling_until

    return {
        "valid_from": str(ceiling_context.get("valid_from")),
        "valid_until": _to_rfc3339_utc(narrowed_until),
        "permitted_regions": list(ceiling_context.get("permitted_regions", [])),
        "permitted_areas": list(ceiling_context.get("permitted_areas", [])),
        "permitted_phases": list(ceiling_context.get("permitted_phases", [])),
        "approved_purposes": list(ceiling_context.get("approved_purposes", [])),
        "resource_classification": int(ceiling_context.get("resource_classification", 1)),
        "minimum_cohort_size": int(ceiling_context.get("minimum_cohort_size", 5)),
    }


def build_narrowed_delegation_context(
    ceiling_context: dict[str, Any],
    delegated_valid_until: datetime,
    requested_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build a delegated context that can optionally be narrowed by manager scope.

    requested_scope may include region/area/phase/purpose keys (single or list)
    and must always be a subset of ceiling_context values.
    """
    delegated = build_delegation_context_from_ceiling(
        ceiling_context,
        delegated_valid_until,
    )
    scope = requested_scope or {}

    requested_regions = _normalize_values(
        _coerce_list(scope, "permitted_regions", "regions", "region"),
        _normalize_region,
    )
    requested_areas = _normalize_values(
        _coerce_list(scope, "permitted_areas", "therapeutic_areas", "areas", "area"),
        _normalize_area,
    )
    requested_phases = _normalize_values(
        _coerce_list(scope, "permitted_phases", "phases", "phase"),
        _normalize_phase,
    )
    requested_purposes = _coerce_list(scope, "approved_purposes", "purposes", "purpose")

    allowed_regions = _normalize_values(
        list(delegated.get("permitted_regions", [])),
        _normalize_region,
    )
    allowed_areas = _normalize_values(
        list(delegated.get("permitted_areas", [])),
        _normalize_area,
    )
    allowed_phases = _normalize_values(
        list(delegated.get("permitted_phases", [])),
        _normalize_phase,
    )
    allowed_purposes = [str(v).strip() for v in delegated.get("approved_purposes", []) if str(v).strip()]

    def _ensure_subset(field_name: str, requested: list[str], allowed: list[str]) -> None:
        if not requested:
            return
        extra = sorted(set(requested) - set(allowed))
        if extra:
            raise ValueError(
                f"Requested {field_name} outside org ceiling: {extra}. "
                f"Allowed: {sorted(set(allowed))}"
            )

    _ensure_subset("regions", requested_regions, allowed_regions)
    _ensure_subset("areas", requested_areas, allowed_areas)
    _ensure_subset("phases", requested_phases, allowed_phases)
    _ensure_subset("purposes", requested_purposes, allowed_purposes)

    if requested_regions:
        delegated["permitted_regions"] = requested_regions
    if requested_areas:
        delegated["permitted_areas"] = requested_areas
    if requested_phases:
        delegated["permitted_phases"] = requested_phases
    if requested_purposes:
        delegated["approved_purposes"] = requested_purposes

    if "resource_classification" in scope:
        requested_classification = int(scope["resource_classification"])
        if requested_classification < int(delegated["resource_classification"]):
            raise ValueError(
                "Requested resource_classification cannot be less restrictive "
                "than org ceiling"
            )
        delegated["resource_classification"] = requested_classification

    if "minimum_cohort_size" in scope:
        requested_min_cohort = int(scope["minimum_cohort_size"])
        if requested_min_cohort < int(delegated["minimum_cohort_size"]):
            raise ValueError(
                "Requested minimum_cohort_size cannot be less restrictive than org ceiling"
            )
        delegated["minimum_cohort_size"] = requested_min_cohort

    return delegated
