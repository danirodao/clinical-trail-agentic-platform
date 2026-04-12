"""
Patient Analytics Tools — counting, demographics, and disposition.

Authorization enforced on every tool:
  - Trial-level filtering (only authorized trials via validate_trial_access)
  - Patient-level cohort filters (per-trial criteria via build_authorized_patient_filter)
  - Access level enforcement: individual → full rows, aggregate → counts/stats only
  - Ceiling principle (mixed access → aggregate)

Tools:
    count_patients             Count patients with flexible grouping
    get_patient_demographics   Demographic breakdown
    get_patient_disposition    Enrollment/completion/withdrawal stats
"""

import logging
from typing import Any, Optional

from fastmcp import FastMCP

from access_control import AccessContext
from utils import success_response, error_response, serialize_row
from db import postgres
from observability import instrument_tool

logger = logging.getLogger(__name__)

# Valid group_by fields — allowlist prevents SQL injection
VALID_GROUP_BY = {
    "sex":                "p.sex",
    "race":               "p.race",
    "ethnicity":          "p.ethnicity",
    "country":            "p.country",
    "arm_assigned":       "p.arm_assigned",
    "disposition_status": "p.disposition_status",
    "age_bucket": (
        "CASE "
        "WHEN p.age < 18 THEN 'Under 18' "
        "WHEN p.age BETWEEN 18 AND 30 THEN '18-30' "
        "WHEN p.age BETWEEN 31 AND 45 THEN '31-45' "
        "WHEN p.age BETWEEN 46 AND 60 THEN '46-60' "
        "WHEN p.age BETWEEN 61 AND 75 THEN '61-75' "
        "WHEN p.age > 75 THEN 'Over 75' "
        "ELSE 'Unknown' END"
    ),
    "trial": "ct.nct_id",
}


