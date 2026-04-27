"""
Semantic ontology tools for the Semantic MCP server.

These tools are the primary interface the agent uses for semantic disambiguation
and ontology inspection. They first query Neo4j (live ontology store) and fall
back to the in-process registry if the graph is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP

from ontology import (
    CONCEPT_REGISTRY,
    FIELD_CONCEPT_MAP,
    ONTOLOGY_VERSION,
    SEMANTIC_LAYER_VERSION,
    get_cognitive_frame,
    resolve_concepts,
)
from neo4j_ontology import run_cypher, run_write_cypher
from observability import instrument_tool
from utils import error_response, success_response

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Neo4j helpers — query the live ontology graph
# ─────────────────────────────────────────────────────────────────────────────


async def _graph_resolve(term: str, limit: int) -> list[dict[str, Any]]:
    """Match term against Concept and Term nodes in Neo4j."""
    try:
        rows = await run_cypher(
            """
            MATCH (t:OntologyTerm)-[:REFERS_TO]->(c:Concept)
            WHERE toLower(t.term) CONTAINS toLower($term)
               OR toLower(c.label) CONTAINS toLower($term)
               OR toLower(c.preferred_term) CONTAINS toLower($term)
            RETURN DISTINCT
                c.concept_id    AS concept_id,
                c.label         AS label,
                c.preferred_term AS preferred_term,
                c.definition    AS definition,
                c.code_system   AS code_system,
                c.allowed_values AS allowed_values
            LIMIT $limit
            """,
            {"term": term, "limit": limit},
        )
        return rows
    except Exception as exc:
        logger.warning("Graph concept resolution failed, using in-process registry: %s", exc)
        return []


async def _graph_get_concept(concept_id: str) -> dict[str, Any] | None:
    """Fetch a single Concept node by ID."""
    try:
        rows = await run_cypher(
            """
            MATCH (c:Concept {concept_id: $cid})
            OPTIONAL MATCH (c)-[:NARROWER_THAN]->(parent:Concept)
            OPTIONAL MATCH (child:Concept)-[:NARROWER_THAN]->(c)
            RETURN
                c.concept_id    AS concept_id,
                c.label         AS label,
                c.preferred_term AS preferred_term,
                c.definition    AS definition,
                c.code_system   AS code_system,
                c.allowed_values AS allowed_values,
                c.synonyms      AS synonyms,
                parent.concept_id AS broader_concept,
                collect(DISTINCT child.concept_id) AS narrower_concepts
            LIMIT 1
            """,
            {"cid": concept_id},
        )
        return rows[0] if rows else None
    except Exception as exc:
        logger.warning("Graph concept fetch failed: %s", exc)
        return None


async def _graph_get_frame() -> list[dict[str, Any]] | None:
    """Load all concepts from Neo4j for the cognitive frame."""
    try:
        rows = await run_cypher(
            """
            MATCH (c:Concept)
            RETURN
                c.concept_id      AS concept_id,
                c.label           AS label,
                c.preferred_term  AS preferred_term,
                c.code_system     AS code_system,
                c.allowed_values  AS allowed_values
            ORDER BY c.concept_id
            """
        )
        return rows if rows else None
    except Exception as exc:
        logger.warning("Graph cognitive frame load failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Tool registration
# ─────────────────────────────────────────────────────────────────────────────


def register_tools(mcp: FastMCP) -> None:

    # ── get_semantic_cognitive_frame ─────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("get_semantic_cognitive_frame")
    async def get_semantic_cognitive_frame(access_context: str = "") -> str:
        """
        Return the full ontology frame for the clinical trial semantic layer.

        The agent MUST call this at the start of any session that requires
        semantic disambiguation. The frame provides:
        - All known concepts with definitions and allowed values
        - Field-to-concept mappings used by data tools
        - Ontology version for compatibility checks

        Returns:
            JSON with core_concepts list and field_concept_map.
        """
        _ = access_context

        # Try graph first
        graph_concepts = await _graph_get_frame()
        if graph_concepts:
            return success_response(
                data={
                    "semantic_layer_version": SEMANTIC_LAYER_VERSION,
                    "ontology_version": ONTOLOGY_VERSION,
                    "source": "neo4j",
                    "core_concepts": graph_concepts,
                    "field_concept_map": FIELD_CONCEPT_MAP,
                },
                metadata={"concept_count": len(graph_concepts)},
            )

        # Registry fallback
        frame = get_cognitive_frame()
        frame["source"] = "registry"
        return success_response(
            data=frame,
            metadata={"concept_count": len(CONCEPT_REGISTRY)},
        )

    # ── resolve_semantic_term ────────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("resolve_semantic_term")
    async def resolve_semantic_term(
        term: str,
        context_hint: str = "",
        limit: int = 8,
        access_context: str = "",
    ) -> str:
        """
        Resolve an ambiguous clinical term into one or more ontology concepts.

        Call this BEFORE executing data queries when a user term is ambiguous
        or maps to a specific code system (e.g., "phase", "code", "status",
        "event", "sex", "gender").

        The response includes:
        - concept_id: stable identifier for the concept
        - definition: canonical meaning
        - code_system: which standard the concept belongs to
        - allowed_values: valid values in this dataset

        Args:
            term: The ambiguous term to resolve (e.g., "phase", "ae term").
            context_hint: Optional domain hint (e.g., "lab", "adverse event").
            limit: Maximum number of matching concepts to return (default 8).
            access_context: Authorization context (injected by system).

        Returns:
            JSON with a 'matches' list ordered by match confidence.
        """
        _ = access_context
        if not term or not term.strip():
            return error_response("term is required", "INVALID_ARGS")

        cap = max(1, min(int(limit), 20))

        # Try graph first
        graph_matches = await _graph_resolve(term.strip(), cap)
        if graph_matches:
            return success_response(
                data={
                    "term": term,
                    "context_hint": context_hint,
                    "source": "neo4j",
                    "matches": graph_matches,
                },
                metadata={"resolved": True, "match_count": len(graph_matches)},
            )

        # Registry fallback
        registry_matches = resolve_concepts(term=term.strip(), limit=cap)
        return success_response(
            data={
                "term": term,
                "context_hint": context_hint,
                "source": "registry",
                "matches": registry_matches,
            },
            metadata={"resolved": bool(registry_matches), "match_count": len(registry_matches)},
        )

    # ── get_concept_definition ───────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("get_concept_definition")
    async def get_concept_definition(concept_id: str, access_context: str = "") -> str:
        """
        Fetch the full definition and metadata for a specific ontology concept.

        Use this when you have a concept_id (from resolve_semantic_term or the
        cognitive frame) and need its canonical definition, code system,
        allowed values, and relationships to other concepts.

        Args:
            concept_id: Stable concept identifier (e.g., 'concept:trial.phase').
            access_context: Authorization context (injected by system).

        Returns:
            JSON with the concept definition including broader/narrower relations.
        """
        _ = access_context
        if not concept_id or not concept_id.strip():
            return error_response("concept_id is required", "INVALID_ARGS")

        cid = concept_id.strip()

        # Try graph first (includes hierarchy links)
        graph_concept = await _graph_get_concept(cid)
        if graph_concept:
            return success_response(
                data={"source": "neo4j", **graph_concept},
                metadata={"resolved": True},
            )

        # Registry fallback
        concept = CONCEPT_REGISTRY.get(cid)
        if not concept:
            return error_response(f"Unknown concept_id: {cid}", "NOT_FOUND")

        return success_response(
            data={"concept_id": cid, "source": "registry", **concept},
            metadata={"resolved": True},
        )

    # ── list_ontology_concepts ───────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("list_ontology_concepts")
    async def list_ontology_concepts(
        code_system: str = "",
        access_context: str = "",
    ) -> str:
        """
        List all known ontology concepts, optionally filtered by code system.

        Useful for discovering which clinical code standards are in use
        (ICD-10, LOINC, RxNorm, MedDRA, SNOMED CT, internal).

        Args:
            code_system: Filter by code system (e.g., 'LOINC', 'ICD-10').
            access_context: Authorization context (injected by system).

        Returns:
            JSON list of all matching concepts.
        """
        _ = access_context
        try:
            cypher_filter = ""
            params: dict[str, Any] = {}
            if code_system.strip():
                cypher_filter = "WHERE toLower(c.code_system) = toLower($cs)"
                params["cs"] = code_system.strip()

            rows = await run_cypher(
                f"""
                MATCH (c:Concept)
                {cypher_filter}
                RETURN
                    c.concept_id     AS concept_id,
                    c.label          AS label,
                    c.code_system    AS code_system,
                    c.preferred_term AS preferred_term
                ORDER BY c.concept_id
                """,
                params,
            )
            if rows:
                return success_response(
                    data=rows,
                    metadata={"source": "neo4j", "total": len(rows)},
                )
        except Exception as exc:
            logger.warning("Graph list_concepts failed, falling back: %s", exc)

        # Registry fallback
        concepts = [
            {
                "concept_id": cid,
                "label": c.get("label"),
                "code_system": c.get("code_system"),
                "preferred_term": c.get("preferred_term"),
            }
            for cid, c in CONCEPT_REGISTRY.items()
            if not code_system.strip()
            or c.get("code_system", "").lower() == code_system.strip().lower()
        ]
        return success_response(
            data=concepts,
            metadata={"source": "registry", "total": len(concepts)},
        )

    # ── get_field_concept_map ────────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("get_field_concept_map")
    async def get_field_concept_map(access_context: str = "") -> str:
        """
        Return the mapping of database field names to ontology concept IDs.

        Use this to understand what each field in a data tool response means
        semantically without having to call resolve_semantic_term for each one.

        Returns:
            JSON dict of field_name -> concept_id.
        """
        _ = access_context
        return success_response(
            data=FIELD_CONCEPT_MAP,
            metadata={
                "field_count": len(FIELD_CONCEPT_MAP),
                "ontology_version": ONTOLOGY_VERSION,
            },
        )

    # ── map_code_to_concept ──────────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("map_code_to_concept")
    async def map_code_to_concept(
        code: str,
        code_system: str = "",
        access_context: str = "",
    ) -> str:
        """
        Map a specific clinical code (e.g. an ICD-10, LOINC, RxNorm, or
        MedDRA code) to its canonical ontology concept.

        Use this when a data tool returns a coded field (e.g. icd10_code,
        loinc_code, meddra_pt) and you need to resolve the human-readable
        concept and its domain semantics.

        Args:
            code:         The raw code string (e.g. 'J18.9', '2160-0', 'C0004096').
            code_system:  Optional hint — 'ICD-10' | 'LOINC' | 'RxNorm' | 'MedDRA'.
            access_context: Authorization context (injected by system).

        Returns:
            JSON with the matched concept or a list of candidates if ambiguous.
        """
        _ = access_context
        if not code or not code.strip():
            return error_response("code is required", "INVALID_ARGS")

        code = code.strip()
        try:
            cypher_params: dict[str, Any] = {"code": code}
            cs_filter = ""
            if code_system.strip():
                cs_filter = "AND toLower(c.code_system) = toLower($cs)"
                cypher_params["cs"] = code_system.strip()

            rows = await run_cypher(
                f"""
                MATCH (t:OntologyTerm)-[:REFERS_TO]->(c:Concept)
                WHERE t.term = $code OR t.code = $code
                {cs_filter}
                RETURN DISTINCT
                    c.concept_id     AS concept_id,
                    c.label          AS label,
                    c.preferred_term AS preferred_term,
                    c.definition     AS definition,
                    c.code_system    AS code_system,
                    c.allowed_values AS allowed_values
                LIMIT 5
                """,
                cypher_params,
            )
            if rows:
                result = rows[0] if len(rows) == 1 else rows
                return success_response(
                    data={"code": code, "mapped_concept": result, "source": "neo4j"},
                    metadata={"resolved": True, "candidate_count": len(rows)},
                )
        except Exception as exc:
            logger.warning("map_code_to_concept graph query failed: %s", exc)

        # Registry fallback — search by code within allowed_values
        matches = []
        code_lower = code.lower()
        for cid, concept in CONCEPT_REGISTRY.items():
            cs = concept.get("code_system", "")
            if code_system.strip() and cs.lower() != code_system.strip().lower():
                continue
            allowed = concept.get("allowed_values", [])
            if isinstance(allowed, list) and any(
                code_lower == str(v).lower() for v in allowed
            ):
                matches.append({"concept_id": cid, **concept})

        if matches:
            return success_response(
                data={"code": code, "mapped_concept": matches[0] if len(matches) == 1 else matches, "source": "registry"},
                metadata={"resolved": True},
            )

        return error_response(
            f"No concept found for code '{code}'" + (f" in system '{code_system}'" if code_system else ""),
            "NOT_FOUND",
        )

    # ── map_concept_to_codes ─────────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("map_concept_to_codes")
    async def map_concept_to_codes(
        concept_id: str,
        access_context: str = "",
    ) -> str:
        """
        Return all ontology terms and codes associated with a given concept.

        The reverse of map_code_to_concept. Use this when you know the concept
        (e.g. 'concept:condition.icd10') and want to discover every valid code
        or term for filtering data queries.

        Args:
            concept_id:    Stable concept identifier (e.g. 'concept:lab.loinc').
            access_context: Authorization context (injected by system).

        Returns:
            JSON with a 'terms' list of all code strings and synonyms.
        """
        _ = access_context
        if not concept_id or not concept_id.strip():
            return error_response("concept_id is required", "INVALID_ARGS")

        cid = concept_id.strip()

        try:
            rows = await run_cypher(
                """
                MATCH (t:OntologyTerm)-[:REFERS_TO]->(c:Concept {concept_id: $cid})
                RETURN t.term AS term, t.code AS code, t.source AS source
                ORDER BY t.term
                """,
                {"cid": cid},
            )
            if rows:
                return success_response(
                    data={"concept_id": cid, "terms": rows, "source": "neo4j"},
                    metadata={"term_count": len(rows)},
                )
        except Exception as exc:
            logger.warning("map_concept_to_codes graph query failed: %s", exc)

        # Registry fallback
        concept = CONCEPT_REGISTRY.get(cid)
        if not concept:
            return error_response(f"Unknown concept_id: {cid}", "NOT_FOUND")

        allowed = concept.get("allowed_values", [])
        terms = [{"term": v, "code": v, "source": "registry"} for v in (allowed if isinstance(allowed, list) else [])]
        synonyms = concept.get("synonyms", [])
        if isinstance(synonyms, list):
            terms.extend({"term": s, "code": None, "source": "registry_synonym"} for s in synonyms)

        return success_response(
            data={"concept_id": cid, "terms": terms, "source": "registry"},
            metadata={"term_count": len(terms)},
        )

    # ── semantic_compatibility_check ─────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("semantic_compatibility_check")
    async def semantic_compatibility_check(
        term_a: str,
        term_b: str,
        access_context: str = "",
    ) -> str:
        """
        Check whether two clinical terms belong to the same semantic concept or
        are semantically related (broader / narrower hierarchy).

        Use this before combining filter criteria — e.g. to verify that 'LOINC
        2160-0' and 'serum creatinine' refer to the same concept, or to check
        that two AE terms are in the same MedDRA system organ class.

        Args:
            term_a:        First term or code.
            term_b:        Second term or code.
            access_context: Authorization context (injected by system).

        Returns:
            JSON with compatibility verdict ('same_concept', 'related',
            'unrelated', or 'unknown') and the resolved concepts for both terms.
        """
        _ = access_context
        if not term_a or not term_b:
            return error_response("term_a and term_b are required", "INVALID_ARGS")

        async def _resolve_one(term: str) -> list[dict]:
            try:
                rows = await run_cypher(
                    """
                    MATCH (t:OntologyTerm)-[:REFERS_TO]->(c:Concept)
                    WHERE toLower(t.term) = toLower($term) OR toLower(t.code) = toLower($term)
                    RETURN DISTINCT c.concept_id AS concept_id, c.label AS label
                    LIMIT 3
                    """,
                    {"term": term.strip()},
                )
                return rows if rows else []
            except Exception:
                return []

        concepts_a = await _resolve_one(term_a)
        concepts_b = await _resolve_one(term_b)

        if not concepts_a or not concepts_b:
            # Fall back to registry substring match
            def _registry_match(term: str) -> list[str]:
                t = term.strip().lower()
                return [
                    cid for cid, c in CONCEPT_REGISTRY.items()
                    if t in c.get("label", "").lower()
                    or t in str(c.get("allowed_values", "")).lower()
                    or t in str(c.get("synonyms", "")).lower()
                ]
            if not concepts_a:
                concepts_a = [{"concept_id": cid, "label": CONCEPT_REGISTRY[cid].get("label")} for cid in _registry_match(term_a)[:3]]
            if not concepts_b:
                concepts_b = [{"concept_id": cid, "label": CONCEPT_REGISTRY[cid].get("label")} for cid in _registry_match(term_b)[:3]]

        ids_a = {c["concept_id"] for c in concepts_a}
        ids_b = {c["concept_id"] for c in concepts_b}

        if not ids_a or not ids_b:
            verdict = "unknown"
        elif ids_a & ids_b:
            verdict = "same_concept"
        else:
            # Check Neo4j hierarchy
            try:
                rel_rows = await run_cypher(
                    """
                    MATCH (a:Concept)-[:NARROWER_THAN*1..3]-(b:Concept)
                    WHERE a.concept_id IN $ids_a AND b.concept_id IN $ids_b
                    RETURN count(*) AS related_count
                    """,
                    {"ids_a": list(ids_a), "ids_b": list(ids_b)},
                )
                verdict = "related" if rel_rows and rel_rows[0].get("related_count", 0) > 0 else "unrelated"
            except Exception:
                verdict = "unrelated"

        return success_response(
            data={
                "term_a": {"input": term_a, "resolved_concepts": concepts_a},
                "term_b": {"input": term_b, "resolved_concepts": concepts_b},
                "verdict": verdict,
                "compatible": verdict in ("same_concept", "related"),
            },
            metadata={"verdict": verdict},
        )

    # ── normalize_clinical_term ──────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("normalize_clinical_term")
    async def normalize_clinical_term(
        raw_term: str,
        target_code_system: str = "",
        access_context: str = "",
    ) -> str:
        """
        Normalize a free-text clinical term to its canonical preferred form
        in the ontology — and optionally to a specific code system.

        Use this when user input uses colloquial, abbreviated, or ambiguous
        clinical language before passing values to data query tools.

        Examples:
          'heart attack'  → preferred_term: 'Myocardial Infarction', ICD-10: 'I21'
          'creat'         → preferred_term: 'Serum Creatinine', LOINC: '2160-0'
          'phase 3'       → preferred_term: 'Phase III'

        Args:
            raw_term:           Free-text user input to normalize.
            target_code_system: Optional target — 'ICD-10' | 'LOINC' | 'RxNorm' | 'MedDRA'.
            access_context:     Authorization context (injected by system).

        Returns:
            JSON with preferred_term, concept_id, and code_system. If the term
            maps to multiple concepts a 'candidates' list is returned for
            disambiguation.
        """
        _ = access_context
        if not raw_term or not raw_term.strip():
            return error_response("raw_term is required", "INVALID_ARGS")

        term = raw_term.strip()
        cs_filter = ""
        params: dict[str, Any] = {"term": term}
        if target_code_system.strip():
            cs_filter = "AND toLower(c.code_system) = toLower($cs)"
            params["cs"] = target_code_system.strip()

        try:
            rows = await run_cypher(
                f"""
                MATCH (t:OntologyTerm)-[:REFERS_TO]->(c:Concept)
                WHERE toLower(t.term) CONTAINS toLower($term)
                   OR toLower(c.label) CONTAINS toLower($term)
                   OR toLower(c.preferred_term) CONTAINS toLower($term)
                {cs_filter}
                RETURN DISTINCT
                    c.concept_id     AS concept_id,
                    c.preferred_term AS preferred_term,
                    c.label          AS label,
                    c.code_system    AS code_system,
                    c.definition     AS definition
                ORDER BY
                    CASE WHEN toLower(c.preferred_term) = toLower($term) THEN 0
                         WHEN toLower(c.label) = toLower($term) THEN 1
                         ELSE 2 END
                LIMIT 5
                """,
                params,
            )
            if rows:
                if len(rows) == 1:
                    return success_response(
                        data={"raw_term": raw_term, "normalized": rows[0], "source": "neo4j"},
                        metadata={"resolved": True},
                    )
                return success_response(
                    data={"raw_term": raw_term, "candidates": rows, "source": "neo4j"},
                    metadata={"resolved": False, "candidate_count": len(rows), "hint": "Multiple matches — use concept_id to disambiguate"},
                )
        except Exception as exc:
            logger.warning("normalize_clinical_term graph query failed: %s", exc)

        # Registry fallback
        matches = resolve_concepts(term=term, limit=5)
        if target_code_system.strip():
            matches = [m for m in matches if m.get("code_system", "").lower() == target_code_system.strip().lower()]

        if not matches:
            return error_response(
                f"Could not normalize term '{raw_term}'",
                "NOT_FOUND",
            )
        if len(matches) == 1:
            return success_response(
                data={"raw_term": raw_term, "normalized": matches[0], "source": "registry"},
                metadata={"resolved": True},
            )
        return success_response(
            data={"raw_term": raw_term, "candidates": matches, "source": "registry"},
            metadata={"resolved": False, "candidate_count": len(matches), "hint": "Multiple matches — use concept_id to disambiguate"},
        )

    # ── explain_metric_semantics ─────────────────────────────────────────────
    @mcp.tool()
    @instrument_tool("explain_metric_semantics")
    async def explain_metric_semantics(
        field_name: str,
        tool_name: str = "",
        access_context: str = "",
    ) -> str:
        """
        Explain the semantic meaning of a field or metric returned by a data
        tool — including its unit, code system, allowed values, and any
        clinical interpretation guidance.

        Use this when a data tool returns a field whose clinical meaning is
        not immediately obvious (e.g., 'result_flag', 'ae_grade', 'loinc_code').

        Args:
            field_name:    The exact field name from a tool response (e.g. 'result_flag').
            tool_name:     Optional — the tool that returned this field (for context).
            access_context: Authorization context (injected by system).

        Returns:
            JSON with concept definition, unit, code system, allowed values, and
            clinical interpretation notes.
        """
        _ = access_context
        if not field_name or not field_name.strip():
            return error_response("field_name is required", "INVALID_ARGS")

        fname = field_name.strip()

        # 1. Field → concept_id from FIELD_CONCEPT_MAP
        concept_id = FIELD_CONCEPT_MAP.get(fname)

        if concept_id:
            # 2. Fetch concept from graph
            graph_concept = await _graph_get_concept(concept_id)
            if graph_concept:
                return success_response(
                    data={
                        "field_name": fname,
                        "tool_name": tool_name or None,
                        "concept": graph_concept,
                        "source": "neo4j",
                    },
                    metadata={"resolved": True},
                )

            # Registry fallback
            concept = CONCEPT_REGISTRY.get(concept_id, {})
            return success_response(
                data={
                    "field_name": fname,
                    "tool_name": tool_name or None,
                    "concept": {"concept_id": concept_id, **concept},
                    "source": "registry",
                },
                metadata={"resolved": True},
            )

        # 3. No direct mapping — try fuzzy match on field name as a term
        try:
            rows = await run_cypher(
                """
                MATCH (c:Concept)
                WHERE toLower(c.label) CONTAINS toLower($fname)
                   OR toLower(c.preferred_term) CONTAINS toLower($fname)
                RETURN c.concept_id AS concept_id, c.label AS label,
                       c.definition AS definition, c.code_system AS code_system,
                       c.allowed_values AS allowed_values
                LIMIT 3
                """,
                {"fname": fname},
            )
            if rows:
                return success_response(
                    data={
                        "field_name": fname,
                        "tool_name": tool_name or None,
                        "candidates": rows,
                        "source": "neo4j",
                        "note": "No direct field mapping found; showing related concepts",
                    },
                    metadata={"resolved": False},
                )
        except Exception as exc:
            logger.warning("explain_metric_semantics fuzzy query failed: %s", exc)

        return error_response(
            f"No semantic definition found for field '{fname}'. "
            "Call list_ontology_concepts() to browse available concepts.",
            "NOT_FOUND",
        )
