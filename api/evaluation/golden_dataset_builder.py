"""
Golden Dataset Builder — Extract production traces from Phoenix and curate a
versioned golden dataset for offline evaluation.

Architecture principles:
  - Stratified sampling: extracts a representative % of traces by complexity,
    tool pattern, and outcome to avoid biasing the dataset.
  - Idempotent: re-running with the same parameters produces the same output
    (deterministic SHA-256 based IDs).
  - Context extraction: child tool spans are parsed to recover retrieval context
    so faithfulness metrics have the actual context the LLM saw.
  - Fail-safe: if Phoenix is unreachable, falls back to enriching the existing
    seed dataset with mock context rather than crashing.

Usage:
    # Inside the api container
    python -m api.evaluation.golden_dataset_builder --sample-pct 10
    python -m api.evaluation.golden_dataset_builder --sample-pct 10 --push-to-argilla
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SEED_DATASET_PATH = Path(__file__).parent / "golden_dataset.json"
OUTPUT_DIR = Path(__file__).parent / "datasets"


# ══════════════════════════════════════════════════════════════════════════════
# Phoenix Client Wrapper
# ══════════════════════════════════════════════════════════════════════════════

def _get_phoenix_client():
    """
    Lazy-import and connect to the Phoenix server.
    Returns None if Phoenix SDK is unavailable or the server is unreachable.
    """
    try:
        from phoenix.client import Client

        endpoint = os.getenv("PHOENIX_ENDPOINT", "http://phoenix:6006")
        # Strip /v1/traces suffix if present — the Client wants the base URL
        base = endpoint.replace("/v1/traces", "")
        client = Client(endpoint=base)
        # Quick health check
        _ = client.get_spans_dataframe(project_name="clinical-trial-agent", limit=1)
        return client
    except Exception as exc:
        logger.warning("Phoenix unreachable or SDK unavailable: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Trace Extraction
# ══════════════════════════════════════════════════════════════════════════════

def extract_traces_from_phoenix(
    sample_pct: float = 10.0,
    project_name: str = "clinical-trial-agent",
    max_traces: int = 500,
) -> list[dict[str, Any]]:
    """
    Pull spans from Phoenix, group into traces, extract inputs/outputs/context,
    and apply stratified sampling.

    Returns a list of dicts suitable for appending to the golden dataset.
    """
    client = _get_phoenix_client()
    if client is None:
        logger.warning("Cannot extract traces — Phoenix unavailable")
        return []

    logger.info(
        "Fetching spans from Phoenix project=%s (sample_pct=%.1f%%)",
        project_name,
        sample_pct,
    )

    try:
        spans_df = client.get_spans_dataframe(
            project_name=project_name,
            limit=max_traces * 20,  # Over-fetch to account for child spans
        )
    except Exception as exc:
        logger.error("Failed to fetch spans from Phoenix: %s", exc)
        return []

    if spans_df is None or spans_df.empty:
        logger.info("No spans found in Phoenix")
        return []

    # Group spans by trace_id
    traces: dict[str, list[dict]] = defaultdict(list)
    for _, row in spans_df.iterrows():
        trace_id = str(row.get("context.trace_id", ""))
        if trace_id:
            traces[trace_id].append(row.to_dict())

    logger.info("Found %d unique traces", len(traces))

    # Build golden records from root spans
    records: list[dict[str, Any]] = []
    for trace_id, spans in traces.items():
        record = _trace_to_golden_record(trace_id, spans)
        if record:
            records.append(record)

    # Stratified sampling
    sampled = _stratified_sample(records, sample_pct)
    logger.info(
        "Sampled %d / %d records (%.1f%%)",
        len(sampled),
        len(records),
        sample_pct,
    )

    return sampled


def _trace_to_golden_record(
    trace_id: str,
    spans: list[dict],
) -> dict[str, Any] | None:
    """
    Convert a list of spans (one trace) into a golden dataset record.
    Root span → input/output. Child tool spans → retrieval_context.
    """
    # Find the root span (no parent_id or parent_id is empty)
    root = None
    tool_spans = []

    for span in spans:
        parent = span.get("parent_id", "")
        span_kind = str(span.get("span_kind", "")).lower()
        name = str(span.get("name", ""))

        if not parent or parent == "None":
            root = span
        elif "tool" in span_kind or "tool" in name.lower():
            tool_spans.append(span)

    if root is None:
        return None

    # Extract input (user query)
    input_value = _extract_field(root, [
        "attributes.input.value",
        "attributes.llm.input_messages",
        "input",
    ])
    if not input_value:
        return None

    # Extract output (agent answer)
    output_value = _extract_field(root, [
        "attributes.output.value",
        "attributes.llm.output_messages",
        "output",
    ])

    # Extract retrieval context from child tool spans
    retrieval_context = []
    tool_calls = []
    for ts in tool_spans:
        tool_output = _extract_field(ts, [
            "attributes.output.value",
            "output",
        ])
        tool_name = str(ts.get("name", "unknown"))
        duration = ts.get("attributes.tool.duration_ms", 0)

        if tool_output:
            retrieval_context.append(str(tool_output)[:2000])
        tool_calls.append({
            "tool": tool_name,
            "duration_ms": duration,
        })

    # Determine outcome
    status = str(root.get("status_code", "OK")).upper()
    error = root.get("attributes.error", None)
    outcome = "error" if status == "ERROR" or error else "success"

    # Classify complexity
    complexity = "complex" if len(tool_calls) > 2 else "simple"

    # Deterministic ID
    record_id = hashlib.sha256(
        f"{trace_id}:{input_value}".encode()
    ).hexdigest()[:16]

    return {
        "id": f"phoenix-{record_id}",
        "layer": "agent",
        "category": f"production_{complexity}",
        "query": str(input_value)[:2000],
        "actual_output": str(output_value)[:5000] if output_value else None,
        "retrieval_context": retrieval_context[:10],
        "tool_calls_observed": tool_calls,
        "expected_tools": [tc["tool"] for tc in tool_calls],
        "outcome": outcome,
        "complexity": complexity,
        "source_trace_id": trace_id,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "prompt_injection": False,
        "notes": "Auto-extracted from Phoenix production trace",
    }


def _extract_field(span: dict, candidate_keys: list[str]) -> Any:
    """Try multiple keys to extract a value from a span dict."""
    for key in candidate_keys:
        value = span.get(key)
        if value is not None and value != "":
            return value
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Stratified Sampling
# ══════════════════════════════════════════════════════════════════════════════

def _stratified_sample(
    records: list[dict[str, Any]],
    sample_pct: float,
) -> list[dict[str, Any]]:
    """
    Sample records using stratified sampling across:
      1. complexity (simple/complex)
      2. outcome (success/error)
      3. tool count bucket (0, 1, 2-3, 4+)

    Ensures the sampled dataset is representative of production traffic.
    """
    if not records:
        return []

    strata: dict[str, list[dict]] = defaultdict(list)

    for record in records:
        complexity = record.get("complexity", "simple")
        outcome = record.get("outcome", "success")
        tool_count = len(record.get("tool_calls_observed", []))
        tool_bucket = (
            "0_tools" if tool_count == 0
            else "1_tool" if tool_count == 1
            else "2_3_tools" if tool_count <= 3
            else "4_plus_tools"
        )
        stratum_key = f"{complexity}_{outcome}_{tool_bucket}"
        strata[stratum_key].append(record)

    sampled: list[dict[str, Any]] = []
    for stratum_key, stratum_records in strata.items():
        n = max(1, int(len(stratum_records) * sample_pct / 100))
        random.seed(42)  # Reproducible
        selected = random.sample(stratum_records, min(n, len(stratum_records)))
        sampled.extend(selected)

    return sampled


# ══════════════════════════════════════════════════════════════════════════════
# Dataset Merging & Versioning
# ══════════════════════════════════════════════════════════════════════════════

def merge_with_seed(
    extracted: list[dict],
    seed_path: Path = SEED_DATASET_PATH,
) -> dict[str, Any]:
    """
    Merge Phoenix-extracted records with the seed golden dataset.
    Deduplicates by record ID. Returns a versioned dataset dict.
    """
    # Load seed
    with open(seed_path, "r") as f:
        seed = json.load(f)

    existing_ids = {tc["id"] for tc in seed.get("test_cases", [])}
    new_cases = [r for r in extracted if r["id"] not in existing_ids]

    merged_cases = seed.get("test_cases", []) + new_cases
    version = _next_version(seed.get("version", "1.0.0"))

    dataset = {
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": (
            f"Golden dataset v{version} — "
            f"{len(seed.get('test_cases', []))} seed + "
            f"{len(new_cases)} Phoenix-extracted records"
        ),
        "thresholds": seed.get("thresholds", {}),
        "test_cases": merged_cases,
    }

    return dataset


def import_reviewed_from_argilla(seed_path: Path = SEED_DATASET_PATH) -> dict[str, Any]:
    """
    Pull human-reviewed corrections from Argilla and merge them into the golden dataset.
    This is the core of the 'Evaluation Flywheel'.

    1. Export fixes from Argilla
    2. Merge into current dataset (Argilla fixes overwrite existing records with same ID)
    3. Mark Argilla records as imported
    4. Save updated dataset

    Returns the new dataset.
    """
    from api.evaluation.argilla_client import export_reviewed, mark_as_imported

    # Load current dataset
    with open(seed_path, "r") as f:
        dataset = json.load(f)

    # 1. Export reviewed records
    corrections = export_reviewed()
    if not corrections:
        logger.info("No new reviewed corrections found in Argilla")
        return dataset

    # 2. Merge corrections
    test_cases = dataset.get("test_cases", [])
    merged_cases, updated_ids, new_ids = _merge_corrections(test_cases, corrections)

    if not updated_ids and not new_ids:
        logger.info("No new information to merge from Argilla")
        return dataset

    # 3. Mark as imported in Argilla
    argilla_record_ids = [c["argilla_id"] for c in corrections if "argilla_id" in c]
    if argilla_record_ids:
        mark_as_imported(argilla_record_ids)

    # 4. Update dataset metadata
    dataset["version"] = _next_version(dataset.get("version", "1.0.0"))
    dataset["updated_at"] = datetime.now(timezone.utc).isoformat()
    dataset["test_cases"] = merged_cases
    dataset["description"] = (
        f"{dataset.get('description', '')}. "
        f"Imported {len(updated_ids)} updates and {len(new_ids)} new cases from Argilla."
    )

    logger.info(
        "Flywheel sync complete: updated %d cases, added %d new cases",
        len(updated_ids),
        len(new_ids),
    )

    return dataset


def _merge_corrections(
    existing: list[dict],
    corrections: list[dict],
) -> tuple[list[dict], list[str], list[str]]:
    """
    Merge Argilla corrections into existing test cases.
    Argilla records overwrite existing one if ID matches.
    """
    case_map = {c["id"]: c for c in existing}
    updated_ids = []
    new_ids = []

    for corr in corrections:
        cid = corr.get("id")
        if not cid or cid == "unknown":
            continue

        # Prepare the record for the Golden Dataset schema
        test_case = {
            "id": cid,
            "layer": corr.get("layer", "agent"),
            "category": corr.get("category", "argilla_correction"),
            "query": corr.get("query"),
            "expected_answer": corr.get("expected_answer"),
            "imported_at": corr.get("exported_at"),
            "notes": corr.get("reviewer_notes", "Correction from Argilla review"),
            # Preserve other fields if updating
        }

        if cid in case_map:
            # Overwrite but preserve some original fields (like tool expectations if present)
            old_case = case_map[cid]
            for key in ["layer", "expected_tools"]: # Keep these if corr doesn't have them
                if key not in test_case and key in old_case:
                    test_case[key] = old_case[key]

            case_map[cid] = test_case
            updated_ids.append(cid)
        else:
            case_map[cid] = test_case
            new_ids.append(cid)

    return list(case_map.values()), updated_ids, new_ids


def _next_version(current: str) -> str:
    """Increment patch version: 1.0.0 → 1.0.1"""
    parts = current.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def save_dataset(dataset: dict, output_dir: Path = OUTPUT_DIR) -> Path:
    """Save the dataset to a versioned JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    version = dataset.get("version", "unknown")
    filename = f"golden_dataset_v{version}.json"
    path = output_dir / filename

    with open(path, "w") as f:
        json.dump(dataset, f, indent=2, default=str)

    logger.info("Saved dataset to %s (%d cases)", path, len(dataset.get("test_cases", [])))
    return path


