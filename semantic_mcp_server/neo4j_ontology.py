"""
Neo4j client for the Semantic MCP server.
Only used for ontology graph reads and writes.
"""

from __future__ import annotations

import os
import logging
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

logger = logging.getLogger(__name__)
_driver: AsyncDriver | None = None


async def init_driver() -> None:
    global _driver
    uri = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "neo4jpassword")
    _driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    try:
        async with _driver.session() as session:
            result = await session.run("RETURN 1 AS ping")
            record = await result.single()
            if record and record["ping"] == 1:
                logger.info("Semantic MCP — Neo4j connected | uri=%s", uri)
    except Exception as exc:
        logger.warning("Semantic MCP — Neo4j connectivity check failed (non-fatal): %s", exc)


async def close_driver() -> None:
    global _driver
    if _driver:
        await _driver.close()
        _driver = None


def _serialize(value: Any) -> Any:
    if hasattr(value, "items"):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    return value


async def run_cypher(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not _driver:
        raise RuntimeError("Neo4j driver not initialised")
    async with _driver.session() as session:
        result = await session.run(query, params or {})
        records = await result.data()
        return [_serialize(r) for r in records]


async def run_write_cypher(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a write transaction (MERGE / CREATE)."""
    if not _driver:
        raise RuntimeError("Neo4j driver not initialised")
    async with _driver.session() as session:
        result = await session.run(query, params or {})
        records = await result.data()
        return [_serialize(r) for r in records]
