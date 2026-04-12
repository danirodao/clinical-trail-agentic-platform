"""
Knowledge Discovery Tools — graph exploration and semantic search.

Authorization enforced on every tool:
  - Trial-level filtering (only authorized trials via validate_trial_access)
  - No enforce_individual_access_only — knowledge graph tools are naturally
    aggregate (they return counts/relationships, not patient rows)
  - search_documents respects trial-level access only (no patient data exposed)
"""

import logging
from typing import Any, Optional

from fastmcp import FastMCP

from access_control import AccessContext
from utils import success_response, error_response
from db import postgres, neo4j_client, qdrant_client
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
    """Resolve NCT IDs to UUIDs, pass UUIDs through unchanged."""
    if not ids:
        return []
    resolved: list[str] = []
    nct_ids: list[str] = []
    for tid in ids:
        if str(tid).upper().startswith("NCT"):
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
async def _postgres_drug_condition_fallback(
    trial_ids: list[str],
    drug_name: str,
    condition_name: str,
    limit: int,
) -> dict[str, list]:
    """
    PostgreSQL fallback for trials that have no data in Neo4j.
    Queries intervention and eligibility_criteria tables directly.
    """
    results: dict[str, list] = {
        "drugs": [],
        "drug_condition_links": [],
        "conditions": [],
    }

    if not trial_ids:
        return results

    try:
        # Build trial filter
        placeholders = ", ".join(f"${i+1}::uuid" for i in range(len(trial_ids)))

        # Drugs used in these trials (via intervention → trial_arm → clinical_trial)
        drug_conditions: list[str] = [f"ct.trial_id IN ({placeholders})"]
        drug_params: list[Any] = list(trial_ids)
        drug_idx = len(trial_ids) + 1

        if drug_name:
            drug_conditions.append(
                f"(LOWER(i.name) LIKE LOWER(${drug_idx}) "
                f"OR LOWER(i.generic_name) LIKE LOWER(${drug_idx}))"
            )
            drug_params.append(f"%{drug_name}%")
            drug_idx += 1

        drug_sql = f"""
            SELECT DISTINCT
                i.name              AS drug_name,
                i.generic_name      AS generic_name,
                i.intervention_type AS drug_type,
                ct.nct_id           AS nct_id,
                ct.trial_id::text   AS trial_id,
                ct.title            AS trial_title,
                ct.phase            AS phase,
                ct.therapeutic_area AS therapeutic_area
            FROM intervention i
            JOIN trial_arm ta     ON i.arm_id     = ta.arm_id
            JOIN clinical_trial ct ON ta.trial_id = ct.trial_id
            WHERE {" AND ".join(drug_conditions)}
            ORDER BY ct.nct_id, i.name
            LIMIT {limit}
        """
        drug_rows = await postgres.fetch(drug_sql, *drug_params)
        results["drugs"] = [serialize_row(r) for r in drug_rows]

        # Conditions studied in these trials (via eligibility_criteria descriptions
        # or a conditions table if you have one)
        cond_conditions: list[str] = [f"ct.trial_id IN ({placeholders})"]
        cond_params: list[Any] = list(trial_ids)
        cond_idx = len(trial_ids) + 1

        if condition_name:
            cond_conditions.append(
                f"LOWER(pc.condition_name) LIKE LOWER(${cond_idx})"
            )
            cond_params.append(f"%{condition_name}%")
            cond_idx += 1

        cond_sql = f"""
            SELECT DISTINCT
                pc.condition_name   AS condition_name,
                pc.icd10_code       AS icd10_code,
                ct.nct_id           AS nct_id,
                ct.trial_id::text   AS trial_id,
                ct.title            AS trial_title,
                ct.phase            AS phase,
                ct.therapeutic_area AS therapeutic_area
            FROM patient_condition pc
            JOIN patient_trial_enrollment pte ON pc.patient_id = pte.patient_id
            JOIN clinical_trial ct             ON pte.trial_id  = ct.trial_id
            WHERE {" AND ".join(cond_conditions)}
            GROUP BY pc.condition_name, pc.icd10_code, ct.nct_id,
                     ct.trial_id, ct.title, ct.phase, ct.therapeutic_area
            ORDER BY ct.nct_id, pc.condition_name
            LIMIT {limit}
        """
        cond_rows = await postgres.fetch(cond_sql, *cond_params)
        results["conditions"] = [serialize_row(r) for r in cond_rows]

        # Drug → Condition links (cross-join drugs and conditions for these trials)
        if results["drugs"] and results["conditions"]:
            # Group by trial so we can pair them
            drugs_by_trial: dict[str, list] = {}
            for d in results["drugs"]:
                drugs_by_trial.setdefault(d["nct_id"], []).append(d)

            conds_by_trial: dict[str, list] = {}
            for c in results["conditions"]:
                conds_by_trial.setdefault(c["nct_id"], []).append(c)

            links: list[dict] = []
            for nct_id in set(drugs_by_trial) & set(conds_by_trial):
                for drug in drugs_by_trial[nct_id]:
                    for cond in conds_by_trial[nct_id]:
                        links.append({
                            "drug_name":      drug["drug_name"],
                            "condition_name": cond["condition_name"],
                            "icd10_code":     cond.get("icd10_code"),
                            "nct_id":         nct_id,
                            "phase":          drug.get("phase"),
                            "source":         "postgresql_fallback",
                        })
                        if len(links) >= limit:
                            break
                    if len(links) >= limit:
                        break

            results["drug_condition_links"] = links

    except Exception as e:
        logger.error(f"PostgreSQL drug-condition fallback failed: {e}", exc_info=True)

    return results

