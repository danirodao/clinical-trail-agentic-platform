"""
AsyncPG connection pool for PostgreSQL.

Usage:
    from db.postgres import fetch, fetchrow, fetchval

    rows = await fetch("SELECT * FROM clinical_trial WHERE phase = $1", "Phase 3")
"""

import os
import logging
import time
from typing import Any

import asyncpg

from observability import MCP_DB_QUERY_DURATION

logger = logging.getLogger(__name__)

async def init_pool() -> None:
    """Initialize the asyncpg connection pool."""
    global _pool
    dsn = os.environ.get(
        "DATABASE_URL",
        "postgresql://ctuser:ctpassword@postgres:5432/clinical_trials",
    )
    _pool = await asyncpg.create_pool(
        dsn,
        min_size=5,
        max_size=20,
        command_timeout=30,
        statement_cache_size=100,
    )
    # Verify connectivity
    async with _pool.acquire() as conn:
        version = await conn.fetchval("SELECT version()")
        logger.info(f"PostgreSQL pool initialized | {version[:60]}...")


async def close_pool() -> None:
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL pool closed")


def get_pool() -> asyncpg.Pool:
    """Get the active connection pool. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError("PostgreSQL pool not initialized. Call init_pool() first.")
    return _pool


async def fetch(query: str, *args: Any) -> list[dict[str, Any]]:
    """Execute a query and return all rows as list of dicts."""
    pool = get_pool()
    start = time.perf_counter()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return [dict(row) for row in rows]
    finally:
        MCP_DB_QUERY_DURATION.labels(db="postgres", operation="fetch").observe(time.perf_counter() - start)


async def fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
    """Execute a query and return a single row as dict, or None."""
    pool = get_pool()
    start = time.perf_counter()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return dict(row) if row else None
    finally:
        MCP_DB_QUERY_DURATION.labels(db="postgres", operation="fetchrow").observe(time.perf_counter() - start)


async def fetchval(query: str, *args: Any) -> Any:
    """Execute a query and return a single scalar value."""
    pool = get_pool()
    start = time.perf_counter()
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(query, *args)
    finally:
        MCP_DB_QUERY_DURATION.labels(db="postgres", operation="fetchval").observe(time.perf_counter() - start)


async def execute(query: str, *args: Any) -> str:
    """Execute a query and return the command status string."""
    pool = get_pool()
    start = time.perf_counter()
    try:
        async with pool.acquire() as conn:
            return await conn.execute(query, *args)
    finally:
        MCP_DB_QUERY_DURATION.labels(db="postgres", operation="execute").observe(time.perf_counter() - start)