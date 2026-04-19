"""
Trial Metadata Tools — outcome measures and interventions.

Authorization enforced on every tool:
  - Trial-level filtering (only authorized trials via validate_trial_access)
  - These tools return trial METADATA only (no patient rows), so they are
    safe for both individual AND aggregate access levels.
  - enforce_individual_access_only is intentionally NOT used here.

Tools:
    get_outcome_measures       Primary/secondary endpoints for a trial
    get_trial_interventions    Drugs, arms, and dosing; enriched from Neo4j
"""

import logging
from typing import Any, Optional

from fastmcp import FastMCP

from access_control import AccessContext
from utils import success_response, error_response, serialize_row
from db import postgres, neo4j_client
from observability import instrument_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

async def _resolve_trial_id(trial_id: str, nct_id: str) -> Optional[str]:
    """Resolve trial_id or nct_id to a UUID string. Returns None if not found."""
    if trial_id and trial_id.strip():
        return trial_id.strip()
    if nct_id and nct_id.strip():
        try:
            row = await postgres.fetchrow(
                "SELECT trial_id FROM clinical_trial WHERE nct_id = $1",
                nct_id.strip()
            )
            return str(row["trial_id"]) if row else None
        except Exception as e:
            logger.warning(f"NCT ID resolution failed for '{nct_id}': {e}")
            return None
    return None


