"""
Kafka consumer that runs inside the API service.
Listens for trial-ingested events and refreshes dynamic collections.

Runs in a background thread (not blocking the FastAPI event loop).
"""

import json
import logging
import threading
import asyncio
from typing import Optional

from confluent_kafka import Consumer, KafkaError

logger = logging.getLogger(__name__)

KAFKA_CONFIG = {
    "bootstrap.servers": "kafka:29092",
    "group.id": "collection-refresh-group",
    "auto.offset.reset": "latest",
    "enable.auto.commit": True,
    "auto.commit.interval.ms": 5000,
    "max.poll.interval.ms": 300000,
}


class CollectionRefreshConsumer:
    """Consumes trial-ingested events and refreshes dynamic collections."""

    def __init__(self, db_pool_factory, fga_client_factory):
        """
        Args:
            db_pool_factory: callable that returns an asyncpg pool
            fga_client_factory: callable that returns an OpenFGA client
        """
        self._db_pool_factory = db_pool_factory
        self._fga_client_factory = fga_client_factory
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self):
        """Start the consumer in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="collection-consumer")
        self._thread.start()
        logger.info("Collection refresh consumer started")

    def stop(self):
        """Stop the consumer gracefully."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Collection refresh consumer stopped")

    def _run(self):
        """Consumer loop running in background thread."""
        # Create a new event loop for this thread
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        consumer = Consumer(KAFKA_CONFIG)
        consumer.subscribe(["trial-ingested"])

        try:
            while self._running:
                msg = consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    logger.error(f"Consumer error: {msg.error()}")
                    continue

                try:
                    event = json.loads(msg.value().decode("utf-8"))
                    logger.info(
                        f"Received trial-ingested: {event.get('nct_id')} "
                        f"(trial_id={event.get('trial_id')})"
                    )
                    self._loop.run_until_complete(self._handle_event(event))
                except Exception as e:
                    logger.error(f"Failed to process trial-ingested event: {e}", exc_info=True)

        finally:
            consumer.close()
            self._loop.close()

    async def _handle_event(self, event: dict):
        """Process a single trial-ingested event."""
        from auth.asset_service import AssetService

        db_pool = self._db_pool_factory()
        fga_client = self._fga_client_factory()

        service = AssetService(db_pool, fga_client)

        try:
            results = await service.refresh_dynamic_collections()
            if results:
                for r in results:
                    logger.info(
                        f"Collection {r['collection_id']}: "
                        f"+{r['new_trials_added']} trials, "
                        f"+{r['auto_grants_written']} auto-grants"
                    )
            else:
                logger.debug(
                    f"No dynamic collections matched trial {event.get('nct_id')}"
                )
        except Exception as e:
            logger.error(f"Dynamic collection refresh failed: {e}", exc_info=True)