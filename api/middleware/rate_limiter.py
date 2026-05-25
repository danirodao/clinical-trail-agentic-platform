"""
Phase 6: Sliding window rate limiter using Redis (or in-memory fallback).

Limits:
  - Per user:         20 queries / 60 seconds
  - Per organization: 100 queries / 60 seconds

Applied only to POST /api/v1/research/query* endpoints.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ..agent.error_handler import AgentErrorCode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

USER_LIMIT      = 20    # requests
USER_WINDOW     = 60    # seconds
ORG_LIMIT       = 100   # requests
ORG_WINDOW      = 60    # seconds

# Endpoints this middleware applies to
RATE_LIMITED_PREFIXES = ["/api/v1/research/query"]


# ---------------------------------------------------------------------------
# In-memory sliding window store (replace with Redis in production)
# ---------------------------------------------------------------------------

class SlidingWindowCounter:
    """
    Thread-safe sliding window counter for rate limiting.
    Stores timestamps of recent requests per key.
    """

    def __init__(self):
        # key → deque of request timestamps
        self._windows: dict[str, deque] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def is_allowed(self, key: str, limit: int, window_seconds: float) -> tuple[bool, int]:
        """
        Returns (allowed: bool, remaining: int).
        Mutates internal state if allowed.
        """
        async with self._lock:
            now = time.monotonic()
            cutoff = now - window_seconds
            dq = self._windows[key]

            # Prune expired entries
            while dq and dq[0] <= cutoff:
                dq.popleft()

            count = len(dq)
            if count >= limit:
                return False, 0

            dq.append(now)
            return True, limit - count - 1

    async def cleanup(self, max_keys: int = 10_000) -> None:
        """Prune keys that have no recent requests. Call periodically."""
        async with self._lock:
            now = time.monotonic()
            dead_keys = [
                k for k, dq in self._windows.items()
                if not dq or dq[-1] < now - max(USER_WINDOW, ORG_WINDOW)
            ]
            for k in dead_keys:
                del self._windows[k]
            if len(self._windows) > max_keys:
                # Evict oldest keys
                sorted_keys = sorted(
                    self._windows.items(),
                    key=lambda item: item[1][-1] if item[1] else 0
                )
                for k, _ in sorted_keys[:len(self._windows) - max_keys]:
                    del self._windows[k]


_user_counter = SlidingWindowCounter()
_org_counter  = SlidingWindowCounter()


# ---------------------------------------------------------------------------
# Redis-backed sliding window (preferred in production)
# ---------------------------------------------------------------------------

class _RedisSlidingWindowCounter:
    """
    Redis sorted-set sliding window — fully atomic via a Lua script.
    Each member is a unique UUID so simultaneous requests never collide.
    Falls back to the in-memory counter on any Redis error so the API
    stays up even during a Redis restart.
    """

    # One Lua script handles check-and-record atomically.
    # KEYS[1] = rate-limit bucket key
    # ARGV[1] = now_ms, ARGV[2] = window_ms, ARGV[3] = limit, ARGV[4] = unique member
    _LUA = b"""
