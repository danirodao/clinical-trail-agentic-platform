"""
Manager endpoints — request access, create cohorts, assign researchers.
"""

from uuid import UUID
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from auth.cohort_service import CohortService, CeilingViolationError
from auth.dependencies import CurrentUser, require_role
from auth.access_request_service import AccessRequestService
from auth.openfga_outbox import enqueue_delete_tuples, enqueue_write_tuples
from auth.openfga.condition_payload import (
    build_condition_context_from_scope,
    build_delegation_context_from_ceiling,
    build_narrowed_delegation_context,
)
from auth.openfga.context_builder import ALLOWED_AREAS, ALLOWED_PHASES, ALLOWED_REGIONS
from api.database import get_db_pool
import json

router = APIRouter()


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


def _coerce_scope_list(scope: dict, *keys: str) -> list[str]:
    for key in keys:
        value = scope.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        if isinstance(value, list):
            cleaned = [str(v).strip() for v in value if str(v).strip()]
            if cleaned:
                return cleaned
    return []


def _expand_region_literals(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        raw = str(value).strip()
        if not raw:
            continue
        canonical = _REGION_ALIASES.get(raw.lower().replace("-", " "), raw.upper())
        for literal in _CANONICAL_REGION_LITERALS.get(canonical, [raw]):
            if literal not in out:
                out.append(literal)
        if raw not in out:
            out.append(raw)
    return out


def _is_full_allowlist(values: list[str], allowed: set[str] | frozenset[str]) -> bool:
    if not values:
        return False
    return set(values) == set(allowed)


class AccessRequestBody(BaseModel):
    asset_id: UUID
    justification: str
    scope: Optional[dict] = None
    requested_duration_days: int = 365


class AssignResearcherBody(BaseModel):
    researcher_username: str  # Keycloak username
    trial_id: Optional[UUID] = None
    cohort_id: Optional[UUID] = None
    access_level: str = "individual"  # 'individual' or 'aggregate'
    duration_days: int = 180
    assignment_scope: Optional[dict] = None

class CohortCreateBody(BaseModel):
    name: str
    description: str = ""
    filter_criteria: dict
    is_dynamic: bool = True
    
class RevokeAssignmentBody(BaseModel):
    reason: str = Field(..., min_length=5)


class AssignmentVisiblePreviewBody(BaseModel):
    trial_id: Optional[UUID] = None
    cohort_id: Optional[UUID] = None
    duration_days: int = 180
    assignment_scope: Optional[dict] = None


def _patient_region_sql_expr() -> str:
    return (
        "COALESCE(NULLIF(TRIM(p.region), ''), "
        "(SELECT cr.region FROM country_region cr "
        "WHERE LOWER(cr.country) = LOWER(p.country) LIMIT 1))"
    )


def _coerce_filter_list(filter_criteria: dict, *keys: str) -> list[str]:
    for key in keys:
        value = filter_criteria.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        if isinstance(value, list):
            cleaned = [str(v).strip() for v in value if str(v).strip()]
            if cleaned:
                return cleaned
    return []


async def _load_trial_ceiling_contexts(conn, organization_id: str, trial_ids: list[str]) -> dict[str, dict]:
    if not trial_ids:
        return {}

    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (da.reference_id)
            da.reference_id::text AS trial_id,
            ag.scope,
            ag.expires_at
        FROM access_grant ag
        JOIN data_asset da ON ag.asset_id = da.asset_id
        WHERE ag.organization_id = $1
          AND da.asset_type = 'clinical_trial'
          AND da.reference_id::text = ANY($2::text[])
          AND ag.revoked_at IS NULL
          AND ag.expires_at > NOW()
        ORDER BY da.reference_id, ag.expires_at DESC
        """,
        organization_id,
        trial_ids,
    )

    contexts: dict[str, dict] = {}
    for row in rows:
        contexts[row["trial_id"]] = build_condition_context_from_scope(
            row["scope"] or {},
            valid_until=row["expires_at"],
        )
    return contexts


async def _count_visible_patients_for_trials(
    conn,
    trial_ids: list[str],
    delegated_contexts: dict[str, dict],
    cohort_filter_criteria: dict | None = None,
) -> int:
    if not trial_ids:
        return 0

    total = 0
    cohort_filter_criteria = cohort_filter_criteria or {}

    for tid in trial_ids:
        ctx = delegated_contexts.get(tid, {})
        conditions = ["pte.trial_id = $1::uuid"]
        params: list = [tid]
        idx = 2

        permitted_regions = [str(v).strip() for v in ctx.get("permitted_regions", []) if str(v).strip()]
        if permitted_regions and not _is_full_allowlist(permitted_regions, ALLOWED_REGIONS):
            expanded_regions = _expand_region_literals(permitted_regions)
            conditions.append(f"{_patient_region_sql_expr()} = ANY(${idx}::text[])")
            params.append(expanded_regions)
            idx += 1

        permitted_areas = [str(v).strip() for v in ctx.get("permitted_areas", []) if str(v).strip()]
        if permitted_areas and not _is_full_allowlist(permitted_areas, ALLOWED_AREAS):
            normalized_areas = [v.lower().replace("-", "_").replace(" ", "_") for v in permitted_areas]
            conditions.append(
                "LOWER(REPLACE(REPLACE(COALESCE(ct.therapeutic_area, ''), '-', '_'), ' ', '_')) "
                f"= ANY(${idx}::text[])"
            )
            params.append(normalized_areas)
            idx += 1

        permitted_phases = [str(v).strip() for v in ctx.get("permitted_phases", []) if str(v).strip()]
        if permitted_phases and not _is_full_allowlist(permitted_phases, ALLOWED_PHASES):
            normalized_phases = [v.upper().replace("PHASE ", "") for v in permitted_phases]
            conditions.append(f"UPPER(REPLACE(COALESCE(ct.phase, ''), 'PHASE ', '')) = ANY(${idx}::text[])")
            params.append(normalized_phases)
            idx += 1

        if cohort_filter_criteria.get("age_min") is not None:
            conditions.append(f"p.age >= ${idx}")
            params.append(int(cohort_filter_criteria["age_min"]))
            idx += 1
        if cohort_filter_criteria.get("age_max") is not None:
            conditions.append(f"p.age <= ${idx}")
            params.append(int(cohort_filter_criteria["age_max"]))
            idx += 1

        for key, col in [
            ("sex", "p.sex"),
            ("ethnicity", "p.ethnicity"),
            ("country", "p.country"),
            ("disposition_status", "p.disposition_status"),
            ("arm_assigned", "p.arm_assigned"),
        ]:
            values = _coerce_filter_list(cohort_filter_criteria, key)
            if values:
                conditions.append(f"{col} = ANY(${idx}::text[])")
                params.append(values)
                idx += 1

        cohort_regions = _coerce_filter_list(cohort_filter_criteria, "region", "regions")
        if cohort_regions:
            conditions.append(f"{_patient_region_sql_expr()} = ANY(${idx}::text[])")
            params.append(_expand_region_literals(cohort_regions))
            idx += 1

        cohort_conditions = _coerce_filter_list(cohort_filter_criteria, "conditions")
        if cohort_conditions:
            conditions.append(
                f"EXISTS (SELECT 1 FROM patient_condition pc WHERE pc.patient_id = p.patient_id AND pc.condition_name = ANY(${idx}::text[]))"
            )
            params.append(cohort_conditions)
            idx += 1

        count_row = await conn.fetchval(
            f"""
            SELECT COUNT(DISTINCT p.patient_id)
            FROM patient p
            JOIN patient_trial_enrollment pte ON pte.patient_id = p.patient_id
            JOIN clinical_trial ct ON ct.trial_id = pte.trial_id
            WHERE {' AND '.join(conditions)}
            """,
            *params,
        )
        total += int(count_row or 0)

    return total


@router.post("/access-requests/", dependencies=[Depends(require_role("manager"))])
async def request_access(
    body: AccessRequestBody,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Manager requests access to a data asset on behalf of their organization."""
    service = AccessRequestService(db_pool)
    return await service.create_request(
        user,
        body.asset_id,
        body.justification,
        body.scope,
        body.requested_duration_days,
    )


@router.get("/my-org/grants", dependencies=[Depends(require_role("manager"))])
async def list_org_grants(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """List all active access grants for my organization."""
    rows = await db_pool.fetch(
        """SELECT ag.grant_id, ag.asset_id, da.reference_id as trial_id, ag.scope, ag.granted_at,
                  ag.expires_at,
                  (ag.revoked_at IS NULL AND ag.expires_at > NOW()) AS is_active,
                                    da.title as asset_title, da.asset_type,
                                    ct.regions AS trial_regions,
                                    CASE
                                        WHEN ct.phase IS NULL OR BTRIM(ct.phase) = '' THEN ARRAY[]::text[]
                                        ELSE ARRAY[ct.phase]::text[]
                                    END AS trial_phases,
                                    ct.therapeutic_area AS trial_therapeutic_area
           FROM access_grant ag
           JOIN data_asset da ON ag.asset_id = da.asset_id
                     LEFT JOIN clinical_trial ct
                         ON da.asset_type = 'clinical_trial'
                        AND da.reference_id = ct.trial_id
           WHERE ag.organization_id = $1
             AND ag.revoked_at IS NULL
             AND ag.expires_at > NOW()
           ORDER BY ag.granted_at DESC""",
        user.organization_id,
    )
    grants = [dict(r) for r in rows]

    for grant in grants:
        scope = dict(grant.get("scope") or {})
        trial_id = grant.get("trial_id")
        permitted_patient_count = 0

        if trial_id:
            conditions = ["pte.trial_id = $1::uuid"]
            params = [str(trial_id)]
            idx = 2

            region_values = _coerce_scope_list(scope, "permitted_regions", "regions", "region")
            if region_values:
                expanded_regions = _expand_region_literals(region_values)
                conditions.append(
                    "COALESCE(NULLIF(TRIM(p.region), ''), "
                    "(SELECT cr.region FROM country_region cr WHERE LOWER(cr.country) = LOWER(p.country) LIMIT 1)) "
                    f"= ANY(${idx}::text[])"
                )
                params.append(expanded_regions)
                idx += 1

            country_values = _coerce_scope_list(scope, "permitted_countries", "countries", "country")
            if country_values:
                conditions.append(f"p.country = ANY(${idx}::text[])")
                params.append(country_values)
                idx += 1

            area_values = _coerce_scope_list(scope, "permitted_areas", "therapeutic_areas", "areas", "area")
            if area_values:
                normalized_areas = [
                    v.lower().replace("-", "_").replace(" ", "_")
                    for v in area_values
                ]
                conditions.append(
                    "LOWER(REPLACE(REPLACE(COALESCE(ct.therapeutic_area, ''), '-', '_'), ' ', '_')) "
                    f"= ANY(${idx}::text[])"
                )
                params.append(normalized_areas)
                idx += 1

            phase_values = _coerce_scope_list(scope, "permitted_phases", "phases", "phase")
            if phase_values:
                normalized_phases = [v.upper().replace("PHASE ", "") for v in phase_values]
                conditions.append("UPPER(REPLACE(COALESCE(ct.phase, ''), 'PHASE ', '')) = ANY($" + str(idx) + "::text[])")
                params.append(normalized_phases)
                idx += 1

            where_clause = " AND ".join(conditions)
            permitted_patient_count = await db_pool.fetchval(
                f"""
                SELECT COUNT(DISTINCT p.patient_id)
                FROM patient p
                JOIN patient_trial_enrollment pte ON pte.patient_id = p.patient_id
                JOIN clinical_trial ct ON ct.trial_id = pte.trial_id
                WHERE {where_clause}
                """,
                *params,
            ) or 0

        grant["permitted_patient_count"] = int(permitted_patient_count)

    return {"grants": grants}


@router.post("/assignments/", dependencies=[Depends(require_role("manager"))])
async def assign_researcher(
    body: AssignResearcherBody,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """
    Assign a researcher to a trial or cohort.
    
    For individual access: writes OpenFGA tuples so the researcher
    can access patient-level data.
    
    For aggregate access: no OpenFGA tuple needed — aggregate access
    comes from org membership + granted_org automatically.
    
    For cohort assignments: expands to per-trial tuples for all trials
    in the cohort.
    """
    if not body.trial_id and not body.cohort_id:
        raise HTTPException(400, "Must specify either trial_id or cohort_id")

    # ── Resolve trial IDs ─────────────────────────────────────
    # If assigning to a cohort, get all trial_ids from cohort_trial
    trial_ids: list[str] = []

    if body.cohort_id:
        rows = await db_pool.fetch(
            "SELECT trial_id FROM cohort_trial WHERE cohort_id = $1",
            to_uuid(body.cohort_id),
        )
        trial_ids = [str(r["trial_id"]) for r in rows]

        if not trial_ids:
            raise HTTPException(400, f"Cohort {body.cohort_id} has no trials linked")

    elif body.trial_id:
        trial_ids = [str(body.trial_id)]

    # ── Ceiling check: verify org has active DB grant for ALL trials ────
    trial_ceiling_contexts: dict[str, dict] = {}
    for tid in trial_ids:
        grant_row = await db_pool.fetchrow(
            """
            SELECT ag.scope, ag.expires_at
            FROM access_grant ag
            JOIN data_asset da ON ag.asset_id = da.asset_id
            WHERE ag.organization_id = $1
              AND da.reference_id = $2::uuid
              AND ag.revoked_at IS NULL
              AND ag.expires_at > NOW()
            ORDER BY ag.expires_at DESC
            LIMIT 1
            """,
            user.organization_id,
            UUID(tid),
        )
        if not grant_row:
            raise HTTPException(
                403,
                f"Organization {user.organization_id} does not have access to "
                f"trial {tid}. Request access from the domain owner first."
            )
        trial_ceiling_contexts[tid] = build_condition_context_from_scope(
            grant_row["scope"] or {},
            valid_until=grant_row["expires_at"],
        )

    # ── Check researcher belongs to same org ──────────────────
    # In production, query Keycloak Admin API to verify.
    # For now, we trust the manager is assigning within their org.

    expires_at = datetime.now(timezone.utc) + timedelta(days=body.duration_days)

    delegated_context_by_trial: dict[str, dict] = {}
    if body.assignment_scope:
        for tid in trial_ids:
            try:
                delegated_context_by_trial[tid] = build_narrowed_delegation_context(
                    trial_ceiling_contexts.get(tid, {}),
                    delegated_valid_until=expires_at,
                    requested_scope=body.assignment_scope,
                )
            except ValueError as exc:
                raise HTTPException(422, f"Invalid assignment_scope for trial {tid}: {exc}")
    else:
        for tid in trial_ids:
            delegated_context_by_trial[tid] = build_delegation_context_from_ceiling(
                trial_ceiling_contexts.get(tid, {}),
                delegated_valid_until=expires_at,
            )

    # ── Write PostgreSQL record ───────────────────────────────
    outbox_tuples_enqueued = 0
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            assignment = await conn.fetchrow(
                """INSERT INTO researcher_assignment
                   (researcher_id, organization_id, trial_id, cohort_id,
                    access_level, assigned_by, expires_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   RETURNING assignment_id""",
                body.researcher_username,
                user.organization_id,
                to_uuid(body.trial_id),
                to_uuid(body.cohort_id),
                body.access_level,
                user.user_id,
                expires_at,
            )

            if body.access_level == "individual":
                tuples = [
                    {
                        "user": f"user:{body.researcher_username}",
                        "relation": "assigned_researcher",
                        "object": f"clinical_trial:{tid}",
                        "condition_name": "check_fine_grained_access",
                        "condition_context": delegated_context_by_trial.get(tid, {}),
                    }
                    for tid in trial_ids
                ]
                outbox_tuples_enqueued += await enqueue_write_tuples(
                    conn,
                    tuples,
                    source="manager.assign_researcher",
                    correlation_id=str(assignment["assignment_id"]),
                )

            if body.cohort_id:
                # Cohort-level tuple uses the safe intersection across all selected trials.
                base_ctx = {
                    "valid_from": delegated_context_by_trial[trial_ids[0]]["valid_from"] if trial_ids else None,
                    "valid_until": min((delegated_context_by_trial[tid]["valid_until"] for tid in trial_ids), default=None),
                    "permitted_regions": sorted(set.intersection(*[
                        set(delegated_context_by_trial[tid].get("permitted_regions", [])) for tid in trial_ids
                    ])) if trial_ids else [],
                    "permitted_areas": sorted(set.intersection(*[
                        set(delegated_context_by_trial[tid].get("permitted_areas", [])) for tid in trial_ids
                    ])) if trial_ids else [],
                    "permitted_phases": sorted(set.intersection(*[
                        set(delegated_context_by_trial[tid].get("permitted_phases", [])) for tid in trial_ids
                    ])) if trial_ids else [],
                    "approved_purposes": sorted(set.intersection(*[
                        set(delegated_context_by_trial[tid].get("approved_purposes", [])) for tid in trial_ids
                    ])) if trial_ids else [],
                    "resource_classification": max((
                        int(delegated_context_by_trial[tid].get("resource_classification", 1)) for tid in trial_ids
                    ), default=1),
                    "minimum_cohort_size": max((
                        int(delegated_context_by_trial[tid].get("minimum_cohort_size", 5)) for tid in trial_ids
                    ), default=5),
                }
                outbox_tuples_enqueued += await enqueue_write_tuples(
                    conn,
                    [{
                        "user": f"user:{body.researcher_username}",
                        "relation": "assigned_researcher",
                        "object": f"cohort:{body.cohort_id}",
                        "condition_name": "check_fine_grained_access",
                        "condition_context": base_ctx if base_ctx.get("valid_until") else build_condition_context_from_scope(
                            {},
                            valid_until=expires_at,
                        ),
                    }],
                    source="manager.assign_researcher",
                    correlation_id=str(assignment["assignment_id"]),
                )

            await conn.execute(
                """INSERT INTO auth_audit_log
                   (action, actor_id, actor_role, target_type, target_id, details)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
                "researcher_assigned",
                user.user_id,
                user.role,
                "researcher_assignment",
                str(assignment["assignment_id"]),
                json.dumps({
                    "researcher": body.researcher_username,
                    "trial_ids": [str(t) for t in trial_ids],
                    "cohort_id": str(body.cohort_id) if body.cohort_id else None,
                    "access_level": body.access_level,
                    "assignment_scope": body.assignment_scope or {},
                    "outbox_tuples_enqueued": outbox_tuples_enqueued,
                    "duration_days": body.duration_days,
                }),
            )

    return {
        "assignment_id": str(assignment["assignment_id"]),
        "researcher": body.researcher_username,
        "trial_ids": trial_ids,
        "access_level": body.access_level,
        "outbox_tuples_enqueued": outbox_tuples_enqueued,
        "fga_sync_status": "queued",
        "expires_at": expires_at.isoformat(),
        "status": "assigned",
    }


@router.post("/assignments/preview-visible-patients", dependencies=[Depends(require_role("manager"))])
async def preview_assignment_visible_patients(
    body: AssignmentVisiblePreviewBody,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    if not body.trial_id and not body.cohort_id:
        raise HTTPException(400, "Must specify either trial_id or cohort_id")

    trial_ids: list[str] = []
    cohort_filter_criteria: dict = {}

    async with db_pool.acquire() as conn:
        if body.cohort_id:
            rows = await conn.fetch(
                "SELECT ct.trial_id, c.filter_criteria FROM cohort_trial ct JOIN cohort c ON c.cohort_id = ct.cohort_id WHERE ct.cohort_id = $1",
                to_uuid(body.cohort_id),
            )
            trial_ids = [str(r["trial_id"]) for r in rows]
            if rows:
                cohort_filter_criteria = dict(rows[0].get("filter_criteria") or {})
        elif body.trial_id:
            trial_ids = [str(body.trial_id)]

        if not trial_ids:
            raise HTTPException(400, "No trials resolved for this assignment target")

        trial_ceiling_contexts = await _load_trial_ceiling_contexts(conn, user.organization_id, trial_ids)
        if len(trial_ceiling_contexts) < len(set(trial_ids)):
            raise HTTPException(403, "One or more selected trials are outside organization grant ceiling")

        expires_at = datetime.now(timezone.utc) + timedelta(days=max(1, int(body.duration_days or 180)))
        delegated_contexts: dict[str, dict] = {}
        for tid in trial_ids:
            if body.assignment_scope:
                delegated_contexts[tid] = build_narrowed_delegation_context(
                    trial_ceiling_contexts.get(tid, {}),
                    delegated_valid_until=expires_at,
                    requested_scope=body.assignment_scope,
                )
            else:
                delegated_contexts[tid] = build_delegation_context_from_ceiling(
                    trial_ceiling_contexts.get(tid, {}),
                    delegated_valid_until=expires_at,
                )

        visible_patient_count = await _count_visible_patients_for_trials(
            conn,
            trial_ids=trial_ids,
            delegated_contexts=delegated_contexts,
            cohort_filter_criteria=cohort_filter_criteria,
        )

    return {
        "visible_patient_count": int(visible_patient_count),
        "trial_count": len(trial_ids),
    }
# ─── Revoke Assignment ───────────────────────────────────────
 # ── Safe UUID conversion (handles str, UUID, or None) ─────
def to_uuid(val) -> UUID | None:
    if val is None:
        return None
    if isinstance(val, UUID):
        return val
    return UUID(str(val))
@router.post("/assignments/{assignment_id}/revoke",
             dependencies=[Depends(require_role("manager"))])
async def revoke_assignment(
    assignment_id: UUID,
    body: RevokeAssignmentBody,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """
    Revoke a researcher's assignment.
    Soft-deletes the PG record AND removes OpenFGA tuples.
    """
    outbox_tuples_enqueued = 0

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            assignment = await conn.fetchrow(
                """SELECT assignment_id, researcher_id, organization_id,
                          trial_id, cohort_id, access_level
                   FROM researcher_assignment
                   WHERE assignment_id = $1
                   AND organization_id = $2
                   AND revoked_at IS NULL""",
                assignment_id,
                user.organization_id,
            )

            if not assignment:
                raise HTTPException(404, "Assignment not found or already revoked")

            trial_ids: list[str] = []
            if assignment["cohort_id"]:
                rows = await conn.fetch(
                    "SELECT trial_id FROM cohort_trial WHERE cohort_id = $1",
                    assignment["cohort_id"],
                )
                trial_ids = [str(r["trial_id"]) for r in rows]
            elif assignment["trial_id"]:
                trial_ids = [str(assignment["trial_id"])]

            await conn.execute(
                """UPDATE researcher_assignment
                   SET revoked_at = NOW(), revoked_by = $1
                   WHERE assignment_id = $2""",
                user.user_id,
                assignment_id,
            )

            fga_delete_tuples: list[dict] = []
            if assignment["access_level"] == "individual" and trial_ids:
                for tid in trial_ids:
                    other_active = await conn.fetchval(
                        """SELECT COUNT(*) FROM researcher_assignment
                           WHERE researcher_id = $1
                           AND organization_id = $2
                           AND access_level = 'individual'
                           AND revoked_at IS NULL
                           AND expires_at > NOW()
                           AND assignment_id != $3
                           AND (
                               trial_id = $4::uuid
                               OR cohort_id IN (
                                   SELECT cohort_id FROM cohort_trial WHERE trial_id = $4::uuid
                               )
                           )""",
                        assignment["researcher_id"],
                        assignment["organization_id"],
                        assignment_id,
                        UUID(tid),
                    )
                    if other_active == 0:
                        fga_delete_tuples.append({
                            "user": f"user:{assignment['researcher_id']}",
                            "relation": "assigned_researcher",
                            "object": f"clinical_trial:{tid}",
                        })

            if assignment["cohort_id"]:
                other_cohort_active = await conn.fetchval(
                    """SELECT COUNT(*) FROM researcher_assignment
                       WHERE researcher_id = $1
                       AND organization_id = $2
                       AND cohort_id = $3
                       AND revoked_at IS NULL
                       AND expires_at > NOW()
                       AND assignment_id != $4""",
                    assignment["researcher_id"],
                    assignment["organization_id"],
                    assignment["cohort_id"],
                    assignment_id,
                )
                if other_cohort_active == 0:
                    fga_delete_tuples.append({
                        "user": f"user:{assignment['researcher_id']}",
                        "relation": "assigned_researcher",
                        "object": f"cohort:{assignment['cohort_id']}",
                    })

            outbox_tuples_enqueued = await enqueue_delete_tuples(
                conn,
                fga_delete_tuples,
                source="manager.revoke_assignment",
                correlation_id=str(assignment_id),
            )

            await conn.execute(
                """INSERT INTO auth_audit_log
                   (action, actor_id, actor_role, target_type, target_id, details)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
                "researcher_assignment_revoked",
                user.user_id,
                user.role,
                "researcher_assignment",
                str(assignment_id),
                json.dumps({
                    "researcher": assignment["researcher_id"],
                    "trial_ids": [str(t) for t in trial_ids],
                    "access_level": assignment["access_level"],
                    "outbox_tuples_enqueued": outbox_tuples_enqueued,
                    "reason": body.reason,
                }),
            )

    return {
        "assignment_id": str(assignment_id),
        "status": "revoked",
        "outbox_tuples_enqueued": outbox_tuples_enqueued,
        "fga_sync_status": "queued",
    }

@router.get("/assignments/", dependencies=[Depends(require_role("manager"))])
async def list_assignments(
    user: CurrentUser,
    include_revoked: bool = Query(False),
    db_pool=Depends(get_db_pool),
):
    """List all researcher assignments in my organization."""
    query = """
        SELECT ra.assignment_id, ra.researcher_id,
               ra.trial_id, ra.cohort_id,
               ra.access_level, ra.assigned_by,
               ra.assigned_at, ra.expires_at,
               ra.revoked_at, ra.revoked_by,
             (ra.revoked_at IS NULL AND ra.expires_at > NOW()) AS is_active,
               ct.nct_id as trial_nct_id, ct.title as trial_title,
                             c.name as cohort_name,
                             c.filter_criteria AS cohort_filter_criteria,
                         assign_audit.assignment_scope AS assignment_scope,
                             trial_grant.scope AS trial_grant_scope,
                             cohort_grant.scope AS cohort_grant_scope
        FROM researcher_assignment ra
        LEFT JOIN clinical_trial ct ON ra.trial_id = ct.trial_id
        LEFT JOIN cohort c ON ra.cohort_id = c.cohort_id
                LEFT JOIN LATERAL (
                    SELECT aal.details -> 'assignment_scope' AS assignment_scope
                    FROM auth_audit_log aal
                    WHERE aal.action = 'researcher_assigned'
                        AND aal.target_type = 'researcher_assignment'
                        AND aal.target_id = ra.assignment_id::text
                    ORDER BY aal.created_at DESC
                    LIMIT 1
                ) assign_audit ON TRUE
                LEFT JOIN LATERAL (
                        SELECT ag.scope
                        FROM data_asset da
                        JOIN access_grant ag ON ag.asset_id = da.asset_id
                        WHERE da.reference_id = ra.trial_id
                            AND da.asset_type = 'clinical_trial'
                            AND ag.organization_id = ra.organization_id
                            AND ag.revoked_at IS NULL
                            AND ag.expires_at > NOW()
                        ORDER BY ag.expires_at DESC
                        LIMIT 1
                ) trial_grant ON TRUE
                LEFT JOIN LATERAL (
                        SELECT ag.scope
                        FROM cohort_trial ct2
                        JOIN data_asset da ON da.reference_id = ct2.trial_id
                        JOIN access_grant ag ON ag.asset_id = da.asset_id
                        WHERE ct2.cohort_id = ra.cohort_id
                            AND da.asset_type = 'clinical_trial'
                            AND ag.organization_id = ra.organization_id
                            AND ag.revoked_at IS NULL
                            AND ag.expires_at > NOW()
                        ORDER BY ag.expires_at DESC
                        LIMIT 1
                ) cohort_grant ON TRUE
        WHERE ra.organization_id = $1
    """
    if not include_revoked:
        query += " AND ra.revoked_at IS NULL"

    query += " ORDER BY ra.assigned_at DESC"

    rows = await db_pool.fetch(query, user.organization_id)
    assignments = [dict(r) for r in rows]

    async with db_pool.acquire() as conn:
        for assignment in assignments:
            trial_ids: list[str] = []
            cohort_filter_criteria: dict = dict(assignment.get("cohort_filter_criteria") or {})
            if assignment.get("trial_id"):
                trial_ids = [str(assignment["trial_id"])]
            elif assignment.get("cohort_id"):
                cohort_rows = await conn.fetch(
                    "SELECT trial_id FROM cohort_trial WHERE cohort_id = $1",
                    assignment["cohort_id"],
                )
                trial_ids = [str(r["trial_id"]) for r in cohort_rows]

            if not trial_ids:
                assignment["visible_patient_count"] = 0
                continue

            trial_ceiling_contexts = await _load_trial_ceiling_contexts(conn, user.organization_id, trial_ids)
            delegated_contexts: dict[str, dict] = {}
            expires_at = assignment.get("expires_at") or (datetime.now(timezone.utc) + timedelta(days=180))
            assignment_scope = assignment.get("assignment_scope") or {}

            for tid in trial_ids:
                ceiling_ctx = trial_ceiling_contexts.get(tid)
                if not ceiling_ctx:
                    continue
                if assignment_scope:
                    delegated_contexts[tid] = build_narrowed_delegation_context(
                        ceiling_ctx,
                        delegated_valid_until=expires_at,
                        requested_scope=assignment_scope,
                    )
                else:
                    delegated_contexts[tid] = build_delegation_context_from_ceiling(
                        ceiling_ctx,
                        delegated_valid_until=expires_at,
                    )

            assignment["visible_patient_count"] = await _count_visible_patients_for_trials(
                conn,
                trial_ids=trial_ids,
                delegated_contexts=delegated_contexts,
                cohort_filter_criteria=cohort_filter_criteria,
            )

    return {"assignments": assignments}
    
@router.get("/cohorts/filter-options", dependencies=[Depends(require_role("manager"))])
async def get_filter_options(
    db_pool=Depends(get_db_pool),
):
    """Get dynamic database-driven enumerations for cohort UI."""
    service = CohortService(db_pool)
    return await service.get_filter_options()

@router.post("/cohorts/preview", dependencies=[Depends(require_role("manager"))])
async def preview_cohort(
    body: CohortCreateBody,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Preview a cohort before saving — returns patient count and ceiling validation."""
    service = CohortService(db_pool)
    return await service.preview_cohort(user, body.filter_criteria)


@router.post("/cohorts/", dependencies=[Depends(require_role("manager"))])
async def create_cohort(
    body: CohortCreateBody,
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """Create a cohort within the organization's access ceiling."""
    service = CohortService(db_pool)
    try:
        return await service.create_cohort(
            user, body.name, body.description,
            body.filter_criteria, body.is_dynamic,
        )
    except CeilingViolationError as e:
        raise HTTPException(403, detail={
            "message": "Cohort contains trials outside your organization's access ceiling",
            "violations": e.violations,
        })


@router.get("/cohorts/", dependencies=[Depends(require_role("manager"))])
async def list_cohorts(
    user: CurrentUser,
    db_pool=Depends(get_db_pool),
):
    """List all cohorts for the manager's organization."""
    service = CohortService(db_pool)
    cohorts = await service.list_org_cohorts(user)
    return {"cohorts": cohorts}