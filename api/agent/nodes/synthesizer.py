"""
Synthesizer node — final node in the LangGraph workflow.

Responsibilities:
  1. Extract the LLM's final answer from the message history
  2. Apply ceiling principle re-check (scrub patient IDs from response)
  3. Assemble QueryResponse with tool_call_records, sources, and metadata
  4. Record AGENT_CEILING_APPLIED_TOTAL when ceiling enforcement fires
  5. Emit an OTel span with key result attributes for Phoenix tracing

Phase 5 changes vs original:
  REMOVED — QUERY_COUNT, QUERY_DURATION, ITERATION_COUNT metric recording.
             These are now recorded in service.py's finally block (correct
             status label) and agent_node.py (correct iteration moment).
             Leaving them here caused every successful query to be counted twice.

  ADDED   — AGENT_CEILING_APPLIED_TOTAL counter when access_level contains
             "aggregate" due to mixed-trial ceiling enforcement.
  ADDED   — OTel span wrapping the full node so Phoenix shows synthesizer
             as a named trace node with access_level, source_count, and
             scrubbed_ids attributes.

Retained:
  - _determine_access_level: handles NCT IDs and empty scoped_trial_ids
  - _ProfileDictProxy: exposes trial_metadata for NCT ID reverse lookup
  - _enforce_aggregate_warning: prepends security notice when ceiling fires
  - _extract_queried_trial_ids: captures both trial_id and trial_ids args
"""

from __future__ import annotations

import json
import re
import time
from types import SimpleNamespace
from typing import Any

import structlog

from langchain_core.messages import AIMessage
from opentelemetry import trace

from ..observability import AGENT_CEILING_APPLIED_TOTAL, get_tracer
from ..access_context import describe_filters
from ..models import (
    AgentState,
    QueryMetadata,
    QueryResponse,
    QuerySource,
    ToolCallRecord,
)

logger = structlog.get_logger(__name__)
tracer = get_tracer()

