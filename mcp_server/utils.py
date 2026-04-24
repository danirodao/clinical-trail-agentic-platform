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
        extra.append(f"LOWER({patient_alias}.ethnicity) LIKE LOWER(${idx})")
        params.append(f"%{ethnicity.strip()}%")
        idx += 1
    
    if country and country.strip():
        extra.append(f"LOWER({patient_alias}.country) LIKE LOWER(${idx})")
        params.append(f"%{country.strip()}%")
        idx += 1
    
    if arm_assigned and arm_assigned.strip():
        extra.append(f"LOWER({patient_alias}.arm_assigned) LIKE LOWER(${idx})")
        params.append(f"%{arm_assigned.strip()}%")
        idx += 1
    
    if disposition_status and disposition_status.strip():
        extra.append(f"LOWER({patient_alias}.disposition_status) LIKE LOWER(${idx})")
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
) -> str:
    """Create a standardized tool response JSON string."""
    response: dict[str, Any] = {"status": status}
    if data is not None:
        response["data"] = data
    if metadata:
        response["metadata"] = metadata
    if error:
        response["error"] = error
    if code:
        response["code"] = code

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


def success_response(data: Any, metadata: dict | None = None) -> str:
    return make_tool_response("success", data=data, metadata=metadata)


def error_response(message: str, code: str = "ERROR") -> str:
    return make_tool_response("error", error=message, code=code)