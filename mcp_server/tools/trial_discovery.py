"""
Trial Discovery Tools — finding and exploring clinical trials.

Authorization enforced on every tool:
  - Trial-level filtering (only authorized trials via validate_trial_access)
  - These tools return trial METADATA only (no patient rows), so they are
    safe for both individual AND aggregate access levels.
  - enforce_individual_access_only is intentionally NOT used here.

Tools:
    search_trials             Semantic + structured trial search
    get_trial_details         Full metadata for a specific trial
    get_eligibility_criteria  Inclusion/exclusion criteria
"""

import logging
from typing import Any, Optional

from fastmcp import FastMCP

from access_control import AccessContext
from utils import success_response, error_response, serialize_row
from db import postgres, qdrant_client

logger = logging.getLogger(__name__)


def _parse_trial_ids(raw: str | list[str] | None) -> list[str]:
    """Parse a comma-separated string or list of trial IDs into a list."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(tid).strip() for tid in raw if str(tid).strip()]
    return [tid.strip() for tid in str(raw).split(",") if tid.strip()]


async def _resolve_nct_id_to_uuid(nct_id: str) -> Optional[str]:
    """Resolve a single NCT ID to its UUID. Returns None if not found."""
    try:
        row = await postgres.fetchrow(
            "SELECT trial_id FROM clinical_trial WHERE nct_id = $1",
            nct_id.strip()
        )
        return str(row["trial_id"]) if row else None
    except Exception as e:
        logger.warning(f"NCT ID resolution failed for {nct_id}: {e}")
        return None


def register_tools(mcp: FastMCP) -> None:
    """Register trial discovery tools on the MCP server."""

    # -----------------------------------------------------------------------
    # search_trials
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def search_trials(
        query: str = "",
        phase: str = "",
        therapeutic_area: str = "",
        status: str = "",
        sponsor: str = "",
        limit: int = 20,
        access_context: str = "",
    ) -> str:
        """
        Search for clinical trials using semantic search and/or structured filters.

        Use this tool when a researcher asks about finding trials, or when you need
        to identify which trials match a topic, drug, condition, or keyword.
        Also use this to resolve an NCT ID to a UUID before calling patient tools.

        This tool searches BOTH the vector store (semantic similarity over trial
        documents) and the structured database (exact match on phase, area, status).
        Results are merged and deduplicated.

        Args:
            query: Natural language search query (e.g., "melanoma immunotherapy",
                   "diabetes Phase 3 trials", "semaglutide safety"). Leave empty
                   to use only structured filters.
            phase: Filter by phase. One of: Phase 1, Phase 2, Phase 3, Phase 4.
            therapeutic_area: Filter by therapeutic area (e.g., Oncology, Cardiology).
            status: Filter by trial status (e.g., Completed, Recruiting, Active).
            sponsor: Filter by lead sponsor name (partial match).
            limit: Maximum number of results (default 20).
            access_context: JSON authorization context (injected by system).

        Returns:
            JSON with matching trials including trial_id (UUID), nct_id, title,
            phase, therapeutic_area, status, enrollment_count, and relevance_score.
        """
        try:
            ctx = AccessContext.from_json(access_context)

            if not ctx.allowed_trial_ids:
                return error_response(
                    "You do not have access to any clinical trials.", "NO_ACCESS"
                )

            max_results = min(int(limit), 50)
            results_by_id: dict[str, dict[str, Any]] = {}

            # --- Semantic search via Qdrant ---
            if query.strip():
                try:
                    vector_results = await qdrant_client.search_vectors(
                        query_text=query.strip(),
                        trial_ids=ctx.allowed_trial_ids,
                        limit=max_results,
                        score_threshold=0.25,
                    )
                    # Group by trial_id, keep highest score per trial
                    for chunk in vector_results:
                        tid = chunk["trial_id"]
                        existing = results_by_id.get(tid)
                        if existing is None or chunk["score"] > existing.get("relevance_score", 0):
                            results_by_id[tid] = {
                                "trial_id":             tid,
                                "nct_id":               chunk["nct_id"],
                                "relevance_score":      chunk["score"],
                                "matched_section":      chunk["section"],
                                "matched_text_preview": chunk["chunk_text"][:200],
                                "_source":              "semantic",
                            }
                except Exception as e:
                    logger.warning(f"Qdrant search failed (falling back to SQL): {e}")

            # --- Structured search via PostgreSQL ---
            conditions: list[str] = []
            params: list[Any] = []
            idx = 1

            # Always filter to authorized trials only
            trial_filter, trial_params, idx = ctx.build_trial_id_filter(
                ctx.allowed_trial_ids, param_offset=idx, column="ct.trial_id"
            )
            conditions.append(trial_filter)
            params.extend(trial_params)

            if phase.strip():
                conditions.append(f"ct.phase = ${idx}")
                params.append(phase.strip())
                idx += 1

            if therapeutic_area.strip():
                conditions.append(f"LOWER(ct.therapeutic_area) LIKE LOWER(${idx})")
                params.append(f"%{therapeutic_area.strip()}%")
                idx += 1

            if status.strip():
                conditions.append(f"LOWER(ct.overall_status) LIKE LOWER(${idx})")
                params.append(f"%{status.strip()}%")
                idx += 1

            if sponsor.strip():
                conditions.append(f"LOWER(ct.lead_sponsor) LIKE LOWER(${idx})")
                params.append(f"%{sponsor.strip()}%")
                idx += 1

            where_clause = " AND ".join(conditions)
            sql = f"""
                SELECT ct.trial_id, ct.nct_id, ct.title, ct.phase,
                       ct.therapeutic_area, ct.overall_status, ct.study_type,
                       ct.enrollment_count, ct.lead_sponsor,
                       ct.start_date, ct.completion_date,
                       ct.regions, ct.countries
                FROM clinical_trial ct
                WHERE {where_clause}
                ORDER BY ct.start_date DESC NULLS LAST
                LIMIT {max_results}
            """

            rows = await postgres.fetch(sql, *params)

            # Merge SQL results with semantic results
            for row in rows:
                tid = str(row["trial_id"])
                if tid in results_by_id:
                    # Enrich existing semantic result with full metadata
                    results_by_id[tid].update(serialize_row(row))
                else:
                    entry = serialize_row(row)
                    entry["relevance_score"] = None
                    entry["_source"] = "structured"
                    results_by_id[tid] = entry

            # Sort: semantic matches first (by score), then structured (by date)
            all_results = list(results_by_id.values())
            all_results.sort(
                key=lambda x: (
                    0 if x.get("relevance_score") else 1,
                    -(x.get("relevance_score") or 0),
                )
            )

            # Enrich each result with its access level for the researcher
            for result in all_results:
                tid = result.get("trial_id", "")
                trial_access = ctx.trial_access.get(tid)
                result["access_level"] = trial_access.access_level if trial_access else "none"

            return success_response(
                data=all_results[:max_results],
                metadata={
                    "total_found":           len(all_results),
                    "returned":              min(len(all_results), max_results),
                    "search_type":           "semantic+structured" if query.strip() else "structured",
                    "authorized_trial_count": len(ctx.allowed_trial_ids),
                },
            )

        except Exception as e:
            logger.error(f"search_trials error: {e}", exc_info=True)
            return error_response(str(e), "TOOL_ERROR")

    # -----------------------------------------------------------------------
    # get_trial_details
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def get_trial_details(
        trial_id: str = "",
        nct_id: str = "",
        access_context: str = "",
    ) -> str:
        """
        Get complete metadata for a specific clinical trial.

        Use this when a researcher asks about a specific trial's details,
        including its phase, sponsor, enrollment, dates, regions, and arms.

        Provide either trial_id (UUID) or nct_id (e.g., NCT10000131).
        This tool returns trial METADATA only — no patient records.
        It is available to both individual and aggregate access levels.

        Args:
            trial_id: UUID of the trial.
            nct_id: NCT identifier (e.g., NCT10000131).
            access_context: JSON authorization context (injected by system).

        Returns:
            JSON with full trial metadata including arms and interventions.
        """
        try:
            ctx = AccessContext.from_json(access_context)

            if not trial_id and not nct_id:
                return error_response("Provide trial_id or nct_id.", "INVALID_ARGS")

            # Lookup trial row
            if trial_id.strip():
                trial_row = await postgres.fetchrow(
                    "SELECT * FROM clinical_trial WHERE trial_id = $1::uuid",
                    trial_id.strip()
                )
            else:
                trial_row = await postgres.fetchrow(
                    "SELECT * FROM clinical_trial WHERE nct_id = $1",
                    nct_id.strip()
                )

            if not trial_row:
                return error_response(
                    f"Trial not found: {trial_id or nct_id}", "NOT_FOUND"
                )

            resolved_id = str(trial_row["trial_id"])

            # Validate access — use validate_trial_access so it resolves defensively
            authorized = ctx.validate_trial_access([resolved_id])
            if not authorized:
                return error_response(
                    f"No access to trial {trial_row.get('nct_id', resolved_id)}.",
                    "ACCESS_DENIED",
                )

            trial_data = serialize_row(trial_row)

            # Fetch arms
            arms_sql = """
                SELECT arm_id, arm_label AS arm_name, arm_type,
                       description, target_enrollment
                FROM trial_arm
                WHERE trial_id = $1::uuid
                ORDER BY arm_label
            """
            arm_rows = await postgres.fetch(arms_sql, resolved_id)
            trial_data["arms"] = [serialize_row(a) for a in arm_rows]

            # Fetch interventions per arm
            if trial_data["arms"]:
                arm_ids = [a["arm_id"] for a in trial_data["arms"]]
                interventions_sql = """
                    SELECT i.intervention_id, i.arm_id,
                           i.name             AS drug_name,
                           i.generic_name,
                           i.intervention_type,
                           i.dosage_form,
                           i.dose_value,
                           i.dose_unit,
                           i.route,
                           i.frequency
                    FROM intervention i
                    WHERE i.arm_id = ANY($1::uuid[])
                    ORDER BY i.name
                """
                try:
                    int_rows = await postgres.fetch(interventions_sql, arm_ids)
                    interventions_by_arm: dict[str, list] = {}
                    for row in int_rows:
                        aid = str(row["arm_id"])
                        entry = serialize_row(row)
                        if row["dose_value"] is not None:
                            entry["dose"] = f"{row['dose_value']} {row['dose_unit'] or ''}".strip()
                        interventions_by_arm.setdefault(aid, []).append(entry)

                    for arm in trial_data["arms"]:
                        arm["interventions"] = interventions_by_arm.get(arm["arm_id"], [])
                except Exception as e:
                    logger.warning(f"Failed to fetch interventions: {e}")

            # Patient count (aggregate-safe — just a COUNT)
            try:
                count = await postgres.fetchval(
                    "SELECT COUNT(DISTINCT pte.patient_id) "
                    "FROM patient_trial_enrollment pte "
                    "WHERE pte.trial_id = $1::uuid",
                    resolved_id,
                )
                trial_data["total_patient_count"] = count or 0
            except Exception:
                trial_data["total_patient_count"] = None

            # Access level info for the researcher
            trial_access = ctx.trial_access.get(resolved_id)
            access_info = {
                "access_level":       trial_access.access_level if trial_access else "none",
                "has_patient_filter": trial_access.has_patient_filter if trial_access else False,
                "is_unrestricted":    trial_access.is_unrestricted if trial_access else False,
            }
            if trial_access and trial_access.has_patient_filter:
                access_info["active_filters"] = [
                    {
                        "cohort_name":      cf.cohort_name,
                        "criteria_summary": {
                            k: v for k, v in cf.criteria.items()
                            if v is not None and v != [] and v != ""
                        },
                    }
                    for cf in trial_access.cohort_filters
                ]

            return success_response(
                data=trial_data,
                metadata={"access": access_info},
            )

        except Exception as e:
            logger.error(f"get_trial_details error: {e}", exc_info=True)
            return error_response(str(e), "TOOL_ERROR")

    # -----------------------------------------------------------------------
    # get_eligibility_criteria
    # -----------------------------------------------------------------------
    @mcp.tool()
    async def get_eligibility_criteria(
        trial_id: str = "",
        nct_id: str = "",
        criteria_type: str = "",
        access_context: str = "",
    ) -> str:
        """
        Get inclusion and/or exclusion criteria for a clinical trial.

        Use this when a researcher asks about who can participate in a trial,
        what the enrollment requirements are, or what conditions exclude patients.

        This tool returns trial METADATA only — no patient records.
        It is available to both individual and aggregate access levels.

        Args:
            trial_id: UUID of the trial.
            nct_id: NCT identifier. One of trial_id or nct_id is required.
            criteria_type: Optional filter — "inclusion" or "exclusion".
                           Leave empty to get both.
            access_context: JSON authorization context (injected by system).

        Returns:
            JSON with eligibility criteria grouped by type.
        """
        try:
            ctx = AccessContext.from_json(access_context)

            if not trial_id and not nct_id:
                return error_response(
                    "Provide either trial_id or nct_id.", "INVALID_ARGS"
                )

            # Resolve trial_id from nct_id if needed
            resolved_id = trial_id.strip() if trial_id.strip() else None
            if not resolved_id and nct_id.strip():
                resolved_id = await _resolve_nct_id_to_uuid(nct_id.strip())
                if not resolved_id:
                    return error_response(
                        f"Trial '{nct_id}' not found.", "NOT_FOUND"
                    )

            # Validate access defensively
            authorized = ctx.validate_trial_access([resolved_id])
            if not authorized:
                return error_response(
                    f"No access to trial {nct_id or resolved_id}.", "ACCESS_DENIED"
                )

            # Build query
            conditions = ["ec.trial_id = $1::uuid"]
            params: list[Any] = [resolved_id]
            idx = 2

            if criteria_type.strip().lower() in ("inclusion", "exclusion"):
                conditions.append(f"ec.criteria_type = ${idx}")
                params.append(criteria_type.strip().lower())
                idx += 1

            where = " AND ".join(conditions)
            sql = f"""
                SELECT ec.criteria_id, ec.criteria_type, ec.description
                FROM eligibility_criteria ec
                WHERE {where}
                ORDER BY ec.criteria_type, ec.criteria_id
            """

            rows = await postgres.fetch(sql, *params)

            # Group by type
            grouped: dict[str, list[str]] = {"inclusion": [], "exclusion": []}
            for row in rows:
                grouped.setdefault(row["criteria_type"], []).append(row["description"])

            # Get trial identifiers for context
            trial_info = await postgres.fetchrow(
                "SELECT nct_id, title FROM clinical_trial WHERE trial_id = $1::uuid",
                resolved_id,
            )

            return success_response(
                data={
                    "trial_id":      resolved_id,
                    "nct_id":        trial_info["nct_id"] if trial_info else None,
                    "title":         trial_info["title"] if trial_info else None,
                    "criteria":      grouped,
                    "total_criteria": len(rows),
                },
            )

        except Exception as e:
            logger.error(f"get_eligibility_criteria error: {e}", exc_info=True)
            return error_response(str(e), "TOOL_ERROR")

    logger.info(
        "Trial discovery tools registered: "
        "search_trials, get_trial_details, get_eligibility_criteria"
    )