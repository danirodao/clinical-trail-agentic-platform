"""
FastAPI API Gateway for the Clinical Trial Research Platform.
All endpoints require JWT authentication via Keycloak.
"""

import os
import logging
from contextlib import asynccontextmanager
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
import asyncpg
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from api.middleware.rate_limiter import RateLimitMiddleware
from api.middleware.audit_logger import AuditLogMiddleware
from api.database import init_db_pool, close_db_pool, get_db_pool
from api.metrics import metrics_router, instrument_app
from api.routers import domain_owner, manager, researcher, marketplace, eval_router
from api.collection_consumer import CollectionRefreshConsumer
from auth.openfga_client import get_openfga_client
from auth.reconciliation_service import ReconciliationService
from api.metrics import metrics_router, instrument_app
from api.logging_config import configure_logging, get_logger
configure_logging()  # structlog configured before anything else logs

from api.agent.observability import setup_observability

setup_observability()


collection_consumer: CollectionRefreshConsumer = None

log = get_logger(__name__)


async def run_reconciliation_job(pool):
    service = ReconciliationService(db_pool=pool)
    result = await service.reconcile_all()
    log.info("OpenFGA reconciliation run completed", result=result)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle manager."""
    global collection_consumer
    pool = await init_db_pool()
    db_url= f"postgresql://{os.environ.get('POSTGRES_USER', 'ctuser')}:{os.environ.get('POSTGRES_PASSWORD', 'ctpassword')}@{os.environ.get('POSTGRES_HOST', 'postgres')}:{os.environ.get('POSTGRES_PORT', 5432)}/{os.environ.get('POSTGRES_DB', 'clinical_trials')}"
    app.state.checkpointer_url =db_url
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    async with AsyncPostgresSaver.from_conn_string(app.state.checkpointer_url) as saver:
        await saver.setup()  # Creates tables if they don't exist
    
     
    collection_consumer = CollectionRefreshConsumer(
        db_pool_factory=lambda: pool,
        fga_client_factory=get_openfga_client,
    )
    collection_consumer.start()

    # ── Nightly Evaluation Scheduler ──────────────────────────────────────
    eval_scheduler = None
    if os.getenv("ENABLE_EVAL_SCHEDULER", "true").lower() == "true":
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger
            from api.routers.eval_router import scheduled_evaluation

            eval_scheduler = AsyncIOScheduler()
            eval_scheduler.add_job(
                scheduled_evaluation,
                CronTrigger(hour=2, minute=0),  # 2:00 AM UTC
                id="nightly_eval",
                name="Nightly Semantic Layer Evaluation",
                replace_existing=True,
            )
            eval_scheduler.start()
            log.info("Evaluation scheduler started (nightly at 02:00 UTC)")

            # ── OpenFGA Reconciliation Scheduler ───────────────────────────
            if os.getenv("ENABLE_FGA_RECONCILIATION", "true").lower() == "true":
                minutes = int(os.getenv("FGA_RECONCILIATION_INTERVAL_MINUTES", "10"))
                eval_scheduler.add_job(
                    run_reconciliation_job,
                    "interval",
                    minutes=minutes,
                    id="fga_reconciliation",
                    name="OpenFGA tuple reconciliation",
                    replace_existing=True,
                    kwargs={"pool": pool},
                )
                log.info("OpenFGA reconciliation scheduler started", interval_minutes=minutes)

                # Run once at startup to heal drift quickly.
                await run_reconciliation_job(pool)
        except ImportError:
            log.warning(
                "apscheduler not installed — nightly evaluation disabled. "
                "Install with: pip install apscheduler"
            )
        except Exception as exc:
            log.warning("Failed to start eval scheduler: %s", exc)

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    if eval_scheduler:
        eval_scheduler.shutdown(wait=False)
    if collection_consumer:
        collection_consumer.stop()
    await close_db_pool()


app = FastAPI(
    title="Clinical Trial Research Platform API",
    version="1.0.0",
    description="Authorization-aware clinical trial data access with Agentic RAG",
    lifespan=lifespan,
)
instrument_app(app)
# ─── Middleware ────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuditLogMiddleware)
app.add_middleware(RateLimitMiddleware)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )


# ─── Dependency: DB Pool ──────────────────────────────────────

# Database dependency is now in api.database


# ─── Routers ──────────────────────────────────────────────────
app.include_router(metrics_router)      
app.include_router(domain_owner.router, prefix="/api/v1", tags=["Domain Owner"])
app.include_router(manager.router, prefix="/api/v1", tags=["Manager"])
app.include_router(researcher.router, prefix="/api/v1", tags=["Researcher"])
app.include_router(marketplace.router, prefix="/api/v1", tags=["Marketplace"])
app.include_router(eval_router.router, prefix="/api/v1", tags=["Evaluation"])

# ─── Health Check ─────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health_check(db_pool=Depends(get_db_pool)):
    checks = {"api": "ok", "collection_consumer": "ok" if collection_consumer and collection_consumer._running else "stopped"}

    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "healthy" if all_ok else "degraded", "checks": checks}

@app.get("/health/agent")
async def agent_health():
    from api.agent.error_handler import mcp_circuit_breaker, openai_circuit_breaker
    from api.agent.embedding_cache import embedding_cache
    return {
        "mcp_circuit":    mcp_circuit_breaker._state,
        "openai_circuit": openai_circuit_breaker._state,
        "embedding_cache": embedding_cache.stats,
    }