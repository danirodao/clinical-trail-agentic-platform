"""
Shared utilities for JSON serialization and common helpers.
"""

import json
import uuid
import datetime
import decimal
import logging
from typing import Any, Optional

from semantic_layer import build_inline_semantic_context
from observability import MCP_DATA_SOURCE_USAGE


logger = logging.getLogger(__name__)


def _append_demographic_filters(
    extra: list[str],
    params: list[Any],
    idx: int,
    sex: Optional[str] = None,
    age_min: Optional[int | str] = None,
    age_max: Optional[int | str] = None,
    ethnicity: Optional[str] = None,
    country: Optional[str] = None,
    arm_assigned: Optional[str] = None,
    disposition_status: Optional[str] = None,
    patient_alias: str = "p",
) -> int:
    """Helper to append demographic filters to the SQL query params."""
    if sex and sex.strip():
        # Handle "M", "F", "Male", "Female"
        s = sex.strip().upper()
        if s.startswith("M"):
            s = "M"
        elif s.startswith("F"):
            s = "F"
        extra.append(f"{patient_alias}.sex = ${idx}")
        params.append(s)
        idx += 1
    
    # Handle both string (from API) and int types
    for val, op in [(age_min, ">="), (age_max, "<=")]:
        if val is not None and str(val).strip():
            try:
                extra.append(f"{patient_alias}.age {op} ${idx}")
                params.append(int(str(val).strip()))
                idx += 1
            except (ValueError, TypeError):
                pass

    if ethnicity and ethnicity.strip():
        extra.append(f"LOWER({patient_alias}.ethnicity) LIKE LOWER(${idx}::text)")
        params.append(f"%{ethnicity.strip()}%")
        idx += 1
    
    if country and country.strip():
        extra.append(f"LOWER({patient_alias}.country) LIKE LOWER(${idx}::text)")
        params.append(f"%{country.strip()}%")
        idx += 1
    
    if arm_assigned and arm_assigned.strip():
        extra.append(f"LOWER({patient_alias}.arm_assigned) LIKE LOWER(${idx}::text)")
        params.append(f"%{arm_assigned.strip()}%")
        idx += 1
    
    if disposition_status and disposition_status.strip():
        extra.append(f"LOWER({patient_alias}.disposition_status) LIKE LOWER(${idx}::text)")
        params.append(f"%{disposition_status.strip()}%")
        idx += 1
        
    return idx



class SafeJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles asyncpg/neo4j types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        if isinstance(obj, datetime.timedelta):
            return str(obj)
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, set):
            return sorted(list(obj))
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return super().default(obj)


def to_json(obj: Any, pretty: bool = False) -> str:
    """Serialize object to JSON string with safe type handling."""
    return json.dumps(
        obj,
        cls=SafeJSONEncoder,
        indent=2 if pretty else None,
        ensure_ascii=False,
    )


def serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a database row (asyncpg Record as dict) to JSON-safe types."""
    result = {}
    for key, value in row.items():
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
        elif isinstance(value, (datetime.date, datetime.datetime)):
            result[key] = value.isoformat()
        elif isinstance(value, decimal.Decimal):
            result[key] = float(value)
        elif isinstance(value, list):
            result[key] = [
                str(v) if isinstance(v, uuid.UUID) else v for v in value
            ]
        else:
            result[key] = value
    return result


def make_tool_response(
    status: str,
    data: Any = None,
    metadata: dict | None = None,
    error: str | None = None,
    code: str | None = None,
    tool_name: str | None = None,
    data_sources: list[str] | None = None,
) -> str:
    """Create a standardized tool response JSON string."""
    response: dict[str, Any] = {"status": status}
    if data is not None:
        response["data"] = data
    if metadata:
        response["metadata"] = metadata
    else:
        response["metadata"] = {}
    if error:
        response["error"] = error
    if code:
        response["code"] = code

    # Embed which data stores contributed to this response
    if data_sources:
        response["metadata"]["data_sources"] = data_sources
        for src in data_sources:
            try:
                MCP_DATA_SOURCE_USAGE.labels(
                    tool_name=tool_name or "unknown", source=src,
                ).inc()
            except Exception:
                pass

    # Attach semantic context inline so downstream agents can interpret fields
    # without separate ontology lookups.
    try:
        response["semantic_context"] = build_inline_semantic_context(
            data=data,
            metadata=metadata,
            tool_name=tool_name,
        )
    except Exception as exc:
        logger.warning("Semantic context generation failed: %s", exc)

    return to_json(response)


def success_response(
    data: Any,
    metadata: dict | None = None,
    data_sources: list[str] | None = None,
) -> str:
    return make_tool_response(
        "success", data=data, metadata=metadata, data_sources=data_sources,
    )


def error_response(message: str, code: str = "ERROR") -> str:
    return make_tool_response("error", error=message, code=code)


def _normalize_area_token(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _extract_requested_areas(abac_context: dict | None) -> list[str]:
    """Collect explicit therapeutic-area constraints from ABAC context."""
    if not abac_context:
        return []

    areas: list[str] = []
    single = _normalize_area_token(abac_context.get("requested_area", ""))
    if single:
        areas.append(single)

    for key in ("requested_areas", "areas", "therapeutic_areas"):
        raw = abac_context.get(key)
        if isinstance(raw, list):
            for item in raw:
                normalized = _normalize_area_token(str(item))
                if normalized:
                    areas.append(normalized)
        elif isinstance(raw, str) and raw.strip():
            normalized = _normalize_area_token(raw)
            if normalized:
                areas.append(normalized)

    deduped: list[str] = []
    for area in areas:
        if area not in deduped:
            deduped.append(area)
    return deduped


def build_abac_sql_filters(
    abac_context: dict | None,
    table_alias: str = "ct",
    param_offset: int = 1,
    skip_allowed_fallbacks: bool = False,
) -> tuple[list[str], list[Any], int]:
    """
    Build SQL WHERE clauses that enforce ABAC scope attributes against the
    real columns that exist in the clinical_trial table.

    Column mapping (DB ↔ ABAC context key):
      clinical_trial.therapeutic_area  ↔  requested_area
      clinical_trial.regions TEXT[]    ↔  requested_region
      clinical_trial.phase             ↔  requested_phase

    resource_classification and minimum_cohort_size do NOT map to DB columns -
    they are enforced at the OpenFGA /check level (in CEL conditions) and by
    the per-query cohort size enforcement in the tools. Do not add SQL filters
    for them here.

    skip_allowed_fallbacks: when True, the allowed_areas / allowed_regions /
    allowed_phases governance-ceiling fallbacks are NOT applied as SQL filters.
    Use this in patient analytics tools where trial authorization is already
    enforced by build_authorized_patient_filter - applying governance ceilings
    on top of explicit individual trial grants incorrectly blocks access.

    Returns (conditions, params, next_param_index).
    """
    if not abac_context:
        return [], [], param_offset

    conditions: list[str] = []
    params: list[Any] = []
    idx = param_offset

    # requested_area / requested_areas -> clinical_trial.therapeutic_area
    requested_areas = _extract_requested_areas(abac_context)
    if requested_areas:
        placeholders = ", ".join(f"${idx + i}" for i in range(len(requested_areas)))
        conditions.append(
            "LOWER(REPLACE(REPLACE("
            f"{table_alias}.therapeutic_area, '-', '_'), ' ', '_')) "
            f"= ANY(ARRAY[{placeholders}]::text[])"
        )
        params.extend(requested_areas)
        idx += len(requested_areas)
    elif not skip_allowed_fallbacks:
        allowed_areas = [
            str(v).strip() for v in (abac_context.get("allowed_areas") or [])
            if str(v).strip()
        ]
        if allowed_areas:
            placeholders = ", ".join(f"${idx + i}" for i in range(len(allowed_areas)))
            conditions.append(
                f"LOWER({table_alias}.therapeutic_area) = ANY(ARRAY[{placeholders}]::text[])"
            )
            params.extend([v.lower() for v in allowed_areas])
            idx += len(allowed_areas)

    # requested_region -> clinical_trial.regions TEXT[] (ANY match).
    # The DB stores freeform PDF-extracted strings; map ABAC tokens to all
    # known synonyms so the filter hits existing data.
    _REGION_SYNONYMS: dict[str, list[str]] = {
        "EU": ["EU", "Europe", "European Union", "EEA"],
        "NA": ["NA", "North America", "United States", "US", "Canada"],
        "APAC": ["APAC", "Asia Pacific", "Asia-Pacific", "Asia"],
        "LATAM": ["LATAM", "Latin America", "South America"],
        "MEA": ["MEA", "Middle East", "Africa", "Middle East and Africa"],
    }
    region = abac_context.get("requested_region", "").strip()
    if region:
        synonyms = _REGION_SYNONYMS.get(region, [region])
        placeholders = ", ".join(f"${idx + i}" for i in range(len(synonyms)))
        conditions.append(
            f"EXISTS (SELECT 1 FROM unnest({table_alias}.regions) r WHERE r = ANY(ARRAY[{placeholders}]::text[]))"
        )
        params.extend(synonyms)
        idx += len(synonyms)
    elif not skip_allowed_fallbacks:
        allowed_regions = [
            str(v).strip() for v in (abac_context.get("allowed_regions") or [])
            if str(v).strip()
        ]
        if allowed_regions:
            all_synonyms: list[str] = []
            for reg in allowed_regions:
                all_synonyms.extend(_REGION_SYNONYMS.get(reg, [reg]))
            uniq_synonyms = sorted(set(all_synonyms))
            placeholders = ", ".join(f"${idx + i}" for i in range(len(uniq_synonyms)))
            conditions.append(
                f"EXISTS (SELECT 1 FROM unnest({table_alias}.regions) r WHERE r = ANY(ARRAY[{placeholders}]::text[]))"
            )
            params.extend(uniq_synonyms)
            idx += len(uniq_synonyms)

    # requested_phase -> clinical_trial.phase (e.g. "Phase 3", "Phase III", "III")
    # The DB stores values like "Phase 3" while the ABAC context uses "III".
    # We do a LIKE match to cover both naming conventions.
    phase = abac_context.get("requested_phase", "").strip()
    if phase:
        # Strip "Phase " prefix if the user sent "Phase III"
        bare = phase.replace("Phase ", "").strip()
        conditions.append(
            f"({table_alias}.phase ILIKE ${idx} OR {table_alias}.phase ILIKE ${idx + 1})"
        )
        params.append(f"%{bare}%")
        params.append(f"%{phase}%")
        idx += 2
    elif not skip_allowed_fallbacks:
        allowed_phases = [
            str(v).strip() for v in (abac_context.get("allowed_phases") or [])
            if str(v).strip()
        ]
        if allowed_phases:
            phase_conds: list[str] = []
            for p in allowed_phases:
                bare = p.replace("Phase ", "").strip()
                phase_conds.append(
                    f"({table_alias}.phase ILIKE ${idx} OR {table_alias}.phase ILIKE ${idx + 1})"
                )
                params.append(f"%{bare}%")
                params.append(f"%{p}%")
                idx += 2
            conditions.append("(" + " OR ".join(phase_conds) + ")")

    return conditions, params, idx
def build_abac_qdrant_filters(
    abac_context: dict | None,
) -> list[Any]:
    """
    Build Qdrant FieldCondition filters that enforce ABAC scope against the
    payload fields stored in the clinical_trial_embeddings collection.

    Qdrant payload fields used:
      therapeutic_area   ↔  requested_area
      phase              ↔  requested_phase

    region is NOT stored in Qdrant payload chunks (stored only in PostgreSQL)
    — region filtering is already handled by the PostgreSQL trial-ID gate that
    restricts `trial_ids` passed to search_vectors().

    Returns a list of FieldCondition objects ready to add to Qdrant must=[].
    """
    from qdrant_client.models import FieldCondition, MatchAny, MatchValue

    if not abac_context:
        return []

    conditions: list[FieldCondition] = []

    requested_areas = _extract_requested_areas(abac_context)
    if requested_areas:
        if len(requested_areas) == 1:
            conditions.append(
                FieldCondition(
                    key="therapeutic_area",
                    match=MatchValue(value=requested_areas[0]),
                )
            )
        else:
            conditions.append(
                FieldCondition(
                    key="therapeutic_area",
                    match=MatchAny(any=requested_areas),
                )
            )
    else:
        allowed_areas = [
            str(v).strip().lower() for v in (abac_context.get("allowed_areas") or [])
            if str(v).strip()
        ]
        if len(allowed_areas) == 1:
            conditions.append(
                FieldCondition(key="therapeutic_area", match=MatchValue(value=allowed_areas[0]))
            )
        elif len(allowed_areas) > 1:
            conditions.append(
                FieldCondition(key="therapeutic_area", match=MatchAny(any=allowed_areas))
            )

    phase = abac_context.get("requested_phase", "").strip()
    if phase:
        bare = phase.replace("Phase ", "").strip()
        conditions.append(
            FieldCondition(key="phase", match=MatchValue(value=bare))
        )
    else:
        allowed_phases = [
            str(v).strip().replace("Phase ", "") for v in (abac_context.get("allowed_phases") or [])
            if str(v).strip()
        ]
        if len(allowed_phases) == 1:
            conditions.append(
                FieldCondition(key="phase", match=MatchValue(value=allowed_phases[0]))
            )

    return conditions