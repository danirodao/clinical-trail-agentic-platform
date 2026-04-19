"""
Evaluation API Router — On-demand and scheduled evaluation endpoints.

Endpoints:
    POST /eval/run           Run a full evaluation (requires manager role)
    GET  /eval/status        Get latest evaluation results
    GET  /eval/golden-dataset Get golden dataset metadata
    POST /eval/build-dataset Build golden dataset from Phoenix traces

Architecture:
    - On-demand via API (this router)
    - Nightly via APScheduler (registered in main.py lifespan)
    - CI via CLI (offline_evaluator.py --ci)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional


from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from auth.dependencies import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/eval", tags=["Evaluation"])

DATASET_PATH = Path(__file__).parent.parent / "evaluation" / "golden_dataset.json"
REPORT_DIR = Path(__file__).parent.parent / "evaluation" / "reports"


# ──────────────────────────────────────────────────────────────────────────────
# Request / Response Models
# ──────────────────────────────────────────────────────────────────────────────

class EvalRunRequest(BaseModel):
    dataset: str = Field(
        default="golden_dataset.json",
        description="Name of the golden dataset file to use",
    )
    layer: Optional[str] = Field(
        default=None,
        description="Evaluate only 'agent' or 'mcp' layer (None = both)",
    )
    push_failures_to_argilla: bool = Field(
        default=True,
        description="Push failed cases to Argilla for human review",
    )
    dataset_source: Literal["static", "merged", "argilla"] = Field(
        default="merged",
        description="Source strategy: static | merged | argilla",
    )
    max_cases: Optional[int] = Field(
        default=None,
        ge=0,
        le=5000,
        description="Optional cap on number of evaluated cases",
    )
    argilla_sample_pct: float = Field(
        default=100.0,
        ge=1.0,
        le=100.0,
        description="Percentage of Argilla gold records to include",
    )


class EvalRunResponse(BaseModel):
    run_id: str
    dataset_version: str
    layer: str
    pass_rate: float
    total_cases: int
    passed_cases: int
    failed_cases: int
    duration_s: float
    aggregate_scores: dict[str, float]
    failed_case_ids: list[str]


class AcceptedEvalResponse(BaseModel):
    status: str = "accepted"
    message: str
    run_id: str
    dataset: str



class DatasetBuildRequest(BaseModel):
    sample_pct: float = Field(default=10.0, ge=1.0, le=100.0)
    max_traces: int = Field(default=500, ge=10, le=5000)
    push_to_argilla: bool = Field(default=False)


class DatasetMetadata(BaseModel):
    version: str
    total_cases: int
    agent_cases: int
    mcp_cases: int
    thresholds: dict[str, float]


# ──────────────────────────────────────────────────────────────────────────────
# POST /eval/run
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/run",
    response_model=AcceptedEvalResponse,
    dependencies=[Depends(require_role("manager", "domain_owner"))],
)
async def run_evaluation(
    body: EvalRunRequest,
    background_tasks: BackgroundTasks
):
    """
    Execute a full evaluation run against the golden dataset.
    Returns immediately; evaluation runs in the background.
    """
    from datetime import datetime, timezone
    
    # Resolve dataset path
    if body.dataset == "golden_dataset.json":
        dataset_path = DATASET_PATH
    else:
        dataset_path = Path(__file__).parent.parent / "evaluation" / "datasets" / body.dataset
        if not dataset_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Dataset not found: {body.dataset}",
            )

    if not dataset_path.exists():
        raise HTTPException(
            status_code=404, detail="Golden dataset not found."
        )

    run_id = f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    logger.info("Scheduling evaluation run %s (layer=%s)", run_id, body.layer or "both")

    # Check for existing run
    lock_file = REPORT_DIR / ".running.lock"
    if lock_file.exists():
        raise HTTPException(
            status_code=409, 
            detail="An evaluation is already in progress. Please wait for it to complete."
        )

    # Trigger background task
    background_tasks.add_task(
        _background_eval_task,
        run_id=run_id,
        dataset_path=dataset_path,
        layer=body.layer,
        push_failures_to_argilla=body.push_failures_to_argilla,
        dataset_source=body.dataset_source,
        max_cases=body.max_cases,
        argilla_sample_pct=body.argilla_sample_pct,
    )

    return AcceptedEvalResponse(
        message="Evaluation started in background.",
        run_id=run_id,
        dataset=body.dataset
    )


async def _background_eval_task(
    run_id: str,
    dataset_path: Path,
    layer: str | None,
    push_failures_to_argilla: bool,
    dataset_source: str,
    max_cases: int | None,
    argilla_sample_pct: float,
):
    """Internal task runner for background evaluations."""
    from api.evaluation.offline_evaluator import run_evaluation as _run_eval
    
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = REPORT_DIR / ".running.lock"
    
    try:
        # Create lock
        with open(lock_file, "w") as f:
            f.write(json.dumps({"run_id": run_id, "started_at": datetime.now(timezone.utc).isoformat()}))

        await _run_eval(
            dataset_path=dataset_path,
            trigger="api",
            push_failures_to_argilla=push_failures_to_argilla,
            layer_filter=layer,
            dataset_source=dataset_source,
            max_cases=max_cases,
            argilla_sample_pct=argilla_sample_pct,
        )
        logger.info("Background evaluation %s completed successfully", run_id)
    except Exception:
        logger.exception("Background evaluation %s failed", run_id)
    finally:
        # Cleanup lock
        if lock_file.exists():
            lock_file.unlink()



# ──────────────────────────────────────────────────────────────────────────────
# GET /eval/status
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/status",
    dependencies=[Depends(require_role("researcher", "manager", "domain_owner"))],
)
async def get_eval_status():
    """
    Get the latest evaluation run results or current progress state.
    """
    if not REPORT_DIR.exists():
        return {"status": "no_runs", "message": "No evaluation runs have been executed yet."}

    # Check for active lock
    lock_file = REPORT_DIR / ".running.lock"
    is_running = lock_file.exists()
    running_info = {}
    if is_running:
        try:
            with open(lock_file) as f:
                running_info = json.load(f)
        except Exception:
            pass

    reports = sorted(REPORT_DIR.glob("eval-*.json"), reverse=True)
    if not reports:
        if is_running:
            return {
                "status": "running",
                "run_id": running_info.get("run_id"),
                "message": "Evaluation in progress...",
                "started_at": running_info.get("started_at")
            }
        return {"status": "no_runs", "message": "No evaluation reports found."}

    latest = reports[0]
    with open(latest) as f:
        report = json.load(f)

    return {
        "status": "running" if is_running else "ok",
        "latest_run": report.get("run_id"),
        "dataset_version": report.get("dataset_version"),
        "pass_rate": report.get("pass_rate"),
        "total_cases": report.get("total_cases"),
        "failed_cases": report.get("failed_cases"),
        "duration_s": report.get("duration_s"),
        "aggregate_scores": report.get("aggregate_scores", {}),
        "run_count": len(reports),
        "active_run_id": running_info.get("run_id") if is_running else None
    }



# ──────────────────────────────────────────────────────────────────────────────
# GET /eval/golden-dataset
# ──────────────────────────────────────────────────────────────────────────────

@router.get(
    "/golden-dataset",
    response_model=DatasetMetadata,
    dependencies=[Depends(require_role("researcher", "manager", "domain_owner"))],
)
async def get_golden_dataset():
    """Get metadata about the current golden dataset."""
    if not DATASET_PATH.exists():
        raise HTTPException(status_code=404, detail="Golden dataset not found.")

    with open(DATASET_PATH) as f:
        ds = json.load(f)

    cases = ds.get("test_cases", [])
    agent = [c for c in cases if c.get("layer") == "agent"]
    mcp = [c for c in cases if c.get("layer") == "mcp"]

    return DatasetMetadata(
        version=ds.get("version", "unknown"),
        total_cases=len(cases),
        agent_cases=len(agent),
        mcp_cases=len(mcp),
        thresholds=ds.get("thresholds", {}),
    )


# ──────────────────────────────────────────────────────────────────────────────
# POST /eval/build-dataset
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/build-dataset",
    dependencies=[Depends(require_role("manager", "domain_owner"))],
)
async def build_dataset(
    body: DatasetBuildRequest,
    background_tasks: BackgroundTasks,
):
    """
    Build a new golden dataset by extracting traces from Phoenix.
    Merges extracted records with the seed dataset and saves a versioned copy.
    """
    from api.evaluation.golden_dataset_builder import (
        extract_traces_from_phoenix,
        merge_with_seed,
        save_dataset,
        push_to_argilla,
    )

    extracted = extract_traces_from_phoenix(
        sample_pct=body.sample_pct,
        max_traces=body.max_traces,
    )

    dataset = merge_with_seed(extracted)
    output_path = save_dataset(dataset)

    # Offload slow Argilla sync to background
    if body.push_to_argilla and extracted:
        background_tasks.add_task(push_to_argilla, extracted)

    cases = dataset.get("test_cases", [])
    return {
        "status": "ok",
        "version": dataset.get("version"),
        "total_cases": len(cases),
        "extracted_from_phoenix": len(extracted),
        "output_path": str(output_path),
        "background_sync": body.push_to_argilla and len(extracted) > 0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# POST /eval/import-reviewed
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/import-reviewed",
    dependencies=[Depends(require_role("manager", "domain_owner"))],
)
async def import_reviewed():
    """
    Sync human-reviewed corrections from Argilla into the golden dataset.
    This completes the 'Evaluation Flywheel' loop.

    1. Pulls corrections from Argilla
    2. Overwrites records in golden_dataset.json if IDs match
    3. Backs up the new version to the datasets/ directory
    """
    from api.evaluation.golden_dataset_builder import (
        import_reviewed_from_argilla,
        save_dataset,
        save_root_dataset,
    )

    try:
        # Import from Argilla
        updated_dataset = import_reviewed_from_argilla()

        # Save both as a versioned backup and the main root file
        backup_path = save_dataset(updated_dataset)
        root_path = save_root_dataset(updated_dataset)

        # Calculate statistics from the description/metadata if possible
        # Or just return the top-level stats
        cases = updated_dataset.get("test_cases", [])

        return {
            "status": "success",
            "message": "Evaluation Flywheel sync completed successfully",
            "new_version": updated_dataset.get("version"),
            "total_cases": len(cases),
            "backup_saved_to": str(backup_path),
            "root_updated": True,
        }
    except Exception as exc:
        logger.exception("Flywheel sync failed")
        raise HTTPException(
            status_code=500,
            detail=f"Argilla sync failed: {str(exc)}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Scheduled Evaluation Function (called by APScheduler in main.py)
# ══════════════════════════════════════════════════════════════════════════════

async def scheduled_evaluation() -> None:
    """
    Entry point for the nightly scheduled evaluation run.
    Called by APScheduler CronTrigger registered in main.py.
    Designed to be fire-and-forget — exceptions are logged, not raised.
    """
    logger.info("Starting scheduled nightly evaluation")

    try:
        from api.evaluation.offline_evaluator import run_evaluation as _run_eval

        if not DATASET_PATH.exists():
            logger.warning("Skipping scheduled eval — golden dataset not found")
            return

        result = await _run_eval(
            dataset_path=DATASET_PATH,
            trigger="schedule",
            push_failures_to_argilla=True,
        )

        logger.info(
            "Scheduled evaluation complete: pass_rate=%.1f%% (%d/%d) in %.1fs",
            result.pass_rate * 100,
            result.passed_cases,
            result.total_cases,
            result.duration_s,
        )

        # Alert if pass rate drops below threshold
        if result.pass_rate < 0.85:
            logger.warning(
                "⚠️ QUALITY ALERT: Evaluation pass rate dropped to %.1f%% "
                "(threshold: 85%%). Review failed cases in Argilla.",
                result.pass_rate * 100,
            )

    except Exception as exc:
        logger.exception("Scheduled evaluation failed: %s", exc)