local key     = KEYS[1]
local now_ms  = tonumber(ARGV[1])
local win_ms  = tonumber(ARGV[2])
local lim     = tonumber(ARGV[3])
local uid     = ARGV[4]
local cutoff  = now_ms - win_ms
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = tonumber(redis.call('ZCARD', key))
if count >= lim then return {0, 0} end
redis.call('ZADD', key, now_ms, uid)
redis.call('PEXPIRE', key, win_ms + 1000)
return {1, lim - count - 1}
"""

    def __init__(self, redis_client, fallback: SlidingWindowCounter) -> None:
        self._r = redis_client
        self._fallback = fallback
        self._script = None  # registered lazily on first call

    async def is_allowed(
        self, key: str, limit: int, window_seconds: float
    ) -> tuple[bool, int]:
        try:
            if self._script is None:
                self._script = self._r.register_script(self._LUA)
            now_ms = int(time.time() * 1000)
            win_ms = int(window_seconds * 1000)
            result = await self._script(
                keys=[f"rl:{key}"],
                args=[now_ms, win_ms, limit, uuid.uuid4().hex],
            )
            return bool(result[0]), int(result[1])
        except Exception as exc:
            logger.warning(
                "redis_rate_limiter_fallback",
                extra={"error": str(exc)},
            )
            return await self._fallback.is_allowed(key, limit, window_seconds)

    async def cleanup(self, max_keys: int = 10_000) -> None:
        pass  # Redis TTL-based expiry handles cleanup automatically


def configure_rate_limiter(redis_client) -> None:
    """
    Switch rate-limiting to a Redis backend.  Call once from main.py lifespan
    after the Redis connection is verified.  The per-counter fallback to the
    in-memory implementation means the API degrades gracefully if Redis
    becomes temporarily unavailable at request time.
    """
    global _user_counter, _org_counter
    _user_counter = _RedisSlidingWindowCounter(redis_client, SlidingWindowCounter())
    _org_counter  = _RedisSlidingWindowCounter(redis_client, SlidingWindowCounter())
    logger.info("rate_limiter_redis_backend_active")


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Applies per-user and per-organization rate limits to research query endpoints.
    Reads user_id and organization_id from the request state (set by JWT middleware).
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Only apply to query endpoints
        if not any(request.url.path.startswith(p) for p in RATE_LIMITED_PREFIXES):
            return await call_next(request)

        # Extract identity from request state (populated by JWT middleware)
        user_id = getattr(request.state, "username", None)
        org_id  = getattr(request.state, "organization_id", None)

        if not user_id or not org_id:
            # No identity — let auth middleware handle the 401
            return await call_next(request)

        # Check per-user limit
        user_allowed, user_remaining = await _user_counter.is_allowed(
            key=f"user:{user_id}",
            limit=USER_LIMIT,
            window_seconds=USER_WINDOW,
        )
        if not user_allowed:
            logger.warning("Rate limit exceeded for user: %s", user_id)
            return JSONResponse(
                status_code=429,
                content={
                    "error": True,
                    "code": AgentErrorCode.RATE_LIMITED.value,
                    "message": (
                        f"You have exceeded the limit of {USER_LIMIT} queries "
                        f"per {USER_WINDOW} seconds. Please wait before trying again."
                    ),
                },
                headers={
                    "Retry-After": str(USER_WINDOW),
                    "X-RateLimit-Limit-User": str(USER_LIMIT),
                    "X-RateLimit-Remaining-User": "0",
                },
            )

        # Check per-org limit
        org_allowed, org_remaining = await _org_counter.is_allowed(
            key=f"org:{org_id}",
            limit=ORG_LIMIT,
            window_seconds=ORG_WINDOW,
        )
        if not org_allowed:
            logger.warning("Rate limit exceeded for org: %s", org_id)
            return JSONResponse(
                status_code=429,
                content={
                    "error": True,
                    "code": AgentErrorCode.RATE_LIMITED.value,
                    "message": (
                        f"Your organization has exceeded the limit of {ORG_LIMIT} queries "
                        f"per {ORG_WINDOW} seconds. Please try again shortly."
                    ),
                },
                headers={
                    "Retry-After": str(ORG_WINDOW),
                    "X-RateLimit-Limit-Org": str(ORG_LIMIT),
                    "X-RateLimit-Remaining-Org": "0",
                },
            )

        # Add rate limit headers to response
        response = await call_next(request)
        response.headers["X-RateLimit-Limit-User"]      = str(USER_LIMIT)
        response.headers["X-RateLimit-Remaining-User"]  = str(user_remaining)
        response.headers["X-RateLimit-Limit-Org"]       = str(ORG_LIMIT)
        response.headers["X-RateLimit-Remaining-Org"]   = str(org_remaining)
        return response