"""
CeilingValidator — enforces Tier 1 → Tier 2 delegation constraints.

The principle:
  Tier 1 (ceiling)   — broad access grant set by the DDO / governance approver
                        for an organisation or a named approver on a data_product.
                        Represents the MAXIMUM scope ever permitted.

  Tier 2 (delegation) — narrower access delegated by a team lead to an individual
                         researcher.  Conditions MUST be a STRICT SUBSET of the
                         corresponding Tier 1 ceiling.  No attribute may exceed
                         (broaden) the ceiling.

Validation rules enforced:
  ① valid_from  of Tier 2 must be ≥ Tier 1 valid_from   (can't start earlier)
  ② valid_until of Tier 2 must be ≤ Tier 1 valid_until  (can't expire later)
  ③ permitted_regions  of Tier 2 must be ⊆ Tier 1        (region subset only)
  ④ permitted_areas    of Tier 2 must be ⊆ Tier 1        (area subset only)
  ⑤ permitted_phases   of Tier 2 must be ⊆ Tier 1        (phase subset only)
  ⑥ approved_purposes  of Tier 2 must be ⊆ Tier 1        (purpose subset only)
  ⑦ resource_classification Tier 2 ≥ Tier 1              (can't lower the bar)
  ⑧ minimum_cohort_size     Tier 2 ≥ Tier 1              (can't weaken k-anon)
  ⑨ Both valid_from and valid_until must be present and non-empty

Usage
-----
validator = CeilingValidator(fga_client)
violations = await validator.validate(
    tier1_user       = "organization:org-pharma-corp",
    tier1_relation   = "approved_consumer",
    data_product_id  = "dp-oncology-2026",
    tier2_conditions = {
        "valid_from":              "2026-03-01T00:00:00Z",
        "valid_until":             "2026-09-30T23:59:59Z",
        "permitted_regions":       ["EU"],
        "permitted_areas":         ["oncology"],
        "permitted_phases":        ["III"],
        "approved_purposes":       ["study_ONCO_2026"],
        "resource_classification": 3,
        "minimum_cohort_size":     10,
    },
)

if violations:
    raise PermissionError(f"Delegation exceeds ceiling: {violations}")

# Safe to write Tier 2 tuple
await fga_client.write_conditional_tuples([...])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from auth.openfga_client import OpenFGAClient

logger = logging.getLogger(__name__)

# RFC3339 UTC format used for all timestamp comparisons
_RFC3339_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_ts(value: str, label: str) -> datetime:
    """Parse an RFC3339 UTC timestamp string into a timezone-aware datetime."""
    for fmt in (_RFC3339_FMT, "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(
        f"Cannot parse timestamp for '{label}': {value!r}. "
        "Expected RFC3339 UTC format, e.g. 2026-01-01T00:00:00Z"
    )


@dataclass
class CeilingViolation:
    """Describes a single constraint that was broken by the Tier 2 proposal."""
    rule:        str   # Short rule identifier, e.g. "valid_until"
    message:     str   # Human-readable explanation
    ceiling_val: object = None  # Tier 1 value for reference
    proposed_val: object = None  # Tier 2 proposed value


@dataclass
class CeilingValidationResult:
    """Complete result of a ceiling validation run."""
    is_valid:   bool
    violations: list[CeilingViolation] = field(default_factory=list)
    ceiling_context: Optional[dict] = None   # Tier 1 conditions that were read

    def raise_if_invalid(self) -> None:
        """Convenience: raise PermissionError if there are violations."""
        if not self.is_valid:
            msgs = "; ".join(v.message for v in self.violations)
            raise PermissionError(
                f"Tier 2 delegation exceeds Tier 1 ceiling ({len(self.violations)} "
                f"violation(s)): {msgs}"
            )


class CeilingValidator:
    """
    Validates that Tier 2 delegation conditions are a subset of the Tier 1
    ceiling conditions stored in OpenFGA.

    Parameters
    ----------
    fga_client : OpenFGAClient
        The shared async OpenFGA client used to read ceiling tuples.
    """

    def __init__(self, fga_client: OpenFGAClient) -> None:
        self._fga = fga_client

    # ── Public API ────────────────────────────────────────────────────────────

    async def validate(
        self,
        tier1_user: str,
        tier1_relation: str,
        data_product_id: str,
        tier2_conditions: dict,
        use_case: Optional[str] = None,
    ) -> CeilingValidationResult:
        """
        Fetch the Tier 1 ceiling tuple and validate tier2_conditions against it.

        Parameters
        ----------
        tier1_user : str
            The user/org that holds the Tier 1 tuple.
            e.g. "organization:org-pharma-corp"
        tier1_relation : str
            The relation name for the Tier 1 ceiling.
            e.g. "approved_consumer"
        data_product_id : str
            The data_product object ID (without type prefix).
            e.g. "dp-oncology-2026"
        tier2_conditions : dict
            The proposed STATIC condition context for the Tier 2 tuple.
        use_case : str, optional
            If provided, validates that tier2 approved_purposes contains this
            use_case and that it is also present in the Tier 1 ceiling.

        Returns
        -------
        CeilingValidationResult
            Contains is_valid flag and a list of CeilingViolation items.
        """
        object_key = f"data_product:{data_product_id}"

        # ── Step 1: Read the Tier 1 ceiling tuple from OpenFGA ────────────────
        ceiling_condition = await self._fga.read_tuple_conditions(
            user     = tier1_user,
            relation = tier1_relation,
            object   = object_key,
        )

        if ceiling_condition is None:
            # No ceiling tuple found — delegation is not allowed at all
            logger.warning(
                "CeilingValidator: no Tier 1 tuple found for "
                "%s | %s | %s — delegation rejected",
                tier1_user, tier1_relation, object_key,
            )
            return CeilingValidationResult(
                is_valid=False,
                violations=[
                    CeilingViolation(
                        rule="ceiling_missing",
                        message=(
                            f"No Tier 1 ceiling tuple found for "
                            f"({tier1_user}, {tier1_relation}, {object_key}). "
                            "A governance approver must create the ceiling before "
                            "delegation is possible."
                        ),
                    )
                ],
            )

        ceiling_ctx: dict = ceiling_condition.get("context", {})
        logger.info(
            "CeilingValidator: read Tier 1 ceiling for %s on %s",
            tier1_user, object_key,
        )

        # ── Step 2: Run all validation rules ──────────────────────────────────
        violations: list[CeilingViolation] = []

        self._check_timestamps(ceiling_ctx, tier2_conditions, violations)
        self._check_list_subset("permitted_regions",  ceiling_ctx, tier2_conditions, violations)
        self._check_list_subset("permitted_areas",    ceiling_ctx, tier2_conditions, violations)
        self._check_list_subset("permitted_phases",   ceiling_ctx, tier2_conditions, violations)
        self._check_list_subset("approved_purposes",  ceiling_ctx, tier2_conditions, violations)
        self._check_int_gte("resource_classification", ceiling_ctx, tier2_conditions, violations)
        self._check_int_gte("minimum_cohort_size",     ceiling_ctx, tier2_conditions, violations)

        # Optional use_case scope check
        if use_case:
            self._check_use_case_in_scope(
                use_case, ceiling_ctx, tier2_conditions, violations
            )

        is_valid = len(violations) == 0

        if is_valid:
            logger.info(
                "CeilingValidator: Tier 2 conditions are valid subset of ceiling "
                "for %s on %s",
                tier1_user, object_key,
            )
        else:
            logger.warning(
                "CeilingValidator: %d violation(s) found for proposed Tier 2 "
                "delegation on %s: %s",
                len(violations),
                object_key,
                [v.rule for v in violations],
            )

        return CeilingValidationResult(
            is_valid        = is_valid,
            violations      = violations,
            ceiling_context = ceiling_ctx,
        )

    # ── Validation rule helpers ───────────────────────────────────────────────

    def _check_timestamps(
        self,
        ceiling: dict,
        proposed: dict,
        violations: list[CeilingViolation],
    ) -> None:
        """Rules ① and ②: temporal window of Tier 2 must be within Tier 1."""
        # Tier 2 must provide both fields
        for field_name in ("valid_from", "valid_until"):
            if not proposed.get(field_name):
                violations.append(CeilingViolation(
                    rule        = field_name,
                    message     = f"'{field_name}' is required in Tier 2 conditions.",
                    proposed_val= proposed.get(field_name),
                ))
        if len([v for v in violations if v.rule in ("valid_from", "valid_until")]) > 0:
            return  # Skip comparison if fields are missing

        c_from  = _parse_ts(ceiling["valid_from"],  "ceiling valid_from")
        c_until = _parse_ts(ceiling["valid_until"], "ceiling valid_until")
        p_from  = _parse_ts(proposed["valid_from"], "proposed valid_from")
        p_until = _parse_ts(proposed["valid_until"], "proposed valid_until")

        # Rule ①: Tier 2 cannot start before Tier 1
        if p_from < c_from:
            violations.append(CeilingViolation(
                rule         = "valid_from",
                message      = (
                    f"Tier 2 valid_from ({proposed['valid_from']}) is earlier than "
                    f"the Tier 1 ceiling ({ceiling['valid_from']}). "
                    "Delegation cannot start before the ceiling approval begins."
                ),
                ceiling_val  = ceiling["valid_from"],
                proposed_val = proposed["valid_from"],
            ))

        # Rule ②: Tier 2 cannot expire after Tier 1
        if p_until > c_until:
            violations.append(CeilingViolation(
                rule         = "valid_until",
                message      = (
                    f"Tier 2 valid_until ({proposed['valid_until']}) is later than "
                    f"the Tier 1 ceiling ({ceiling['valid_until']}). "
                    "Delegation cannot outlive the ceiling approval."
                ),
                ceiling_val  = ceiling["valid_until"],
                proposed_val = proposed["valid_until"],
            ))

        # Sanity: Tier 2 window must be internally consistent
        if p_from >= p_until:
            violations.append(CeilingViolation(
                rule         = "valid_window",
                message      = (
                    f"Tier 2 valid_from ({proposed['valid_from']}) is not before "
                    f"valid_until ({proposed['valid_until']}). Invalid time window."
                ),
                proposed_val = f"{proposed['valid_from']} → {proposed['valid_until']}",
            ))

    def _check_list_subset(
        self,
        field_name: str,
        ceiling: dict,
        proposed: dict,
        violations: list[CeilingViolation],
    ) -> None:
        """Rules ③–⑥: list attributes of Tier 2 must be subsets of Tier 1."""
        ceiling_vals  = set(ceiling.get(field_name) or [])
        proposed_vals = set(proposed.get(field_name) or [])

        if not proposed_vals:
            violations.append(CeilingViolation(
                rule         = field_name,
                message      = f"'{field_name}' is empty in Tier 2 conditions — at least one value is required.",
                proposed_val = [],
            ))
            return

        extras = proposed_vals - ceiling_vals
        if extras:
            violations.append(CeilingViolation(
                rule         = field_name,
                message      = (
                    f"Tier 2 '{field_name}' contains values not in the Tier 1 ceiling: "
                    f"{sorted(extras)}. Ceiling allows: {sorted(ceiling_vals)}."
                ),
                ceiling_val  = sorted(ceiling_vals),
                proposed_val = sorted(proposed_vals),
            ))

    def _check_int_gte(
        self,
        field_name: str,
        ceiling: dict,
        proposed: dict,
        violations: list[CeilingViolation],
    ) -> None:
        """Rules ⑦–⑧: integer attributes of Tier 2 must be ≥ Tier 1 ceiling.

        For resource_classification: higher value = more sensitive restriction.
        Tier 2 can require HIGHER clearance but must not LOWER the bar.

        For minimum_cohort_size: Tier 2 must enforce AT LEAST the same k-anon
        floor — it cannot weaken the privacy guard.
        """
        ceiling_val  = ceiling.get(field_name)
        proposed_val = proposed.get(field_name)

        if proposed_val is None:
            violations.append(CeilingViolation(
                rule         = field_name,
                message      = f"'{field_name}' is required in Tier 2 conditions.",
                proposed_val = None,
            ))
            return

        try:
            c_int = int(ceiling_val)
            p_int = int(proposed_val)
        except (TypeError, ValueError):
            violations.append(CeilingViolation(
                rule         = field_name,
                message      = f"'{field_name}' must be an integer. Got ceiling={ceiling_val!r}, proposed={proposed_val!r}.",
                ceiling_val  = ceiling_val,
                proposed_val = proposed_val,
            ))
            return

        if p_int < c_int:
            violations.append(CeilingViolation(
                rule         = field_name,
                message      = (
                    f"Tier 2 '{field_name}' ({p_int}) is less than the Tier 1 "
                    f"ceiling ({c_int}). Delegation cannot lower this threshold."
                ),
                ceiling_val  = c_int,
                proposed_val = p_int,
            ))

    def _check_use_case_in_scope(
        self,
        use_case: str,
        ceiling: dict,
        proposed: dict,
        violations: list[CeilingViolation],
    ) -> None:
        """Optional: verify the specified use_case is approved in both tiers."""
        ceiling_purposes  = set(ceiling.get("approved_purposes") or [])
        proposed_purposes = set(proposed.get("approved_purposes") or [])

        if use_case not in ceiling_purposes:
            violations.append(CeilingViolation(
                rule         = "use_case_not_in_ceiling",
                message      = (
                    f"use_case '{use_case}' is not in the Tier 1 ceiling's "
                    f"approved_purposes: {sorted(ceiling_purposes)}."
                ),
                ceiling_val  = sorted(ceiling_purposes),
                proposed_val = use_case,
            ))

        if use_case not in proposed_purposes:
            violations.append(CeilingViolation(
                rule         = "use_case_not_in_delegation",
                message      = (
                    f"use_case '{use_case}' must be present in the Tier 2 "
                    f"approved_purposes: {sorted(proposed_purposes)}."
                ),
                proposed_val = sorted(proposed_purposes),
            ))
