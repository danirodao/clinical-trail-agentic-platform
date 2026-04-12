"""
Clinical Analysis Tools — adverse events, labs, vitals, medications, arm comparisons.

Authorization enforced on every tool:
  - Trial-level filtering (only authorized trials)
  - Patient-level cohort filters (per-trial criteria)
  - Access level enforcement (aggregate vs individual)
  - Ceiling principle (mixed access → aggregate)
"""

import logging
from typing import Any, Optional

from fastmcp import FastMCP

from access_control import AccessContext
from utils import success_response, error_response, serialize_row
from db import postgres
from observability import instrument_tool

logger = logging.getLogger(__name__)


def _parse_trial_ids(raw: str | list[str] | None) -> list[str]:
    """Parse a comma-separated string or list of trial IDs into a list."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(tid).strip() for tid in raw if str(tid).strip()]
    return [tid.strip() for tid in str(raw).split(",") if tid.strip()]


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
        except Exception as e:
            logger.warning(f"NCT ID resolution failed: {e}")
    return resolved


def register_tools(mcp: FastMCP) -> None:

    # ------------------------------------------------------------------
    # get_adverse_events
    # ------------------------------------------------------------------
    @mcp.tool()
    @instrument_tool("get_adverse_events")
    async def get_adverse_events(
        trial_ids: list[str],
        access_context: str,
        severity: Optional[str] = None,
        serious_only: bool = False,
        event_term: Optional[str] = None,
        group_by: Optional[str] = None,
        limit: int = 100,
    ) -> str:
        """Get adverse event data from clinical trials."""
        try:
            ctx = AccessContext.from_json(access_context)
            
            # 1. Defensively resolve and validate IDs
            requested = await _resolve_nct_ids(_parse_trial_ids(trial_ids))
            authorized = ctx.validate_trial_access(requested)

            if not authorized:
                return error_response("No authorized trials found for adverse events query.", "ACCESS_DENIED")

            # 2. Determine access level
            effective_level = ctx.get_effective_access_level(authorized)
            max_records = min(limit, 500)

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
            if event_term and event_term.strip():
                extra.append(f"LOWER(ae.ae_term) LIKE LOWER(${idx})")
                params.append(f"%{event_term.strip()}%")
                idx += 1

            all_conds = [f"({auth_where})"] + extra
            where = " AND ".join(all_conds)

            # --- Summary ---
            summary_sql = f"""
                SELECT
                    COUNT(*)                                         AS total_events,
                    COUNT(DISTINCT ae.patient_id)                   AS patients_with_ae,
                    COUNT(*) FILTER (WHERE ae.serious = TRUE)       AS serious_count,
                    COUNT(*) FILTER (WHERE ae.severity = 'Mild')    AS mild_count,
                    COUNT(*) FILTER (WHERE ae.severity = 'Moderate') AS moderate_count,
                    COUNT(*) FILTER (WHERE ae.severity = 'Severe')  AS severe_count
                FROM adverse_event ae
                JOIN patient p ON ae.patient_id = p.patient_id
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                WHERE ae.trial_id = pte.trial_id AND {where}
            """
            summary_rows = await postgres.fetch(summary_sql, *params)
            summary = serialize_row(summary_rows[0]) if summary_rows else {}

            # --- Top AE terms ---
            top_sql = f"""
                SELECT ae.ae_term, COUNT(*) AS count
                FROM adverse_event ae
                JOIN patient p ON ae.patient_id = p.patient_id
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                WHERE ae.trial_id = pte.trial_id AND {where}
                GROUP BY ae.ae_term
                ORDER BY count DESC
                LIMIT 20
            """
            top_rows = await postgres.fetch(top_sql, *params)
            top_terms = [serialize_row(r) for r in top_rows]

            # --- Grouping ---
            VALID_GROUP_BY = {
                "severity":  "ae.severity", "serious":   "ae.serious",
                "term":      "ae.ae_term",  "arm":       "p.arm_assigned",
                "trial":     "ct.nct_id",   "outcome":   "ae.outcome",
            }
            grouped_data: list[dict] = []
            if group_by and group_by.strip():
                group_cols: list[str] = []
                group_exprs: list[str] = []
                needs_trial_join = False
                for dim in group_by.strip().split(","):
                    dim = dim.strip().lower()
                    if dim not in VALID_GROUP_BY:
                        return error_response(f"Invalid group_by '{dim}'. Options: {list(VALID_GROUP_BY.keys())}", "INVALID_ARGS")
                    expr = VALID_GROUP_BY[dim]
                    group_cols.append(f"{expr} AS {dim}")
                    group_exprs.append(expr)
                    if dim == "trial":
                        needs_trial_join = True

                trial_join = "JOIN clinical_trial ct ON ae.trial_id = ct.trial_id" if needs_trial_join else ""
                group_sql = f"""
                    SELECT {', '.join(group_cols)},
                           COUNT(*)                      AS event_count,
                           COUNT(DISTINCT ae.patient_id) AS patient_count
                    FROM adverse_event ae
                    JOIN patient p ON ae.patient_id = p.patient_id
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    {trial_join}
                    WHERE ae.trial_id = pte.trial_id AND {where}
                    GROUP BY {', '.join(group_exprs)}
                    ORDER BY event_count DESC
                    LIMIT 50
                """
                group_rows = await postgres.fetch(group_sql, *params)
                grouped_data = [serialize_row(r) for r in group_rows]

            # --- Individual records (ONLY IF INDIVIDUAL ACCESS) ---
            individual_records: list[dict] = []
            if effective_level == "individual":
                rec_sql = f"""
                    SELECT ae.ae_id, ae.patient_id, p.subject_id,
                           ae.ae_term, ae.severity, ae.serious, ae.outcome, 
                           ae.onset_date, ae.resolution_date,
                           p.arm_assigned, ct.nct_id
                    FROM adverse_event ae
                    JOIN patient p ON ae.patient_id = p.patient_id
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    JOIN clinical_trial ct ON ae.trial_id = ct.trial_id
                    WHERE ae.trial_id = pte.trial_id AND {where}
                    ORDER BY ae.onset_date DESC NULLS LAST
                    LIMIT {max_records}
                """
                rec_rows = await postgres.fetch(rec_sql, *params)
                individual_records = [serialize_row(r) for r in rec_rows]

            return success_response(
                data={
                    "summary": summary,
                    "top_ae_terms": top_terms,
                    "grouped": grouped_data or None,
                    "individual_records": individual_records or None,
                    "records_returned": len(individual_records),
                },
                metadata={"trials_queried": len(authorized), "effective_access_level": effective_level},
            )
        except Exception as e:
            return error_response(str(e), "TOOL_ERROR")

    # ------------------------------------------------------------------
    # get_lab_results
    # ------------------------------------------------------------------
    @mcp.tool()
    @instrument_tool("get_lab_results")
    async def get_lab_results(
        trial_ids: list[str],
        access_context: str,
        test_name: Optional[str] = None,
        loinc_code: Optional[str] = None,
        summary_only: bool = True,
    ) -> str:
        """Get laboratory test results from clinical trials."""
        try:
            ctx = AccessContext.from_json(access_context)
            requested = await _resolve_nct_ids(_parse_trial_ids(trial_ids))
            authorized = ctx.validate_trial_access(requested)

            if not authorized:
                return error_response("No authorized trials found for lab results query.", "ACCESS_DENIED")

            effective_level = ctx.get_effective_access_level(authorized)
            
            # FORCE summary only if they only have aggregate access
            if effective_level == "aggregate":
                summary_only = True

            params: list[Any] = []
            idx = 1
            auth_where, auth_params, idx = ctx.build_authorized_patient_filter(
                authorized, param_offset=idx
            )
            params.extend(auth_params)

            extra: list[str] = []
            if test_name and test_name.strip():
                extra.append(f"LOWER(lr.test_name) LIKE LOWER(${idx})")
                params.append(f"%{test_name.strip()}%")
                idx += 1
            if loinc_code and loinc_code.strip():
                extra.append(f"lr.loinc_code = ${idx}")
                params.append(loinc_code.strip())
                idx += 1

            all_conds = [f"({auth_where})"] + extra
            where = " AND ".join(all_conds)

            stats_data: list[dict] = []
            individual_records: list[dict] = []

            if summary_only:
                stats_sql = f"""
                    SELECT
                        lr.test_name,
                        lr.result_unit                                     AS unit,
                        COUNT(*)                                           AS result_count,
                        COUNT(DISTINCT lr.patient_id)                     AS patient_count,
                        ROUND(AVG(lr.result_value)::numeric, 2)           AS mean_value,
                        MIN(lr.result_value)                              AS min_value,
                        MAX(lr.result_value)                              AS max_value
                    FROM lab_result lr
                    JOIN patient p ON lr.patient_id = p.patient_id
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    WHERE lr.trial_id = pte.trial_id AND {where}
                    GROUP BY lr.test_name, lr.result_unit
                    ORDER BY result_count DESC
                    LIMIT 30
                """
                stats_rows = await postgres.fetch(stats_sql, *params)
                stats_data = [serialize_row(r) for r in stats_rows]
            else:
                rec_sql = f"""
                    SELECT lr.lab_id, lr.patient_id, p.subject_id,
                           lr.test_name, lr.loinc_code,
                           lr.result_value, lr.result_unit,
                           lr.collection_date, p.arm_assigned, ct.nct_id
                    FROM lab_result lr
                    JOIN patient p ON lr.patient_id = p.patient_id
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    JOIN clinical_trial ct ON lr.trial_id = ct.trial_id
                    WHERE lr.trial_id = pte.trial_id AND {where}
                    ORDER BY lr.collection_date DESC NULLS LAST
                    LIMIT 100
                """
                rec_rows = await postgres.fetch(rec_sql, *params)
                individual_records = [serialize_row(r) for r in rec_rows]

            return success_response(
                data={
                    "test_statistics":  stats_data if summary_only else None,
                    "individual_records": individual_records if not summary_only else None,
                },
                metadata={"effective_access_level": effective_level},
            )
        except Exception as e:
            return error_response(str(e), "TOOL_ERROR")

    # ------------------------------------------------------------------
    # get_vital_signs
    # ------------------------------------------------------------------
    @mcp.tool()
    @instrument_tool("get_vital_signs")
    async def get_vital_signs(
        trial_ids: list[str],
        access_context: str,
        vital_type: Optional[str] = None,
        summary_only: bool = True,
    ) -> str:
        """Get vital sign measurements from clinical trials."""
        try:
            ctx = AccessContext.from_json(access_context)
            requested = await _resolve_nct_ids(_parse_trial_ids(trial_ids))
            authorized = ctx.validate_trial_access(requested)

            if not authorized:
                return error_response("No authorized trials found for vital signs query.", "ACCESS_DENIED")

            effective_level = ctx.get_effective_access_level(authorized)
            if effective_level == "aggregate":
                summary_only = True

            params: list[Any] = []
            idx = 1
            auth_where, auth_params, idx = ctx.build_authorized_patient_filter(
                authorized, param_offset=idx
            )
            params.extend(auth_params)

            extra: list[str] = []
            if vital_type and vital_type.strip():
                extra.append(f"LOWER(vs.test_name) LIKE LOWER(${idx})")
                params.append(f"%{vital_type.strip()}%")
                idx += 1

            all_conds = [f"({auth_where})"] + extra
            where = " AND ".join(all_conds)

            stats_data = []
            individual_records = []

            if summary_only:
                stats_sql = f"""
                    SELECT
                        vs.test_name                                AS vital_type,
                        vs.result_unit                              AS unit,
                        COUNT(*)                                    AS measurement_count,
                        ROUND(AVG(vs.result_value)::numeric, 2)     AS mean_value,
                        MIN(vs.result_value)                        AS min_value,
                        MAX(vs.result_value)                        AS max_value
                    FROM vital_sign vs
                    JOIN patient p ON vs.patient_id = p.patient_id
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    WHERE vs.trial_id = pte.trial_id AND {where}
                    GROUP BY vs.test_name, vs.result_unit
                    ORDER BY measurement_count DESC
                """
                stats_rows = await postgres.fetch(stats_sql, *params)
                stats_data = [serialize_row(r) for r in stats_rows]
            else:
                rec_sql = f"""
                    SELECT vs.vital_id, vs.test_name AS vital_type, vs.result_value AS value,
                           vs.result_unit AS unit, vs.collection_date AS measurement_date,
                           p.arm_assigned
                    FROM vital_sign vs
                    JOIN patient p ON vs.patient_id = p.patient_id
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    WHERE vs.trial_id = pte.trial_id AND {where}
                    ORDER BY vs.collection_date DESC NULLS LAST
                    LIMIT 100
                """
                rec_rows = await postgres.fetch(rec_sql, *params)
                individual_records = [serialize_row(r) for r in rec_rows]

            return success_response(
                data={
                    "vital_statistics": stats_data if summary_only else None,
                    "individual_records": individual_records if not summary_only else None,
                },
                metadata={"effective_access_level": effective_level},
            )
        except Exception as e:
            return error_response(str(e), "TOOL_ERROR")

    # ------------------------------------------------------------------
    # get_concomitant_medications
    # ------------------------------------------------------------------
    @mcp.tool()
    @instrument_tool("get_concomitant_medications")
    async def get_concomitant_medications(
        trial_ids: list[str],
        access_context: str,
        medication_name: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """Get concomitant medications taken by patients in clinical trials."""
        try:
            ctx = AccessContext.from_json(access_context)
            requested = await _resolve_nct_ids(_parse_trial_ids(trial_ids))
            authorized = ctx.validate_trial_access(requested)

            if not authorized:
                return error_response("No authorized trials found for medications query.", "ACCESS_DENIED")

            effective_level = ctx.get_effective_access_level(authorized)
            max_records = min(limit, 500)

            params: list[Any] = []
            idx = 1
            auth_where, auth_params, idx = ctx.build_authorized_patient_filter(
                authorized, param_offset=idx
            )
            params.extend(auth_params)

            extra: list[str] = []
            if medication_name and medication_name.strip():
                extra.append(f"LOWER(pm.medication_name) LIKE LOWER(${idx})")
                params.append(f"%{medication_name.strip()}%")
                idx += 1

            all_conds = [f"({auth_where})"] + extra
            where = " AND ".join(all_conds)

            freq_sql = f"""
                SELECT
                    pm.medication_name,
                    COUNT(*)                      AS prescription_count,
                    COUNT(DISTINCT pm.patient_id) AS patient_count
                FROM patient_medication pm
                JOIN patient p ON pm.patient_id = p.patient_id
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                WHERE {where}
                GROUP BY pm.medication_name
                ORDER BY patient_count DESC
                LIMIT 50
            """
            freq_rows = await postgres.fetch(freq_sql, *params)
            frequency_data = [serialize_row(r) for r in freq_rows]

            individual_records = []
            if effective_level == "individual":
                rec_sql = f"""
                    SELECT pm.medication_id, pm.patient_id, pm.medication_name,
                           pm.dose_value, pm.dose_unit, pm.route,
                           pm.start_date, pm.end_date, p.arm_assigned
                    FROM patient_medication pm
                    JOIN patient p ON pm.patient_id = p.patient_id
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    WHERE {where}
                    ORDER BY pm.medication_name, pm.start_date
                    LIMIT {max_records}
                """
                rec_rows = await postgres.fetch(rec_sql, *params)
                individual_records = [serialize_row(r) for r in rec_rows]

            return success_response(
                data={
                    "medication_frequency": frequency_data,
                    "individual_records": individual_records or None,
                },
                metadata={"effective_access_level": effective_level},
            )
        except Exception as e:
            return error_response(str(e), "TOOL_ERROR")

    # ------------------------------------------------------------------
    # compare_treatment_arms
    # ------------------------------------------------------------------
    @mcp.tool()
    @instrument_tool("compare_treatment_arms")
    async def compare_treatment_arms(
        trial_id: str,
        access_context: str,
        dimensions: Optional[list[str]] = None,
    ) -> str:
        """Compare outcomes across treatment arms within a single clinical trial."""
        try:
            ctx = AccessContext.from_json(access_context)
            resolved = ctx.validate_trial_access([trial_id])
            
            if not resolved:
                return error_response("No authorized access to this trial.", "ACCESS_DENIED")
                
            resolved_id = resolved[0]
            effective_level = ctx.get_effective_access_level([resolved_id])

            params: list[Any] = []
            idx = 1
            auth_where, auth_params, idx = ctx.build_authorized_patient_filter(
                [resolved_id], param_offset=idx
            )
            params.extend(auth_params)

            requested_metrics = set(dimensions) if dimensions else {"demographics", "disposition", "adverse_events"}

            comparison: dict[str, Any] = {}

            # Arms list
            arms_rows = await postgres.fetch(
                "SELECT arm_id, arm_label AS arm_name, arm_type "
                "FROM trial_arm WHERE trial_id = $1::uuid ORDER BY arm_label",
                resolved_id,
            )
            comparison["arms"] = [serialize_row(r) for r in arms_rows]

            if "demographics" in requested_metrics:
                demo_sql = f"""
                    SELECT
                        p.arm_assigned,
                        COUNT(DISTINCT p.patient_id)            AS patient_count,
                        ROUND(AVG(p.age)::numeric, 1)           AS avg_age,
                        COUNT(*) FILTER (WHERE p.sex = 'M')     AS male_count,
                        COUNT(*) FILTER (WHERE p.sex = 'F')     AS female_count
                    FROM patient p
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    WHERE {auth_where}
                    GROUP BY p.arm_assigned
                    ORDER BY p.arm_assigned
                """
                demo_rows = await postgres.fetch(demo_sql, *params)
                comparison["demographics_by_arm"] = [serialize_row(r) for r in demo_rows]

            if "disposition" in requested_metrics:
                disp_sql = f"""
                    SELECT p.arm_assigned, p.disposition_status,
                           COUNT(DISTINCT p.patient_id) AS count
                    FROM patient p
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    WHERE {auth_where}
                    GROUP BY p.arm_assigned, p.disposition_status
                    ORDER BY p.arm_assigned, count DESC
                """
                disp_rows = await postgres.fetch(disp_sql, *params)
                by_arm: dict[str, list] = {}
                for row in disp_rows:
                    arm = row["arm_assigned"] or "Unknown"
                    by_arm.setdefault(arm, []).append({
                        "disposition_status": row["disposition_status"],
                        "count": row["count"],
                    })
                comparison["disposition_by_arm"] = [
                    {"arm": arm, "statuses": statuses}
                    for arm, statuses in by_arm.items()
                ]

            if "adverse_events" in requested_metrics:
                ae_sql = f"""
                    SELECT
                        p.arm_assigned,
                        COUNT(*)                                  AS total_events,
                        COUNT(DISTINCT ae.patient_id)             AS patients_with_ae,
                        COUNT(*) FILTER (WHERE ae.serious = TRUE) AS serious_events
                    FROM adverse_event ae
                    JOIN patient p ON ae.patient_id = p.patient_id
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    WHERE ae.trial_id = pte.trial_id AND {auth_where}
                    GROUP BY p.arm_assigned
                    ORDER BY p.arm_assigned
                """
                ae_rows = await postgres.fetch(ae_sql, *params)
                comparison["adverse_events_by_arm"] = [serialize_row(r) for r in ae_rows]

            trial_info = await postgres.fetchrow(
                "SELECT nct_id, title, phase FROM clinical_trial WHERE trial_id = $1::uuid",
                resolved_id,
            )

            return success_response(
                data={
                    "trial_id": resolved_id,
                    "nct_id":   trial_info["nct_id"]   if trial_info else None,
                    "title":    trial_info["title"]     if trial_info else None,
                    "comparison": comparison,
                },
                metadata={
                    "metrics_compared": sorted(requested_metrics),
                    "effective_access_level": effective_level, # Note: this query naturally supports aggregate access
                },
            )
        except Exception as e:
            return error_response(str(e), "TOOL_ERROR")