def _parse_trial_ids(raw: str | list[str] | None) -> list[str]:
    """Parse a comma-separated string or list of trial IDs into a list."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(tid).strip() for tid in raw if str(tid).strip()]
    return [tid.strip() for tid in str(raw).split(",") if tid.strip()]


async def _resolve_nct_ids(ids: list[str]) -> list[str]:
    """Resolve NCT IDs to UUIDs, pass UUIDs through unchanged."""
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


def _describe_filters(*args: str) -> list[str]:
    """Build list of human-readable filter descriptions from non-empty args."""
    labels = [
        "sex", "ethnicity", "country", "age_min", "age_max",
        "condition", "arm_assigned", "disposition_status",
    ]
    return [
        f"{label}={value.strip()}"
        for label, value in zip(labels, args)
        if value and value.strip()
    ]


def register_tools(mcp: FastMCP) -> None:
    """Register patient analytics tools on the MCP server."""

    # -----------------------------------------------------------------------
    # count_patients
    # -----------------------------------------------------------------------
    @mcp.tool()
    @instrument_tool("count_patients")
    async def count_patients(
        trial_ids: list[str] | str | None = None,
        group_by: str = "",
        sex: str = "",
        ethnicity: str = "",
        country: str = "",
        age_min: str = "",
        age_max: str = "",
        condition: str = "",
        arm_assigned: str = "",
        disposition_status: str = "",
        access_context: str = "",
    ) -> str:
        """
        Count patients across one or more clinical trials with optional
        grouping and filtering.

        Use it for questions like:
        - "How many patients are in trial X?"
        - "How many female patients across all my trials?"
        - "Patient count by ethnicity in the melanoma study"
        - "How many Hispanic patients over 60 are enrolled?"

        The count respects cohort filters — if your access is restricted to
        specific patient subgroups, only those patients are counted.

        Args:
            trial_ids: Comma-separated trial UUIDs or NCT IDs. Leave empty
                       to count across ALL authorized trials.
            group_by: Optional grouping dimension. One of:
                      sex, race, ethnicity, country, arm_assigned,
                      disposition_status, age_bucket, trial.
            sex: Filter by sex — "M" or "F".
            ethnicity: Filter by ethnicity (e.g., "Hispanic or Latino").
            country: Filter by country.
            age_min: Minimum age (inclusive).
            age_max: Maximum age (inclusive).
            condition: Filter by medical condition name.
            arm_assigned: Filter by treatment arm name.
            disposition_status: Filter by status (Enrolled, Completed, Withdrawn).
            access_context: JSON authorization context (injected by system).
        """
        try:
            ctx = AccessContext.from_json(access_context)

            # 1. Defensively resolve and validate IDs
            requested = await _resolve_nct_ids(_parse_trial_ids(trial_ids))
            authorized = ctx.validate_trial_access(requested)

            if not authorized:
                return error_response(
                    "No authorized trials found for count query.", "ACCESS_DENIED"
                )

            # 2. Determine effective access level (ceiling principle)
            effective_level = ctx.get_effective_access_level(authorized)

            # 3. Build authorization WHERE clause
            params: list[Any] = []
            idx = 1
            auth_where, auth_params, idx = ctx.build_authorized_patient_filter(
                authorized, param_offset=idx
            )
            params.extend(auth_params)

            # 4. Additional user-supplied filters
            extra_conditions: list[str] = []

            if sex.strip():
                extra_conditions.append(f"p.sex = ${idx}")
                params.append(sex.strip().upper())
                idx += 1

            if ethnicity.strip():
                extra_conditions.append(f"LOWER(p.ethnicity) LIKE LOWER(${idx})")
                params.append(f"%{ethnicity.strip()}%")
                idx += 1

            if country.strip():
                extra_conditions.append(f"LOWER(p.country) LIKE LOWER(${idx})")
                params.append(f"%{country.strip()}%")
                idx += 1

            if age_min.strip():
                extra_conditions.append(f"p.age >= ${idx}")
                params.append(int(age_min.strip()))
                idx += 1

            if age_max.strip():
                extra_conditions.append(f"p.age <= ${idx}")
                params.append(int(age_max.strip()))
                idx += 1

            if arm_assigned.strip():
                extra_conditions.append(f"LOWER(p.arm_assigned) LIKE LOWER(${idx})")
                params.append(f"%{arm_assigned.strip()}%")
                idx += 1

            if disposition_status.strip():
                extra_conditions.append(
                    f"LOWER(p.disposition_status) LIKE LOWER(${idx})"
                )
                params.append(f"%{disposition_status.strip()}%")
                idx += 1

            if condition.strip():
                extra_conditions.append(f"""
                    EXISTS (
                        SELECT 1 FROM patient_condition pc
                        WHERE pc.patient_id = p.patient_id
                        AND LOWER(pc.condition_name) LIKE LOWER(${idx})
                    )
                """)
                params.append(f"%{condition.strip()}%")
                idx += 1

            # 5. Combine WHERE conditions
            all_conditions = [f"({auth_where})"] + extra_conditions
            where_clause = " AND ".join(all_conditions)

            # 6. Build GROUP BY
            group_columns: list[str] = []
            group_aliases: list[str] = []
            needs_trial_join = False

            if group_by.strip():
                for dim in group_by.strip().split(","):
                    dim = dim.strip().lower()
                    if dim not in VALID_GROUP_BY:
                        return error_response(
                            f"Invalid group_by dimension: '{dim}'. "
                            f"Valid options: {', '.join(sorted(VALID_GROUP_BY.keys()))}",
                            "INVALID_ARGS",
                        )
                    group_columns.append(f"{VALID_GROUP_BY[dim]} AS {dim}")
                    group_aliases.append(dim)
                    if dim == "trial":
                        needs_trial_join = True

            trial_join = (
                "JOIN clinical_trial ct ON pte.trial_id = ct.trial_id"
                if needs_trial_join else ""
            )

            # 7. Execute query
            if group_columns:
                select_cols = ", ".join(group_columns)
                group_exprs = ", ".join(
                    VALID_GROUP_BY[dim.strip().lower()]
                    for dim in group_by.strip().split(",")
                )
                sql = f"""
                    SELECT {select_cols},
                           COUNT(DISTINCT p.patient_id) AS count
                    FROM patient p
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    {trial_join}
                    WHERE {where_clause}
                    GROUP BY {group_exprs}
                    ORDER BY count DESC
                """
            else:
                sql = f"""
                    SELECT COUNT(DISTINCT p.patient_id) AS count
                    FROM patient p
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    {trial_join}
                    WHERE {where_clause}
                """

            rows = await postgres.fetch(sql, *params)

            if group_columns:
                grouped_data = [serialize_row(row) for row in rows]
                total = sum(row.get("count", 0) for row in grouped_data)
                data = {
                    "total_count": total,
                    "grouped_by": group_aliases,
                    "groups": grouped_data,
                }
            else:
                total = rows[0]["count"] if rows else 0
                data = {"total_count": total}

            return success_response(
                data=data,
                metadata={
                    "trials_queried": len(authorized),
                    "effective_access_level": effective_level,
                    "filters_applied": _describe_filters(
                        sex, ethnicity, country, age_min, age_max,
                        condition, arm_assigned, disposition_status,
                    ),
                    "cohort_filters_applied": ctx.get_filter_descriptions(),
                },
            )

        except Exception as e:
            logger.error(f"count_patients error: {e}", exc_info=True)
            return error_response(str(e), "TOOL_ERROR")

    # -----------------------------------------------------------------------
    # get_patient_demographics
    # -----------------------------------------------------------------------
    @mcp.tool()
    @instrument_tool("get_patient_demographics")
    async def get_patient_demographics(
        trial_ids: list[str] | str | None = None,
        access_context: str = "",
        include_individual_records: bool = False,
    ) -> str:
        """
        Get demographic breakdown for patients in authorized trials.

        Returns age statistics, sex distribution, ethnicity distribution,
        and optionally individual patient rows (only if individual access).

        Args:
            trial_ids: List of trial UUIDs or NCT IDs.
            access_context: JSON authorization context (injected by system).
            include_individual_records: If True and access level is individual,
                                        returns up to 100 individual patient rows.
        """
        try:
            ctx = AccessContext.from_json(access_context)

            # 1. Defensively resolve and validate IDs
            requested = await _resolve_nct_ids(_parse_trial_ids(trial_ids))
            authorized = ctx.validate_trial_access(requested)

            if not authorized:
                return error_response(
                    "No authorized trials found for demographics query.", "ACCESS_DENIED"
                )

            # 2. Ceiling principle
            effective_level = ctx.get_effective_access_level(authorized)

            # NEVER return individual records for aggregate-only access
            if effective_level == "aggregate":
                include_individual_records = False

            # 3. Build authorization WHERE clause
            params: list[Any] = []
            idx = 1
            auth_where, auth_params, idx = ctx.build_authorized_patient_filter(
                authorized, param_offset=idx
            )
            params.extend(auth_params)

            # 4. Age statistics
            age_sql = f"""
                SELECT
                    COUNT(DISTINCT p.patient_id) AS total_patients,
                    ROUND(AVG(p.age)::numeric, 1) AS avg_age,
                    MIN(p.age) AS min_age,
                    MAX(p.age) AS max_age
                FROM patient p
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                WHERE {auth_where}
            """

            age_group_sql = f"""
                SELECT
                    CASE
                        WHEN p.age < 18 THEN '<18'
                        WHEN p.age BETWEEN 18 AND 34 THEN '18-34'
                        WHEN p.age BETWEEN 35 AND 49 THEN '35-49'
                        WHEN p.age BETWEEN 50 AND 64 THEN '50-64'
                        WHEN p.age >= 65 THEN '65+'
                        ELSE 'Unknown'
                    END AS age_bucket,
                    COUNT(DISTINCT p.patient_id) AS count
                FROM patient p
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                WHERE {auth_where}
                GROUP BY age_bucket
                ORDER BY age_bucket
            """

            sex_sql = f"""
                SELECT p.sex, COUNT(DISTINCT p.patient_id) AS count
                FROM patient p
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                WHERE {auth_where}
                GROUP BY p.sex ORDER BY count DESC
            """

            eth_sql = f"""
                SELECT p.ethnicity, COUNT(DISTINCT p.patient_id) AS count
                FROM patient p
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                WHERE {auth_where}
                GROUP BY p.ethnicity ORDER BY count DESC
            """

            race_sql = f"""
                SELECT p.race, COUNT(DISTINCT p.patient_id) AS count
                FROM patient p
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                WHERE {auth_where}
                GROUP BY p.race ORDER BY count DESC
            """

            age_row = await postgres.fetchrow(age_sql, *params)
            age_group_rows = await postgres.fetch(age_group_sql, *params)
            sex_rows = await postgres.fetch(sex_sql, *params)
            eth_rows = await postgres.fetch(eth_sql, *params)
            race_rows = await postgres.fetch(race_sql, *params)

            # 5. Individual records — ONLY if individual access
            individual_records: list[dict] = []
            if include_individual_records and effective_level == "individual":
                rec_sql = f"""
                    SELECT
                        p.patient_id, p.subject_id, p.age, p.sex, p.race,
                        p.ethnicity, p.country, p.arm_assigned,
                        p.disposition_status, pte.trial_id
                    FROM patient p
                    JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                    WHERE {auth_where}
                    ORDER BY p.subject_id
                    LIMIT 100
                """
                rec_rows = await postgres.fetch(rec_sql, *params)
                individual_records = [serialize_row(r) for r in rec_rows]

            return success_response(
                data={
                    "total_patients": age_row["total_patients"] if age_row else 0,
                    "age_stats": serialize_row(age_row) if age_row else {},
                    "age_distribution": [serialize_row(r) for r in age_group_rows],
                    "sex_distribution": [serialize_row(r) for r in sex_rows],
                    "ethnicity_distribution": [serialize_row(r) for r in eth_rows],
                    "race_distribution": [serialize_row(r) for r in race_rows],
                    "individual_records": individual_records or None,
                },
                metadata={
                    "trials_queried": len(authorized),
                    "effective_access_level": effective_level,
                    "individual_records_returned": len(individual_records),
                    "cohort_filters_applied": ctx.get_filter_descriptions(),
                },
            )

        except Exception as e:
            logger.error(f"get_patient_demographics error: {e}", exc_info=True)
            return error_response(str(e), "TOOL_ERROR")

    # -----------------------------------------------------------------------
    # get_patient_disposition
    # -----------------------------------------------------------------------
    @mcp.tool()
    @instrument_tool("get_patient_disposition")
    async def get_patient_disposition(
        trial_ids: list[str] | str | None = None,
        access_context: str = "",
    ) -> str:
        """
        Get patient disposition data: enrollment, completion, and withdrawal
        statistics across one or more trials.

        Use this for questions like:
        - "How many patients completed a trial?"
        - "What is the dropout/withdrawal rate?"
        - "Show me patient flow through the study."
        - "What is the retention rate?"

        Args:
            trial_ids: List of trial UUIDs or NCT IDs. Leave empty
                       for all authorized trials.
            access_context: JSON authorization context (injected by system).
        """
        try:
            ctx = AccessContext.from_json(access_context)

            # 1. Defensively resolve and validate IDs
            requested = await _resolve_nct_ids(_parse_trial_ids(trial_ids))
            authorized = ctx.validate_trial_access(requested)

            if not authorized:
                return error_response(
                    "No authorized trials found for disposition query.", "ACCESS_DENIED"
                )

            # 2. Ceiling principle
            effective_level = ctx.get_effective_access_level(authorized)

            # 3. Build authorization WHERE clause
            params: list[Any] = []
            idx = 1
            auth_where, auth_params, idx = ctx.build_authorized_patient_filter(
                authorized, param_offset=idx
            )
            params.extend(auth_params)

            # 4. Overall disposition counts
            overall_sql = f"""
                SELECT p.disposition_status,
                       COUNT(DISTINCT p.patient_id) AS count
                FROM patient p
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                WHERE {auth_where}
                GROUP BY p.disposition_status
                ORDER BY count DESC
            """

            # 5. Disposition broken down by trial
            by_trial_sql = f"""
                SELECT ct.nct_id, ct.title,
                       p.disposition_status,
                       COUNT(DISTINCT p.patient_id) AS count
                FROM patient p
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                JOIN clinical_trial ct ON pte.trial_id = ct.trial_id
                WHERE {auth_where}
                GROUP BY ct.nct_id, ct.title, p.disposition_status
                ORDER BY ct.nct_id, count DESC
            """

            # 6. Disposition broken down by arm (within each trial)
            by_arm_sql = f"""
                SELECT ct.nct_id,
                       p.arm_assigned,
                       p.disposition_status,
                       COUNT(DISTINCT p.patient_id) AS count
                FROM patient p
                JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
                JOIN clinical_trial ct ON pte.trial_id = ct.trial_id
                WHERE {auth_where}
                GROUP BY ct.nct_id, p.arm_assigned, p.disposition_status
                ORDER BY ct.nct_id, p.arm_assigned, count DESC
            """

            overall_rows = await postgres.fetch(overall_sql, *params)
            by_trial_rows = await postgres.fetch(by_trial_sql, *params)
            by_arm_rows = await postgres.fetch(by_arm_sql, *params)

            # 7. Build overall summary with percentages
            overall = [serialize_row(r) for r in overall_rows]
            total = sum(r.get("count", 0) for r in overall)
            for entry in overall:
                entry["percentage"] = (
                    round(entry["count"] / total * 100, 1) if total > 0 else 0
                )

            # 8. Group by-trial rows
            trial_disposition: dict[str, dict] = {}
            for row in by_trial_rows:
                nct = row["nct_id"]
                if nct not in trial_disposition:
                    trial_disposition[nct] = {
                        "nct_id": nct,
                        "title": row["title"],
                        "statuses": [],
                    }
                trial_disposition[nct]["statuses"].append({
                    "disposition_status": row["disposition_status"],
                    "count": row["count"],
                })

            for trial_data in trial_disposition.values():
                trial_total = sum(s["count"] for s in trial_data["statuses"])
                trial_data["total_patients"] = trial_total
                for s in trial_data["statuses"]:
                    s["percentage"] = (
                        round(s["count"] / trial_total * 100, 1)
                        if trial_total > 0 else 0
                    )

            # 9. Group by-arm rows
            arm_disposition: dict[str, list] = {}
            for row in by_arm_rows:
                key = f"{row['nct_id']}|{row['arm_assigned']}"
                arm_disposition.setdefault(key, []).append({
                    "nct_id": row["nct_id"],
                    "arm_assigned": row["arm_assigned"],
                    "disposition_status": row["disposition_status"],
                    "count": row["count"],
                })

            return success_response(
                data={
                    "total_patients": total,
                    "overall_disposition": overall,
                    "by_trial": list(trial_disposition.values()),
                    "by_arm": [
                        {
                            "nct_id": entries[0]["nct_id"],
                            "arm_assigned": entries[0]["arm_assigned"],
                            "statuses": [
                                {
                                    "disposition_status": e["disposition_status"],
                                    "count": e["count"],
                                }
                                for e in entries
                            ],
                        }
                        for entries in arm_disposition.values()
                    ],
                },
                metadata={
                    "trials_queried": len(authorized),
                    "effective_access_level": effective_level,
                    "cohort_filters_applied": ctx.get_filter_descriptions(),
                },
            )

        except Exception as e:
            logger.error(f"get_patient_disposition error: {e}", exc_info=True)
            return error_response(str(e), "TOOL_ERROR")

    logger.info(
        "Patient analytics tools registered: "
        "count_patients, get_patient_demographics, get_patient_disposition"
    )