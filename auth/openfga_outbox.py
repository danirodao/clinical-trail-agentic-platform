"""
OpenFGA tuple outbox.

Replaces brittle dual-write (PostgreSQL + OpenFGA in the same request path)
with transactional outbox persistence and async relay.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import asyncpg

from auth.openfga_client import OpenFGAClient, get_openfga_client

logger = logging.getLogger(__name__)

OUTBOX_BATCH_SIZE = int(os.environ.get("OPENFGA_OUTBOX_BATCH_SIZE", "200"))
OUTBOX_MAX_ATTEMPTS = int(os.environ.get("OPENFGA_OUTBOX_MAX_ATTEMPTS", "10"))
OUTBOX_RETRY_BASE_SECONDS = int(os.environ.get("OPENFGA_OUTBOX_RETRY_BASE_SECONDS", "5"))
OUTBOX_RETRY_MAX_SECONDS = int(os.environ.get("OPENFGA_OUTBOX_RETRY_MAX_SECONDS", "300"))
OUTBOX_POLL_SECONDS = float(os.environ.get("OPENFGA_OUTBOX_POLL_SECONDS", "3"))


async def ensure_outbox_schema(db_pool: asyncpg.Pool) -> None:
    """Create outbox table and indexes if they do not exist."""
    await db_pool.execute(
        """
        CREATE TABLE IF NOT EXISTS openfga_tuple_outbox (
            id BIGSERIAL PRIMARY KEY,
            operation VARCHAR(10) NOT NULL CHECK (operation IN ('write', 'delete')),
            tuple_user TEXT NOT NULL,
            tuple_relation TEXT NOT NULL,
            tuple_object TEXT NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'processing', 'failed', 'applied', 'dead')),
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source TEXT,
            correlation_id TEXT,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            processed_at TIMESTAMPTZ
        )
        """
    )
    await db_pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_openfga_outbox_pending
        ON openfga_tuple_outbox (status, available_at, id)
        """
    )
    await db_pool.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_openfga_outbox_correlation
        ON openfga_tuple_outbox (correlation_id)
        """
    )


def _validate_tuple(t: dict) -> tuple[str, str, str]:
    user = t.get("user")
    relation = t.get("relation")
    obj = t.get("object")
    if not user or not relation or not obj:
        raise ValueError(f"Invalid tuple payload: {t}")
    return str(user), str(relation), str(obj)


async def enqueue_tuples(
    conn: asyncpg.Connection,
    tuples: list[dict],
    operation: str,
    source: str,
    correlation_id: Optional[str] = None,
) -> int:
    """Enqueue tuple write/delete operations inside an existing DB transaction."""
    if operation not in {"write", "delete"}:
        raise ValueError("operation must be 'write' or 'delete'")
    if not tuples:
        return 0

    rows = []
    for t in tuples:
        user, relation, obj = _validate_tuple(t)
        rows.append((operation, user, relation, obj, source, correlation_id))

    await conn.executemany(
        """
        INSERT INTO openfga_tuple_outbox
            (operation, tuple_user, tuple_relation, tuple_object, source, correlation_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        rows,
    )
    return len(rows)


async def enqueue_write_tuples(
    conn: asyncpg.Connection,
    tuples: list[dict],
    source: str,
    correlation_id: Optional[str] = None,
) -> int:
    return await enqueue_tuples(conn, tuples, "write", source, correlation_id)


async def enqueue_delete_tuples(
    conn: asyncpg.Connection,
    tuples: list[dict],
    source: str,
    correlation_id: Optional[str] = None,
) -> int:
    return await enqueue_tuples(conn, tuples, "delete", source, correlation_id)


