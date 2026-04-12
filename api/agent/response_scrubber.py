"""
Phase 6: Post-LLM response scrubber.

Detects and redacts patient UUIDs that should not appear in aggregate-only
responses. Enforces the ceiling principle at the output layer as a final
safety net, even if the LLM or a tool malfunctioned.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Matches any UUID v4 (lowercase or uppercase)
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}"
    r"-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)

_REDACTION_PLACEHOLDER = "[REDACTED-PATIENT-ID]"


@dataclass
class ScrubResult:
    scrubbed_text: str
    redaction_count: int
    redacted_uuids: list[str] = field(default_factory=list)
    was_modified: bool = False


def scrub_patient_ids(
    text: str,
    allowed_uuids: set[str],  # Trial IDs + any other non-patient UUIDs that ARE allowed
) -> ScrubResult:
    """
    Scans LLM output for UUID patterns.
    Any UUID NOT in `allowed_uuids` is redacted.

    `allowed_uuids` should contain:
      - All trial_ids the researcher can access
      - cohort_ids
      - Any other non-patient UUIDs that legitimately appear in responses

    Patient UUIDs should never be in allowed_uuids for aggregate-only researchers.
    For individual-access researchers, patient UUIDs are allowed but we still
    log them for audit purposes.
    """
    found_uuids = _UUID_RE.findall(text)
    if not found_uuids:
        return ScrubResult(scrubbed_text=text, redaction_count=0)

    redacted: list[str] = []
    result_text = text

    for uuid in set(found_uuids):  # deduplicate
        normalized = uuid.lower()
        if normalized not in {u.lower() for u in allowed_uuids}:
            result_text = result_text.replace(uuid, _REDACTION_PLACEHOLDER)
            redacted.append(uuid)

    if redacted:
        logger.warning(
            "Response scrubber redacted %d UUID(s) not in allowed set: %s",
            len(redacted),
            redacted[:5],  # log first 5 only
        )

    return ScrubResult(
        scrubbed_text=result_text,
        redaction_count=len(redacted),
        redacted_uuids=redacted,
        was_modified=bool(redacted),
    )


def build_allowed_uuid_set(access_profile) -> set[str]:
    """
    Builds the set of UUIDs that are safe to appear in LLM responses.
    Only includes trial-level and cohort-level IDs, never patient IDs.
    """
    allowed: set[str] = set()

    # All authorized trial IDs
    for tid in access_profile.allowed_trial_ids:
        allowed.add(tid.lower())

    # Cohort IDs from trial scopes
    for scope in access_profile.trial_scopes.values():
        for cohort_scope in scope.cohort_scopes:
            allowed.add(cohort_scope.cohort_id.lower())

    return allowed