"""
Composite Clinical Tools — high-value multi-join queries that replace 3–5 individual
tool calls with a single, semantically coherent response.

Each tool is designed around a specific analyst workflow rather than a data table.
Authorization is enforced identically to all other MCP tools:
  - Trial-level: validate_trial_access() via AccessContext
  - Patient-level: build_authorized_patient_filter() for row-level security
  - Access ceiling: mixed-level access → aggregate output only

Tools registered here:
    cross_trial_safety_summary     AE landscape across ≥2 trials in one call
    cohort_outcome_snapshot        Disposition + key outcomes for a cohort
    trial_comparison_brief         Side-by-side KPIs for ≥2 trials
    patient_timeline_snapshot      Chronological milestones for one patient
    data_quality_overview          Completeness + anomaly flags per trial
"""

import logging
from typing import Any, Optional

from fastmcp import FastMCP

from access_control import AccessContext
from utils import success_response, error_response, serialize_row, _append_demographic_filters
from db import postgres
from observability import instrument_tool

logger = logging.getLogger(__name__)


def _parse_trial_ids(raw: str | list[str] | None) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t.strip() for t in str(raw).split(",") if t.strip()]


async def _resolve_nct_ids(ids: list[str]) -> list[str]:
    if not ids:
        return []
    resolved: list[str] = []
    nct_ids: list[str] = []
    for tid in ids:
        if tid.upper().startswith("NCT"):
            nct_ids.append(tid)
        else:
            resolved.append(tid)
    if nct_ids:
        try:
            rows = await postgres.fetch(
                "SELECT trial_id FROM clinical_trial WHERE nct_id = ANY($1::text[])",
                nct_ids,
            )
            resolved.extend(str(r["trial_id"]) for r in rows)
        except Exception as exc:
            logger.warning("NCT ID resolution failed: %s", exc)
    return resolved


