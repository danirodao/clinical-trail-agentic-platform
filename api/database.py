import os
import logging
import asyncpg
from typing import Optional

logger = logging.getLogger(__name__)

# Global database pool
db_pool: Optional[asyncpg.Pool] = None

async def init_db_pool():
    """Initialize the global database pool."""
    global db_pool
    if db_pool is not None:
        return db_pool

    async def init_connection(conn):
        """Register JSON/JSONB codecs on every connection."""
        import json
        await conn.set_type_codec(
            "json",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog"
        )
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog"
        )

    db_pool = await asyncpg.create_pool(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        database=os.environ.get("POSTGRES_DB", "clinical_trials"),
        user=os.environ.get("POSTGRES_USER", "ctuser"),
        password=os.environ.get("POSTGRES_PASSWORD", "ctpassword"),
        min_size=5,
        max_size=20,
        init=init_connection,
    )
    logger.info("Database pool initialized")
    return db_pool

async def close_db_pool():
    """Close the global database pool."""
    global db_pool
    if db_pool:
        await db_pool.close()
        db_pool = None
        logger.info("Database pool closed")

async def get_db_pool() -> asyncpg.Pool:
    """Dependency to get the database pool."""
    if db_pool is None:
        # Fallback for testing or if lifespan wasn't triggered
        await init_db_pool()
    return db_pool