class OpenFGAOutboxRelay:
    """Consumes outbox rows and syncs tuples to OpenFGA."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        fga_client: Optional[OpenFGAClient] = None,
        batch_size: int = OUTBOX_BATCH_SIZE,
        max_attempts: int = OUTBOX_MAX_ATTEMPTS,
        retry_base_seconds: int = OUTBOX_RETRY_BASE_SECONDS,
        retry_max_seconds: int = OUTBOX_RETRY_MAX_SECONDS,
    ):
        self.db = db_pool
        self.fga = fga_client or get_openfga_client()
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds

    async def process_batch(self) -> dict:
        """Process one batch of ready outbox entries."""
        rows = await self.db.fetch(
            """
            WITH picked AS (
                SELECT id
                FROM openfga_tuple_outbox
                WHERE status IN ('pending', 'failed')
                  AND available_at <= NOW()
                ORDER BY id
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE openfga_tuple_outbox o
            SET status = 'processing',
                attempts = o.attempts + 1,
                updated_at = NOW()
            FROM picked
            WHERE o.id = picked.id
            RETURNING o.id, o.operation, o.tuple_user, o.tuple_relation,
                      o.tuple_object, o.attempts
            """,
            self.batch_size,
        )

        result = {"picked": len(rows), "applied": 0, "failed": 0, "dead": 0}
        if not rows:
            return result

        for row in rows:
            tuple_payload = {
                "user": row["tuple_user"],
                "relation": row["tuple_relation"],
                "object": row["tuple_object"],
            }
            try:
                logger.debug(
                    f"Processing outbox row {row['id']}: {row['operation']} "
                    f"{row['tuple_user']} {row['tuple_relation']} {row['tuple_object']} "
                    f"(attempt {row['attempts']}/{self.max_attempts})"
                )
                if row["operation"] == "write":
                    success = await self.fga.write_tuples([tuple_payload])
                else:
                    success = await self.fga.delete_tuples([tuple_payload])

                if success:
                    logger.info(f"Outbox row {row['id']} applied successfully")
                    await self._mark_applied(row["id"])
                    result["applied"] += 1
                else:
                    logger.warning(f"Outbox row {row['id']} sync failed, will retry")
                    dead = await self._mark_failed(
                        row["id"], row["attempts"], "OpenFGA client returned success=false"
                    )
                    result["dead" if dead else "failed"] += 1

            except Exception as exc:
                logger.error(f"Outbox row {row['id']} exception: {exc}")
                dead = await self._mark_failed(row["id"], row["attempts"], str(exc))
                result["dead" if dead else "failed"] += 1

        return result

    async def _mark_applied(self, row_id: int) -> None:
        await self.db.execute(
            """
            UPDATE openfga_tuple_outbox
            SET status = 'applied',
                processed_at = NOW(),
                updated_at = NOW(),
                last_error = NULL
            WHERE id = $1
            """,
            row_id,
        )

    async def _mark_failed(self, row_id: int, attempts: int, error: str) -> bool:
        if attempts >= self.max_attempts:
            await self.db.execute(
                """
                UPDATE openfga_tuple_outbox
                SET status = 'dead',
                    last_error = $2,
                    updated_at = NOW()
                WHERE id = $1
                """,
                row_id,
                error[:2000],
            )
            logger.error(
                "OpenFGA outbox row moved to dead-letter: row_id=%s error=%s",
                row_id,
                error,
            )
            return True

        backoff = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** max(attempts - 1, 0)))
        await self.db.execute(
            """
            UPDATE openfga_tuple_outbox
            SET status = 'failed',
                last_error = $2,
                available_at = NOW() + ($3 * INTERVAL '1 second'),
                updated_at = NOW()
            WHERE id = $1
            """,
            row_id,
            error[:2000],
            int(backoff),
        )
        return False


async def run_outbox_relay_loop(
    relay: OpenFGAOutboxRelay,
    stop_event: asyncio.Event,
    poll_seconds: float = OUTBOX_POLL_SECONDS,
) -> None:
    """Background loop for continuous outbox processing."""
    logger.info("OpenFGA outbox relay loop started (poll_seconds=%s)", poll_seconds)
    while not stop_event.is_set():
        try:
            stats = await relay.process_batch()
            if stats.get("picked", 0) > 0:
                logger.info("OpenFGA outbox batch processed: %s", stats)
        except Exception as exc:
            logger.error("OpenFGA outbox relay loop error: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_seconds)
        except asyncio.TimeoutError:
            continue

    logger.info("OpenFGA outbox relay loop stopped")
