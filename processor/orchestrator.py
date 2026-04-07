# processor/orchestrator.py
"""
Orchestrates the complete processing pipeline for a single PDF event.
Called by the Kafka consumer for each message.

Pipeline: Download PDF → Parse → Extract → Embed → Load (PG + Qdrant + Neo4j)
"""
import os
import shutil
import time
import logging
import asyncio
import uuid
import json
from datetime import datetime, timezone
from openai import AsyncOpenAI
from confluent_kafka import Producer

from shared.config import AppConfig
from shared.storage import ObjectStorage
from shared.kafka_schemas import PDFGeneratedEvent, TrialIngestedEvent
from processor.pdf_parser import ClinicalTrialPDFParser
from processor.entity_extractor import ClinicalTrialEntityExtractor
from processor.embedding_generator import ClinicalTrialEmbeddingGenerator
from processor.loaders.postgres_loader import PostgresLoader
from processor.loaders.qdrant_loader import QdrantLoader
from processor.loaders.neo4j_loader import Neo4jGraphLoader

logger = logging.getLogger(__name__)


class ProcessingOrchestrator:
    """
    Stateless orchestrator: processes a single PDF event end-to-end.
    Designed to be called by the Kafka consumer for each message.
    All state is in the external stores.
    """

    def __init__(self, config: AppConfig):
        self.config = config

        # External clients
        self.storage = ObjectStorage(config.minio)
        self.openai = AsyncOpenAI(api_key=config.openai.api_key)

        # Pipeline stages
        self.parser = ClinicalTrialPDFParser()
        self.extractor = ClinicalTrialEntityExtractor(
            self.openai, model=config.openai.llm_model
        )
        self.embedder = ClinicalTrialEmbeddingGenerator(
            self.openai,
            embedding_model=config.openai.embedding_model,
            expected_dim=config.openai.embedding_dimensions
        )

        # Data store loaders
        self.pg = PostgresLoader(config.postgres.dsn)
        self.qdrant = QdrantLoader(config.qdrant)
        self.graph = Neo4jGraphLoader(
            config.neo4j.uri, config.neo4j.user, config.neo4j.password
        )

        # Kafka Producer (for internal events)
        self.producer = Producer({
            'bootstrap.servers': config.kafka.bootstrap_servers,
            'acks': 'all'
        })

        self._initialized = False

    async def initialize(self):
        """Initialize all connections. Call once at startup."""
        if self._initialized:
            return

        logger.info("Initializing processing pipeline...")
        await self.pg.connect()
        await self.qdrant.initialize()
        await self.graph.setup_constraints()
        self._initialized = True
        logger.info("Pipeline initialized — all stores connected")

    async def shutdown(self):
        """Clean shutdown of all connections."""
        await self.pg.close()
        await self.graph.close()
        logger.info("Pipeline shutdown complete")

    async def process_event(self, event: PDFGeneratedEvent) -> dict:
        """
        Process a single PDF-generated event.
        This is the callback invoked by the Kafka consumer.

        Returns a result dict with processing statistics.
        """
        if not self._initialized:
            await self.initialize()

        result = {
            "nct_id": event.nct_id,
            "trial_id": None,
            "patient_count": 0,
            "chunk_count": 0,
            "graph_nodes": 0,
            "graph_relationships": 0,
            "errors": []
        }

        temp_pdf_path = None

        try:
            # ═══════════════════════════════════════════
            # STEP 1: Download PDF from MinIO
            # ═══════════════════════════════════════════
            logger.info(
                f"[{event.nct_id}] Step 1/5: "
                f"Downloading from MinIO: {event.object_key}"
            )
            temp_pdf_path = self.storage.download_pdf(event.object_key)
            logger.info(
                f"[{event.nct_id}] Downloaded to {temp_pdf_path} "
                f"({event.file_size_bytes:,} bytes)"
            )

            # ═══════════════════════════════════════════
            # STEP 2: Parse PDF
            # ═══════════════════════════════════════════
            logger.info(f"[{event.nct_id}] Step 2/5: Parsing PDF...")
            parsed_doc = self.parser.parse(temp_pdf_path)
            logger.info(
                f"[{event.nct_id}] Parsed {len(parsed_doc.sections)} sections "
                f"from {parsed_doc.total_pages} pages"
            )

            # ═══════════════════════════════════════════
            # STEP 3: Extract entities
            # ═══════════════════════════════════════════
            logger.info(f"[{event.nct_id}] Step 3/5: Extracting entities...")
            extracted = await self.extractor.extract_all(parsed_doc)
            trial_data = extracted["trial"]
            patients_data = extracted["patients"]

            # Enrich with event metadata (generator knows accurate data)
            trial_data.setdefault("nct_id", event.nct_id)
            trial_data.setdefault("therapeutic_area", event.therapeutic_area)
            trial_data.setdefault("phase", event.phase)
            trial_data.setdefault("lead_sponsor", event.sponsor)
            trial_data.setdefault("regions", event.regions)

            logger.info(
                f"[{event.nct_id}] Extracted trial data + "
                f"{len(patients_data)} patients"
            )

            # ═══════════════════════════════════════════
            # STEP 4: Load into PostgreSQL
            # ═══════════════════════════════════════════
            logger.info(f"[{event.nct_id}] Step 4/5: Loading into stores...")

            # PostgreSQL (relational)
            trial_id = await self.pg.ingest_trial(trial_data)
            result["trial_id"] = trial_id

            patient_ids = []
            for pdata in patients_data:
                try:
                    pid = await self.pg.ingest_patient(pdata, trial_id)
                    patient_ids.append((pid, pdata))
                except Exception as e:
                    logger.warning(
                        f"[{event.nct_id}] Failed to ingest patient "
                        f"{pdata.get('subject_id')}: {e}"
                    )
                    result["errors"].append(f"Patient ingestion: {e}")

            result["patient_count"] = len(patient_ids)
            logger.info(
                f"[{event.nct_id}] PostgreSQL: trial={trial_id}, "
                f"patients={len(patient_ids)}"
            )

            # ═══════════════════════════════════════════
            # STEP 5: Generate embeddings + Load Qdrant + Neo4j
            # Run embedding generation and graph loading concurrently
            # ═══════════════════════════════════════════
            logger.info(f"[{event.nct_id}] Step 5/5: Embeddings + Graph...")

            embedding_task = self._generate_and_load_embeddings(
                trial_data, trial_id, event.nct_id,
                patient_ids, result
            )
            graph_task = self._load_knowledge_graph(
                trial_data, trial_id, patient_ids, result
            )

            await asyncio.gather(embedding_task, graph_task)

            logger.info(
                f"[{event.nct_id}] Pipeline complete: "
                f"chunks={result['chunk_count']}, "
                f"errors={len(result['errors'])}"
            )
            self._publish_trial_ingested(trial_data)

        finally:
            # ── Cleanup temp files ──
            if temp_pdf_path:
                temp_dir = os.path.dirname(temp_pdf_path)
                shutil.rmtree(temp_dir, ignore_errors=True)

        return result
    def _publish_trial_ingested(self, trial_data: dict):
        """Publish event so dynamic collections can be updated."""
        try:
            event = TrialIngestedEvent(
                trial_id=str(trial_data.get("trial_id", "")),
                nct_id=trial_data.get("nct_id", ""),
                therapeutic_area=trial_data.get("therapeutic_area"),
                phase=trial_data.get("phase"),
                study_type=trial_data.get("study_type"),
                regions=trial_data.get("regions", []),
                countries=trial_data.get("countries", []),
                patient_count=trial_data.get("patient_count", 0)
            )

            self.producer.produce(
                topic="trial-ingested",
                key=event.nct_id,
                value=event.model_dump_json().encode("utf-8"),
            )
            self.producer.flush(timeout=5)
            logger.info(f"Published trial-ingested event for {event.nct_id}")

        except Exception as e:
            # Non-fatal — ingestion succeeded, collection refresh can happen later
            logger.warning(f"Failed to publish trial-ingested event: {e}")

    async def _generate_and_load_embeddings(
        self,
        trial_data: dict,
        trial_id: str,
        nct_id: str,
        patient_ids: list[tuple[str, dict]],
        result: dict
    ):
        """Generate embeddings and load into Qdrant."""
        all_chunks = []

        try:
            # Trial-level embeddings
            trial_chunks = await self.embedder.generate_trial_chunks(
                trial_data, trial_id, nct_id
            )
            all_chunks.extend(trial_chunks)

            # Patient-level embeddings
            for pid, pdata in patient_ids:
                try:
                    patient_chunks = await self.embedder.generate_patient_chunks(
                        pdata, pid, trial_id, nct_id
                    )
                    all_chunks.extend(patient_chunks)
                except Exception as e:
                    logger.warning(f"Embedding failed for patient {pid}: {e}")
                    result["errors"].append(f"Embedding error: {e}")

            # Load into Qdrant
            if all_chunks:
                qdrant_points = [
                    {
                        "chunk_id": chunk.chunk_id,
                        "text": chunk.text,
                        "embedding": chunk.embedding,
                        "chunk_type": chunk.chunk_type,
                        "metadata": chunk.metadata
                    }
                    for chunk in all_chunks
                ]
                self.qdrant.ingest_chunks(qdrant_points)

            result["chunk_count"] = len(all_chunks)

        except Exception as e:
            logger.error(f"Embedding pipeline error: {e}")
            result["errors"].append(f"Embedding pipeline: {e}")

    async def _load_knowledge_graph(
        self,
        trial_data: dict,
        trial_id: str,
        patient_ids: list[tuple[str, dict]],
        result: dict
    ):
        """Load data into Neo4j knowledge graph."""
        try:
            await self.graph.ingest_trial(trial_data, trial_id)
            node_count = 1  # trial node
            rel_count = 0

            for pid, pdata in patient_ids:
                try:
                    await self.graph.ingest_patient(pdata, pid, trial_id)
                    node_count += 1
                    # Estimate relationships
                    rel_count += 1  # ENROLLED_IN
                    rel_count += len(pdata.get('conditions', []))
                    rel_count += len(pdata.get('medications', []))
                    rel_count += len(pdata.get('adverse_events', []))
                except Exception as e:
                    logger.warning(f"Graph load failed for patient {pid}: {e}")
                    result["errors"].append(f"Graph error: {e}")

            result["graph_nodes"] = node_count
            result["graph_relationships"] = rel_count

        except Exception as e:
            logger.error(f"Knowledge graph pipeline error: {e}")
            result["errors"].append(f"Graph pipeline: {e}")
    async def notify_dynamic_collections(self, trial_id: str, db_pool, fga_client):
        """
        After ingesting a new trial, check if it matches any dynamic collection filters.
        If so, auto-link and auto-grant.
        """
        try:
            from auth.asset_service import AssetService
            service = AssetService(db_pool, fga_client)
            results = await service.refresh_dynamic_collections()
            if results:
                logger.info(f"Dynamic collection refresh after trial {trial_id}: {results}")
        except Exception as e:
            # Non-fatal — collection refresh failure shouldn't block ingestion
            logger.warning(f"Dynamic collection refresh failed: {e}")