def register_tools(mcp: FastMCP) -> None:

    # ------------------------------------------------------------------
    # find_drug_condition_relationships
    # ------------------------------------------------------------------
    @mcp.tool()
    @instrument_tool("find_drug_condition_relationships")
    async def find_drug_condition_relationships(
        drug_name: str = "",
        condition_name: str = "",
        ae_term: str = "",
        limit: str = "50",
        access_context: str = "",
    ) -> str:
        """
        Explore drug, condition, and adverse event relationships in the
        clinical trial knowledge graph.

        Use this when a researcher asks about:
        - What drugs are tested in trials for a specific condition
        - What conditions are studied alongside a drug
        - Which adverse events are associated with a drug or condition
        - Comorbid conditions that appear together in trials
        - How drugs, conditions, and trials are connected

        This tool queries ALL authorized trials automatically.
        Do NOT pass trial_ids — it always covers your full authorized scope.

        Args:
            drug_name: Search by drug name or generic name (partial match).
            condition_name: Search by condition name or ICD-10 code (partial match).
            ae_term: Search for adverse events by MedDRA term (partial match).
            limit: Maximum results per query type (default 50, max 100).
            access_context: JSON authorization context (injected by system).
        """
        try:
            ctx = AccessContext.from_json(access_context)

            # Always use ALL authorized trials — never let the LLM scope this down
            authorized = ctx.validate_trial_access(None)

            if not authorized:
                return error_response("No trial access.", "NO_ACCESS")

            if not drug_name.strip() and not condition_name.strip() and not ae_term.strip():
                return error_response(
                    "Provide at least one of: drug_name, condition_name, ae_term.",
                    "INVALID_ARGS",
                )

            max_results = min(int(limit), 100)

            # Log exactly which trials we are querying so we can diagnose gaps
            logger.info(
                f"find_drug_condition_relationships: querying {len(authorized)} trials: "
                f"{authorized}"
            )

            # Build Cypher trial filter — UUIDs from our own DB, safe to inline
            trial_ids_cypher = (
                "[" + ", ".join(f'"{t}"' for t in authorized) + "]"
            )

            results: dict[str, Any] = {
                "drugs_in_authorized_trials":       [],
                "conditions_in_authorized_trials":  [],
                "adverse_events":                   [],
                "comorbid_conditions":              [],
                "drug_condition_links":             [],
                "trial_drug_condition_summary":     [],
            }

            # ----------------------------------------------------------
            # Neo4j queries (primary source)
            # ----------------------------------------------------------
            neo4j_trial_ids_found: set[str] = set()

            if drug_name.strip():
                try:
                    drug_trial_rows = await neo4j_client.run_cypher(
                        f"""
                        MATCH (t:ClinicalTrial)-[:TESTS_INTERVENTION]->(d:Drug)
                        WHERE t.trial_id IN {trial_ids_cypher}
                        AND (toLower(d.name) CONTAINS toLower($drug_name)
                            OR toLower(d.generic_name) CONTAINS toLower($drug_name))
                        RETURN DISTINCT
                            d.name          AS drug_name,
                            d.generic_name  AS generic_name,
                            d.rxnorm_code   AS rxnorm_code,
                            d.type          AS drug_type,
                            t.nct_id        AS nct_id,
                            t.trial_id      AS trial_id,
                            t.title         AS trial_title,
                            t.phase         AS phase,
                            t.therapeutic_area AS therapeutic_area
                        ORDER BY t.nct_id
                        LIMIT $limit
                        """,
                        {"drug_name": drug_name.strip(), "limit": max_results},
                    )
                    results["drugs_in_authorized_trials"] = drug_trial_rows
                    neo4j_trial_ids_found.update(r.get("trial_id", "") for r in drug_trial_rows)

                    drug_cond_rows = await neo4j_client.run_cypher(
                        f"""
                        MATCH (t:ClinicalTrial)-[:TESTS_INTERVENTION]->(d:Drug)
                        MATCH (t)-[:STUDIES]->(c:Condition)
                        WHERE t.trial_id IN {trial_ids_cypher}
                        AND (toLower(d.name) CONTAINS toLower($drug_name)
                            OR toLower(d.generic_name) CONTAINS toLower($drug_name))
                        RETURN DISTINCT
                            d.name         AS drug_name,
                            c.name         AS condition_name,
                            c.icd10_code   AS icd10_code,
                            t.nct_id       AS nct_id,
                            t.phase        AS phase
                        ORDER BY c.name
                        LIMIT $limit
                        """,
                        {"drug_name": drug_name.strip(), "limit": max_results},
                    )
                    results["drug_condition_links"] = drug_cond_rows

                except Exception as e:
                    logger.warning(f"Neo4j drug query failed: {e}")

            if condition_name.strip():
                try:
                    cond_trial_rows = await neo4j_client.run_cypher(
                        f"""
                        MATCH (t:ClinicalTrial)-[:STUDIES]->(c:Condition)
                        WHERE t.trial_id IN {trial_ids_cypher}
                        AND (toLower(c.name) CONTAINS toLower($condition_name)
                            OR toLower(c.icd10_code) CONTAINS toLower($condition_name))
                        RETURN DISTINCT
                            c.name        AS condition_name,
                            c.icd10_code  AS icd10_code,
                            t.nct_id      AS nct_id,
                            t.trial_id    AS trial_id,
                            t.title       AS trial_title,
                            t.phase       AS phase,
                            t.therapeutic_area AS therapeutic_area
                        ORDER BY t.nct_id
                        LIMIT $limit
                        """,
                        {"condition_name": condition_name.strip(), "limit": max_results},
                    )
                    results["conditions_in_authorized_trials"] = cond_trial_rows
                    neo4j_trial_ids_found.update(r.get("trial_id", "") for r in cond_trial_rows)

                    if not drug_name.strip():
                        cond_drug_rows = await neo4j_client.run_cypher(
                            f"""
                            MATCH (t:ClinicalTrial)-[:STUDIES]->(c:Condition)
                            MATCH (t)-[:TESTS_INTERVENTION]->(d:Drug)
                            WHERE t.trial_id IN {trial_ids_cypher}
                            AND (toLower(c.name) CONTAINS toLower($condition_name)
                                OR toLower(c.icd10_code) CONTAINS toLower($condition_name))
                            RETURN DISTINCT
                                d.name         AS drug_name,
                                d.generic_name AS generic_name,
                                d.type         AS drug_type,
                                c.name         AS for_condition,
                                t.nct_id       AS nct_id,
                                t.phase        AS phase
                            ORDER BY d.name
                            LIMIT $limit
                            """,
                            {"condition_name": condition_name.strip(), "limit": max_results},
                        )
                        results["drug_condition_links"] = cond_drug_rows

                    comorbid_rows = await neo4j_client.run_cypher(
                        """
                        MATCH (c1:Condition)-[:COMORBID_WITH]->(c2:Condition)
                        WHERE toLower(c1.name) CONTAINS toLower($condition_name)
                        OR toLower(c2.name) CONTAINS toLower($condition_name)
                        RETURN DISTINCT
                            c1.name AS condition_a,
                            c2.name AS condition_b,
                            c1.icd10_code AS icd10_a,
                            c2.icd10_code AS icd10_b
                        LIMIT $limit
                        """,
                        {"condition_name": condition_name.strip(), "limit": max_results},
                    )
                    results["comorbid_conditions"] = comorbid_rows

                except Exception as e:
                    logger.warning(f"Neo4j condition query failed: {e}")

            if ae_term.strip():
                try:
                    ae_rows = await neo4j_client.run_cypher(
                        f"""
                        MATCH (p:Patient)-[:EXPERIENCED]->(ae:AdverseEvent)
                        MATCH (p)-[:ENROLLED_IN]->(t:ClinicalTrial)
                        WHERE t.trial_id IN {trial_ids_cypher}
                        AND (toLower(ae.term) CONTAINS toLower($ae_term)
                            OR toLower(ae.meddra_pt) CONTAINS toLower($ae_term))
                        RETURN DISTINCT
                            ae.term       AS ae_term,
                            ae.meddra_pt  AS meddra_pt,
                            ae.soc        AS soc,
                            t.nct_id      AS nct_id,
                            t.phase       AS phase,
                            COUNT(p)      AS patient_count
                        ORDER BY patient_count DESC
                        LIMIT $limit
                        """,
                        {"ae_term": ae_term.strip(), "limit": max_results},
                    )
                    results["adverse_events"] = ae_rows
                    neo4j_trial_ids_found.update(r.get("nct_id", "") for r in ae_rows)

                except Exception as e:
                    logger.warning(f"Neo4j AE query failed: {e}")

            if drug_name.strip() and condition_name.strip():
                try:
                    summary_rows = await neo4j_client.run_cypher(
                        f"""
                        MATCH (t:ClinicalTrial)-[:TESTS_INTERVENTION]->(d:Drug)
                        MATCH (t)-[:STUDIES]->(c:Condition)
                        WHERE t.trial_id IN {trial_ids_cypher}
                        AND (toLower(d.name) CONTAINS toLower($drug_name)
                            OR toLower(d.generic_name) CONTAINS toLower($drug_name))
                        AND (toLower(c.name) CONTAINS toLower($condition_name)
                            OR toLower(c.icd10_code) CONTAINS toLower($condition_name))
                        RETURN DISTINCT
                            t.nct_id       AS nct_id,
                            t.title        AS trial_title,
                            t.phase        AS phase,
                            d.name         AS drug_name,
                            c.name         AS condition_name
                        ORDER BY t.nct_id
                        LIMIT $limit
                        """,
                        {
                            "drug_name":      drug_name.strip(),
                            "condition_name": condition_name.strip(),
                            "limit":          max_results,
                        },
                    )
                    results["trial_drug_condition_summary"] = summary_rows
                except Exception as e:
                    logger.warning(f"Neo4j summary query failed: {e}")

            # ----------------------------------------------------------
            # PostgreSQL FALLBACK for trials missing from Neo4j
            # ----------------------------------------------------------
            # Find which authorized trials got zero results from Neo4j
            missing_from_neo4j = [
                tid for tid in authorized
                if tid not in neo4j_trial_ids_found
            ]

            if missing_from_neo4j:
                logger.warning(
                    f"Neo4j returned no data for {len(missing_from_neo4j)} trials: "
                    f"{missing_from_neo4j}. Falling back to PostgreSQL for these trials."
                )

                pg_fallback = await _postgres_drug_condition_fallback(
                    trial_ids=missing_from_neo4j,
                    drug_name=drug_name.strip(),
                    condition_name=condition_name.strip(),
                    limit=max_results,
                )

                # Merge PostgreSQL fallback into results
                results["drugs_in_authorized_trials"].extend(
                    pg_fallback.get("drugs", [])
                )
                results["drug_condition_links"].extend(
                    pg_fallback.get("drug_condition_links", [])
                )
                results["conditions_in_authorized_trials"].extend(
                    pg_fallback.get("conditions", [])
                )

            return success_response(
                data=results,
                metadata={
                    "search_criteria": {
                        "drug_name":      drug_name or None,
                        "condition_name": condition_name or None,
                        "ae_term":        ae_term or None,
                    },
                    "result_counts": {k: len(v) for k, v in results.items()},
                    "authorized_trials":         len(authorized),
                    "trials_with_neo4j_data":    len(neo4j_trial_ids_found),
                    "trials_from_pg_fallback":   len(missing_from_neo4j),
                    "graph_schema": {
                        "nodes": [
                            "ClinicalTrial", "Drug", "Condition",
                            "AdverseEvent", "Patient", "LabTest"
                        ],
                        "relationships": [
                            "TESTS_INTERVENTION", "STUDIES", "ENROLLED_IN",
                            "HAS_CONDITION", "EXPERIENCED", "COMORBID_WITH"
                        ],
                    },
                },
            )

        except Exception as e:
            logger.error(f"find_drug_condition_relationships error: {e}", exc_info=True)
            return error_response(str(e), "TOOL_ERROR")

    # ------------------------------------------------------------------
    # search_documents
    # ------------------------------------------------------------------
    @mcp.tool()
    @instrument_tool("search_documents")
    async def search_documents(
        query: str,
        section: str = "",
        trial_ids: Optional[list[str]] = None,  # FIX: was list[str] but treated as str
        limit: str = "10",
        access_context: str = "",
    ) -> str:
        """
        Semantic search over clinical trial document chunks.

        Use this when a researcher asks about protocol details, methodology,
        study design, or any topic that requires searching the full text
        of trial documents.

        Args:
            query: Natural language search query. Required.
            section: Optional document section filter. Options:
                     adverse_events, demographics, results, eligibility,
                     methodology, endpoints, interventions, summary.
            trial_ids: List of trial UUIDs or NCT IDs.
                       Leave empty to search all authorized trials.
            limit: Maximum chunks to return (default 10, max 30).
            access_context: JSON authorization context (injected by system).
        """
        try:
            ctx = AccessContext.from_json(access_context)

            if not query or not query.strip():
                return error_response("Query parameter is required.", "INVALID_ARGS")

            if not ctx.allowed_trial_ids:
                return error_response("No trial access.", "NO_ACCESS")

            # FIX: Use _parse_trial_ids to safely handle list or None
            # The old code called trial_ids.strip() which crashes on a list
            raw_ids = _parse_trial_ids(trial_ids)

            if raw_ids:
                # Resolve NCT IDs to UUIDs
                resolved = await _resolve_nct_ids(raw_ids)
                # Validate against authorized trials
                search_in = ctx.validate_trial_access(resolved)
                if not search_in:
                    return error_response(
                        "None of the requested trials are authorized.", "ACCESS_DENIED"
                    )
            else:
                # No trial_ids specified — search all authorized trials
                search_in = ctx.allowed_trial_ids

            max_chunks = min(int(limit), 30)

            valid_sections = {
                "adverse_events", "demographics", "results", "eligibility",
                "methodology", "endpoints", "interventions", "summary",
            }
            section_filter: Optional[str] = None
            if section and section.strip():
                s = section.strip().lower()
                if s in valid_sections:
                    section_filter = s
                else:
                    # Fuzzy match
                    matches = [v for v in valid_sections if s in v]
                    section_filter = matches[0] if matches else None
                    if not section_filter:
                        logger.warning(f"Unknown section '{s}', ignoring filter.")

            try:
                chunks = await qdrant_client.search_vectors(
                    query_text=query.strip(),
                    trial_ids=search_in,
                    limit=max_chunks,
                    section=section_filter,
                    score_threshold=0.25,
                )
            except Exception as e:
                logger.error(f"Qdrant search error: {e}", exc_info=True)
                return error_response(f"Document search failed: {e}", "SEARCH_ERROR")

            results = [
                {
                    "chunk_id":         chunk["id"],
                    "trial_id":         chunk["trial_id"],
                    "nct_id":           chunk["nct_id"],
                    "section":          chunk["section"],
                    "relevance_score":  chunk["score"],
                    "text":             chunk["chunk_text"],
                    "source_pdf":       chunk["source_pdf"],
                    "chunk_index":      chunk.get("chunk_index"),
                    "therapeutic_area": chunk.get("therapeutic_area"),
                    "phase":            chunk.get("phase"),
                }
                for chunk in chunks
            ]

            return success_response(
                data={
                    "query":       query.strip(),
                    "chunks":      results,
                    "total_found": len(results),
                },
                metadata={
                    "section_filter":      section_filter,
                    "trials_searched":     len(search_in),
                    "trials_with_results": len({c["trial_id"] for c in chunks}),
                    "limit":               max_chunks,
                    "authorized_trials":   len(ctx.allowed_trial_ids),
                },
            )

        except Exception as e:
            logger.error(f"search_documents error: {e}", exc_info=True)
            return error_response(str(e), "TOOL_ERROR")

    logger.info(
        "Knowledge discovery tools registered: "
        "find_drug_condition_relationships, search_documents"
    )