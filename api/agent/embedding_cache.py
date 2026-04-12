"""
Phase 6: LRU cache for OpenAI text embeddings used in Qdrant searches.

Avoids redundant embedding API calls for identical or near-identical queries.
Cache is in-memory with a 1-hour TTL and max 512 entries.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any

import openai

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "text-embedding-3-small"
_VECTOR_DIM      = 1536
_MAX_ENTRIES     = 512
_TTL_SECONDS     = 3_600   # 1 hour


class EmbeddingCache:
    """
    Thread-safe async LRU cache for text embeddings.

    Key: SHA-256 of the normalized query text
    Value: (embedding_vector, expiry_timestamp)
    """

    def __init__(
        self,
        max_entries: int = _MAX_ENTRIES,
        ttl_seconds:  float = _TTL_SECONDS,
        model: str = _EMBEDDING_MODEL,
    ):
        self._max    = max_entries
        self._ttl    = ttl_seconds
        self._model  = model
        self._store: OrderedDict[str, tuple[list[float], float]] = OrderedDict()
        self._lock   = asyncio.Lock()
        self._hits   = 0
        self._misses = 0

    def _cache_key(self, text: str) -> str:
        normalized = " ".join(text.lower().split())
        return hashlib.sha256(normalized.encode()).hexdigest()

    async def get_or_create(self, text: str) -> list[float]:
        """
        Returns cached embedding or calls OpenAI and caches the result.
        """
        key = self._cache_key(text)

        async with self._lock:
            if key in self._store:
                vector, expiry = self._store[key]
                if time.monotonic() < expiry:
                    # Cache hit — move to end (LRU)
                    self._store.move_to_end(key)
                    self._hits += 1
                    logger.debug("Embedding cache HIT (total hits=%d)", self._hits)
                    return vector
                else:
                    # Expired — remove
                    del self._store[key]

        # Cache miss — call OpenAI outside the lock to avoid blocking
        self._misses += 1
        logger.debug("Embedding cache MISS (total misses=%d)", self._misses)

        client = openai.AsyncOpenAI()
        response = await client.embeddings.create(
            model=self._model,
            input=text,
        )
        vector = response.data[0].embedding

        async with self._lock:
            # Evict LRU entries if at capacity
            while len(self._store) >= self._max:
                self._store.popitem(last=False)

            self._store[key] = (vector, time.monotonic() + self._ttl)

        return vector

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "size":       len(self._store),
            "max":        self._max,
            "hits":       self._hits,
            "misses":     self._misses,
            "hit_rate":   round(self._hits / max(self._hits + self._misses, 1), 3),
            "ttl_seconds": self._ttl,
        }

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()
            self._hits   = 0
            self._misses = 0


# Singleton — shared across all agent queries
embedding_cache = EmbeddingCache()