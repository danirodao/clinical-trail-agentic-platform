"""
Phase 6: Query input sanitization and prompt injection detection.
Applied before the query reaches the LLM.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .error_handler import AgentError, AgentErrorCode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_QUERY_LENGTH   = 2_000    # characters
MIN_QUERY_LENGTH   = 3

# Phrases that indicate prompt injection attempts
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.I),
    re.compile(r"disregard\s+(all\s+)?previous\s+instructions?", re.I),
    re.compile(r"you\s+are\s+now\s+", re.I),
    re.compile(r"act\s+as\s+(if\s+)?you\s+are\s+", re.I),
    re.compile(r"forget\s+(everything|your\s+instructions)", re.I),
    re.compile(r"(system|hidden|secret)\s+prompt", re.I),
    re.compile(r"jailbreak", re.I),
    re.compile(r"<\s*system\s*>", re.I),           # Fake system tags
    re.compile(r"\[INST\]|\[\/INST\]", re.I),       # Llama-style injection
    re.compile(r"###\s*instruction", re.I),          # Instruction override pattern
    re.compile(r"reveal\s+(your\s+)?(system\s+prompt|instructions)", re.I),
    re.compile(r"print\s+(your\s+)?(system\s+prompt|instructions)", re.I),
    re.compile(r"access_context|bearer\s+token|mcp_bearer", re.I),  # Probe for internals
]

# SQL injection indicators in natural language queries
_SQL_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r";\s*(drop|delete|truncate|update|insert|alter)\s+", re.I),
    re.compile(r"union\s+select", re.I),
    re.compile(r"--\s*$", re.M),
    re.compile(r"/\*.*?\*/", re.S),
    re.compile(r"xp_cmdshell", re.I),
    re.compile(r"information_schema", re.I),
]


@dataclass
class ValidationResult:
    is_valid: bool
    sanitized_query: str
    rejection_reason: str | None = None


def validate_query(raw_query: str) -> ValidationResult:
    """
    Validates and sanitizes a researcher's natural language query.

    Steps:
    1. Type and length checks
    2. Unicode normalization (prevent homoglyph attacks)
    3. Prompt injection detection
    4. SQL injection pattern detection
    5. Light sanitization (strip excess whitespace, control chars)

    Returns ValidationResult. Raises AgentError on hard failures.
    """
    # --- Type check ---
    if not isinstance(raw_query, str):
        raise AgentError(
            code=AgentErrorCode.INPUT_INVALID,
            message="Query must be a text string.",
        )

    # --- Length checks ---
    if len(raw_query.strip()) < MIN_QUERY_LENGTH:
        raise AgentError(
            code=AgentErrorCode.INPUT_INVALID,
            message="Query is too short. Please ask a complete question.",
        )
    if len(raw_query) > MAX_QUERY_LENGTH:
        raise AgentError(
            code=AgentErrorCode.INPUT_INVALID,
            message=f"Query exceeds maximum length of {MAX_QUERY_LENGTH} characters. Please shorten your question.",
        )

    # --- Unicode normalization (NFC) — prevents homoglyph injection ---
    normalized = unicodedata.normalize("NFC", raw_query)

    # --- Strip control characters (keep tabs and newlines for readability) ---
    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", normalized)

    # --- Collapse excessive whitespace ---
    sanitized = re.sub(r"\s{3,}", "  ", sanitized).strip()

    # --- Prompt injection detection ---
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(sanitized):
            raise AgentError(
                code=AgentErrorCode.INPUT_INVALID,
                message="Your query contains patterns that cannot be processed. Please rephrase your clinical question.",
                detail=f"Injection pattern matched: {pattern.pattern}",
            )

    # --- SQL injection detection ---
    for pattern in _SQL_INJECTION_PATTERNS:
        if pattern.search(sanitized):
            raise AgentError(
                code=AgentErrorCode.INPUT_INVALID,
                message="Your query contains characters or patterns that cannot be processed. Please rephrase as a natural language question.",
                detail=f"SQL injection pattern matched: {pattern.pattern}",
            )

    return ValidationResult(is_valid=True, sanitized_query=sanitized)


# ---------------------------------------------------------------------------
# MCP tool input allowlist validator (used server-side in mcp_server/)
# ---------------------------------------------------------------------------

_VALID_PHASES = {"Phase 1", "Phase 2", "Phase 3", "Phase 4"}
_VALID_SEXES  = {"M", "F"}
_VALID_SEVERITIES = {"Mild", "Moderate", "Severe"}
_VALID_ACCESS_LEVELS = {"individual", "aggregate"}
_VALID_GROUP_BY = {
    "sex", "age_bucket", "country", "race", "ethnicity",
    "arm_assigned", "disposition_status", "phase",
}

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


def validate_uuid(value: str, field: str) -> str:
    """Validates a single UUID v4 string. Raises ValueError on failure."""
    if not UUID4_RE.match(value.strip()):
        raise ValueError(f"Invalid UUID format for field '{field}': {value!r}")
    return value.strip().lower()


def validate_uuid_list(values: list[str], field: str) -> list[str]:
    """Validates a list of UUID v4 strings."""
    return [validate_uuid(v, field) for v in values]


def validate_enum(value: str, allowed: set[str], field: str) -> str:
    """Validates a string against an allowlist."""
    if value not in allowed:
        raise ValueError(
            f"Invalid value for '{field}': {value!r}. "
            f"Allowed: {sorted(allowed)}"
        )
    return value


def validate_positive_int(value: int, field: str, max_val: int = 10_000) -> int:
    """Validates a non-negative integer within a reasonable range."""
    if not isinstance(value, int) or value < 0 or value > max_val:
        raise ValueError(f"'{field}' must be an integer between 0 and {max_val}.")
    return value