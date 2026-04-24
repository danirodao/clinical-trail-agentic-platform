"""
Neo4j ontology schema setup and concept seeder.

Applies uniqueness constraints and merges all concepts from the in-process
CONCEPT_REGISTRY into Neo4j (:Concept, :OntologyTerm, :OntologyRelease nodes).

Idempotent — safe to run on every startup.
"""

from __future__ import annotations

import logging
from typing import Any

from neo4j_ontology import run_write_cypher
from ontology import CONCEPT_REGISTRY, FIELD_CONCEPT_MAP, ONTOLOGY_VERSION

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Schema: constraints and indexes
# ─────────────────────────────────────────────────────────────────────────────

_CONSTRAINTS = [
    "CREATE CONSTRAINT ontology_concept_id IF NOT EXISTS FOR (c:Concept) REQUIRE c.concept_id IS UNIQUE",
    "CREATE CONSTRAINT ontology_term_unique IF NOT EXISTS FOR (t:OntologyTerm) REQUIRE (t.term, t.concept_id) IS UNIQUE",
    "CREATE CONSTRAINT ontology_release_unique IF NOT EXISTS FOR (r:OntologyRelease) REQUIRE r.version IS UNIQUE",
]

_INDEXES = [
    "CREATE INDEX concept_label_idx IF NOT EXISTS FOR (c:Concept) ON (c.label)",
    "CREATE INDEX concept_cs_idx IF NOT EXISTS FOR (c:Concept) ON (c.code_system)",
    "CREATE FULLTEXT INDEX concept_fulltext IF NOT EXISTS FOR (c:Concept) ON EACH [c.label, c.preferred_term, c.definition]",
]


async def apply_schema() -> None:
    """Apply uniqueness constraints and search indexes."""
    for cypher in _CONSTRAINTS + _INDEXES:
        try:
            await run_write_cypher(cypher)
        except Exception as exc:
            # Index/constraint already exists or Neo4j community edition limitation — non-fatal
            logger.debug("Schema statement skipped (may already exist): %s | %s", cypher[:60], exc)
    logger.info("Ontology schema applied")


# ─────────────────────────────────────────────────────────────────────────────
# Seeder: merge registry into Neo4j
# ─────────────────────────────────────────────────────────────────────────────


async def seed_ontology() -> None:
    """Merge all CONCEPT_REGISTRY entries into Neo4j. Idempotent via MERGE."""
    # 1) Ensure an OntologyRelease node exists for the current version
    await run_write_cypher(
        """
        MERGE (r:OntologyRelease {version: $version})
        SET r.seeded_at = datetime()
        """,
        {"version": ONTOLOGY_VERSION},
    )

    # 2) Merge each concept
    for concept_id, concept in CONCEPT_REGISTRY.items():
        allowed = concept.get("allowed_values", [])
        synonyms = concept.get("synonyms", [])

        await run_write_cypher(
            """
            MERGE (c:Concept {concept_id: $concept_id})
            SET
                c.label           = $label,
                c.preferred_term  = $preferred_term,
                c.definition      = $definition,
                c.code_system     = $code_system,
                c.allowed_values  = $allowed_values,
                c.synonyms        = $synonyms,
                c.ontology_version = $version
            WITH c
            MATCH (r:OntologyRelease {version: $version})
            MERGE (c)-[:DEFINED_IN]->(r)
            """,
            {
                "concept_id":    concept_id,
                "label":         concept.get("label", ""),
                "preferred_term": concept.get("preferred_term", ""),
                "definition":    concept.get("definition", ""),
                "code_system":   concept.get("code_system", ""),
                "allowed_values": allowed,
                "synonyms":      synonyms,
                "version":       ONTOLOGY_VERSION,
            },
        )

        # 3) Merge OntologyTerm nodes for preferred_term + synonyms
        all_terms = [concept.get("preferred_term", "")] + synonyms
        for term_str in all_terms:
            if not term_str:
                continue
            await run_write_cypher(
                """
                MERGE (t:OntologyTerm {term: $term, concept_id: $concept_id})
                WITH t
                MATCH (c:Concept {concept_id: $concept_id})
                MERGE (t)-[:REFERS_TO]->(c)
                """,
                {"term": term_str.lower(), "concept_id": concept_id},
            )

    # 4) Merge broader/narrower hierarchy edges
    for concept_id, concept in CONCEPT_REGISTRY.items():
        broader = concept.get("broader")
        if broader and broader in CONCEPT_REGISTRY:
            await run_write_cypher(
                """
                MATCH (child:Concept {concept_id: $child_id})
                MATCH (parent:Concept {concept_id: $parent_id})
                MERGE (child)-[:NARROWER_THAN]->(parent)
                """,
                {"child_id": concept_id, "parent_id": broader},
            )

    # 5) Merge FieldConceptMap nodes for runtime field resolution
    for field_name, concept_id in FIELD_CONCEPT_MAP.items():
        await run_write_cypher(
            """
            MERGE (f:FieldMapping {field_name: $field_name})
            SET f.concept_id = $concept_id
            WITH f
            MATCH (c:Concept {concept_id: $concept_id})
            MERGE (f)-[:MAPS_TO]->(c)
            """,
            {"field_name": field_name, "concept_id": concept_id},
        )

    logger.info(
        "Ontology seeded | version=%s | concepts=%d | fields=%d",
        ONTOLOGY_VERSION,
        len(CONCEPT_REGISTRY),
        len(FIELD_CONCEPT_MAP),
    )
