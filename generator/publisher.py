# generator/publisher.py
"""
Publishes PDF-generated events to Kafka after uploading PDFs to MinIO.
Implements exactly-once semantics with idempotent producer.
"""
import json
import logging
from confluent_kafka import Producer
from shared.config import KafkaConfig, MinIOConfig
from shared.storage import ObjectStorage
from shared.kafka_schemas import PDFGeneratedEvent

logger = logging.getLogger(__name__)


class PDFPublisher:
    """
    Handles the two-step publish:
    1. Upload PDF to MinIO (object store)
    2. Publish event to Kafka with MinIO reference
    
    This pattern avoids sending large PDF binaries through Kafka.
    Kafka carries lightweight event metadata; MinIO stores the payload.
    """

    def __init__(self, kafka_config: KafkaConfig, minio_config: MinIOConfig):
        self.kafka_config = kafka_config
        self.storage = ObjectStorage(minio_config)

        # Idempotent producer configuration
        self.producer = Producer({
            'bootstrap.servers': kafka_config.bootstrap_servers,
            'enable.idempotence': True,
            'acks': 'all',
            'retries': 5,
            'max.in.flight.requests.per.connection': 1,
            'linger.ms': 100,       # Batch small messages
            'compression.type': 'lz4',
        })

    def publish_trial_pdf(
        self,
        local_pdf_path: str,
        nct_id: str,
        therapeutic_area: str,
        phase: str,
        sponsor: str,
        num_patients: int,
        regions: list[str],
        generation_seed: int
    ) -> PDFGeneratedEvent:
        """
        Upload PDF to MinIO, then publish event to Kafka.
        Uses nct_id as Kafka message key for partition ordering —
        all events for the same trial go to the same partition.
        """
        # ── Step 1: Upload to MinIO ──
        object_key = f"trials/{nct_id}/{nct_id}_protocol.pdf"
        file_size = self.storage.upload_pdf(local_pdf_path, object_key)

        # ── Step 2: Create event ──
        event = PDFGeneratedEvent(
            bucket=self.storage.config.bucket,
            object_key=object_key,
            file_size_bytes=file_size,
            nct_id=nct_id,
            therapeutic_area=therapeutic_area,
            phase=phase,
            sponsor=sponsor,
            num_patients=num_patients,
            regions=regions,
            generation_seed=generation_seed
        )

        # ── Step 3: Publish to Kafka ──
        self.producer.produce(
            topic=self.kafka_config.topic_pdf_generated,
            key=nct_id.encode('utf-8'),              # Partition by trial
            value=event.model_dump_json().encode('utf-8'),
            headers={
                'event_type': event.event_type.value.encode('utf-8'),
                'nct_id': nct_id.encode('utf-8'),
                'content_type': b'application/json'
            },
            callback=self._delivery_callback
        )

        # Block until the message is delivered
        self.producer.flush(timeout=30)

        logger.info(
            f"Published event for {nct_id}: "
            f"{object_key} ({file_size:,} bytes)"
        )
        return event

    def _delivery_callback(self, err, msg):
        if err:
            logger.error(
                f"Kafka delivery failed: {err} "
                f"(topic={msg.topic()}, key={msg.key()})"
            )
            raise Exception(f"Kafka delivery failed: {err}")
        else:
            logger.debug(
                f"Delivered to {msg.topic()} "
                f"[partition={msg.partition()}, offset={msg.offset()}]"
            )

    def close(self):
        remaining = self.producer.flush(timeout=30)
        if remaining > 0:
            logger.warning(f"{remaining} messages were not delivered")