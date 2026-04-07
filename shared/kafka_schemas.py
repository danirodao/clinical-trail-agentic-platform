# shared/kafka_schemas.py
"""
Kafka message schemas — all messages are JSON-serialized Pydantic models.
"""
from pydantic import BaseModel, Field
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
import uuid


class EventType(str, Enum):
    PDF_GENERATED = "pdf.generated"
    PDF_PROCESSING_STARTED = "pdf.processing.started"
    PDF_PROCESSING_COMPLETED = "pdf.processing.completed"
    PDF_PROCESSING_FAILED = "pdf.processing.failed"


class PDFGeneratedEvent(BaseModel):
    """
    Published by Generator when a PDF is created and uploaded to MinIO.
    This is the message consumed by the Processor.
    """
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType = EventType.PDF_GENERATED
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # PDF location in MinIO
    bucket: str
    object_key: str                    # e.g., "trials/NCT10000042/NCT10000042_protocol.pdf"
    file_size_bytes: int

    # Pre-extracted metadata (from the generator — it knows the data)
    nct_id: str
    therapeutic_area: str
    phase: str
    sponsor: str
    num_patients: int
    regions: list[str]

    # Generation metadata
    generation_seed: int
    generator_version: str = "1.0.0"


class ProcessingStatusEvent(BaseModel):
    """
    Published by Processor to report progress/completion.
    """
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Reference to the source event
    source_event_id: str
    nct_id: str
    object_key: str

    # Processing results
    trial_id: str | None = None
    patient_count: int = 0
    embedding_chunk_count: int = 0
    graph_node_count: int = 0
    graph_relationship_count: int = 0

    # Timing
    processing_duration_seconds: float | None = None

    # Errors
    error_message: str | None = None
    error_type: str | None = None
    retry_count: int = 0


class TrialIngestedEvent(BaseModel):
    """Emitted by processor after successful ingestion into all three stores."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trial_id: str          # UUID of the ingested trial
    nct_id: str
    therapeutic_area: Optional[str] = None
    phase: Optional[str] = None
    study_type: Optional[str] = None
    regions: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    patient_count: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)