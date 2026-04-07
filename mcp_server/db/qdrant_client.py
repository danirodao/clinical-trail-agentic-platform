"""
Qdrant vector search client with OpenAI embedding generation.

Usage:
    from db.qdrant_client import search_vectors

    results = await search_vectors(
        query_text="melanoma immunotherapy adverse events",
        trial_ids=["uuid1", "uuid2"],
        limit=10,
    )
"""

import os
import logging
from typing import Any

from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

logger = logging.getLogger(__name__)

COLLECTION_NAME = "clinical_trial_embeddings"

# Default model — can be overridden via environment variable.
# Must match whatever the processor used when indexing.
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "3072"))

_client: AsyncQdrantClient | None = None
_openai: AsyncOpenAI | None = None


async def init_client() -> None:
    """Initialize Qdrant and OpenAI clients."""
    global _client, _openai

    host = os.environ.get("QDRANT_HOST", "qdrant")
    port = int(os.environ.get("QDRANT_PORT", "6333"))

    _client = AsyncQdrantClient(host=host, port=port, timeout=30)
    _openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    try:
        collections = await _client.get_collections()
        names = [c.name for c in collections.collections]
        if COLLECTION_NAME in names:
            info = await _client.get_collection(COLLECTION_NAME)
            # Detect actual dimension from the collection config
            actual_dim = _get_vector_dimension(info)
            logger.info(
                f"Qdrant connected | collection='{COLLECTION_NAME}' "
                f"points={info.points_count} dim={actual_dim} "
                f"embedding_model={EMBEDDING_MODEL}"
            )
            if actual_dim and actual_dim != EMBEDDING_DIM:
                logger.error(
                    f"DIMENSION MISMATCH: collection has dim={actual_dim} "
                    f"but EMBEDDING_DIM={EMBEDDING_DIM}. "
                    f"Set EMBEDDING_MODEL and EMBEDDING_DIM env vars to match "
                    f"what the processor used during indexing."
                )
        else:
            logger.warning(
                f"Qdrant connected but collection '{COLLECTION_NAME}' not found. "
                f"Available: {names}."
            )
    except Exception as e:
        logger.warning(f"Qdrant health check failed (non-fatal): {e}")


def _get_vector_dimension(info) -> int | None:
    """Extract vector dimension from collection info, handling different client versions."""
    try:
        vectors_config = info.config.params.vectors
        # Could be a dict (named vectors) or a VectorParams object
        if hasattr(vectors_config, "size"):
            return vectors_config.size
        if isinstance(vectors_config, dict):
            # Named vectors — get the first one
            for v in vectors_config.values():
                if hasattr(v, "size"):
                    return v.size
        return None
    except Exception:
        return None


async def close_client() -> None:
    """Close the Qdrant client."""
    global _client, _openai
    if _client:
        await _client.close()
        _client = None
    _openai = None
    logger.info("Qdrant client closed")


async def embed_text(text: str) -> list[float]:
    """Generate embedding vector for text using OpenAI."""
    if _openai is None:
        raise RuntimeError("OpenAI client not initialized")
    response = await _openai.embeddings.create(
        input=text,
        model=EMBEDDING_MODEL,
    )
    return response.data[0].embedding


async def search_vectors(
    query_text: str,
    trial_ids: list[str],
    limit: int = 10,
    section: str | None = None,
    score_threshold: float = 0.3,
) -> list[dict[str, Any]]:
    """
    Semantic search over clinical trial document chunks.

    Args:
        query_text: Natural language search query.
        trial_ids: List of authorized trial_id UUIDs to search within.
        limit: Maximum results to return.
        section: Optional section filter (e.g., 'adverse_events', 'demographics').
        score_threshold: Minimum similarity score.

    Returns:
        List of dicts with keys: id, score, chunk_text, trial_id, nct_id, section, source_pdf.
    """
    if _client is None:
        logger.error("Qdrant client not initialized")
        return []

    if not trial_ids:
        return []

    try:
        embedding = await embed_text(query_text)

        must_conditions: list[FieldCondition] = [
            FieldCondition(key="trial_id", match=MatchAny(any=trial_ids))
        ]
        if section:
            must_conditions.append(
                FieldCondition(key="section", match=MatchValue(value=section))
            )

        results = await _client.search(
            collection_name=COLLECTION_NAME,
            query_vector=embedding,
            query_filter=Filter(must=must_conditions),
            limit=limit,
            with_payload=True,
            score_threshold=score_threshold,
        )

        return [
            {
                "id": str(point.id),
                "score": round(point.score, 4),
                "chunk_text": point.payload.get("chunk_text", ""),
                "trial_id": point.payload.get("trial_id", ""),
                "nct_id": point.payload.get("nct_id", ""),
                "section": point.payload.get("section", ""),
                "source_pdf": point.payload.get("source_pdf", ""),
                "chunk_index": point.payload.get("chunk_index"),
                "therapeutic_area": point.payload.get("therapeutic_area", ""),
                "phase": point.payload.get("phase", ""),
            }
            for point in results
        ]

    except Exception as e:
        logger.error(f"Qdrant search error: {e}", exc_info=True)
        return []


async def collection_exists() -> bool:
    """Check if the clinical_trials collection exists."""
    if _client is None:
        return False
    try:
        collections = await _client.get_collections()
        return any(c.name == COLLECTION_NAME for c in collections.collections)
    except Exception:
        return False