def register_tools(mcp: FastMCP) -> None:

    # ──────────────────────────────────────────────────────────────────────────
    # cross_trial_safety_summary
    # ──────────────────────────────────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("cross_trial_safety_summary")
    async def cross_trial_safety_summary(
        trial_ids: list[str],
        access_context: str,
        severity: Optional[str] = None,
        serious_only: bool = False,
        top_n_terms: int = 10,
        sex: Optional[str] = None,
        age_min: Optional[int] = None,
        age_max: Optional[int] = None,
    ) -> str:
        """
        Return a comparative adverse-event safety landscape across two or more trials.

        Replaces multiple get_adverse_events calls when the analyst needs a
        cross-trial comparison in a single response. Returns per-trial AE
        counts, serious event rates, the top N event terms globally, and a
        severity breakdown — all scoped to the caller's authorized access.

        Args:
            trial_ids:      List (or comma-string) of trial IDs / NCT IDs to compare.
            access_context: Authorization context (injected by system).
            severity:       Optional filter — 'Mild' | 'Moderate' | 'Severe'.
            serious_only:   If True, include only events flagged as serious.
            top_n_terms:    Number of most frequent AE terms to surface (default 10).
            sex:            Optional demographic filter — 'M' | 'F' | 'Other'.
            age_min:        Optional minimum patient age at enrollment.
            age_max:        Optional maximum patient age at enrollment.

        Returns:
            JSON with per_trial_summary list, global_top_terms, and overall totals.
        """
        try:
            ctx = AccessContext.from_json(access_context)
            requested = await _resolve_nct_ids(_parse_trial_ids(trial_ids))
            authorized = ctx.validate_trial_access(requested)

            if not authorized:
                return error_response("No authorized trials for safety summary.", "ACCESS_DENIED")

            if len(authorized) < 2:
                return error_response(
                    "cross_trial_safety_summary requires at least 2 authorized trials. "
                    "Use get_adverse_events for single-trial queries.",
                    "INSUFFICIENT_TRIALS",
                )

            effective_level = ctx.get_effective_access_level(authorized)
            n = max(1, min(int(top_n_terms), 50))

            params: list[Any] = []
            idx = 1
            auth_where, auth_params, idx = ctx.build_authorized_patient_filter(
                authorized, param_offset=idx
            )
            params.extend(auth_params)

            extra: list[str] = []
            if severity and severity.strip():
                extra.append(f"ae.severity = ${idx}")
                params.append(severity.strip())
                idx += 1
            if serious_only:
                extra.append("ae.serious = TRUE")

            idx = _append_demographic_filters(
                extra, params, idx,
                sex=sex, age_min=age_min, age_max=age_max,
            )

            where = f"({auth_where})" + ("".join(f" AND {c}" for c in extra) if extra else "")

            # -- Per-trial AE summary --
            per_trial_sql = f"""
                SELECT
                    ae.trial_id::text                                    AS trial_id,
                    COUNT(*)                                             AS total_events,
                    COUNT(DISTINCT ae.patient_id)                        AS patients_with_ae,
                    COUNT(*) FILTER (WHERE ae.serious = TRUE)            AS serious_count,
                    COUNT(*) FILTER (WHERE ae.severity = 'Mild')         AS mild_count,
                    COUNT(*) FILTER (WHERE ae.severity = 'Moderate')     AS moderate_count,
                    COUNT(*) FILTER (WHERE ae.severity = 'Severe')       AS severe_count,
                    ROUND(
                        100.0 * COUNT(*) FILTER (WHERE ae.serious = TRUE)
                        / NULLIF(COUNT(*), 0), 2
                    )                                                    AS serious_pct
                FROM adverse_event ae
                JOIN patient p       ON ae.patient_id = p.patient_id
                JOIN patient_trial_enrollment pte
                                     ON p.patient_id = pte.patient_id
                WHERE ae.trial_id = pte.trial_id AND {where}
                GROUP BY ae.trial_id
                ORDER BY total_events DESC
            """
            per_trial_rows = await postgres.fetch(per_trial_sql, *params)

            # -- Global top N AE terms --
            top_terms_sql = f"""
                SELECT ae.ae_term, COUNT(*) AS occurrences,
                       COUNT(DISTINCT ae.trial_id) AS trial_count
                FROM adverse_event ae
                JOIN patient p       ON ae.patient_id = p.patient_id
                JOIN patient_trial_enrollment pte
                                     ON p.patient_id = pte.patient_id
                WHERE ae.trial_id = pte.trial_id AND {where}
                GROUP BY ae.ae_term
                ORDER BY occurrences DESC
                LIMIT {n}
            """
            top_terms_rows = await postgres.fetch(top_terms_sql, *params)

            return success_response(
                data={
                    "per_trial_summary": [serialize_row(r) for r in per_trial_rows],
                    "global_top_terms": [serialize_row(r) for r in top_terms_rows],
                    "trials_compared": len(authorized),
                    "access_level": effective_level,
                    "filters_applied": {
                        "severity": severity,
                        "serious_only": serious_only,
                        "top_n_terms": n,
                    },
                },
                metadata={"trial_count": len(authorized)},
            )

        except Exception as exc:
            logger.error("cross_trial_safety_summary failed: %s", exc, exc_info=True)
            return error_response(str(exc), "INTERNAL_ERROR")

    # ──────────────────────────────────────────────────────────────────────────
    # cohort_outcome_snapshot
    # ──────────────────────────────────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("cohort_outcome_snapshot")
    async def cohort_outcome_snapshot(
        trial_ids: list[str],
        access_context: str,
        arm_assigned: Optional[str] = None,
        sex: Optional[str] = None,
        age_min: Optional[int] = None,
        age_max: Optional[int] = None,
        ethnicity: Optional[str] = None,
    ) -> str:
        """
        Return a disposition and key-outcome snapshot for a cohort in one call.

        Combines enrollment counts, disposition status distribution, completion
        rates, and withdrawal reasons — covering what previously required
        separate patient_analytics and clinical_analysis calls.

        Args:
            trial_ids:      Trial IDs / NCT IDs (one or more).
            access_context: Authorization context (injected by system).
            arm_assigned:   Optional arm filter (e.g., 'Placebo', 'Treatment A').
            sex:            Optional sex filter.
            age_min:        Optional minimum patient age.
            age_max:        Optional maximum patient age.
            ethnicity:      Optional ethnicity filter.

        Returns:
            JSON with enrollment_summary, disposition_breakdown, and
            completion_stats per trial.
        """
        try:
            ctx = AccessContext.from_json(access_context)
            requested = await _resolve_nct_ids(_parse_trial_ids(trial_ids))
            authorized = ctx.validate_trial_access(requested)

            if not authorized:
                return error_response("No authorized trials for cohort snapshot.", "ACCESS_DENIED")

            effective_level = ctx.get_effective_access_level(authorized)

            params: list[Any] = []
            idx = 1
            auth_where, auth_params, idx = ctx.build_authorized_patient_filter(
                authorized, param_offset=idx
            )
            params.extend(auth_params)

            extra: list[str] = []
            idx = _append_demographic_filters(
                extra, params, idx,
                sex=sex, age_min=age_min, age_max=age_max,
                ethnicity=ethnicity, arm_assigned=arm_assigned,
            )

            where = f"({auth_where})" + ("".join(f" AND {c}" for c in extra) if extra else "")

            # -- Enrollment counts per trial + arm --
            enroll_sql = f"""
                SELECT
                    pte.trial_id::text          AS trial_id,
                    p.arm_assigned              AS arm,
                    COUNT(DISTINCT pte.patient_id) AS enrolled
                FROM patient_trial_enrollment pte
                JOIN patient p ON pte.patient_id = p.patient_id
                WHERE {where}
                GROUP BY pte.trial_id, p.arm_assigned
                ORDER BY pte.trial_id, enrolled DESC
            """
            enroll_rows = await postgres.fetch(enroll_sql, *params)

            # -- Disposition breakdown --
            disp_sql = f"""
                SELECT
                    pte.trial_id::text          AS trial_id,
                    p.disposition_status        AS status,
                    COUNT(*)                    AS count,
                    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY pte.trial_id), 2)
                                                AS pct
                FROM patient_trial_enrollment pte
                JOIN patient p ON pte.patient_id = p.patient_id
                WHERE {where}
                GROUP BY pte.trial_id, p.disposition_status
                ORDER BY pte.trial_id, count DESC
            """
            disp_rows = await postgres.fetch(disp_sql, *params)

            # -- Completion + withdrawal rates --
            rates_sql = f"""
                SELECT
                    pte.trial_id::text AS trial_id,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE p.disposition_status = 'Completed')  AS completed,
                    COUNT(*) FILTER (WHERE p.disposition_status = 'Withdrawn')  AS withdrawn,
                    COUNT(*) FILTER (WHERE p.disposition_status = 'Screen Failed') AS screen_failed,
                    ROUND(100.0 * COUNT(*) FILTER (WHERE p.disposition_status = 'Completed')
                        / NULLIF(COUNT(*), 0), 2) AS completion_rate_pct
                FROM patient_trial_enrollment pte
                JOIN patient p ON pte.patient_id = p.patient_id
                WHERE {where}
                GROUP BY pte.trial_id
            """
            rates_rows = await postgres.fetch(rates_sql, *params)

            return success_response(
                data={
                    "enrollment_by_arm": [serialize_row(r) for r in enroll_rows],
                    "disposition_breakdown": [serialize_row(r) for r in disp_rows],
                    "completion_stats": [serialize_row(r) for r in rates_rows],
                    "access_level": effective_level,
                },
                metadata={"trial_count": len(authorized)},
            )

        except Exception as exc:
            logger.error("cohort_outcome_snapshot failed: %s", exc, exc_info=True)
            return error_response(str(exc), "INTERNAL_ERROR")

    # ──────────────────────────────────────────────────────────────────────────
    # trial_comparison_brief
    # ──────────────────────────────────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("trial_comparison_brief")
    async def trial_comparison_brief(
        trial_ids: list[str],
        access_context: str,
    ) -> str:
        """
        Side-by-side KPI comparison for two or more authorized trials.

        Surfaces the most decision-relevant trial characteristics in one
        response: status, phase, enrollment, sponsor, indication, primary
        endpoint, AE burden, and completion rate. Designed for executive
        summaries and portfolio reviews.

        Args:
            trial_ids:      At least 2 trial IDs / NCT IDs.
            access_context: Authorization context (injected by system).

        Returns:
            JSON with a 'trials' list of KPI objects (one per trial) and a
            'comparison_notes' section highlighting key differences.
        """
        try:
            ctx = AccessContext.from_json(access_context)
            requested = await _resolve_nct_ids(_parse_trial_ids(trial_ids))
            authorized = ctx.validate_trial_access(requested)

            if len(authorized) < 2:
                return error_response(
                    "trial_comparison_brief requires at least 2 authorized trials.",
                    "INSUFFICIENT_TRIALS",
                )

            # -- Core trial metadata --
            meta_sql = """
                SELECT
                    ct.trial_id::text           AS trial_id,
                    ct.nct_id,
                    ct.title,
                    ct.phase,
                    ct.status,
                    ct.sponsor,
                    ct.indication,
                    ct.primary_endpoint
                FROM clinical_trial ct
                WHERE ct.trial_id = ANY($1::uuid[])
                ORDER BY ct.trial_id
            """
            meta_rows = await postgres.fetch(meta_sql, authorized)

            # -- Enrollment counts --
            enroll_sql = """
                SELECT
                    pte.trial_id::text AS trial_id,
                    COUNT(DISTINCT pte.patient_id) AS total_enrolled,
                    COUNT(*) FILTER (WHERE pte.disposition_status = 'Completed')  AS completed,
                    ROUND(
                        100.0 * COUNT(*) FILTER (WHERE pte.disposition_status = 'Completed')
                        / NULLIF(COUNT(DISTINCT pte.patient_id), 0), 2
                    ) AS completion_rate_pct
                FROM patient_trial_enrollment pte
                WHERE pte.trial_id = ANY($1::uuid[])
                GROUP BY pte.trial_id
            """
            enroll_rows = await postgres.fetch(enroll_sql, authorized)
            enroll_map = {r["trial_id"]: serialize_row(r) for r in enroll_rows}

            # -- AE burden per trial --
            ae_sql = """
                SELECT
                    ae.trial_id::text AS trial_id,
                    COUNT(*)          AS total_aes,
                    COUNT(*) FILTER (WHERE ae.serious = TRUE) AS serious_aes,
                    ROUND(
                        100.0 * COUNT(*) FILTER (WHERE ae.serious = TRUE)
                        / NULLIF(COUNT(*), 0), 2
                    ) AS serious_ae_pct
                FROM adverse_event ae
                WHERE ae.trial_id = ANY($1::uuid[])
                GROUP BY ae.trial_id
            """
            ae_rows = await postgres.fetch(ae_sql, authorized)
            ae_map = {r["trial_id"]: serialize_row(r) for r in ae_rows}

            # Compose KPI objects
            kpis: list[dict] = []
            phases: list[str] = []
            statuses: list[str] = []
            for row in meta_rows:
                tid = row["trial_id"]
                kpi = {
                    **serialize_row(row),
                    **enroll_map.get(tid, {"total_enrolled": 0, "completed": 0, "completion_rate_pct": None}),
                    **ae_map.get(tid, {"total_aes": 0, "serious_aes": 0, "serious_ae_pct": None}),
                }
                kpis.append(kpi)
                phases.append(str(row.get("phase", "")))
                statuses.append(str(row.get("status", "")))

            # Lightweight comparison notes
            notes: list[str] = []
            unique_phases = set(phases)
            if len(unique_phases) > 1:
                notes.append(f"Trials span multiple phases: {', '.join(sorted(unique_phases))}.")
            unique_statuses = set(statuses)
            if len(unique_statuses) > 1:
                notes.append(f"Mixed trial statuses detected: {', '.join(sorted(unique_statuses))}.")

            return success_response(
                data={
                    "trials": kpis,
                    "comparison_notes": notes,
                    "trials_compared": len(authorized),
                },
                metadata={"trial_count": len(authorized)},
            )

        except Exception as exc:
            logger.error("trial_comparison_brief failed: %s", exc, exc_info=True)
            return error_response(str(exc), "INTERNAL_ERROR")

    # ──────────────────────────────────────────────────────────────────────────
    # patient_timeline_snapshot
    # ──────────────────────────────────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("patient_timeline_snapshot")
    async def patient_timeline_snapshot(
        patient_id: str,
        trial_id: str,
        access_context: str,
    ) -> str:
        """
        Return a chronological milestone timeline for a single patient within
        a trial — enrollment, visits, key lab results, adverse events, and
        disposition — all in one sorted response.

        Requires individual-level access. Returns an error if the caller only
        has aggregate access to the requested trial.

        Args:
            patient_id:     UUID of the patient.
            trial_id:       Trial ID / NCT ID.
            access_context: Authorization context (injected by system).

        Returns:
            JSON with a 'timeline' list of events sorted by event_date, plus
            a 'patient_summary' header block.
        """
        try:
            ctx = AccessContext.from_json(access_context)
            requested = await _resolve_nct_ids([trial_id] if trial_id else [])
            authorized = ctx.validate_trial_access(requested)

            if not authorized:
                return error_response("No authorized access to this trial.", "ACCESS_DENIED")

            effective_level = ctx.get_effective_access_level(authorized)
            if effective_level != "individual":
                return error_response(
                    "patient_timeline_snapshot requires individual-level access. "
                    "You only have aggregate access to this trial.",
                    "ACCESS_LEVEL_INSUFFICIENT",
                )

            tid = authorized[0]

            # Verify patient is enrolled in the trial and in scope
            enroll_row = await postgres.fetchrow(
                """
                SELECT pte.patient_id::text, pte.arm_assigned, pte.enrollment_date,
                       pte.disposition_status, p.sex, p.age_at_enrollment, p.ethnicity
                FROM patient_trial_enrollment pte
                JOIN patient p ON pte.patient_id = p.patient_id
                WHERE pte.patient_id = $1::uuid
                  AND pte.trial_id = $2::uuid
                """,
                patient_id,
                tid,
            )
            if not enroll_row:
                return error_response(
                    "Patient not found in the requested trial or access denied.",
                    "NOT_FOUND",
                )

            # Collect timeline events from multiple tables
            timeline: list[dict] = []

            # Enrollment event
            timeline.append({
                "event_type": "enrollment",
                "event_date": str(enroll_row["enrollment_date"]) if enroll_row["enrollment_date"] else None,
                "description": f"Enrolled in arm: {enroll_row['arm_assigned']}",
            })

            # Adverse events
            ae_rows = await postgres.fetch(
                """
                SELECT ae_term, severity, serious, onset_date AS event_date
                FROM adverse_event
                WHERE patient_id = $1::uuid AND trial_id = $2::uuid
                ORDER BY onset_date NULLS LAST
                """,
                patient_id, tid,
            )
            for ae in ae_rows:
                timeline.append({
                    "event_type": "adverse_event",
                    "event_date": str(ae["event_date"]) if ae["event_date"] else None,
                    "description": f"AE: {ae['ae_term']} [{ae['severity']}{'  — SERIOUS' if ae['serious'] else ''}]",
                })

            # Lab results (most recent per test)
            lab_rows = await postgres.fetch(
                """
                SELECT DISTINCT ON (test_name)
                    test_name, result_value, unit, reference_range, result_flag,
                    collection_date AS event_date
                FROM lab_result
                WHERE patient_id = $1::uuid AND trial_id = $2::uuid
                ORDER BY test_name, collection_date DESC NULLS LAST
                LIMIT 20
                """,
                patient_id, tid,
            )
            for lab in lab_rows:
                flag = f" [{lab['result_flag']}]" if lab.get("result_flag") else ""
                timeline.append({
                    "event_type": "lab_result",
                    "event_date": str(lab["event_date"]) if lab["event_date"] else None,
                    "description": (
                        f"Lab: {lab['test_name']} = {lab['result_value']} {lab['unit'] or ''}"
                        f"{flag} (ref: {lab['reference_range'] or 'N/A'})"
                    ),
                })

            # Disposition (final event)
            timeline.append({
                "event_type": "disposition",
                "event_date": None,
                "description": f"Disposition: {enroll_row['disposition_status']}",
            })

            # Sort by date (None last)
            def _sort_key(ev: dict) -> str:
                return ev["event_date"] or "9999-99-99"

            timeline.sort(key=_sort_key)

            return success_response(
                data={
                    "patient_summary": {
                        "patient_id": patient_id,
                        "trial_id": tid,
                        "arm": enroll_row["arm_assigned"],
                        "sex": enroll_row["sex"],
                        "age_at_enrollment": enroll_row["age_at_enrollment"],
                        "ethnicity": enroll_row["ethnicity"],
                        "disposition": enroll_row["disposition_status"],
                    },
                    "timeline": timeline,
                    "event_count": len(timeline),
                },
                metadata={"access_level": effective_level},
            )

        except Exception as exc:
            logger.error("patient_timeline_snapshot failed: %s", exc, exc_info=True)
            return error_response(str(exc), "INTERNAL_ERROR")

    # ──────────────────────────────────────────────────────────────────────────
    # data_quality_overview
    # ──────────────────────────────────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("data_quality_overview")
    async def data_quality_overview(
        trial_ids: list[str],
        access_context: str,
    ) -> str:
        """
        Return a data-quality assessment for one or more trials: missing-data
        rates, enrollment anomalies, and AE-reporting completeness.

        Useful as a pre-flight check before analysis, or to surface data
        collection issues to data managers.

        Args:
            trial_ids:      Trial IDs / NCT IDs to assess.
            access_context: Authorization context (injected by system).

        Returns:
            JSON with per-trial quality_flags, completeness_pct, and a
            global quality_summary.
        """
        try:
            ctx = AccessContext.from_json(access_context)
            requested = await _resolve_nct_ids(_parse_trial_ids(trial_ids))
            authorized = ctx.validate_trial_access(requested)

            if not authorized:
                return error_response("No authorized trials for data quality check.", "ACCESS_DENIED")

            # -- Patient count and fields with nulls per trial --
            quality_sql = """
                SELECT
                    pte.trial_id::text AS trial_id,
                    COUNT(DISTINCT pte.patient_id)                           AS total_patients,
                    COUNT(*) FILTER (WHERE p.sex IS NULL)                    AS missing_sex,
                    COUNT(*) FILTER (WHERE p.age_at_enrollment IS NULL)      AS missing_age,
                    COUNT(*) FILTER (WHERE p.ethnicity IS NULL)              AS missing_ethnicity,
                    COUNT(*) FILTER (WHERE pte.arm_assigned IS NULL)         AS missing_arm,
                    COUNT(*) FILTER (WHERE pte.disposition_status IS NULL)   AS missing_disposition
                FROM patient_trial_enrollment pte
                JOIN patient p ON pte.patient_id = p.patient_id
                WHERE pte.trial_id = ANY($1::uuid[])
                GROUP BY pte.trial_id
                ORDER BY pte.trial_id
            """
            quality_rows = await postgres.fetch(quality_sql, authorized)

            # -- AE completeness: patients with at least one AE on record --
            ae_coverage_sql = """
                SELECT
                    pte.trial_id::text AS trial_id,
                    COUNT(DISTINCT pte.patient_id)                                AS enrolled,
                    COUNT(DISTINCT ae.patient_id)                                 AS patients_with_aes,
                    ROUND(
                        100.0 * COUNT(DISTINCT ae.patient_id)
                        / NULLIF(COUNT(DISTINCT pte.patient_id), 0), 2
                    )                                                             AS ae_coverage_pct
                FROM patient_trial_enrollment pte
                LEFT JOIN adverse_event ae
                    ON ae.patient_id = pte.patient_id AND ae.trial_id = pte.trial_id
                WHERE pte.trial_id = ANY($1::uuid[])
                GROUP BY pte.trial_id
            """
            ae_cov_rows = await postgres.fetch(ae_coverage_sql, authorized)
            ae_cov_map = {r["trial_id"]: serialize_row(r) for r in ae_cov_rows}

            per_trial: list[dict] = []
            overall_flags: list[str] = []

            for row in quality_rows:
                tid = row["trial_id"]
                total = row["total_patients"] or 1
                ae_data = ae_cov_map.get(tid, {})

                flags: list[str] = []
                if row["missing_sex"] / total > 0.05:
                    flags.append(f"Sex missing in {row['missing_sex']} records ({100 * row['missing_sex'] // total}%)")
                if row["missing_age"] / total > 0.05:
                    flags.append(f"Age missing in {row['missing_age']} records")
                if row["missing_arm"] / total > 0.02:
                    flags.append(f"Arm assignment missing in {row['missing_arm']} records")
                if row["missing_disposition"] / total > 0.02:
                    flags.append(f"Disposition missing in {row['missing_disposition']} records")

                ae_cov = ae_data.get("ae_coverage_pct", 100)
                if ae_cov is not None and ae_cov < 30:
                    flags.append(
                        f"Low AE coverage: only {ae_cov}% of patients have at least one AE reported"
                    )

                completeness_fields = [
                    1 - row["missing_sex"] / total,
                    1 - row["missing_age"] / total,
                    1 - row["missing_ethnicity"] / total,
                    1 - row["missing_arm"] / total,
                    1 - row["missing_disposition"] / total,
                ]
                overall_completeness = round(100 * sum(completeness_fields) / len(completeness_fields), 2)

                per_trial.append({
                    "trial_id": tid,
                    "total_patients": total,
                    "completeness_pct": overall_completeness,
                    "ae_coverage_pct": ae_data.get("ae_coverage_pct"),
                    "quality_flags": flags,
                })
                overall_flags.extend(f"[{tid}] {f}" for f in flags)

            return success_response(
                data={
                    "per_trial": per_trial,
                    "quality_summary": {
                        "trials_assessed": len(authorized),
                        "total_flags": len(overall_flags),
                        "all_flags": overall_flags,
                    },
                },
                metadata={"trial_count": len(authorized)},
            )

        except Exception as exc:
            logger.error("data_quality_overview failed: %s", exc, exc_info=True)
            return error_response(str(exc), "INTERNAL_ERROR")
