"""
Neo4j async driver wrapper for knowledge graph queries.

Usage:
    from db.neo4j_client import run_cypher

    records = await run_cypher(
        "MATCH (d:Drug)<-[:TESTS_INTERVENTION]-(t:ClinicalTrial)-[:STUDIES]->(c:Condition) WHERE d.name = $name RETURN c",
        {"name": "Nivolumab"}
    )
"""

import os
import logging
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

logger = logging.getLogger(__name__)

_driver: AsyncDriver | None = None


async def init_driver() -> None:
    """Initialize the Neo4j async driver."""
    global _driver
    uri = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "password")

    _driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    # Verify connectivity
    try:
        async with _driver.session() as session:
            result = await session.run("RETURN 1 AS ping")
            record = await result.single()
            if record and record["ping"] == 1:
                logger.info(f"Neo4j driver initialized | uri={uri}")
            else:
                logger.warning("Neo4j ping returned unexpected result")
    except Exception as e:
        logger.warning(f"Neo4j connectivity check failed (non-fatal): {e}")


async def close_driver() -> None:
    """Close the Neo4j driver."""
    global _driver
    if _driver:
        await _driver.close()
        _driver = None
        logger.info("Neo4j driver closed")


def _serialize_neo4j_value(value: Any) -> Any:
    """Convert Neo4j-specific types to JSON-safe Python types."""
    if hasattr(value, "items"):
        # Node or dict-like object
        return {k: _serialize_neo4j_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_neo4j_value(v) for v in value]
    if hasattr(value, "id") and hasattr(value, "labels"):
        # Neo4j Node
        props = dict(value.items())
        return {
            "_labels": list(value.labels),
            **{k: _serialize_neo4j_value(v) for k, v in props.items()},
        }
    if hasattr(value, "type") and hasattr(value, "start_node"):
        # Neo4j Relationship
        props = dict(value.items())
        return {
            "_type": value.type,
            **{k: _serialize_neo4j_value(v) for k, v in props.items()},
        }
    return value


async def run_cypher(
    query: str,
    parameters: dict[str, Any] | None = None,
    database: str | None = None,
) -> list[dict[str, Any]]:
    """
    Execute a Cypher query and return results as list of dicts.

    Each dict maps return aliases to their values.
    Neo4j Nodes/Relationships are converted to plain dicts.
    """
    if _driver is None:
        logger.error("Neo4j driver not initialized")
        return []

    try:
        async with _driver.session(database=database or "neo4j") as session:
            result = await session.run(query, parameters or {})
            records = []
            async for record in result:
                row = {}
                for key in record.keys():
                    row[key] = _serialize_neo4j_value(record[key])
                records.append(row)
            return records
    except Exception as e:
        logger.error(f"Neo4j query error: {e}", exc_info=True)
        logger.debug(f"Failed query: {query} | params: {parameters}")
        return []


async def run_cypher_single(
    query: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Execute a Cypher query and return a single record, or None."""
    results = await run_cypher(query, parameters)
    return results[0] if results else None