# Regex to detect UUID-format strings that may be patient IDs
_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def synthesizer_node(state: AgentState, start_time: float) -> dict:
    """
    Assemble the final QueryResponse from the completed agent execution.

    Args:
        state:      Final AgentState after all tool calls and LLM turns.
        start_time: perf_counter timestamp captured in AgentService before
                    graph execution began. Used for duration calculation so
                    the metric includes all pre-node overhead.
    """
    with tracer.start_as_current_span("synthesizer") as span:

        # ── Extract final answer ──────────────────────────────────────────
        answer = _extract_final_answer(state["messages"])

        # ── Security: scrub patient UUIDs from LLM output ────────────────
        answer, scrubbed_count = _scrub_patient_ids(answer, state)
        span.set_attribute("synthesizer.scrubbed_patient_ids", scrubbed_count)

        # ── Collect tool call records ─────────────────────────────────────
        raw_records = state.get("tool_call_records", [])
        tool_call_records = [ToolCallRecord(**r) for r in raw_records]

        # ── Build sources from tool results ──────────────────────────────
        sources = _extract_sources(state["messages"], state["access_profile_dict"])
        span.set_attribute("synthesizer.source_count", len(sources))

        # ── Determine effective access level (ceiling principle) ──────────
        profile_dict = state["access_profile_dict"]
        queried_trial_ids = _extract_queried_trial_ids(raw_records)
        proxy = _ProfileDictProxy(profile_dict)
        access_level, ceiling_applied = _determine_access_level(proxy, queried_trial_ids)

        span.set_attribute("synthesizer.access_level", access_level)
        span.set_attribute("synthesizer.queried_trial_count", len(queried_trial_ids))

        # ── Ceiling metric: record when mixed access is downgraded ────────
        if ceiling_applied:
            AGENT_CEILING_APPLIED_TOTAL.inc()
            span.set_attribute("synthesizer.ceiling_applied", True)
            logger.info(
                "synthesizer: aggregate ceiling applied",
                access_level=access_level,
                queried_trials=queried_trial_ids,
            )

        # ── Active filters (human-readable) ──────────────────────────────
        filters_applied = describe_filters(proxy)

        # ── Security: prepend warning if aggregate answer has row-level data
        # Pass ceiling_applied so the warning can mention enforcement
        answer = _enforce_aggregate_warning(answer, access_level, ceiling_applied)

        # ── Timing and token metadata ─────────────────────────────────────
        # duration_ms is computed here for inclusion in QueryResponse.metadata
        # but is NOT used for the QUERY_DURATION Prometheus metric —
        # service.py records that from the true wall-clock start_time.
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        model_used = state.get("model_name", "unknown")
        prompt_tokens = state.get("total_prompt_tokens", 0)
        completion_tokens = state.get("total_completion_tokens", 0)
        iteration_count = state.get("iteration_count", 0)

        span.set_attribute("synthesizer.duration_ms", duration_ms)
        span.set_attribute("synthesizer.model", model_used)
        span.set_attribute("synthesizer.iteration_count", iteration_count)
        span.set_attribute(
            "synthesizer.total_tokens", prompt_tokens + completion_tokens
        )
        span.set_attribute("synthesizer.tool_call_count", len(tool_call_records))

        # ── Assemble final response ───────────────────────────────────────
        response = QueryResponse(
            answer=answer,
            sources=sources,
            tool_calls=tool_call_records,
            access_level_applied=access_level,
            filters_applied=filters_applied,
            metadata=QueryMetadata(
                model_used=model_used,
                total_tokens=prompt_tokens + completion_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_ms=duration_ms,
                iteration_count=iteration_count,
            ),
        )

        logger.info(
            "synthesizer_complete",
            duration_ms=duration_ms,
            model=model_used,
            iteration_count=iteration_count,
            total_tokens=prompt_tokens + completion_tokens,
            tool_call_count=len(tool_call_records),
            source_count=len(sources),
            access_level=access_level,
            scrubbed_ids=scrubbed_count,
        )

    return {"final_response": response.model_dump()}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _determine_access_level(
    proxy: "_ProfileDictProxy", queried_trial_ids: list[str]
) -> str:
    """
    Determine the effective access level for this query.

    When queried_trial_ids is empty, applies ceiling across ALL authorized
    trials. Resolves NCT IDs to UUIDs via trial_metadata. Unknown IDs
    default to "aggregate" (most restrictive / safe fallback).
    """
    trial_scopes = proxy.trial_scopes

    if not queried_trial_ids:
        all_levels = {
            getattr(scope, "access_level", "aggregate")
            for scope in trial_scopes.values()
        }
        if not all_levels:
            return "none", False
        return ("aggregate", False) if "aggregate" in all_levels else ("individual", False)

    levels: set[str] = set()

    for tid in queried_trial_ids:
        # 1. Direct UUID lookup
        scope = trial_scopes.get(tid)

        # 2. NCT ID fallback — walk trial_metadata for a matching UUID
        if not scope:
            for uuid_key, meta in proxy.trial_metadata.items():
                if meta.get("nct_id") == tid:
                    scope = trial_scopes.get(uuid_key)
                    break

        if scope:
            levels.add(getattr(scope, "access_level", "aggregate"))
        else:
            logger.warning(
                f"synthesizer: could not resolve trial '{tid}' in access profile — "
                "defaulting to aggregate"
            )
            levels.add("aggregate")

    if not levels:
        return "none", False
    if "aggregate" in levels and "individual" in levels:
        # Mixed access — ceiling principle forces aggregate for the entire response
        return "aggregate", True
    if "aggregate" in levels:
        return "aggregate", False
    return "individual", False


def _enforce_aggregate_warning(answer: str, access_level: str, ceiling_applied: bool = False) -> str:
    """
    If the effective access level is aggregate, scan the answer for signs
    that the LLM included individual patient rows and prepend a warning.
    """
    if access_level not in ("aggregate", "mixed"):
        return answer

    prefix = (
        "⚠️ **Access Level: aggregate (ceiling applied — mixed trial access detected)**\n\n"
        if ceiling_applied
        else "⚠️ **Access Level: aggregate**\n\n"
    )

    individual_indicators = [
        "patient_id", "subject_id", "individual record",
        "patient record", "row by row", "each patient",
    ]

    if any(indicator in answer.lower() for indicator in individual_indicators):
        logger.warning(
            "synthesizer: aggregate-only response appears to contain "
            "individual patient references — prepending security notice"
        )
        return (
            f"⚠️ **Access Level: {access_level}** — "
            "Individual patient records are not permitted for this query. "
            "Showing aggregate statistics only.\n\n"
            + answer
        )

    return answer


