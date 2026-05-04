"""
Researcher endpoints — query data with two-layer authorization.

Phase 6 hardened:
  • Input validation + prompt-injection detection on every query
  • Response scrubbing (patient UUID redaction) on every answer
  • Circuit-breaker check before hitting the agent
  • Audit trail via AuditLogMiddleware (automatic, no code needed here)
  • Rate limiting via RateLimitMiddleware (automatic, no code needed here)
  • Suggested-questions endpoint
  • Conversation history endpoint (LangGraph checkpoint reader)
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from auth.dependencies import CurrentUser, require_role, get_current_user
from auth.middleware import UserContext
from auth.authorization_service import AuthorizationService
from auth.openfga_client import get_openfga_client
from auth.openfga.context_builder import (
    OpenFGAContextBuilder,
    DEFAULT_ALLOWED_PURPOSES,
    ALLOWED_REGIONS,
    ALLOWED_AREAS,
    ALLOWED_PHASES,
)
from api.database import get_db_pool

from api.agent.models import QueryRequest, QueryResponse
from api.agent.service import AgentService
from api.agent.input_validator import validate_query
from api.agent.response_scrubber import scrub_patient_ids, build_allowed_uuid_set
from api.agent.suggested_questions import generate_suggested_questions
from api.agent.error_handler import (
    AgentError,
    AgentErrorCode,
    mcp_circuit_breaker,
)

router = APIRouter()
logger = logging.getLogger(__name__)

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

_CANONICAL_REGION_LITERALS = {
    "EU": ["EU", "Europe"],
    "NA": ["NA", "North America"],
    "APAC": ["APAC", "Asia-Pacific", "Asia Pacific"],
    "LATAM": ["LATAM", "Latin America"],
    "MEA": ["MEA", "Middle East and Africa"],
}

PURPOSE_MISMATCH_MODE = os.environ.get("PURPOSE_MISMATCH_MODE", "block").strip().lower()
GRANT_ENVELOPE_CACHE_TTL_SECONDS = int(
    os.environ.get("GRANT_ENVELOPE_CACHE_TTL_SECONDS", "60")
)

# Key: user|org|sorted(trials). Value: (expires_at_monotonic, envelope)
_GRANT_ENVELOPE_CACHE: dict[str, tuple[float, dict[str, set[str]]]] = {}


def _normalize_region_value(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    canonical = _REGION_ALIASES.get(raw.lower().replace("-", " "), raw.upper())
    return canonical if canonical in ALLOWED_REGIONS else raw


def _normalize_area_value(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return raw.replace(" ", "_").replace("-", "_")


def _normalize_phase_value(value: str) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    return raw.replace("PHASE ", "")


def _coerce_scope_values(scope: dict, keys: list[str]) -> list[str]:
    values: list[str] = []
    for key in keys:
        val = scope.get(key)
        if isinstance(val, list):
            values.extend(str(v).strip() for v in val if str(v).strip())
        elif isinstance(val, str) and val.strip():
            values.append(val.strip())
    deduped: list[str] = []
    for v in values:
        if v not in deduped:
            deduped.append(v)
    return deduped


def _region_literals_for_trial(
    canonical_regions: list[str],
    trial_region_values: list[str],
) -> list[str]:
    """
    Convert canonical region set (e.g., EU/LATAM) into trial-compatible
    literal values (e.g., Europe/Latin America) for SQL filtering and UI.
    """
    wanted = {r for r in canonical_regions if r}
    if not wanted:
        return []

    trial_literals: list[str] = []
    for raw in trial_region_values:
        lit = str(raw or "").strip()
        if not lit:
            continue
        if _normalize_region_value(lit) in wanted and lit not in trial_literals:
            trial_literals.append(lit)

    # Keep canonical fallback for robustness when trial metadata is sparse.
    for canonical in canonical_regions:
        if canonical and canonical not in trial_literals:
            trial_literals.append(canonical)

    # Add common display literals for canonical values (e.g., EU -> Europe)
    # so SQL filters still match patient.region labels when trial metadata lacks
    # explicit regions.
    for canonical in canonical_regions:
        for literal in _CANONICAL_REGION_LITERALS.get(canonical, []):
            if literal and literal not in trial_literals:
                trial_literals.append(literal)

    return trial_literals


def _infer_scope_from_prompt(prompt: str) -> dict[str, str]:
    """
    Lightweight deterministic scope inference from query text.

    This is a UX helper only.  Inferred values are always validated against
    OpenFGA grant envelope before use.
    """
    text = (prompt or "").lower()
    inferred: dict[str, str] = {}

    region_patterns: list[tuple[str, str]] = [
        (r"\b(eu|europe|european)\b", "EU"),
        (r"\b(north\s*america|na|usa|us|canada)\b", "NA"),
        (r"\b(apac|asia\s*pacific|asia-pacific)\b", "APAC"),
        (r"\b(latam|latin\s*america|south\s*america)\b", "LATAM"),
        (r"\b(mea|middle\s*east\s*(and)?\s*africa|middle\s*east|africa)\b", "MEA"),
    ]
    for pattern, region in region_patterns:
        if re.search(pattern, text):
            inferred["region"] = region
            break

    area_patterns: list[tuple[str, str]] = [
        (r"\boncology\b", "oncology"),
        (r"\bcardiology\b", "cardiology"),
        (r"\bneurology\b", "neurology"),
        (r"\bimmunology\b", "immunology"),
        (r"\binfectious\s*disease\b", "infectious_disease"),
        (r"\brare\s*disease\b", "rare_disease"),
        (r"\bmetabolic\b", "metabolic"),
    ]
    for pattern, area in area_patterns:
        if re.search(pattern, text):
            inferred["area"] = area
            break

    phase_patterns: list[tuple[str, str]] = [
        (r"\bphase\s*i/ii\b|\bphase\s*1/2\b", "I/II"),
        (r"\bphase\s*ii/iii\b|\bphase\s*2/3\b", "II/III"),
        (r"\bphase\s*iv\b|\bphase\s*4\b", "IV"),
        (r"\bphase\s*iii\b|\bphase\s*3\b", "III"),
        (r"\bphase\s*ii\b|\bphase\s*2\b", "II"),
        (r"\bphase\s*i\b|\bphase\s*1\b", "I"),
    ]
    for pattern, phase in phase_patterns:
        if re.search(pattern, text):
            inferred["phase"] = phase
            break

    purpose_patterns: list[tuple[str, str]] = [
        (r"\bpharmacovigilance\b", "pharmacovigilance"),
        (r"\bsafety\s*monitoring\b|\bsafety\b", "safety_monitoring"),
        (r"\bregulatory\s*submission\b|\bregulatory\b", "regulatory_submission"),
        (r"\bonco\s*2026\b", "study_ONCO_2026"),
        (r"\bcard\s*2026\b", "study_CARD_2026"),
    ]
    for pattern, purpose in purpose_patterns:
        if re.search(pattern, text):
            inferred["purpose"] = purpose
            break

    return inferred


async def _load_grant_envelope(
    user: UserContext,
    profile,
    fga,
) -> dict[str, set[str]]:
    """
    Aggregate granted region/area/phase/purpose values from tuple conditions.

    We read conditional tuple context from:
      1) user assigned_researcher on trial (Tier 2)
      2) organization granted_org on trial (Tier 1)
    """
    cache_key = (
        f"{user.username}|{user.organization_id}|"
        + ",".join(sorted(profile.allowed_trial_ids))
    )
    now = time.monotonic()
    cached = _GRANT_ENVELOPE_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return {
            "regions": set(cached[1]["regions"]),
            "areas": set(cached[1]["areas"]),
            "phases": set(cached[1]["phases"]),
            "purposes": set(cached[1]["purposes"]),
        }

    envelope: dict[str, set[str]] = {
        "regions": set(),
        "areas": set(),
        "phases": set(),
        "purposes": set(),
    }
    trial_regions: dict[str, set[str]] = {}

    org_ref = f"organization:{user.organization_id}" if user.organization_id else ""
    for trial_id in profile.allowed_trial_ids:
        obj = f"clinical_trial:{trial_id}"

        cond = await fga.read_tuple_conditions(
            user=f"user:{user.username}",
            relation="assigned_researcher",
            object=obj,
        )
        if not cond and org_ref:
            cond = await fga.read_tuple_conditions(
                user=org_ref,
                relation="granted_org",
                object=obj,
            )

        context = (cond or {}).get("context", {})
        permitted_regions = context.get("permitted_regions", []) or []
        envelope["regions"].update(permitted_regions)
        trial_regions[trial_id] = {
            str(r).strip()
            for r in permitted_regions
            if str(r).strip()
        }
        envelope["areas"].update(context.get("permitted_areas", []) or [])
        envelope["phases"].update(context.get("permitted_phases", []) or [])
        envelope["purposes"].update(context.get("approved_purposes", []) or [])

    _GRANT_ENVELOPE_CACHE[cache_key] = (
        now + GRANT_ENVELOPE_CACHE_TTL_SECONDS,
        {
            "regions": set(envelope["regions"]),
            "areas": set(envelope["areas"]),
            "phases": set(envelope["phases"]),
            "purposes": set(envelope["purposes"]),
        },
    )

    envelope["trial_regions"] = trial_regions

    return envelope


async def _load_global_purpose_allowlist(db_pool) -> set[str]:
    rows = await db_pool.fetch(
        """
        SELECT purpose_key
        FROM governance_purpose
        WHERE is_active = TRUE AND owner_id IS NULL
        """
    )
    purposes = {str(r["purpose_key"]).strip() for r in rows if str(r["purpose_key"]).strip()}
    if not purposes:
        purposes = set(DEFAULT_ALLOWED_PURPOSES)
    return purposes


def _autofill_scope_from_envelope(
    scope: dict[str, str],
    envelope: dict[str, set[str]],
) -> dict[str, str]:
    """Fill missing scope dimensions only when policy is unambiguous (single value)."""
    resolved = dict(scope)
    mapping = (
        ("region", "regions"),
        ("area", "areas"),
        ("phase", "phases"),
        ("purpose", "purposes"),
    )
    for target, env_key in mapping:
        if resolved.get(target):
            continue
        values = sorted(v for v in envelope.get(env_key, set()) if v)
        if len(values) == 1:
            resolved[target] = values[0]
    return resolved


def _autofill_scope_from_single_trial(
    scope: dict[str, str],
    trial_ids: list[str] | None,
    trial_metadata: dict[str, dict],
) -> dict[str, str]:
    """
    Fill missing scope values from trial metadata when a single trial is targeted.

    This reduces UI burden for common prompts like "from trial X ..." where
    phase/area are deterministic from the selected trial.
    """
    resolved = dict(scope)
    if not trial_ids or len(trial_ids) != 1:
        return resolved

    trial_id = str(trial_ids[0])
    meta = trial_metadata.get(trial_id) or {}

    if not (resolved.get("area") or "").strip():
        area = str(meta.get("therapeutic_area") or "").strip()
        if area:
            resolved["area"] = area

    if not (resolved.get("phase") or "").strip():
        phase = str(meta.get("phase") or "").strip()
        if phase:
            resolved["phase"] = phase

    # Only auto-fill region from metadata when unambiguous for the trial.
    if not (resolved.get("region") or "").strip():
        regions = [str(r).strip() for r in (meta.get("regions") or []) if str(r).strip()]
        if len(regions) == 1:
            resolved["region"] = regions[0]

    return resolved


def _validate_scope_against_envelope(
    scope: dict[str, str],
    envelope: dict[str, set[str]],
) -> None:
    """Deny if inferred/declared scope asks for a value outside granted envelope."""
    checks = (
        ("region", "regions"),
        ("area", "areas"),
        ("phase", "phases"),
        ("purpose", "purposes"),
    )
    for key, env_key in checks:
        requested = (scope.get(key) or "").strip()
        granted = envelope.get(env_key, set())
        if requested and granted and requested not in granted:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": True,
                    "code": AgentErrorCode.ACCESS_DENIED.value,
                    "message": (
                        f"Requested {key}='{requested}' is outside your granted scope. "
                        f"Allowed: {sorted(granted)}"
                    ),
                },
            )


def _resolve_missing_scope(
    scope: dict[str, str],
    envelope: dict[str, set[str]],
) -> tuple[list[str], list[str]]:
    """
    Return (missing_fields, ambiguous_fields) for governance dimensions.

    Only 'purpose' is required from the caller.  Region, area, and phase are
    optional — when absent they are auto-derived from the query prompt and trial
    metadata, or left unconstrained so the grant envelope covers SQL filtering.
    """
    missing: list[str] = []
    ambiguous: list[str] = []

    if not (scope.get("purpose") or "").strip():
        granted_purposes = sorted(v for v in envelope.get("purposes", set()) if v)
        if len(granted_purposes) > 1:
            ambiguous.append("purpose")
        else:
            missing.append("purpose")

    return missing, ambiguous


def _validate_purpose_prompt_consistency(
    explicit_scope: dict[str, str],
    inferred_scope: dict[str, str],
) -> dict[str, str] | None:
    """
    Secondary PBAC guard: detect declared-purpose vs prompt-intent mismatch.

    Source of truth remains declared purpose. We only block when:
      - user explicitly declared a purpose, and
      - prompt intent inferred a different purpose with a deterministic match.
    """
    declared = (explicit_scope.get("purpose") or "").strip()
    inferred = (inferred_scope.get("purpose") or "").strip()
    if declared and inferred and declared != inferred:
        if PURPOSE_MISMATCH_MODE == "warn":
            logger.warning(
                "Purpose mismatch (warn mode): declared=%s inferred=%s",
                declared,
                inferred,
            )
            return {
                "purpose_mismatch": "true",
                "declared_purpose": declared,
                "inferred_purpose": inferred,
                "message": (
                    "Declared purpose conflicts with prompt intent, but request "
                    "was allowed because PURPOSE_MISMATCH_MODE=warn."
                ),
            }

        if PURPOSE_MISMATCH_MODE == "off":
            return None

        raise HTTPException(
            status_code=422,
            detail={
                "error": True,
                "code": AgentErrorCode.INPUT_INVALID.value,
                "purpose_mismatch": True,
                "declared_purpose": declared,
                "inferred_purpose": inferred,
                "message": (
                    "Declared purpose conflicts with the prompt intent. "
                    f"Declared='{declared}', inferred='{inferred}'. "
                    "Please align purpose with the query intent."
                ),
            },
        )
    return None


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def get_agent_service() -> AgentService:
    """Dependency: returns a singleton-like AgentService instance."""
    return AgentService()


async def _build_auth_service(
    db_pool=Depends(get_db_pool),
    fga=Depends(get_openfga_client),
) -> AuthorizationService:
    """Dependency: constructs AuthorizationService with pool + FGA client."""
    return AuthorizationService(db_pool=db_pool, fga_client=fga)


# ---------------------------------------------------------------------------
# GET /research/my-access
# ---------------------------------------------------------------------------

@router.get(
    "/research/my-access",
    dependencies=[Depends(require_role("researcher", "manager"))],
)
async def get_my_access(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
    fga=Depends(get_openfga_client),
):
    """
    Returns the current user's full access profile including cohort scopes,
    per-trial access levels, and patient counts.
    """
    auth_service = AuthorizationService(db_pool=db_pool, fga_client=fga)
    profile = await auth_service.compute_access_profile(user)

    # Batch-fetch metadata for all accessible trials (without patient count initially)
    trial_metadata: dict[str, dict] = {}
    if profile.allowed_trial_ids:
        rows = await db_pool.fetch(
            """
            SELECT
                ct.trial_id,
                ct.nct_id,
                ct.title,
                ct.phase,
                ct.therapeutic_area,
                ct.overall_status,
                ct.study_type,
                ct.enrollment_count,
                ct.regions
            FROM clinical_trial ct
            WHERE ct.trial_id = ANY($1::uuid[])
            """,
            profile.allowed_trial_ids,
        )
        trial_metadata = {str(r["trial_id"]): dict(r) for r in rows}

    # Load active org grant scopes per trial so aggregate access is capped
    # by grant ceiling (not only by collection/runtime regions).
    grant_scope_regions_by_trial: dict[str, list[str]] = {}
    if profile.allowed_trial_ids:
        grant_rows = await db_pool.fetch(
            """
            SELECT DISTINCT ON (da.reference_id)
                da.reference_id::text AS trial_id,
                ag.scope
            FROM access_grant ag
            JOIN data_asset da ON da.asset_id = ag.asset_id
            WHERE ag.organization_id = $1
              AND ag.revoked_at IS NULL
              AND ag.expires_at > NOW()
              AND da.asset_type = 'clinical_trial'
              AND da.reference_id::text = ANY($2::text[])
            ORDER BY da.reference_id, ag.expires_at DESC
            """,
            user.organization_id,
            profile.allowed_trial_ids,
        )
        for row in grant_rows:
            scope = dict(row.get("scope") or {})
            values = _coerce_scope_values(scope, ["permitted_regions", "regions", "region"])
            grant_scope_regions_by_trial[row["trial_id"]] = [
                _normalize_region_value(v)
                for v in values
                if _normalize_region_value(v)
            ]

    # Build trial_access list from trial_scopes
    trial_details = []
    for trial_id, scope in profile.trial_scopes.items():
        meta = trial_metadata.get(trial_id, {})
        trial_region_values = [str(r).strip() for r in (meta.get("regions") or []) if str(r).strip()]
        trial_regions = {
            _normalize_region_value(str(r).strip())
            for r in (meta.get("regions") or [])
            if str(r).strip()
        }
        trial_regions = {r for r in trial_regions if r}
        trial_area = str(meta.get("therapeutic_area") or "").strip()
        trial_phase = str(meta.get("phase") or "").strip()

        trial_tuple_regions: list[str] = []
        try:
            tuple_cond = await fga.read_tuple_conditions(
                user=f"user:{user.username}",
                relation="assigned_researcher",
                object=f"clinical_trial:{trial_id}",
            )
            tuple_ctx = (tuple_cond or {}).get("context", {})
            trial_tuple_regions = [
                _normalize_region_value(str(r).strip())
                for r in (tuple_ctx.get("permitted_regions") or [])
                if str(r).strip()
            ]
        except Exception:
            # Keep my-access resilient if tuple context lookup is temporarily unavailable.
            trial_tuple_regions = []

        runtime_regions = [
            _normalize_region_value(str(r).strip())
            for r in (profile.requested_regions or [])
            if str(r).strip()
        ]
        grant_scope_regions = [
            _normalize_region_value(str(r).strip())
            for r in (grant_scope_regions_by_trial.get(trial_id) or [])
            if str(r).strip()
        ]

        region_sets = [
            set(values)
            for values in [trial_tuple_regions, grant_scope_regions, runtime_regions]
            if values
        ]
        effective_regions = sorted(set.intersection(*region_sets)) if region_sets else []

        if effective_regions and trial_regions:
            effective_regions = sorted(set(effective_regions) & trial_regions)

        effective_region_literals = _region_literals_for_trial(effective_regions, trial_region_values)
        
        # Compute the filtered patient count for this trial based on cohort filters
        original_requested_regions = list(profile.requested_regions or [])
        try:
            profile.requested_regions = list(effective_region_literals)
            patient_filter = auth_service.build_patient_sql_filter(profile, trial_id)
        finally:
            profile.requested_regions = original_requested_regions
        patient_count = 0
        
        count_row = await db_pool.fetchval(
            f"""
            SELECT COUNT(DISTINCT p.patient_id)
            FROM patient p
            JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
            WHERE pte.trial_id = $1 AND {patient_filter}
            """,
            trial_id,
        )
        patient_count = count_row or 0

        cohort_filters_payload = []
        for cs in scope.cohort_scopes:
            effective_filter = dict(cs.filter_criteria or {})

            scope_regions = _coerce_scope_values(effective_filter, ["region", "regions"])
            if scope_regions:
                normalized_scope_regions = {
                    _normalize_region_value(v) for v in scope_regions if _normalize_region_value(v)
                }
                applicable_regions = (
                    sorted(normalized_scope_regions & trial_regions)
                    if trial_regions else sorted(normalized_scope_regions)
                )
                if applicable_regions:
                    effective_filter["region"] = applicable_regions
                else:
                    effective_filter.pop("region", None)
                effective_filter.pop("regions", None)

            scope_areas = _coerce_scope_values(effective_filter, ["therapeutic_areas", "areas", "area"])
            if scope_areas:
                normalized_scope_areas = {
                    _normalize_area_value(v) for v in scope_areas if _normalize_area_value(v)
                }
                trial_area_norm = _normalize_area_value(trial_area)
                if trial_area_norm and trial_area_norm in normalized_scope_areas:
                    effective_filter["therapeutic_areas"] = [trial_area]
                else:
                    effective_filter.pop("therapeutic_areas", None)
                effective_filter.pop("areas", None)
                effective_filter.pop("area", None)

            scope_phases = _coerce_scope_values(effective_filter, ["phases", "phase"])
            if scope_phases:
                normalized_scope_phases = {
                    _normalize_phase_value(v) for v in scope_phases if _normalize_phase_value(v)
                }
                trial_phase_norm = _normalize_phase_value(trial_phase)
                if trial_phase_norm and trial_phase_norm in normalized_scope_phases:
                    effective_filter["phases"] = [trial_phase]
                else:
                    effective_filter.pop("phases", None)
                effective_filter.pop("phase", None)

            # Runtime region scope is enforced globally; mirror it in the payload
            # so the UI can display region as part of effective restrictions.
            if effective_region_literals:
                effective_filter.setdefault("region", list(effective_region_literals))

            cohort_filters_payload.append(
                {
                    "cohort_id": cs.cohort_id,
                    "cohort_name": cs.cohort_name,
                    "filter_criteria": effective_filter,
                }
            )

        # If access is unrestricted but runtime region guard exists, surface it
        # as a synthetic restriction so users can see why counts are reduced.
        if not cohort_filters_payload and effective_region_literals:
            cohort_filters_payload.append(
                {
                    "cohort_id": "__runtime_scope__",
                    "cohort_name": "Runtime Scope Restrictions",
                    "filter_criteria": {"region": list(effective_region_literals)},
                }
            )
        
        trial_details.append({
            "trial_id":         trial_id,
            "nct_id":           meta.get("nct_id"),
            "title":            meta.get("title"),
            "phase":            meta.get("phase", ""),
            "therapeutic_area": meta.get("therapeutic_area", ""),
            "overall_status":   meta.get("overall_status", ""),
            "enrollment_count": meta.get("enrollment_count", 0),
            "patient_count":    patient_count,
            "access_level":     scope.access_level,
            "is_unrestricted":  scope.is_unrestricted,
            "effective_restrictions_label": "Effective Restrictions",
            "effective_restrictions": cohort_filters_payload,
            "cohort_filters": cohort_filters_payload,
        })

    # Sort: individual first, then by nct_id
    trial_details.sort(key=lambda t: (
        0 if t["access_level"] == "individual" else 1,
        t.get("nct_id") or "",
    ))

    return {
        "user_id":         user.user_id,
        "username":        user.username,
        "role":            user.role,
        "organization_id": user.organization_id,
        "access_summary": {
            "has_any_access":        profile.has_any_access,
            "aggregate_only":        profile.aggregate_only,
            "aggregate_trial_count": len(profile.aggregate_trial_ids),
            "individual_trial_count": len(profile.individual_trial_ids),
            "aggregate_trial_ids":   profile.aggregate_trial_ids,
            "individual_trial_ids":  profile.individual_trial_ids,
        },
        "trial_access": trial_details,
    }


# ---------------------------------------------------------------------------
# GET /research/suggested-questions
# ---------------------------------------------------------------------------

@router.get(
    "/research/suggested-questions",
    dependencies=[Depends(require_role("researcher", "manager"))],
)
async def get_suggested_questions(
    user: UserContext = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
    fga=Depends(get_openfga_client),
):
    """
    Returns personalized suggested question chips for the frontend query UI,
    based on the researcher's current access profile.
    """
    auth_service = AuthorizationService(db_pool=db_pool, fga_client=fga)
    profile = await auth_service.compute_access_profile(user)

    if not profile.has_any_access or not profile.allowed_trial_ids:
        return {"suggestions": []}

    # Build a parameterized placeholder list: $1, $2, ...
    placeholders = ", ".join(
        f"${i + 1}" for i in range(len(profile.allowed_trial_ids))
    )
    rows = await db_pool.fetch(
        f"""
        SELECT trial_id::text, nct_id, title, phase, therapeutic_area
        FROM clinical_trial
        WHERE trial_id::text IN ({placeholders})
        """,
        *profile.allowed_trial_ids,
    )
    trial_metadata = [dict(r) for r in rows]

    suggestions = generate_suggested_questions(profile, trial_metadata)

    return {
        "suggestions": [
            {
                "text":         s.text,
                "category":     s.category,
                "trial_ids":    s.trial_ids,
                "access_level": s.access_level,
            }
            for s in suggestions
        ]
    }


# ---------------------------------------------------------------------------
# GET /research/governance-options
# ---------------------------------------------------------------------------

@router.get(
    "/research/governance-options",
    dependencies=[Depends(require_role("researcher", "manager", "domain_owner"))],
)
async def get_governance_options(
    user: UserContext = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
    fga=Depends(get_openfga_client),
):
    """
    Return dynamic governance scope options for the current user.

    Values are derived as intersection of:
      1) global server allowlists (OpenFGA context builder)
      2) the user's grant envelope from tuple conditions
    """
    auth_service = AuthorizationService(db_pool=db_pool, fga_client=fga)
    profile = await auth_service.compute_access_profile(user)

    if not profile.has_any_access:
        return {
            "regions": [],
            "areas": [],
            "phases": [],
            "purposes": [],
            "purpose_mismatch_mode": PURPOSE_MISMATCH_MODE,
        }

    envelope = await _load_grant_envelope(user, profile, fga)
    global_purposes = await _load_global_purpose_allowlist(db_pool)

    regions = sorted(set(ALLOWED_REGIONS) & envelope["regions"])
    areas = sorted(set(ALLOWED_AREAS) & envelope["areas"])
    phases = sorted(set(ALLOWED_PHASES) & envelope["phases"])
    purposes = sorted(global_purposes & envelope["purposes"])

    # Fallback for legacy tuples without conditions: expose global allowlist
    # so UI remains usable while governance data is being backfilled.
    if not regions:
        regions = sorted(ALLOWED_REGIONS)
    if not areas:
        areas = sorted(ALLOWED_AREAS)
    if not phases:
        phases = sorted(ALLOWED_PHASES)
    if not purposes:
        purposes = sorted(global_purposes)

    return {
        "regions": regions,
        "areas": areas,
        "phases": phases,
        "purposes": purposes,
        "purpose_mismatch_mode": PURPOSE_MISMATCH_MODE,
    }


# ---------------------------------------------------------------------------
# POST /research/query  (synchronous JSON response)
# ---------------------------------------------------------------------------

@router.post(
    "/research/query",
    response_model=QueryResponse,
    dependencies=[Depends(require_role("researcher", "manager", "domain_owner"))],
)
async def execute_query(
    body: QueryRequest,
    user: UserContext = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
    fga=Depends(get_openfga_client),
    agent_service: AgentService = Depends(get_agent_service),
) -> QueryResponse:
    """
    Synchronous semantic query endpoint.

    Phase 6 hardening applied:
      1. Input validation + sanitization
      2. Circuit-breaker check
      3. Access profile computation
      4. Agent execution
      5. Response scrubbing (patient UUID redaction)
    """
    # ── Step 1: validate + sanitize input ─────────────────────────────────
    try:
        validation = validate_query(body.query)
        body = body.model_copy(update={"query": validation.sanitized_query})
    except AgentError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())

    # ── Step 2: circuit-breaker check ────────────────────────────────────
    try:
        mcp_circuit_breaker.check()
    except AgentError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict())

    # ── Step 3: compute baseline profile + resolve governance scope ───────
    auth_service = AuthorizationService(db_pool=db_pool, fga_client=fga)
    profile = await auth_service.compute_access_profile(user)

    if not profile.has_any_access:
        raise HTTPException(
            status_code=403,
            detail={
                "error": True,
                "code":    AgentErrorCode.ACCESS_DENIED.value,
                "message": "You do not have access to any clinical trial data.",
            },
        )

    explicit_scope = {
        str(k): str(v).strip()
        for k, v in (body.scope_params or {}).items()
        if isinstance(v, str) and str(v).strip()
    }
    inferred_scope = _infer_scope_from_prompt(body.query)

    purpose_mismatch_warning = _validate_purpose_prompt_consistency(
        explicit_scope,
        inferred_scope,
    )

    # Precedence: explicit request scope > prompt-inferred scope.
    resolved_scope = dict(inferred_scope)
    resolved_scope.update(explicit_scope)

    grant_envelope = await _load_grant_envelope(user, profile, fga)
    _validate_scope_against_envelope(resolved_scope, grant_envelope)
    resolved_scope = _autofill_scope_from_envelope(resolved_scope, grant_envelope)
    resolved_scope = _autofill_scope_from_single_trial(
        resolved_scope,
        body.trial_ids,
        profile.trial_metadata,
    )

    # Only purpose is required.  Region / area / phase are derived from the
    # prompt and trial context by the agent; missing them is not an error.
    missing_scope, ambiguous_scope = _resolve_missing_scope(
        resolved_scope,
        grant_envelope,
    )
    if missing_scope or ambiguous_scope:
        primary_missing = ambiguous_scope if ambiguous_scope else missing_scope
        raise HTTPException(
            status_code=422,
            detail={
                "error": True,
                "code": AgentErrorCode.INPUT_INVALID.value,
                "missing_scope_fields": primary_missing,
                "ambiguous_scope_fields": ambiguous_scope,
                "required_scope_fields": missing_scope,
                "message": (
                    "Missing required governance attribute: "
                    f"{primary_missing}. Please select a purpose for this query."
                ),
            },
        )

    # Attach full grant envelope for downstream SQL filter fallback so MCP tools
    # can scope to the full allowed set even when specific dimensions are absent.
    scope_params: dict[str, object] = dict(resolved_scope)
    if grant_envelope["regions"]:
        scope_params["allowed_regions"] = sorted(grant_envelope["regions"])
    if grant_envelope["areas"]:
        scope_params["allowed_areas"] = sorted(grant_envelope["areas"])
    if grant_envelope["phases"]:
        scope_params["allowed_phases"] = sorted(grant_envelope["phases"])

    try:
        abac_context = OpenFGAContextBuilder(
            jwt_token             = user.raw_token,
            tool_call_params      = resolved_scope,
            pre_calculated_values = {"actual_cohort_size": scope_params.get("cohort_size", 0)},
            allowed_purposes      = set(grant_envelope["purposes"]) if grant_envelope["purposes"] else None,
        ).build()
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": True,
                "code": AgentErrorCode.INPUT_INVALID.value,
                "message": f"Invalid scope parameters: {exc}",
            },
        )

    # Always embed allowed_* from the grant envelope into the ABAC context so
    # that MCP tool SQL filters (build_abac_sql_filters) can use them as the
    # fallback when specific requested_region/area/phase are absent.
    #
    # IMPORTANT: Do NOT widen these sets with trial metadata. Governance
    # ceilings are mandatory upper bounds and must never be broadened.
    abac_context["allowed_regions"] = sorted(grant_envelope["regions"])
    abac_context["allowed_areas"] = sorted(set(a.lower() for a in grant_envelope["areas"]))
    abac_context["allowed_phases"] = sorted(grant_envelope["phases"])
    abac_context["per_trial_allowed_regions"] = {
        tid: sorted(list(regs))
        for tid, regs in (grant_envelope.get("trial_regions") or {}).items()
    }

    # Only run per-trial ABAC /check when all 4 dimensions are fully declared.
    # When only purpose is provided, the base ReBAC profile already enforces
    # trial-level access; region/area/phase are enforced via SQL filters in
    # the MCP tools using the allowed_* envelope values above.
    has_full_scope = all(
        (resolved_scope.get(k) or "").strip()
        for k in ("region", "area", "phase", "purpose")
    )
    if has_full_scope:
        profile = await auth_service.compute_access_profile_with_abac(user, abac_context)
        if not profile.has_any_access:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": True,
                    "code": AgentErrorCode.ACCESS_DENIED.value,
                    "message": "No trials matched your governance scope.",
                },
            )

    # Narrow scope if the caller provided explicit trial_ids
    if body.trial_ids:
        unauthorized = [t for t in body.trial_ids if t not in profile.allowed_trial_ids]
        if unauthorized:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": True,
                    "code":    AgentErrorCode.ACCESS_DENIED.value,
                    "message": f"You are not authorized to query trial(s): {unauthorized}",
                },
            )

    body = body.model_copy(update={
        "scope_params": scope_params,
        "abac_context": abac_context,
    })

    # ── Step 4: run the agent ─────────────────────────────────────────────
    try:
        response: QueryResponse = await agent_service.query(body, profile)
    except AgentError as exc:
        logger.error(
            "AgentError during query for user=%s code=%s detail=%s",
            user.username, exc.code, exc.detail,
        )
        status = 503 if exc.retryable else 422
        raise HTTPException(status_code=status, detail=exc.to_dict())
    except Exception as exc:
        logger.exception("Unexpected agent error for user=%s", user.username)
        raise HTTPException(
            status_code=500,
            detail={
                "error":   True,
                "code":    AgentErrorCode.UNEXPECTED.value,
                "message": "An unexpected error occurred. Please try again.",
            },
        )

    # ── Step 5: scrub patient UUIDs from LLM output ───────────────────────
    allowed_uuids = build_allowed_uuid_set(profile)
    scrub_result  = scrub_patient_ids(response.answer, allowed_uuids)

    if scrub_result.was_modified:
        logger.warning(
            "Response scrubber redacted %d UUID(s) for user=%s",
            scrub_result.redaction_count,
            user.username,
        )
        response = response.model_copy(update={"answer": scrub_result.scrubbed_text})

    if purpose_mismatch_warning:
        response.filters_applied.append("purpose_mismatch_warning")

    return response


# ---------------------------------------------------------------------------
# POST /research/query/stream  (NDJSON streaming response)
# ---------------------------------------------------------------------------

@router.post(
    "/research/query/stream",
    dependencies=[Depends(require_role("researcher", "manager", "domain_owner"))],
)
async def execute_query_stream(
    body: QueryRequest,
    user: UserContext = Depends(get_current_user),
    db_pool=Depends(get_db_pool),
    fga=Depends(get_openfga_client),
    agent_service: AgentService = Depends(get_agent_service),
) -> StreamingResponse:
    """
    Streaming semantic query endpoint.
    Yields newline-delimited JSON events:
      status | tool_call | tool_result | thinking | answer_token | complete | error

    Phase 6 hardening: same validation + circuit-breaker + scrubbing as
    the synchronous endpoint, but scrubbing is applied to the final
    'complete' event answer before it is flushed to the client.
    """
    # ── Step 1: validate + sanitize ───────────────────────────────────────
    try:
        validation = validate_query(body.query)
        body = body.model_copy(update={"query": validation.sanitized_query})
    except AgentError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())

    # ── Step 2: circuit-breaker check ─────────────────────────────────────
    try:
        mcp_circuit_breaker.check()
    except AgentError as exc:
        raise HTTPException(status_code=503, detail=exc.to_dict())

    # ── Step 3: compute baseline profile + resolve governance scope ───────
    auth_service = AuthorizationService(db_pool=db_pool, fga_client=fga)
    profile = await auth_service.compute_access_profile(user)

    if not profile.has_any_access:
        raise HTTPException(
            status_code=403,
            detail={
                "error":   True,
                "code":    AgentErrorCode.ACCESS_DENIED.value,
                "message": "You do not have access to any clinical trial data.",
            },
        )

    explicit_scope = {
        str(k): str(v).strip()
        for k, v in (body.scope_params or {}).items()
        if isinstance(v, str) and str(v).strip()
    }
    inferred_scope = _infer_scope_from_prompt(body.query)

    purpose_mismatch_warning = _validate_purpose_prompt_consistency(
        explicit_scope,
        inferred_scope,
    )
    resolved_scope = dict(inferred_scope)
    resolved_scope.update(explicit_scope)

    grant_envelope = await _load_grant_envelope(user, profile, fga)
    _validate_scope_against_envelope(resolved_scope, grant_envelope)
    resolved_scope = _autofill_scope_from_envelope(resolved_scope, grant_envelope)
    resolved_scope = _autofill_scope_from_single_trial(
        resolved_scope,
        body.trial_ids,
        profile.trial_metadata,
    )

    missing_scope, ambiguous_scope = _resolve_missing_scope(
        resolved_scope,
        grant_envelope,
    )
    if missing_scope or ambiguous_scope:
        primary_missing = ambiguous_scope if ambiguous_scope else missing_scope
        raise HTTPException(
            status_code=422,
            detail={
                "error": True,
                "code": AgentErrorCode.INPUT_INVALID.value,
                "missing_scope_fields": primary_missing,
                "ambiguous_scope_fields": ambiguous_scope,
                "required_scope_fields": missing_scope,
                "message": (
                    "Missing required governance attribute: "
                    f"{primary_missing}. Please select a purpose for this query."
                ),
            },
        )

    scope_params: dict[str, object] = dict(resolved_scope)
    if grant_envelope["regions"]:
        scope_params["allowed_regions"] = sorted(grant_envelope["regions"])
    if grant_envelope["areas"]:
        scope_params["allowed_areas"] = sorted(grant_envelope["areas"])
    if grant_envelope["phases"]:
        scope_params["allowed_phases"] = sorted(grant_envelope["phases"])

    try:
        abac_context = OpenFGAContextBuilder(
            jwt_token             = user.raw_token,
            tool_call_params      = resolved_scope,
            pre_calculated_values = {"actual_cohort_size": scope_params.get("cohort_size", 0)},
            allowed_purposes      = set(grant_envelope["purposes"]) if grant_envelope["purposes"] else None,
        ).build()
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": True,
                "code": AgentErrorCode.INPUT_INVALID.value,
                "message": f"Invalid scope parameters: {exc}",
            },
        )

    # Always embed allowed_* from the grant envelope for MCP SQL fallback.
    # Do not widen with trial metadata: governance ceiling is a strict upper
    # bound and must never be broadened.
    abac_context["allowed_regions"] = sorted(grant_envelope["regions"])
    abac_context["allowed_areas"] = sorted(set(a.lower() for a in grant_envelope["areas"]))
    abac_context["allowed_phases"] = sorted(grant_envelope["phases"])
    abac_context["per_trial_allowed_regions"] = {
        tid: sorted(list(regs))
        for tid, regs in (grant_envelope.get("trial_regions") or {}).items()
    }

    # Only run per-trial ABAC /check when all 4 scope dimensions are declared.
    has_full_scope = all(
        (resolved_scope.get(k) or "").strip()
        for k in ("region", "area", "phase", "purpose")
    )
    if has_full_scope:
        profile = await auth_service.compute_access_profile_with_abac(user, abac_context)
        if not profile.has_any_access:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": True,
                    "code": AgentErrorCode.ACCESS_DENIED.value,
                    "message": "No trials matched your governance scope.",
                },
            )

    if body.trial_ids:
        unauthorized = [t for t in body.trial_ids if t not in profile.allowed_trial_ids]
        if unauthorized:
            raise HTTPException(
                status_code=403,
                detail={
                    "error":   True,
                    "code":    AgentErrorCode.ACCESS_DENIED.value,
                    "message": f"You are not authorized to query trial(s): {unauthorized}",
                },
            )

    body = body.model_copy(update={
        "scope_params": scope_params,
        "abac_context": abac_context,
    })

    # ── Step 4: build allowed UUID set for scrubbing ───────────────────────
    allowed_uuids = build_allowed_uuid_set(profile)

    # ── Step 5: stream events ─────────────────────────────────────────────
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            if purpose_mismatch_warning:
                from api.agent.models import StatusEvent

                warning_event = StatusEvent(
                    event="status",
                    data={
                        "level": "warning",
                        "code": "purpose_mismatch",
                        "message": purpose_mismatch_warning["message"],
                        "declared_purpose": purpose_mismatch_warning["declared_purpose"],
                        "inferred_purpose": purpose_mismatch_warning["inferred_purpose"],
                    },
                )
                yield warning_event.model_dump_json() + "\n"

            async for event in agent_service.query_stream(body, profile):
                # Intercept the final 'complete' event and scrub its answer
                if event.event == "complete" and hasattr(event, "data"):
                    answer = getattr(event.data, "answer", "")
                    scrub_result = scrub_patient_ids(answer, allowed_uuids)
                    if scrub_result.was_modified:
                        logger.warning(
                            "Stream scrubber redacted %d UUID(s) for user=%s",
                            scrub_result.redaction_count,
                            user.username,
                        )
                        event.data.answer = scrub_result.scrubbed_text

                    if purpose_mismatch_warning:
                        event.data.filters_applied.append("purpose_mismatch_warning")

                yield event.model_dump_json() + "\n"

        except AgentError as exc:
            logger.error(
                "AgentError during stream for user=%s code=%s",
                user.username, exc.code,
            )
            from api.agent.models import ErrorEvent
            error_event = ErrorEvent(
                event="error",
                data={
                    "code":    exc.code.value,
                    "message": exc.message,
                },
            )
            yield error_event.model_dump_json() + "\n"

        except Exception as exc:
            logger.exception("Unexpected stream error for user=%s", user.username)
            from api.agent.models import ErrorEvent
            error_event = ErrorEvent(
                event="error",
                data={
                    "code":    AgentErrorCode.UNEXPECTED.value,
                    "message": "An unexpected error occurred. Please try again.",
                },
            )
            yield error_event.model_dump_json() + "\n"

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control":    "no-cache",
            "Connection":       "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /research/conversations/{session_id}
# ---------------------------------------------------------------------------

@router.get(
    "/research/conversations/{session_id}",
    dependencies=[Depends(require_role("researcher", "manager"))],
)
async def get_conversation_history(
    session_id: str,
    request: Request,
    user: UserContext = Depends(get_current_user),
):
    """
    Reads LangGraph checkpoint state for a given session_id and returns
    the conversation messages (human + AI turns only).
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    try:
        async with AsyncPostgresSaver.from_conn_string(
            request.app.state.checkpointer_url
        ) as saver:
            config = {"configurable": {"thread_id": session_id}}
            checkpoint_tuple = await saver.aget_tuple(config)

            if not checkpoint_tuple:
                return {"messages": []}

            state = checkpoint_tuple.checkpoint.get("channel_values", {})
            messages = state.get("messages", [])

            formatted: list[dict] = []
            for msg in messages:
                if not (hasattr(msg, "type") and hasattr(msg, "content")):
                    continue
                if msg.type not in ("human", "ai"):
                    continue
                # Skip empty AI messages (mid-loop tool-call frames)
                if not msg.content:
                    continue
                formatted.append({
                    "role":    "user" if msg.type == "human" else "agent",
                    "content": msg.content,
                })

            return {"messages": formatted}

    except Exception as exc:
        logger.warning(
            "Failed to fetch conversation history session=%s user=%s error=%s",
            session_id, user.username, exc,
        )
        return {"messages": []}