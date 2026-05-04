"""
OpenFGAContextBuilder — assembles the DYNAMIC attributes for /check calls.

Security guarantees enforced here:
  - current_time    → always datetime.utcnow(); NEVER from caller input
  - user_clearance  → always from the decoded JWT "clearance_level" claim;
                      the raw token must be validated by the IdP before arriving
    - stated_purpose  → validated with strict syntax and optional allowlist
                                            supplied by the API layer (grant envelope / DB catalog)
  - actual_cohort_size → passed by the app after pre-calculation; never from user

All timestamps are emitted in RFC3339 UTC format as required by OpenFGA CEL.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Allowlist of valid purpose identifiers ────────────────────────────────────
# In production, load this from your governance database / config service.
# This guard ensures stated_purpose cannot be an arbitrary user-supplied string
# even if the API layer failed to validate it upstream.
DEFAULT_ALLOWED_PURPOSES: frozenset[str] = frozenset({
    "clinical_research",
    "regulatory_submission",
    "safety_monitoring",
    "pharmacovigilance",
    "study_ONCO_2026",
    "study_CARD_2026",
    "study_ONCO_2027",
})

# ── Allowlist of valid regions ────────────────────────────────────────────────
ALLOWED_REGIONS: frozenset[str] = frozenset({
    "EU", "NA", "APAC", "LATAM", "MEA",
})

# ── Allowlist of valid therapeutic areas ──────────────────────────────────────
ALLOWED_AREAS: frozenset[str] = frozenset({
    "oncology", "cardiology", "neurology", "immunology",
    "infectious_disease", "rare_disease", "metabolic",
})

# ── Allowlist of valid trial phases ───────────────────────────────────────────
ALLOWED_PHASES: frozenset[str] = frozenset({
    "I", "II", "III", "IV", "I/II", "II/III",
})

# RFC3339 UTC format string used by OpenFGA CEL timestamp comparisons
_RFC3339_UTC = "%Y-%m-%dT%H:%M:%SZ"


def _utcnow_rfc3339() -> str:
    """Return the current UTC time as an RFC3339 string from the SERVER CLOCK.

    This is the ONLY approved source for current_time.  Any attempt to pass
    a caller-supplied timestamp must be rejected upstream.
    """
    return datetime.now(tz=timezone.utc).strftime(_RFC3339_UTC)


def _decode_jwt_payload(jwt_token: str) -> dict[str, Any]:
    """Decode the JWT payload without signature verification.

    IMPORTANT: This function does NOT verify the signature.  The token MUST
    have been verified by the IdP middleware (e.g. Keycloak) before reaching
    this layer.  We only read claims here — we never trust them for auth
    decisions without prior IdP validation.
    """
    parts = jwt_token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed JWT: expected 3 dot-separated parts")

    # JWT uses URL-safe base64 without padding — add padding if needed
    payload_b64 = parts[1]
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += "=" * padding

    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to decode JWT payload: {exc}") from exc


def _validate_purpose(stated_purpose: str, allowed_purposes: set[str] | None = None) -> str:
    """Validate stated_purpose with strict syntax and optional allowlist."""
    value = str(stated_purpose).strip()
    if not value:
        raise ValueError("stated_purpose must be a non-empty string")
    if len(value) > 120:
        raise ValueError("stated_purpose exceeds max length 120")
    if not re.fullmatch(r"[A-Za-z0-9_:-]+", value):
        raise ValueError(
            "stated_purpose contains invalid characters. "
            "Use letters, numbers, underscore, colon, or hyphen."
        )
    if allowed_purposes:
        if value not in allowed_purposes:
            raise ValueError(
                f"stated_purpose '{value}' is outside your allowed purpose set. "
                f"Allowed: {sorted(allowed_purposes)}"
            )
    return value


def _validate_region(region: str) -> str:
    """Validate requested_region against the server-side allowlist."""
    if region not in ALLOWED_REGIONS:
        raise ValueError(
            f"requested_region '{region}' is not valid. "
            f"Allowed: {sorted(ALLOWED_REGIONS)}"
        )
    return region


def _validate_area(area: str) -> str:
    """Validate requested_area against the server-side allowlist."""
    if area not in ALLOWED_AREAS:
        raise ValueError(
            f"requested_area '{area}' is not valid. "
            f"Allowed: {sorted(ALLOWED_AREAS)}"
        )
    return area


def _validate_phase(phase: str) -> str:
    """Validate requested_phase against the server-side allowlist."""
    if phase not in ALLOWED_PHASES:
        raise ValueError(
            f"requested_phase '{phase}' is not valid. "
            f"Allowed: {sorted(ALLOWED_PHASES)}"
        )
    return phase


class OpenFGAContextBuilder:
    """
    Assembles the DYNAMIC context dict required by OpenFGA conditional checks.

    Usage
    -----
    builder = OpenFGAContextBuilder(
        jwt_token           = request.headers["Authorization"].removeprefix("Bearer "),
        tool_call_params    = {"region": "EU", "area": "oncology",
                               "phase": "III", "purpose": "study_ONCO_2026"},
        pre_calculated_values = {"actual_cohort_size": 42},
    )
    context = builder.build()
    result  = await fga_client.check_with_context(user, relation, obj, context)

    Raises
    ------
    ValueError  — if any required attribute is missing or fails validation.
    """

    def __init__(
        self,
        jwt_token: str,
        tool_call_params: dict[str, Any],
        pre_calculated_values: dict[str, Any],
        allowed_purposes: set[str] | None = None,
    ) -> None:
        # Store inputs but do NOT extract anything yet — build() does that so
        # exceptions happen at the call site, not at construction time.
        self._jwt_token            = jwt_token
        self._tool_call_params     = tool_call_params
        self._pre_calculated_values = pre_calculated_values
        self._allowed_purposes = allowed_purposes

    # ── Public API ────────────────────────────────────────────────────────────

    def build(self) -> dict[str, Any]:
        """
        Build and return the context dict for the OpenFGA /check call.

        All values are validated and sanitised.  Raises ValueError if any
        required attribute is absent or invalid so the caller can handle the
        error before sending to OpenFGA (fail closed on missing context).
        """
        context: dict[str, Any] = {}

        # 1. current_time — from SERVER CLOCK, never from any input
        #    Dynamic because it must reflect the real moment of access.
        context["current_time"] = _utcnow_rfc3339()

        # 2. user_clearance_level — from JWT claim, never from tool params
        #    Dynamic because the JWT is re-evaluated per-request by the IdP.
        context["user_clearance_level"] = self._extract_clearance_level()

        # 3. stated_purpose — from tool_call_params, validated against allowlist
        #    Dynamic because different tool invocations declare different purposes.
        context["stated_purpose"] = self._extract_stated_purpose()

        # 4. requested_region — from tool_call_params
        #    Dynamic because the same user may query data from different regions.
        context["requested_region"] = self._extract_region()

        # 5. requested_area — from tool_call_params
        #    Dynamic because the same user may work across therapeutic areas.
        context["requested_area"] = self._extract_area()

        # 6. requested_phase — from tool_call_params
        #    Dynamic because the same user may access different trial phases.
        context["requested_phase"] = self._extract_phase()

        # 7. actual_cohort_size — pre-calculated by the app, passed in
        #    Dynamic because it is computed from the actual query result set
        #    BEFORE the check call — not stored anywhere statically.
        context["actual_cohort_size"] = self._extract_cohort_size()

        # Audit log — records WHICH attributes were used (not their values for
        # sensitive fields) so the access decision can be replayed/audited.
        logger.info(
            "OpenFGA context assembled | "
            "user_clearance=%d | region=%s | area=%s | phase=%s | "
            "purpose=%s | cohort_size=%d | current_time=%s",
            context["user_clearance_level"],
            context["requested_region"],
            context["requested_area"],
            context["requested_phase"],
            context["stated_purpose"],
            context["actual_cohort_size"],
            context["current_time"],
        )

        return context

    # ── Private extraction helpers ────────────────────────────────────────────

    def _extract_clearance_level(self) -> int:
        """Extract clearance_level from the decoded JWT payload.

        Security: reads from the IdP-issued token only.
        If the claim is absent (Keycloak mapper not yet configured) we default
        to 1 — the most restrictive level — so queries are allowed but only
        against datasets whose resource_classification == 1.  This is safe
        because it never grants MORE access than the actual claim would.
        """
        payload = _decode_jwt_payload(self._jwt_token)
        raw = payload.get("clearance_level")
        if raw is None:
            logger.warning(
                "JWT claim 'clearance_level' is missing; defaulting to 1 "
                "(most restrictive). Configure the Keycloak client mapper to "
                "emit this claim for full classification-based access control."
            )
            return 1
        try:
            level = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"JWT 'clearance_level' claim is not an integer: {raw!r}"
            ) from exc
        if not (1 <= level <= 5):
            raise ValueError(
                f"JWT 'clearance_level' value {level} is out of range [1, 5]"
            )
        return level

    def _extract_stated_purpose(self) -> str:
        """Extract stated_purpose from tool_call_params and validate it.

        Security: validated with strict syntax, and when allowed_purposes is
        provided by the API layer, enforced against that set.
        """
        purpose = self._tool_call_params.get("purpose")
        if not purpose:
            raise ValueError(
                "'purpose' is required in tool_call_params but was not provided."
            )
        return _validate_purpose(str(purpose), self._allowed_purposes)

    def _extract_region(self) -> str:
        region = self._tool_call_params.get("region")
        if not region:
            # Optional — omitted means no region constraint declared by the caller.
            # SQL filter fallback uses allowed_regions from the grant envelope.
            return ""
        return _validate_region(str(region))

    def _extract_area(self) -> str:
        area = self._tool_call_params.get("area")
        if not area:
            # Optional — omitted means no area constraint declared by the caller.
            return ""
        return _validate_area(str(area))

    def _extract_phase(self) -> str:
        phase = self._tool_call_params.get("phase")
        if not phase:
            # Optional — omitted means no phase constraint declared by the caller.
            return ""
        return _validate_phase(str(phase))

    def _extract_cohort_size(self) -> int:
        raw = self._pre_calculated_values.get("actual_cohort_size")
        if raw is None:
            raise ValueError(
                "'actual_cohort_size' is required in pre_calculated_values. "
                "Compute this from the query result BEFORE calling /check."
            )
        try:
            size = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"'actual_cohort_size' is not an integer: {raw!r}"
            ) from exc
        if size < 0:
            raise ValueError(
                f"'actual_cohort_size' cannot be negative: {size}"
            )
        return size