def _extract_final_answer(messages: list) -> str:
    """
    Walk the message list in reverse to find the last substantive AIMessage
    (text content only, not a tool-call message).
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                # Anthropic-style content blocks
                text_blocks = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                combined = " ".join(text_blocks).strip()
                if combined:
                    return combined

    return (
        "I was unable to generate a response. "
        "Please try rephrasing your question."
    )


def _scrub_patient_ids(answer: str, state: AgentState) -> tuple[str, int]:
    """
    Remove UUID-format strings from the answer that are not trial IDs.

    Returns:
        (scrubbed_answer, count_of_replacements)
    """
    allowed_uuids = {
        u.lower()
        for u in state["access_profile_dict"].get("allowed_trial_ids", [])
    }
    scrub_count = 0

    def replace_if_patient_id(match: re.Match) -> str:
        nonlocal scrub_count
        val = match.group(0)
        if val.lower() in allowed_uuids:
            return val  # Trial UUID — safe to show
        scrub_count += 1
        return "[PATIENT-ID-REDACTED]"

    scrubbed = _UUID_PATTERN.sub(replace_if_patient_id, answer)

    if scrub_count:
        logger.warning(
            f"synthesizer: scrubbed {scrub_count} potential patient ID(s) "
            "from LLM response"
        )

    return scrubbed, scrub_count


def _extract_sources(messages: list, profile_dict: dict) -> list[QuerySource]:
    """
    Extract cited trial information from ToolMessage content in the history.
    Returns deduplicated QuerySource objects for trials actually queried.
    """
    seen_ids: set[str] = set()
    sources: list[QuerySource] = []

    trial_metadata: dict = profile_dict.get("trial_metadata", {})

    for message in messages:
        content = getattr(message, "content", "")
        if not isinstance(content, str):
            continue

        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue

        trial_ids_in_result: list[str] = []

        if "trial_id" in data and data["trial_id"]:
            trial_ids_in_result.append(str(data["trial_id"]))

        if "trials" in data and isinstance(data["trials"], list):
            for t in data["trials"]:
                if isinstance(t, dict) and "trial_id" in t:
                    trial_ids_in_result.append(str(t["trial_id"]))

        # Some tools wrap results under a "data" key
        nested = data.get("data", {})
        if isinstance(nested, dict) and "trial_id" in nested and nested["trial_id"]:
            trial_ids_in_result.append(str(nested["trial_id"]))

        for tid in trial_ids_in_result:
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                meta = trial_metadata.get(tid, {})
                sources.append(
                    QuerySource(
                        trial_id=tid,
                        nct_id=meta.get("nct_id", ""),
                        title=meta.get("title", ""),
                    )
                )

    return sources


def _extract_queried_trial_ids(records: list[dict]) -> list[str]:
    """
    Collect all trial IDs passed to tool calls from ToolCallRecord args.
    Handles both trial_ids (list) and trial_id (string) fields.
    Filters out short garbage values like "3" or "all".
    """
    ids: set[str] = set()

    for record in records:
        args = record.get("args", {})

        raw_list = args.get("trial_ids", [])
        if isinstance(raw_list, list):
            for tid in raw_list:
                tid_str = str(tid).strip()
                if len(tid_str) > 8:
                    ids.add(tid_str)
        elif isinstance(raw_list, str) and len(raw_list) > 8:
            ids.add(raw_list.strip())

        single = args.get("trial_id", "")
        if single and len(str(single).strip()) > 8:
            ids.add(str(single).strip())

    return list(ids)


class _ProfileDictProxy:
    """
    Minimal proxy over the access_profile_dict stored in AgentState.
    Satisfies describe_filters() and _determine_access_level().
    Exposes trial_metadata for NCT ID → UUID reverse lookup.
    """

    def __init__(self, d: dict):
        self._d = d

    @property
    def trial_scopes(self) -> dict:
        scopes = self._d.get("trial_scopes", {})
        wrapped = {}
        for k, v in scopes.items():
            if isinstance(v, dict):
                cohorts = [
                    SimpleNamespace(**cs) for cs in v.get("cohort_scopes", [])
                ]
                v_copy = dict(v)
                v_copy["cohort_scopes"] = cohorts
                wrapped[k] = SimpleNamespace(**v_copy)
            else:
                wrapped[k] = v
        return wrapped

    @property
    def trial_metadata(self) -> dict:
        return self._d.get("trial_metadata", {})

    @property
    def allowed_trial_ids(self) -> list[str]:
        return self._d.get("allowed_trial_ids", [])