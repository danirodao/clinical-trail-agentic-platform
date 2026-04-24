"""
Semantic MCP Server — ontology, concept disambiguation, and semantic context.

Port 8002 (separate from Data MCP on 8001).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import warnings
from contextlib import asynccontextmanager

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from fastmcp import FastMCP
from jose import jwt, JWTError
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route

from neo4j_ontology import init_driver, close_driver
from ontology_seeder import apply_schema, seed_ontology
from observability import metrics_response
from tools import register_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-28s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("semantic_mcp")


# ─────────────────────────────────────────────────────────────────────────────
# FastMCP instance
# ─────────────────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "Clinical Trial Semantic Layer",
    instructions=(
        "Semantic MCP server exposing ontology lookup, concept disambiguation, "
        "and the clinical trial semantic model backed by Neo4j."
    ),
)

register_tools(mcp)

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    mcp_sse_app = mcp.sse_app()


# ─────────────────────────────────────────────────────────────────────────────
# JWT middleware (mirrors Data MCP)
# ─────────────────────────────────────────────────────────────────────────────

class KeycloakAuthMiddleware:
    def __init__(self, app):
        self.app = app
        self.keycloak_url = os.getenv("KEYCLOAK_URL", "http://keycloak:8180")
        self.realm = "clinical-trials"
        self.jwks_url = (
            f"{self.keycloak_url}/realms/{self.realm}/protocol/openid-connect/certs"
        )
        self.jwks = None

    async def get_jwks(self):
        if not self.jwks:
            async with httpx.AsyncClient() as client:
                resp = await client.get(self.jwks_url)
                self.jwks = resp.json()
        return self.jwks

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        if not scope["path"].startswith("/sse"):
            return await self.app(scope, receive, send)

        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8")

        if not auth_header or not auth_header.startswith("Bearer "):
            await self._send_error(send, 401, "Missing Authorization header")
            return

        token = auth_header.split(" ")[1]
        try:
            jwks = await self.get_jwks()
            payload = jwt.decode(
                token,
                jwks,
                algorithms=["RS256"],
                audience="account",
                options={"verify_aud": False},
            )
            scope["client_id"] = payload.get("clientId")
        except JWTError as exc:
            await self._send_error(send, 401, f"Invalid Token: {exc}")
            return

        await self.app(scope, receive, send)

    async def _send_error(self, send, status: int, message: str):
        body = json.dumps({"error": message}).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


# ─────────────────────────────────────────────────────────────────────────────
# Lifespan: connect Neo4j → apply schema → seed ontology
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: Starlette):
    logger.info("=" * 70)
    logger.info("🧠 Semantic MCP Server starting...")
    await init_driver()
    await apply_schema()
    await seed_ontology()
    logger.info("✅ Ontology seeded into Neo4j")
    logger.info("📋 Registered tools: %d", len(mcp._tools) if hasattr(mcp, "_tools") else 0)
    logger.info("=" * 70)

    async with mcp_sse_app.lifespan(mcp_sse_app):
        yield

    await close_driver()


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


async def handle_metrics(request: Request) -> Response:
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type)


_starlette_app = Starlette(
    routes=[
        Route("/health", endpoint=health, methods=["GET"]),
        Route("/healthz", endpoint=health, methods=["GET"]),
        Route("/metrics", endpoint=handle_metrics, methods=["GET"]),
        Mount("/", app=mcp_sse_app),
    ],
    lifespan=lifespan,
)

app = KeycloakAuthMiddleware(_starlette_app)

if __name__ == "__main__":
    port = int(os.environ.get("SEMANTIC_MCP_PORT", "8002"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
