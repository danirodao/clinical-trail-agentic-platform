"""
Synthesizer node — final node in the LangGraph workflow.

Responsibilities:
  1. Extract the LLM's final answer from the message history
  2. Apply ceiling principle re-check (validate no patient IDs in response)
  3. Assemble QueryResponse with tool_call_records, sources, and metadata
  4. Record Prometheus metrics for the completed query

FIXED:
  - determine_access_level_applied now handles NCT IDs and empty scoped_trial_ids
  - _ProfileDictProxy now exposes trial_metadata for NCT ID reverse lookup
  - Added aggregate ceiling enforcement warning in final answer
  - _extract_queried_trial_ids now captures trial_id (singular) tool args too
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from langchain_core.messages import AIMessage

from ..access_context import describe_filters
from ..models import (
    AgentState,
    QueryMetadata,
    QueryResponse,
    QuerySource,
    ToolCallRecord,
)
from ..observability import ITERATION_COUNT, QUERY_COUNT, QUERY_DURATION

logger = logging.getLogger(__name__)

# Regex pattern to detect potential UUID-format patient IDs in responses
_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def synthesizer_node(state: AgentState, start_time: float) -> dict:
    """
    Assemble the final QueryResponse from the completed agent execution.

    Args:
        state:      Final AgentState after all tool calls and LLM turns
        start_time: Unix timestamp (perf_counter) from AgentService for duration calc
    """
    # ── Extract final answer text ─────────────────────────────────────────────
    answer = _extract_final_answer(state["messages"])

    # ── Security: ceiling principle re-check ──────────────────────────────────
    answer = _scrub_patient_ids(answer, state)

    # ── Collect tool call records ─────────────────────────────────────────────
    raw_records = state.get("tool_call_records", [])
    tool_call_records = [ToolCallRecord(**r) for r in raw_records]

    # ── Build sources from tool results ──────────────────────────────────────
    sources = _extract_sources(state["messages"], state["access_profile_dict"])

    # ── Determine effective access level ─────────────────────────────────────
    profile_dict = state["access_profile_dict"]
    queried_trial_ids = _extract_queried_trial_ids(raw_records)
    proxy = _ProfileDictProxy(profile_dict)

    # FIXED: Use the local _determine_access_level function that handles
    # NCT IDs and empty scoped_trial_ids correctly
    access_level = _determine_access_level(proxy, queried_trial_ids)

    # ── Active filters (human-readable) ──────────────────────────────────────
    filters_applied = describe_filters(proxy)

    # ── Security: warn in answer if aggregate ceiling applies ─────────────────
    answer = _enforce_aggregate_warning(answer, access_level)

    # ── Timing and token metadata ─────────────────────────────────────────────
    duration_ms = int((time.perf_counter() - start_time) * 1000)
    model_used = state.get("model_name", "unknown")
    prompt_tokens = state.get("total_prompt_tokens", 0)
    completion_tokens = state.get("total_completion_tokens", 0)
    iteration_count = state.get("iteration_count", 0)

    # ── Prometheus metrics ────────────────────────────────────────────────────
    complexity = state.get("query_complexity", "simple")
    QUERY_COUNT.labels(model=model_used, status="success", complexity=complexity).inc()
    QUERY_DURATION.labels(model=model_used, complexity=complexity).observe(duration_ms / 1000.0)
    ITERATION_COUNT.observe(iteration_count)

    # ── Assemble final response ───────────────────────────────────────────────
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
        f"Query synthesized: "
        f"duration={duration_ms}ms, model={model_used}, "
        f"iterations={iteration_count}, tokens={prompt_tokens + completion_tokens}, "
        f"tools_called={len(tool_call_records)}, access_level={access_level}"
    )

    return {"final_response": response.model_dump()}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _determine_access_level(proxy: "_ProfileDictProxy", queried_trial_ids: list[str]) -> str:
    """
    Determine the effective access level for this query.

    FIXED over the original determine_access_level_applied:
      1. When queried_trial_ids is empty, computes ceiling across ALL authorized trials
         instead of returning "none".
      2. Resolves NCT IDs to UUIDs using trial_metadata for correct lookup.
      3. Unknown trial IDs default to "aggregate" (safe fallback).
    """
    trial_scopes = proxy.trial_scopes

    if not queried_trial_ids:
        # No specific scope recorded — apply ceiling across ALL authorized trials
        all_levels = {
            getattr(scope, "access_level", "aggregate")
            for scope in trial_scopes.values()
        }
        if not all_levels:
            return "none"
        return "aggregate" if "aggregate" in all_levels else "individual"

    levels: set[str] = set()

    for tid in queried_trial_ids:
        # 1. Direct UUID lookup
        scope = trial_scopes.get(tid)

        # 2. NCT ID fallback — search trial_metadata for a matching UUID
        if not scope:
            for uuid_key, meta in proxy.trial_metadata.items():
                if meta.get("nct_id") == tid:
                    scope = trial_scopes.get(uuid_key)
                    break

        if scope:
            levels.add(getattr(scope, "access_level", "aggregate"))
        else:
            # Unknown trial ID (possibly hallucinated) → treat as most restrictive
            logger.warning(
                f"synthesizer: could not resolve trial '{tid}' in access profile. "
                "Defaulting to aggregate."
            )
            levels.add("aggregate")

    if not levels:
        return "none"
    if "aggregate" in levels and "individual" in levels:
        return "mixed → aggregate (ceiling applied)"
    if "aggregate" in levels:
        return "aggregate"
    return "individual"


def _enforce_aggregate_warning(answer: str, access_level: str) -> str:
    """
    If the effective access level is aggregate, scan the answer for signs that
    the LLM included individual patient rows and prepend a warning if found.
    """
    if "aggregate" not in access_level:
        return answer

    # Indicators that the answer might contain individual patient data
    individual_indicators = [
        "patient_id", "subject_id", "individual record",
        "patient record", "row by row", "each patient",
    ]

    if any(indicator in answer.lower() for indicator in individual_indicators):
        logger.warning(
            "synthesizer: aggregate-only response appears to contain individual "
            "patient references. Prepending security notice."
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
    (i.e., one with actual text content, not just tool_calls).
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            content = message.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                # Anthropic-style content blocks
                text_blocks = [
                    block.get("text", "") for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                combined = " ".join(text_blocks).strip()
                if combined:
                    return combined

    return "I was unable to generate a response. Please try rephrasing your question."


def _scrub_patient_ids(answer: str, state: AgentState) -> str:
    """
    Remove any UUID-format strings from the answer that look like patient IDs.

    Trial IDs in the access profile are allowlisted — only patient-style UUIDs
    that the LLM incorrectly included in the response are scrubbed.
    """
    allowed_uuids = set(state["access_profile_dict"].get("allowed_trial_ids", []))

    def replace_if_patient_id(match: re.Match) -> str:
        uuid = match.group(0)
        if uuid.lower() in {u.lower() for u in allowed_uuids}:
            return uuid  # Trial UUID — allowed in response
        return "[PATIENT-ID-REDACTED]"

    scrubbed = _UUID_PATTERN.sub(replace_if_patient_id, answer)
    if scrubbed != answer:
        logger.warning("Synthesizer scrubbed potential patient ID(s) from LLM response")
    return scrubbed


def _extract_sources(messages: list, profile_dict: dict) -> list[QuerySource]:
    """
    Extract cited trial information from tool call results in the message history.
    Returns deduplicated QuerySource objects for trials that were actually queried.

    FIXED: Also reads trial_metadata to populate nct_id and title in sources.
    """
    seen_ids: set[str] = set()
    sources: list[QuerySource] = []

    # trial_metadata maps UUID → {nct_id, title}
    trial_metadata: dict = profile_dict.get("trial_metadata", {})

    # trial_scopes also has access_level per trial
    trial_scopes: dict = profile_dict.get("trial_scopes", {})

    for message in messages:
        content = getattr(message, "content", "")
        if not isinstance(content, str):
            continue

        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue

        # Tool results may contain trial_id (singular) or trials list
        trial_ids_in_result: list[str] = []

        if "trial_id" in data and data["trial_id"]:
            trial_ids_in_result.append(str(data["trial_id"]))

        if "trials" in data and isinstance(data["trials"], list):
            for t in data["trials"]:
                if isinstance(t, dict) and "trial_id" in t:
                    trial_ids_in_result.append(str(t["trial_id"]))

        # Also check nested data.data for tools that wrap results
        nested = data.get("data", {})
        if isinstance(nested, dict):
            if "trial_id" in nested and nested["trial_id"]:
                trial_ids_in_result.append(str(nested["trial_id"]))

        for tid in trial_ids_in_result:
            if tid not in seen_ids and tid:
                seen_ids.add(tid)
                meta = trial_metadata.get(tid, {})
                scope = trial_scopes.get(tid, {})
                sources.append(QuerySource(
                    trial_id=tid,
                    nct_id=meta.get("nct_id", ""),
                    title=meta.get("title", ""),
                ))

    return sources


def _extract_queried_trial_ids(records: list[dict]) -> list[str]:
    """
    Collect all trial IDs that were actually passed to tool calls.
    Handles both trial_ids (list) and trial_id (string) tool args.
    Only keeps valid IDs (length > 8 to filter out index numbers like '3').
    """
    ids: set[str] = set()

    for record in records:
        args = record.get("args", {})

        # Handle trial_ids (list) — most patient analytics tools
        raw_list = args.get("trial_ids", [])
        if isinstance(raw_list, list):
            for tid in raw_list:
                tid_str = str(tid).strip()
                if len(tid_str) > 8:  # Filter out garbage like "3" or "all"
                    ids.add(tid_str)
        elif isinstance(raw_list, str) and len(raw_list) > 8:
            ids.add(raw_list.strip())

        # Handle trial_id (string) — metadata tools like get_trial_details
        single = args.get("trial_id", "")
        if single and len(str(single).strip()) > 8:
            ids.add(str(single).strip())

    return list(ids)


class _ProfileDictProxy:
    """
    Minimal proxy over the access_profile_dict stored in AgentState.
    Satisfies describe_filters() and the local _determine_access_level().

    FIXED: Now exposes trial_metadata for NCT ID reverse lookup in
    _determine_access_level so NCT IDs resolve to correct access levels.
    """

    def __init__(self, d: dict):
        self._d = d

    @property
    def trial_scopes(self) -> dict:
        """Returns trial scopes as SimpleNamespace objects for attribute access."""
        scopes = self._d.get("trial_scopes", {})
        from types import SimpleNamespace
        wrapped = {}
        for k, v in scopes.items():
            if isinstance(v, dict):
                cohorts = [
                    SimpleNamespace(**cs)
                    for cs in v.get("cohort_scopes", [])
                ]
                v_copy = dict(v)
                v_copy["cohort_scopes"] = cohorts
                wrapped[k] = SimpleNamespace(**v_copy)
            else:
                wrapped[k] = v
        return wrapped

    @property
    def trial_metadata(self) -> dict:
        """
        Maps UUID → {nct_id, title} for NCT ID reverse lookup.
        FIXED: Was missing entirely, causing NCT IDs to never resolve.
        """
        return self._d.get("trial_metadata", {})

    @property
    def allowed_trial_ids(self) -> list[str]:
        return self._d.get("allowed_trial_ids", [])