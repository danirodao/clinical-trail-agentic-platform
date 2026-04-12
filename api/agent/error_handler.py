"""
Phase 6: Centralized error handling for the agent system.
Covers all failure modes defined in the hardening matrix.
"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from functools import wraps
from typing import Any, Callable, TypeVar

import httpx
import openai
from tenacity import (
    AsyncRetrying,
    RetryError,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------

class AgentErrorCode(str, Enum):
    MCP_UNAVAILABLE        = "MCP_UNAVAILABLE"
    MCP_AUTH_FAILED        = "MCP_AUTH_FAILED"
    TOOL_EMPTY_RESULT      = "TOOL_EMPTY_RESULT"
    TOOL_EXECUTION_FAILED  = "TOOL_EXECUTION_FAILED"
    ACCESS_DENIED          = "ACCESS_DENIED"
    RATE_LIMITED           = "RATE_LIMITED"
    INPUT_INVALID          = "INPUT_INVALID"
    LLM_RATE_LIMITED       = "LLM_RATE_LIMITED"
    LLM_TOKEN_LIMIT        = "LLM_TOKEN_LIMIT"
    DB_POOL_EXHAUSTED      = "DB_POOL_EXHAUSTED"
    UNEXPECTED             = "UNEXPECTED"


class AgentError(Exception):
    """Structured agent error with user-facing message."""

    def __init__(
        self,
        code: AgentErrorCode,
        message: str,                   # User-facing
        detail: str | None = None,      # Internal/log only
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.retryable = retryable

    def to_dict(self) -> dict:
        return {
            "error": True,
            "code": self.code.value,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Retry decorator for MCP calls
# ---------------------------------------------------------------------------

def with_mcp_retry(max_attempts: int = 3, min_wait: float = 0.5, max_wait: float = 4.0):
    """
    Decorator that retries MCP tool calls on transient network failures.
    Raises AgentError(MCP_UNAVAILABLE) after all attempts exhausted.
    """
    def decorator(fn: F) -> F:
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, ConnectionError)),
                    stop=stop_after_attempt(max_attempts),
                    wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
                    before_sleep=before_sleep_log(logger, logging.WARNING),
                    reraise=False,
                ):
                    with attempt:
                        return await fn(*args, **kwargs)
            except RetryError as exc:
                raise AgentError(
                    code=AgentErrorCode.MCP_UNAVAILABLE,
                    message="Data services are temporarily unavailable. Please try again in a moment.",
                    detail=str(exc),
                    retryable=True,
                ) from exc
        return wrapper  # type: ignore[return-value]
    return decorator


# ---------------------------------------------------------------------------
# Retry decorator for OpenAI calls
# ---------------------------------------------------------------------------

def with_llm_retry(max_attempts: int = 3):
    """
    Retries OpenAI calls on rate limit (429) and transient server errors (5xx).
    Raises AgentError(LLM_RATE_LIMITED) after exhaustion.
    """
    def decorator(fn: F) -> F:
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type((
                        openai.RateLimitError,
                        openai.APIStatusError,
                        openai.APIConnectionError,
                    )),
                    stop=stop_after_attempt(max_attempts),
                    wait=wait_exponential(multiplier=2, min=1, max=30),
                    before_sleep=before_sleep_log(logger, logging.WARNING),
                    reraise=False,
                ):
                    with attempt:
                        return await fn(*args, **kwargs)
            except RetryError as exc:
                cause = exc.last_attempt.exception()
                if isinstance(cause, openai.RateLimitError):
                    raise AgentError(
                        code=AgentErrorCode.LLM_RATE_LIMITED,
                        message="The AI service is currently busy. Please wait a moment and try again.",
                        detail=str(cause),
                        retryable=True,
                    ) from exc
                raise AgentError(
                    code=AgentErrorCode.UNEXPECTED,
                    message="An unexpected error occurred while generating your response.",
                    detail=str(cause),
                    retryable=False,
                ) from exc
        return wrapper  # type: ignore[return-value]
    return decorator


# ---------------------------------------------------------------------------
# asyncpg pool exhaustion guard
# ---------------------------------------------------------------------------

class PoolExhaustionGuard:
    """
    Context manager that wraps asyncpg pool.acquire() with a timeout.
    Raises AgentError(DB_POOL_EXHAUSTED) → caller returns 503.
    """

    def __init__(self, pool, timeout: float = 5.0):
        self._pool = pool
        self._timeout = timeout
        self._conn = None

    async def __aenter__(self):
        try:
            self._conn = await asyncio.wait_for(
                self._pool.acquire(),
                timeout=self._timeout,
            )
            return self._conn
        except asyncio.TimeoutError as exc:
            raise AgentError(
                code=AgentErrorCode.DB_POOL_EXHAUSTED,
                message="The system is under high load. Please try again shortly.",
                detail="asyncpg pool acquire timeout",
                retryable=True,
            ) from exc

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._conn is not None:
            await self._pool.release(self._conn)


# ---------------------------------------------------------------------------
# Token limit truncation
# ---------------------------------------------------------------------------

MAX_TOOL_RESULT_CHARS = 12_000   # ~3k tokens — keeps total context safe


def truncate_tool_result(result: str, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    """
    Truncates oversized tool results before they enter the LLM context.
    Appends a notice so the model knows data was trimmed.
    """
    if len(result) <= max_chars:
        return result

    truncated = result[:max_chars]
    notice = (
        "\n\n[NOTICE: Result truncated to fit context window. "
        "Consider narrowing your query filters for complete data.]"
    )
    logger.warning("Tool result truncated: %d → %d chars", len(result), max_chars)
    return truncated + notice


# ---------------------------------------------------------------------------
# Empty result handler
# ---------------------------------------------------------------------------

def explain_empty_result(tool_name: str, filters_applied: dict) -> str:
    """
    Returns a human-readable explanation when a tool returns no data.
    Included in the tool result so the LLM can self-correct or explain.
    """
    filter_summary = ", ".join(
        f"{k}={v}" for k, v in filters_applied.items() if v is not None
    ) or "none"

    return (
        f"[EMPTY RESULT] Tool '{tool_name}' returned no data for filters: {filter_summary}. "
        "This may mean: (1) no records match the criteria within authorized trials, "
        "(2) the cohort filter excludes all patients, or "
        "(3) the trial has not yet enrolled patients matching these criteria. "
        "Do not fabricate data. Inform the researcher of the empty result."
    )


# ---------------------------------------------------------------------------
# Circuit breaker (simple in-memory, per-service)
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """
    Simple circuit breaker: opens after `failure_threshold` failures
    within `window_seconds`. Resets after `recovery_seconds`.

    States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing)
    """

    CLOSED    = "CLOSED"
    OPEN      = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        window_seconds: float = 60.0,
        recovery_seconds: float = 30.0,
    ):
        self.name = name
        self._threshold = failure_threshold
        self._window = window_seconds
        self._recovery = recovery_seconds

        self._state = self.CLOSED
        self._failures: list[float] = []
        self._opened_at: float | None = None

    def _prune_failures(self) -> None:
        cutoff = time.monotonic() - self._window
        self._failures = [t for t in self._failures if t > cutoff]

    def record_success(self) -> None:
        self._failures.clear()
        self._state = self.CLOSED
        self._opened_at = None

    def record_failure(self) -> None:
        self._prune_failures()
        self._failures.append(time.monotonic())
        if len(self._failures) >= self._threshold:
            self._state = self.OPEN
            self._opened_at = time.monotonic()
            logger.error("Circuit breaker OPEN for service: %s", self.name)

    def allow_request(self) -> bool:
        if self._state == self.CLOSED:
            return True
        if self._state == self.OPEN:
            if self._opened_at and (time.monotonic() - self._opened_at) > self._recovery:
                self._state = self.HALF_OPEN
                logger.info("Circuit breaker HALF_OPEN for service: %s", self.name)
                return True
            return False
        # HALF_OPEN: allow one probe
        return True

    def check(self) -> None:
        """Call before a request. Raises AgentError if circuit is open."""
        if not self.allow_request():
            raise AgentError(
                code=AgentErrorCode.MCP_UNAVAILABLE,
                message="Data services are temporarily unavailable due to repeated failures. Please try again later.",
                detail=f"Circuit breaker OPEN for {self.name}",
                retryable=True,
            )


# Singleton breakers — one per external service
mcp_circuit_breaker   = CircuitBreaker("mcp-server", failure_threshold=5, recovery_seconds=30)
openai_circuit_breaker = CircuitBreaker("openai",    failure_threshold=10, recovery_seconds=60)