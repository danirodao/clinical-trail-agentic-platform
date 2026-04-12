"""
Phase 6: Server-side tool input validation for the MCP server.
Validates all tool parameters against allowlists before any DB query runs.
Prevents SQL injection and malformed inputs reaching the database layer.
"""
from __future__ import annotations

import re
from typing import Any

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)

VALID_PHASES        = {"Phase 1", "Phase 2", "Phase 3", "Phase 4"}
VALID_STATUSES      = {"Recruiting", "Completed", "Active, not recruiting", "Withdrawn", "Terminated", "Suspended"}
VALID_SEXES         = {"M", "F"}
VALID_SEVERITIES    = {"Mild", "Moderate", "Severe"}
VALID_CRITERIA_TYPES = {"inclusion", "exclusion"}
VALID_MEASURE_TYPES  = {"primary", "secondary"}
VALID_GROUP_BY_FIELDS = {
    "sex", "age_bucket", "country", "race", "ethnicity",
    "arm_assigned", "disposition_status", "phase", "therapeutic_area",
}
VALID_COMPARISON_METRICS = {"adverse_events", "demographics", "completion", "lab_results"}
MAX_LIMIT = 500
MAX_QUERY_CHARS = 1_000


class ToolInputError(ValueError):
    """Raised when tool input fails validation. Message is user-safe."""
    pass


def validate_uuid(value: str, field: str = "id") -> str:
    if not isinstance(value, str) or not UUID4_RE.match(value.strip()):
        raise ToolInputError(f"Invalid UUID for '{field}': {value!r}")
    return value.strip().lower()


def validate_uuid_list(values: Any, field: str = "trial_ids") -> list[str]:
    if not isinstance(values, list):
        raise ToolInputError(f"'{field}' must be a list of UUIDs.")
    if len(values) > 50:
        raise ToolInputError(f"'{field}' list exceeds maximum of 50 items.")
    return [validate_uuid(v, field) for v in values]


def validate_enum_list(values: Any, allowed: set[str], field: str) -> list[str]:
    if not isinstance(values, list):
        raise ToolInputError(f"'{field}' must be a list.")
    invalid = [v for v in values if v not in allowed]
    if invalid:
        raise ToolInputError(
            f"Invalid values for '{field}': {invalid}. "
            f"Allowed: {sorted(allowed)}"
        )
    return values


def validate_group_by(value: Any) -> str | None:
    if value is None:
        return None
    if value not in VALID_GROUP_BY_FIELDS:
        raise ToolInputError(
            f"Invalid group_by value: {value!r}. "
            f"Allowed: {sorted(VALID_GROUP_BY_FIELDS)}"
        )
    return value


def validate_limit(value: Any, default: int = 100) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or value < 1 or value > MAX_LIMIT:
        raise ToolInputError(f"'limit' must be an integer between 1 and {MAX_LIMIT}.")
    return value


def validate_query_text(value: Any, field: str = "query") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"'{field}' must be a non-empty string.")
    if len(value) > MAX_QUERY_CHARS:
        raise ToolInputError(f"'{field}' exceeds maximum of {MAX_QUERY_CHARS} characters.")
    return value.strip()


def validate_age_range(age_min: Any, age_max: Any) -> tuple[int | None, int | None]:
    if age_min is not None:
        if not isinstance(age_min, int) or age_min < 0 or age_min > 120:
            raise ToolInputError("'age_min' must be an integer between 0 and 120.")
    if age_max is not None:
        if not isinstance(age_max, int) or age_max < 0 or age_max > 120:
            raise ToolInputError("'age_max' must be an integer between 0 and 120.")
    if age_min is not None and age_max is not None and age_min > age_max:
        raise ToolInputError("'age_min' cannot be greater than 'age_max'.")
    return age_min, age_max