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
