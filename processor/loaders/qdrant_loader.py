from __future__ import annotations
"""
Loads embedding chunks into Qdrant vector database.
Every point includes trial_id and patient_id in the payload
for authorization-aware filtered search.
"""
import logging
from qdrant_client import QdrantClient, models
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchAny, MatchValue,
    PayloadSchemaType, TextIndexParams, TokenizerType
)
from shared.config import QdrantConfig

logger = logging.getLogger(__name__)


class QdrantLoader:
    """
    Manages the Qdrant collection for clinical trial embeddings.
    Optimized for authorization-filtered similarity search.
    """

    def __init__(self, config: QdrantConfig):
        self.config = config
        self.client = QdrantClient(
            host=config.host,
            port=config.port,
            grpc_port=config.grpc_port,
            prefer_grpc=False         # Debugging: switch to HTTP to rule out gRPC issues
        )

    async def initialize(self):
        """Create collection with optimized configuration."""
        logger.info("DEBUG_MARKER: QdrantLoader version with validation v2")
        collections = self.client.get_collections().collections
        existing = [c.name for c in collections]

        if self.config.collection_name not in existing:
            self.client.create_collection(
                collection_name=self.config.collection_name,
                vectors_config=VectorParams(
                    size=3072,                          # text-embedding-3-large
                    distance=Distance.COSINE,
                    on_disk=True                        # Memory-efficient for large datasets
                ),
                # HNSW index optimized for filtered search
                hnsw_config=models.HnswConfigDiff(
                    m=16,
                    ef_construct=128,
                    full_scan_threshold=10000,
                    on_disk=False                       # Keep HNSW graph in RAM
                ),
                # Quantization for memory efficiency
                quantization_config=models.ScalarQuantization(
                    scalar=models.ScalarQuantizationConfig(
                        type=models.ScalarType.INT8,
                        quantile=0.99,
                        always_ram=True
                    )
                ),
                # Optimizers
                optimizers_config=models.OptimizersConfigDiff(
                    indexing_threshold=20000
                ),
                # Sharding for scalability
                shard_number=2
            )
            logger.info(
                f"Created Qdrant collection: {self.config.collection_name}"
            )

            # Create payload indexes for fast filtering
            # These are critical for authorization pre-filtering
            self._create_payload_indexes()
        else:
            logger.info(
                f"Collection {self.config.collection_name} already exists"
            )

    def _create_payload_indexes(self):
        """
        Create payload indexes for fields used in authorization filtering.
        Without these, filtered search would be slow on large collections.
        """
        index_fields = {
            "trial_id": PayloadSchemaType.KEYWORD,
            "patient_id": PayloadSchemaType.KEYWORD,
            "nct_id": PayloadSchemaType.KEYWORD,
            "chunk_type": PayloadSchemaType.KEYWORD,
            "therapeutic_area": PayloadSchemaType.KEYWORD,
            "phase": PayloadSchemaType.KEYWORD,
        }

        for field_name, schema_type in index_fields.items():
            self.client.create_payload_index(
                collection_name=self.config.collection_name,
                field_name=field_name,
                field_schema=schema_type
            )
            logger.debug(f"Created payload index: {field_name}")

        # Full-text index for content search
        self.client.create_payload_index(
            collection_name=self.config.collection_name,
            field_name="content",
            field_schema=TextIndexParams(
                type="text",
                tokenizer=TokenizerType.WORD,
                min_token_len=3,
                max_token_len=20,
                lowercase=True
            )
        )

    def ingest_chunks(self, chunks: list[dict]):
        """
        Batch upsert embedding chunks into Qdrant.

        Each chunk dict must have:
        - chunk_id: str
        - text: str
        - embedding: list[float]
        - chunk_type: str
        - metadata: dict (must include trial_id, optionally patient_id)
        """
        if not chunks:
            return

        points = []
        expected_dim = 3072  # Hardcoded in collection config above
        
        for chunk in chunks:
            embedding = chunk.get("embedding")
            
            # ── Step 1: Validate vector exists and has correct dimension ──
            if not embedding:
                logger.error(
                    f"CRITICAL: Found chunk with None/empty embedding. "
                    f"Chunk ID: {chunk['chunk_id']}, Type: {chunk['chunk_type']}. "
                    f"Skipping point."
                )
                continue
                
            actual_dim = len(embedding)
            if actual_dim != expected_dim:
                logger.error(
                    f"CRITICAL: Dimension mismatch in Qdrant loader. "
                    f"Expected {expected_dim}, got {actual_dim}. "
                    f"Chunk ID: {chunk['chunk_id']}, Type: {chunk['chunk_type']}. "
                    f"Skipping point."
                )
                continue

            # ── Step 2: Build the payload ──
            payload = {
                "content": chunk["text"],
                "chunk_type": chunk["chunk_type"],
                "trial_id": chunk["metadata"].get("trial_id", ""),
                "patient_id": chunk["metadata"].get("patient_id", ""),
                "nct_id": chunk["metadata"].get("nct_id", ""),
                # Copy all metadata fields to top level for indexing
                **{
                    k: v for k, v in chunk["metadata"].items()
                    if k not in ("trial_id", "patient_id", "nct_id")
                    and isinstance(v, (str, int, float, bool))
                }
            }

            points.append(PointStruct(
                id=chunk["chunk_id"],
                vector=embedding,
                payload=payload
            ))

        # Batch upsert (Qdrant handles dedup by point ID)
        if points:
            logger.info(f"DEBUG: Calling upsert with {len(points)} points. Sample point ID: {points[0].id}")
            self.client.upsert(
                collection_name=self.config.collection_name,
                points=points,
                wait=True                      # Wait for indexing to complete
            )
        else:
            logger.warning("DEBUG: ingest_chunks called but ALL points were filtered out.")

        logger.info(
            f"Upserted {len(points)} points to Qdrant "
            f"(collection: {self.config.collection_name})"
        )

    def search(
        self,
        query_vector: list[float],
        authorized_trial_ids: list[str],
        authorized_patient_ids: list[str] | None = None,
        chunk_types: list[str] | None = None,
        top_k: int = 20
    ) -> list[dict]:
        """
        Authorization-aware similarity search.
        ALWAYS filters by authorized trial IDs (security boundary).
        """
        # Build filter conditions
        must_conditions = [
            FieldCondition(
                key="trial_id",
                match=MatchAny(any=authorized_trial_ids)
            )
        ]

        if authorized_patient_ids:
            # Allow trial-level docs (no patient_id) OR authorized patients
            must_conditions.append(
                models.Filter(
                    should=[
                        FieldCondition(
                            key="patient_id",
                            match=MatchValue(value="")
                        ),
                        FieldCondition(
                            key="patient_id",
                            match=MatchAny(any=authorized_patient_ids)
                        )
                    ]
                )
            )

        if chunk_types:
            must_conditions.append(
                FieldCondition(
                    key="chunk_type",
                    match=MatchAny(any=chunk_types)
                )
            )

        results = self.client.search(
            collection_name=self.config.collection_name,
            query_vector=query_vector,
            query_filter=Filter(must=must_conditions),
            limit=top_k,
            with_payload=True,
            score_threshold=0.5           # Minimum similarity threshold
        )

        return [
            {
                "chunk_id": hit.id,
                "score": hit.score,
                "content": hit.payload.get("content", ""),
                "chunk_type": hit.payload.get("chunk_type", ""),
                "trial_id": hit.payload.get("trial_id", ""),
                "patient_id": hit.payload.get("patient_id", ""),
                "nct_id": hit.payload.get("nct_id", ""),
                "metadata": hit.payload
            }
            for hit in results
        ]