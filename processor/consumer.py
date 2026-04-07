# processor/consumer.py
"""
Kafka consumer for PDF processing events.
Implements:
- Manual commit (at-least-once delivery)
- Dead letter queue for permanent failures
- Graceful shutdown
- Back-pressure via sequential processing
"""
import json
import signal
import logging
import asyncio
from datetime import datetime
from typing import Callable, Awaitable
from confluent_kafka import Consumer, KafkaError, KafkaException, TopicPartition
from shared.config import KafkaConfig
from shared.kafka_schemas import PDFGeneratedEvent, ProcessingStatusEvent, EventType

logger = logging.getLogger(__name__)


class PDFEventConsumer:
    """
    Consumes pdf-generated events from Kafka.
    Processes one PDF at a time (sequential for resource control).
    Publishes status events for monitoring.
    """

    MAX_RETRIES = 3
    POLL_TIMEOUT_SECONDS = 1.0

    def __init__(
        self,
        kafka_config: KafkaConfig,
        process_callback: Callable[[PDFGeneratedEvent], Awaitable[dict]],
    ):
        self.kafka_config = kafka_config
        self.process_callback = process_callback
        self._running = False

        # Consumer with manual offset commit
        self.consumer = Consumer({
            'bootstrap.servers': kafka_config.bootstrap_servers,
            'group.id': kafka_config.consumer_group,
            'auto.offset.reset': 'earliest',
            'enable.auto.commit': False,         # Manual commit after processing
            'max.poll.interval.ms': 600000,      # 10 min (PDF processing can be slow)
            'session.timeout.ms': 30000,
            'fetch.max.bytes': 1048576,
            'max.partition.fetch.bytes': 1048576,
        })

        # Status producer (for reporting processing results)
        from confluent_kafka import Producer
        self.status_producer = Producer({
            'bootstrap.servers': kafka_config.bootstrap_servers,
            'acks': 'all'
        })

        # Graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self._running = False

    async def start(self):
        """
        Main consumer loop.
        Subscribes to topic and processes messages one at a time.
        """
        self.consumer.subscribe([self.kafka_config.topic_pdf_generated])
        self._running = True

        logger.info(
            f"Consumer started. Listening on topic: "
            f"{self.kafka_config.topic_pdf_generated} "
            f"(group: {self.kafka_config.consumer_group})"
        )

        while self._running:
            try:
                msg = self.consumer.poll(timeout=self.POLL_TIMEOUT_SECONDS)

                if msg is None:
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        logger.debug(
                            f"Reached end of partition {msg.partition()} "
                            f"at offset {msg.offset()}"
                        )
                        continue
                    else:
                        logger.error(f"Kafka error: {msg.error()}")
                        continue

                # ── Parse the event ──
                try:
                    event_data = json.loads(msg.value().decode('utf-8'))
                    event = PDFGeneratedEvent(**event_data)
                except Exception as e:
                    logger.error(
                        f"Failed to parse message at offset {msg.offset()}: {e}"
                    )
                    await self._send_to_dlq(msg, str(e))
                    self.consumer.commit(message=msg)
                    continue

                # ── Process the PDF ──
                logger.info(
                    f"Processing event: {event.nct_id} "
                    f"(event_id={event.event_id}, "
                    f"partition={msg.partition()}, "
                    f"offset={msg.offset()})"
                )

                await self._process_with_retry(event, msg)

            except Exception as e:
                logger.error(f"Unexpected error in consumer loop: {e}", exc_info=True)
                await asyncio.sleep(5)  # Back off on unexpected errors

        # ── Graceful shutdown ──
        logger.info("Shutting down consumer...")
        self.consumer.close()
        self.status_producer.flush(timeout=10)
        logger.info("Consumer shut down cleanly")

    async def _process_with_retry(self, event: PDFGeneratedEvent, msg):
        """
        Process a single event with retry logic.
        On permanent failure, send to DLQ.
        """
        last_error = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                start_time = datetime.utcnow()

                # Publish "started" status
                await self._publish_status(
                    EventType.PDF_PROCESSING_STARTED,
                    event, retry_count=attempt - 1
                )

                # ── Call the processing callback ──
                result = await self.process_callback(event)

                elapsed = (datetime.utcnow() - start_time).total_seconds()

                # Publish "completed" status
                await self._publish_status(
                    EventType.PDF_PROCESSING_COMPLETED,
                    event,
                    trial_id=result.get("trial_id"),
                    patient_count=result.get("patient_count", 0),
                    embedding_chunk_count=result.get("chunk_count", 0),
                    processing_duration=elapsed
                )

                # ── Commit offset (message processed successfully) ──
                self.consumer.commit(message=msg)

                logger.info(
                    f"✅ Processed {event.nct_id} in {elapsed:.1f}s "
                    f"(trial={result.get('trial_id')}, "
                    f"patients={result.get('patient_count', 0)}, "
                    f"chunks={result.get('chunk_count', 0)})"
                )
                return  # Success

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Attempt {attempt}/{self.MAX_RETRIES} failed for "
                    f"{event.nct_id}: {e}"
                )
                if attempt < self.MAX_RETRIES:
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.info(f"Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)

        # ── All retries exhausted → DLQ ──
        logger.error(
            f"❌ Permanently failed after {self.MAX_RETRIES} attempts: "
            f"{event.nct_id}: {last_error}"
        )
        await self._send_to_dlq(msg, str(last_error))
        await self._publish_status(
            EventType.PDF_PROCESSING_FAILED,
            event,
            error_message=str(last_error),
            error_type=type(last_error).__name__,
            retry_count=self.MAX_RETRIES
        )
        # Commit so we don't reprocess
        self.consumer.commit(message=msg)

    async def _publish_status(
        self,
        event_type: EventType,
        source_event: PDFGeneratedEvent,
        trial_id: str | None = None,
        patient_count: int = 0,
        embedding_chunk_count: int = 0,
        processing_duration: float | None = None,
        error_message: str | None = None,
        error_type: str | None = None,
        retry_count: int = 0
    ):
        """Publish a processing status event for monitoring."""
        status = ProcessingStatusEvent(
            event_type=event_type,
            source_event_id=source_event.event_id,
            nct_id=source_event.nct_id,
            object_key=source_event.object_key,
            trial_id=trial_id,
            patient_count=patient_count,
            embedding_chunk_count=embedding_chunk_count,
            processing_duration_seconds=processing_duration,
            error_message=error_message,
            error_type=error_type,
            retry_count=retry_count
        )

        self.status_producer.produce(
            topic=self.kafka_config.topic_processing_status,
            key=source_event.nct_id.encode('utf-8'),
            value=status.model_dump_json().encode('utf-8'),
            headers={
                'event_type': event_type.value.encode('utf-8'),
            }
        )
        self.status_producer.poll(0)  # Trigger delivery callbacks

    async def _send_to_dlq(self, msg, error: str):
        """Send failed message to the dead letter queue."""
        headers = list(msg.headers() or [])
        headers.extend([
            ('dlq_reason', error.encode('utf-8')),
            ('dlq_timestamp', datetime.utcnow().isoformat().encode('utf-8')),
            ('original_topic', msg.topic().encode('utf-8')),
            ('original_partition', str(msg.partition()).encode('utf-8')),
            ('original_offset', str(msg.offset()).encode('utf-8')),
        ])

        self.status_producer.produce(
            topic=self.kafka_config.topic_dlq,
            key=msg.key(),
            value=msg.value(),
            headers=headers
        )
        self.status_producer.flush(timeout=10)
        logger.info(f"Sent message to DLQ: {self.kafka_config.topic_dlq}")