"""
Prometheus /metrics endpoint for the FastAPI application.

Registers:
  - All agent metrics defined in agent/observability.py
  - HTTP request metrics (method, path, status, duration)
  - Active connections gauge

Mount this in main.py with:
    from api.metrics import metrics_router, instrument_app
    app.include_router(metrics_router)
    instrument_app(app)

IMPORTANT — Why NOT BaseHTTPMiddleware:
    BaseHTTPMiddleware buffers the entire response body before forwarding it,
    which causes a deadlock on SSE/streaming endpoints (the generator never
    finishes because nobody is reading, and the middleware waits for it to
    finish before it starts reading). We use a raw ASGI middleware instead,
    which wraps only the `send` callable and never touches the body.
"""

from __future__ import annotations

import time
from typing import Callable

from fastapi import APIRouter, Request, Response
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# ── HTTP-level metrics ─────────────────────────────────────────────────────────

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests received",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently being processed",
    ["method", "path"],
)

# ── Router ────────────────────────────────────────────────────────────────────

metrics_router = APIRouter(tags=["observability"])


@metrics_router.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """
    Standard Prometheus scrape endpoint.
    Prometheus scrapes this every 15 seconds (configured in prometheus.yml).
    """
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


# ── Pure ASGI Middleware (SSE-safe) ───────────────────────────────────────────

class PrometheusMiddleware:
    """
    Raw ASGI middleware that records HTTP request counts and duration.

    This is intentionally NOT a BaseHTTPMiddleware subclass.
    BaseHTTPMiddleware buffers the complete response body before returning,
    which deadlocks Server-Sent Events (SSE) streaming endpoints used by the
    researcher query API.

    Instead, we wrap the `send` callable: we capture the HTTP status code
    when the `http.response.start` message is sent (headers only, no body),
    then record metrics in the `http.response.body` phase. The body bytes
    flow through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = self._resolve_path(scope)

        # Skip self-instrumentation of the /metrics endpoint
        if path == "/metrics":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        HTTP_REQUESTS_IN_PROGRESS.labels(method=method, path=path).inc()
        start = time.perf_counter()
        status_code = "500"  # default; overwritten when response headers arrive

        async def send_with_metrics(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                # Headers phase — capture status, record timing
                status_code = str(message["status"])
                duration = time.perf_counter() - start
                HTTP_REQUESTS_TOTAL.labels(
                    method=method, path=path, status_code=status_code
                ).inc()
                HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(duration)
                HTTP_REQUESTS_IN_PROGRESS.labels(method=method, path=path).dec()
            await send(message)

        try:
            await self.app(scope, receive, send_with_metrics)
        except Exception:
            # If the app raises before sending headers, still decrement gauge
            # (the counter/duration are only recorded once headers are sent)
            try:
                HTTP_REQUESTS_IN_PROGRESS.labels(method=method, path=path).dec()
            except Exception:
                pass
            raise

    @staticmethod
    def _resolve_path(scope: Scope) -> str:
        """
        Return the route template path (e.g. /api/v1/trial/{trial_id})
        rather than the actual path to avoid high-cardinality labels.
        """
        app = scope.get("app")
        if not app:
            return scope.get("path", "/")

        # Walk defined routes to find a match for the current scope
        for route in getattr(app, "routes", []):
            match, _ = route.matches(scope)
            if match == Match.FULL:
                return getattr(route, "path", scope.get("path", "/"))

        # Fallback to raw path if no route matched
        path: str = scope.get("path", "/")
        return path.split("?")[0]


def instrument_app(app: "FastAPI") -> None:  # noqa: F821
    """Add the SSE-safe Prometheus middleware to the FastAPI app."""
    app.add_middleware(PrometheusMiddleware)