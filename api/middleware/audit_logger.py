"""
Phase 6: Structured audit trail for all research query activity.
Non-repudiation logging: every query + outcome recorded permanently.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# Use structlog if available, fall back to stdlib
try:
    import structlog
    _audit_log = structlog.get_logger("audit")
except ImportError:
    _audit_log = logging.getLogger("audit")

AUDITED_PATHS = ["/api/v1/research/query"]
MAX_QUERY_LOG_CHARS = 1000


def _preview(value: str, max_chars: int) -> str:
    """Return a safe, bounded preview string for logs."""
    text = value or ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "... [truncated]"


class AuditLogMiddleware(BaseHTTPMiddleware):
    """
    Records an audit event for every research query request.

    Captures:
      - Who: user_id, organization_id
      - What: query text, trial_ids scoped
      - When: ISO timestamp
      - Outcome: status_code, duration_ms
      - How: request_id (correlates with Phoenix traces)

    Stored in structured JSON to stdout — forward to your SIEM or
    append to a dedicated PostgreSQL audit_log table in production.
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not any(request.url.path.startswith(p) for p in AUDITED_PATHS):
            return await call_next(request)

        request_id = str(uuid.uuid4())
        start_time = time.monotonic()

        # Read body (needed for audit — body can only be read once)
        body_bytes = await request.body()
        try:
            body = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            body = {}

        # Inject body back so downstream can re-read it
        async def receive():
            return {"type": "http.request", "body": body_bytes}

        request = Request(request.scope, receive)

        # Extract identity
        user_id  = getattr(request.state, "username",       "unknown")
        org_id   = getattr(request.state, "organization_id", "unknown")
        role     = getattr(request.state, "role",            "unknown")

        _audit_log.info(
            "incoming_request",
            request_id=request_id,
            path=request.url.path,
            method=request.method,
            user_id=user_id,
            organization_id=org_id,
            role=role,
            session_id=body.get("session_id"),
            trial_ids=body.get("trial_ids", []),
            query_preview=_preview(str(body.get("query", "")), MAX_QUERY_LOG_CHARS),
        )

        response = await call_next(request)

        duration_ms = int((time.monotonic() - start_time) * 1000)

        audit_event = {
            "event":           "research_query",
            "request_id":      request_id,
            "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "user_id":         user_id,
            "organization_id": org_id,
            "role":            role,
            "path":            request.url.path,
            "method":          request.method,
            "query":           _preview(str(body.get("query", "")), MAX_QUERY_LOG_CHARS),
            "trial_ids_scoped": body.get("trial_ids", []),
            "session_id":      body.get("session_id"),
            "status_code":     response.status_code,
            "duration_ms":     duration_ms,
            "ip_address":      request.client.host if request.client else "unknown",
            "user_agent":      request.headers.get("user-agent", "unknown")[:200],
        }

        # Emit as structured JSON
        try:
            _audit_log.info("research_query_audit", **audit_event)
        except TypeError:
            # Fallback for stdlib logger
            logging.getLogger("audit").info(json.dumps(audit_event))

        # Attach request_id to response for correlation
        response.headers["X-Request-ID"] = request_id
        return response