"""
Clinical Trial MCP Server — FastMCP with explicit tool registration.
"""

import logging
import os
import warnings
from contextlib import asynccontextmanager

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send
from jose import jwt, JWTError
import httpx
import json
from observability import metrics_response, instrument_tool
from db import postgres, qdrant_client, neo4j_client

logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG for better visibility
    format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mcp_server")
async def handle_metrics(request: Request) -> Response:
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type)

# Initialize FastMCP
mcp = FastMCP(
    "Clinical Trial Data Server",
    instructions="MCP server providing tools to query clinical trial data."
)

def register_all_tools():
    """Explicitly register all tool modules."""
    modules = [
        "trial_discovery",
        "trial_metadata",
        "patient_analytics",
        "clinical_analysis",
        "knowledge_discovery",
    ]

    for module_name in modules:
        try:
            # More reliable import
            full_module_name = f"tools.{module_name}"
            mod = __import__(full_module_name, fromlist=["register_tools"])
            
            if hasattr(mod, "register_tools"):
                mod.register_tools(mcp)
                logger.info(f"✅ Successfully registered tools from: {module_name}")
            else:
                logger.warning(f"⚠️  Module {module_name} has no register_tools() function")
                
        except ImportError as e:
            logger.error(f"❌ Failed to import tools.{module_name}: {e}")
        except Exception as e:
            logger.error(f"❌ Error registering tools from {module_name}: {e}", exc_info=True)



# Register tools at startup
register_all_tools()

# Get SSE app
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    mcp_sse_app = mcp.sse_app()

UNPROTECTED_PATHS = {"/health", "/healthz"}


# === Your existing KeycloakAuthMiddleware (unchanged) ===
class KeycloakAuthMiddleware:
    def __init__(self, app):
        self.app = app
        self.keycloak_url = os.getenv("KEYCLOAK_URL", "http://keycloak:8180")
        self.realm = "clinical-trials"
        self.jwks_url = f"{self.keycloak_url}/realms/{self.realm}/protocol/openid-connect/certs"
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
            await self._send_json_error(send, 401, "Missing or invalid Authorization header")
            return

        token = auth_header.split(" ")[1]

        try:
            jwks = await self.get_jwks()
            payload = jwt.decode(
                token, 
                jwks, 
                algorithms=["RS256"], 
                audience="account",
                options={"verify_aud": False}
            )
            scope["client_id"] = payload.get("clientId")
        except JWTError as e:
            await self._send_json_error(send, 401, f"Invalid Token: {str(e)}")
            return

        await self.app(scope, receive, send)

    async def _send_json_error(self, send, status_code: int, message: str):
        body = json.dumps({"error": message}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("utf-8"))
            ]
        })
        await send({
            "type": "http.response.body",
            "body": body
        })


async def health_check(request: Request) -> PlainTextResponse:
    return PlainTextResponse("OK")


@asynccontextmanager
async def lifespan(app: Starlette):
    logger.info("=" * 80)
    logger.info("🚀 Clinical Trial MCP Server starting...")
    
    await postgres.init_pool()
    await qdrant_client.init_client()
    await neo4j_client.init_driver()
    
    logger.info("✅ Database connections initialized")
    logger.info(f"📋 Registered tools: {len(mcp._tools) if hasattr(mcp, '_tools') else 0}")
    logger.info("=" * 80)
    
    async with mcp_sse_app.lifespan(mcp_sse_app):
        yield

    await postgres.close_pool()
    await qdrant_client.close_client()
    await neo4j_client.close_driver()


# Starlette app
_starlette_app = Starlette(
    routes=[
        Route("/health", endpoint=health_check, methods=["GET"]),
        Route("/healthz", endpoint=health_check, methods=["GET"]),
        Route("/metrics", endpoint=handle_metrics, methods=["GET"]),
        Mount("/", app=mcp_sse_app),
    ],
    lifespan=lifespan,
)

# Apply middleware
app = KeycloakAuthMiddleware(_starlette_app)

if __name__ == "__main__":
    port = int(os.environ.get("MCP_PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")