async def _search_by_drug(ctx: AccessContext, drug_name: str) -> str:
    """
    Search interventions by drug name across all authorized trials.
    Module-level so it can be called from get_trial_interventions cleanly.
    """
    try:
        trial_filter, trial_params, idx = ctx.build_trial_id_filter(
            ctx.allowed_trial_ids, param_offset=1, column="ct.trial_id"
        )

        # FIX: Use separate parameter indices for the two LIKE conditions
        # Old code used ${idx} twice which caused a duplicate parameter bug
        drug_param_idx = idx
        idx += 1

        sql = f"""
            SELECT
                ct.trial_id, ct.nct_id, ct.title,
                ct.phase, ct.therapeutic_area,
                ta.arm_label        AS arm_name,
                ta.arm_type,
                i.name              AS drug_name,
                i.generic_name,
                i.intervention_type,
                i.dose_value,
                i.dose_unit,
                i.route
            FROM intervention i
            JOIN trial_arm ta     ON i.arm_id     = ta.arm_id
            JOIN clinical_trial ct ON ta.trial_id = ct.trial_id
            WHERE {trial_filter}
              AND (
                    LOWER(i.name)         LIKE LOWER(${drug_param_idx})
                 OR LOWER(i.generic_name) LIKE LOWER(${drug_param_idx})
              )
            ORDER BY ct.nct_id, ta.arm_label
        """
        params: list[Any] = [*trial_params, f"%{drug_name}%"]

        rows = await postgres.fetch(sql, *params)

        by_trial: dict[str, dict] = {}
        for row in rows:
            tid = str(row["trial_id"])
            if tid not in by_trial:
                by_trial[tid] = {
                    "trial_id":               tid,
                    "nct_id":                 row["nct_id"],
                    "title":                  row["title"],
                    "phase":                  row["phase"],
                    "therapeutic_area":       row["therapeutic_area"],
                    "matching_interventions": [],
                }
            dose_str = None
            if row["dose_value"] is not None:
                dose_str = f"{row['dose_value']} {row['dose_unit'] or ''}".strip()

            by_trial[tid]["matching_interventions"].append({
                "arm_name":          row["arm_name"],
                "arm_type":          row["arm_type"],
                "drug_name":         row["drug_name"],
                "generic_name":      row["generic_name"],
                "intervention_type": row["intervention_type"],
                "dose":              dose_str,
                "route":             row["route"],
            })

        # Neo4j enrichment — drug→condition relationships across authorized trials
        trial_ids_cypher = (
            "[" + ", ".join(f'"{t}"' for t in ctx.allowed_trial_ids) + "]"
        )
        kg_data: list[dict] = []
        try:
            kg_data = await neo4j_client.run_cypher(
                f"""
                MATCH (t:ClinicalTrial)-[:TESTS_INTERVENTION]->(d:Drug)
                MATCH (t)-[:STUDIES]->(c:Condition)
                WHERE t.trial_id IN {trial_ids_cypher}
                  AND (toLower(d.name)         CONTAINS toLower($drug_name)
                       OR toLower(d.generic_name) CONTAINS toLower($drug_name))
                RETURN DISTINCT
                    d.name         AS drug_name,
                    d.generic_name AS generic_name,
                    c.name         AS condition_name,
                    c.icd10_code   AS icd10_code,
                    t.nct_id       AS nct_id
                LIMIT 30
                """,
                {"drug_name": drug_name},
            )
        except Exception as e:
            logger.warning(f"Neo4j drug search failed: {e}")

        return success_response(
            data={
                "search_drug":                 drug_name,
                "trials_with_drug":            list(by_trial.values()),
                "drug_condition_relationships": kg_data,
                "trials_searched":             len(ctx.allowed_trial_ids),
                "trials_matched":              len(by_trial),
            },
        )

    except Exception as e:
        logger.error(f"_search_by_drug error: {e}", exc_info=True)
        return error_response(str(e), "TOOL_ERROR")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    # ------------------------------------------------------------------
    # get_outcome_measures
    # ------------------------------------------------------------------
    @mcp.tool()
    @instrument_tool("get_outcome_measures")
    async def get_outcome_measures(
        trial_id: str = "",
        nct_id: str = "",
        measure_type: str = "",
        access_context: str = "",
    ) -> str:
        """
        Get primary and secondary outcome measures for a clinical trial.

        Use this when a researcher asks about what a trial is measuring,
        its endpoints, or what outcomes are being tracked.

        This tool returns trial METADATA only — no patient records.
        It is available to both individual and aggregate access levels.

        Args:
            trial_id: UUID of the trial.
            nct_id: NCT identifier. One of trial_id or nct_id is required.
            measure_type: Optional filter — "primary" or "secondary".
            access_context: JSON authorization context (injected by system).

        Returns:
            JSON with outcome measures grouped by type (primary / secondary).
        """
        try:
            ctx = AccessContext.from_json(access_context)

            if not trial_id and not nct_id:
                return error_response("Provide trial_id or nct_id.", "INVALID_ARGS")

            # Resolve to UUID defensively
            resolved_id = await _resolve_trial_id(trial_id, nct_id)
            if not resolved_id:
                return error_response(
                    f"Trial not found: {trial_id or nct_id}", "NOT_FOUND"
                )

            # Validate access — metadata tools work for both access levels
            authorized = ctx.validate_trial_access([resolved_id])
            if not authorized:
                return error_response(
                    f"No access to trial {nct_id or resolved_id}.", "ACCESS_DENIED"
                )

            # Build query
            conditions = ["om.trial_id = $1::uuid"]
            params: list[Any] = [resolved_id]
            idx = 2

            if measure_type.strip().lower() in ("primary", "secondary"):
                conditions.append(f"om.outcome_type = ${idx}")
                params.append(measure_type.strip().lower())
                idx += 1

            where = " AND ".join(conditions)
            sql = f"""
                SELECT om.outcome_id, om.outcome_type, om.measure,
                       om.description, om.time_frame
                FROM outcome_measure om
                WHERE {where}
                ORDER BY
                    CASE om.outcome_type WHEN 'primary' THEN 0 ELSE 1 END,
                    om.measure
            """

            rows = await postgres.fetch(sql, *params)

            # Group by type
            grouped: dict[str, list[dict]] = {"primary": [], "secondary": []}
            for row in rows:
                entry = serialize_row(row)
                mt = entry.pop("outcome_type", "secondary")
                grouped.setdefault(mt, []).append(entry)

            trial_info = await postgres.fetchrow(
                "SELECT nct_id, title FROM clinical_trial WHERE trial_id = $1::uuid",
                resolved_id,
            )

            return success_response(
                data={
                    "trial_id":         resolved_id,
                    "nct_id":           trial_info["nct_id"] if trial_info else None,
                    "title":            trial_info["title"]  if trial_info else None,
                    "outcome_measures": grouped,
                    "total_measures":   len(rows),
                },
            )

        except Exception as e:
            logger.error(f"get_outcome_measures error: {e}", exc_info=True)
            return error_response(str(e), "TOOL_ERROR")

    # ------------------------------------------------------------------
    # get_trial_interventions
    # ------------------------------------------------------------------
    @mcp.tool()
    async def get_trial_interventions(
        trial_id: str = "",
        nct_id: str = "",
        drug_name: str = "",
        access_context: str = "",
    ) -> str:
        """
         Returns the PROTOCOL-LEVEL drug design for a trial: which investigational
    drugs are assigned to which treatment arms, including dosage and route.

    USE THIS TOOL WHEN the question is about:
      ✅ "What drugs are being tested in this trial?"
      ✅ "What is the treatment arm design?"
      ✅ "What dose of Nivolumab is used in the experimental arm?"
      ✅ "What is the drug mechanism being studied?"

    DO NOT USE THIS TOOL WHEN the question is about:
      ❌ Patient medications (use get_concomitant_medications)
      ❌ What a specific patient is taking (use get_concomitant_medications)
      ❌ Medication history of enrolled subjects (use get_concomitant_medications)

    Data source: trial_arm + intervention tables (protocol design, NOT patient records).
        """
        try:
            ctx = AccessContext.from_json(access_context)

            # Drug-only search across all authorized trials
            if not trial_id.strip() and not nct_id.strip() and drug_name.strip():
                return await _search_by_drug(ctx, drug_name.strip())

            if not trial_id and not nct_id:
                return error_response(
                    "Provide trial_id, nct_id, or drug_name.", "INVALID_ARGS"
                )

            # Resolve to UUID defensively
            resolved_id = await _resolve_trial_id(trial_id, nct_id)
            if not resolved_id:
                return error_response(
                    f"Trial not found: {trial_id or nct_id}", "NOT_FOUND"
                )

            # Validate access — metadata tools work for both access levels
            authorized = ctx.validate_trial_access([resolved_id])
            if not authorized:
                return error_response(
                    f"No access to trial {nct_id or resolved_id}.", "ACCESS_DENIED"
                )

            # Arms + interventions
            sql = """
                SELECT
                    ta.arm_id,
                    ta.arm_label        AS arm_name,
                    ta.arm_type,
                    ta.description      AS arm_description,
                    i.intervention_id,
                    i.name              AS drug_name,
                    i.generic_name,
                    i.intervention_type,
                    i.dosage_form,
                    i.dose_value,
                    i.dose_unit,
                    i.route,
                    i.frequency
                FROM trial_arm ta
                LEFT JOIN intervention i ON ta.arm_id = i.arm_id
                WHERE ta.trial_id = $1::uuid
                ORDER BY ta.arm_label, i.name
            """

            rows = await postgres.fetch(sql, resolved_id)

            # Group by arm
            arms: dict[str, dict] = {}
            all_drug_names: set[str] = set()

            for row in rows:
                aid = str(row["arm_id"])
                if aid not in arms:
                    arms[aid] = {
                        "arm_id":        aid,
                        "arm_name":      row["arm_name"],
                        "arm_type":      row["arm_type"],
                        "description":   row["arm_description"],
                        "interventions": [],
                    }

                if row["intervention_id"]:
                    drug = row["drug_name"] or ""

                    # Apply optional drug_name filter
                    if drug_name.strip() and drug_name.strip().lower() not in drug.lower():
                        continue

                    dose_str = None
                    if row["dose_value"] is not None:
                        dose_str = f"{row['dose_value']} {row['dose_unit'] or ''}".strip()

                    arms[aid]["interventions"].append({
                        "intervention_id":   str(row["intervention_id"]),
                        "drug_name":         drug,
                        "generic_name":      row["generic_name"],
                        "intervention_type": row["intervention_type"],
                        "dosage_form":       row["dosage_form"],
                        "dose":              dose_str,
                        "route":             row["route"],
                        "frequency":         row["frequency"],
                    })
                    if drug:
                        all_drug_names.add(drug)

            # Neo4j enrichment — drug→condition relationships for this trial
            kg_data: list[dict] = []
            trial_ids_cypher = f'["{resolved_id}"]'
            for drug in list(all_drug_names)[:10]:
                try:
                    rows_kg = await neo4j_client.run_cypher(
                        f"""
                        MATCH (t:ClinicalTrial)-[:TESTS_INTERVENTION]->(d:Drug)
                        MATCH (t)-[:STUDIES]->(c:Condition)
                        WHERE t.trial_id IN {trial_ids_cypher}
                          AND (toLower(d.name)         CONTAINS toLower($drug_name)
                               OR toLower(d.generic_name) CONTAINS toLower($drug_name))
                        RETURN DISTINCT
                            d.name         AS drug_name,
                            c.name         AS condition_name,
                            c.icd10_code   AS icd10_code
                        LIMIT 20
                        """,
                        {"drug_name": drug},
                    )
                    kg_data.extend(rows_kg)
                except Exception as e:
                    logger.warning(f"Neo4j enrichment failed for '{drug}': {e}")

            trial_info = await postgres.fetchrow(
                "SELECT nct_id, title FROM clinical_trial WHERE trial_id = $1::uuid",
                resolved_id,
            )

            return success_response(
                data={
                    "trial_id":                    resolved_id,
                    "nct_id":                      trial_info["nct_id"] if trial_info else None,
                    "title":                       trial_info["title"]  if trial_info else None,
                    "arms":                        list(arms.values()),
                    "unique_drugs":                sorted(all_drug_names),
                    "drug_condition_relationships": kg_data,
                },
            )

        except Exception as e:
            logger.error(f"get_trial_interventions error: {e}", exc_info=True)
            return error_response(str(e), "TOOL_ERROR")

    logger.info(
        "Trial metadata tools registered: "
        "get_outcome_measures, get_trial_interventions"
    )