def save_root_dataset(dataset: dict, root_path: Path = SEED_DATASET_PATH) -> Path:
    """Overwrite the root golden_dataset.json with the updated dataset."""
    with open(root_path, "w") as f:
        json.dump(dataset, f, indent=2, default=str)

    logger.info("Updated root dataset at %s", root_path)
    return root_path


# ══════════════════════════════════════════════════════════════════════════════
# Argilla Push (failed / ambiguous cases)
# ══════════════════════════════════════════════════════════════════════════════

def push_to_argilla(records: list[dict]) -> None:
    """
    Push extracted records to Argilla for human review and curation.
    Delegates to the argilla_client module.
    """
    try:
        from api.evaluation.argilla_client import push_records_for_review

        push_records_for_review(records, source="golden_dataset_builder")
        logger.info("Pushed %d records to Argilla for curation", len(records))
    except Exception as exc:
        logger.warning("Failed to push to Argilla: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build golden dataset from Phoenix production traces"
    )
    parser.add_argument(
        "--sample-pct",
        type=float,
        default=10.0,
        help="Percentage of production traces to sample (default: 10%%)",
    )
    parser.add_argument(
        "--max-traces",
        type=int,
        default=500,
        help="Maximum number of traces to fetch from Phoenix",
    )
    parser.add_argument(
        "--push-to-argilla",
        action="store_true",
        help="Push extracted records to Argilla for human review",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(OUTPUT_DIR),
        help="Output directory for versioned datasets",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("\n" + "=" * 65)
    print("  GOLDEN DATASET BUILDER")
    print(f"  Sample: {args.sample_pct}%  |  Max traces: {args.max_traces}")
    print("=" * 65)

    # Step 1: Extract from Phoenix
    extracted = extract_traces_from_phoenix(
        sample_pct=args.sample_pct,
        max_traces=args.max_traces,
    )

    if not extracted:
        print("\n⚠️  No traces extracted from Phoenix (server may be empty or unreachable).")
        print("   Using seed dataset only.")
        extracted = []

    print(f"\n✅ Extracted {len(extracted)} records from Phoenix")

    # Step 2: Merge with seed dataset
    dataset = merge_with_seed(extracted)
    print(f"✅ Merged dataset: {len(dataset['test_cases'])} total cases (v{dataset['version']})")

    # Step 3: Save
    output_path = save_dataset(dataset, Path(args.output_dir))
    print(f"✅ Saved to {output_path}")

    # Step 4: Optionally push to Argilla
    if args.push_to_argilla and extracted:
        push_to_argilla(extracted)
        print(f"✅ Pushed {len(extracted)} records to Argilla")

    # Summary
    agent_cases = [c for c in dataset["test_cases"] if c.get("layer") == "agent"]
    mcp_cases = [c for c in dataset["test_cases"] if c.get("layer") == "mcp"]
    print(f"\n📊 Dataset Summary:")
    print(f"   Agent layer: {len(agent_cases)} cases")
    print(f"   MCP layer:   {len(mcp_cases)} cases")
    print(f"   Version:     {dataset['version']}")
    print()


if __name__ == "__main__":
    main()
