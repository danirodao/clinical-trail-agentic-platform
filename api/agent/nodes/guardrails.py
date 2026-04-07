"""
Guardrails node — first node in the LangGraph workflow.

Responsibilities:
  1. Validate that the researcher has ANY authorized trial access
  2. Apply optional trial_ids scoping (subset of authorized trials)
  3. Classify query complexity → select LLM model
  4. Build access_context_json for tool injection
  5. Construct the system message with access summary
  6. Initialize iteration counter and token tracking

If access validation fails, this node sets final_response directly,
causing the graph to route to the synthesizer immediately (no LLM call).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import SystemMessage

from ..access_context import (
    build_access_summary_for_prompt,
    describe_filters,
    serialize_access_profile,
)
from ..config import agent_config
from ..models import AgentState, QueryMetadata, QueryResponse
from ..observability import ACCESS_DENIED_COUNT
from ..prompts import build_system_prompt, classify_query_complexity

logger = logging.getLogger(__name__)


def guardrails_node(state: AgentState) -> dict:
    """
    Pre-flight validation and state initialization.

    Returns a state update dict. If access is denied, sets final_response
    so the graph terminates without calling the LLM.
    """
    # ── Deserialize access profile dict back to a usable object ──────────────
    # We store it as a plain dict in AgentState (dataclasses aren't JSON-serializable
    # in LangGraph's checkpointer). The _AccessProfileProxy wraps it for attribute access.
    profile = _AccessProfileProxy(state["access_profile_dict"])

    # ── Guard 1: Basic access check ───────────────────────────────────────────
    if not profile.has_any_access:
        logger.warning(f"Access denied: user has no authorized trials")
        ACCESS_DENIED_COUNT.labels(reason="no_access").inc()
        return {
            "final_response": _denied_response("You do not have access to any clinical trials. "
                                                "Please contact your manager to request access."),
            "iteration_count": 0,
            "tool_call_records": [],
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
        }

    # ── Guard 2: Scope trial_ids if provided ──────────────────────────────────
    # The request may include a subset of trial IDs; validate each is authorized
    requested_ids: list[str] | None = state.get("requested_trial_ids")
    if requested_ids:
        unauthorized = [tid for tid in requested_ids if tid not in profile.allowed_trial_ids]
        if unauthorized:
            logger.warning(f"Scope request includes unauthorized trials: {unauthorized}")
            ACCESS_DENIED_COUNT.labels(reason="unauthorized_trial_scope").inc()
            return {
                "final_response": _denied_response(
                    f"The following trial IDs are not in your access profile: "
                    f"{unauthorized}. Please select only trials you are authorized to access."
                ),
                "iteration_count": 0,
                "tool_call_records": [],
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
            }

        # Rebuild profile scoped to requested trials
        profile = _AccessProfileProxy(
            state["access_profile_dict"],
            scoped_trial_ids=requested_ids,
        )

    # ── Guard 3: Classify complexity, select model ────────────────────────────
    user_query = state["user_query"]
    complexity = classify_query_complexity(user_query, agent_config)
    model_name = (
        agent_config.complex_model if complexity == "complex"
        else agent_config.simple_model
    )
    max_iterations = (
        agent_config.max_iterations if complexity == "complex"
        else agent_config.simple_query_max_iterations
    )

    logger.info(
        f"Query classified: complexity={complexity}, model={model_name}, "
        f"trials={len(profile.allowed_trial_ids)}, "
        f"individual={len(profile.individual_trial_ids)}"
    )

    # ── Build access context JSON for tool injection ──────────────────────────
    access_context_json = serialize_access_profile(profile)

    # ── Build system message ──────────────────────────────────────────────────
    system_prompt = build_system_prompt(profile, complexity)
    system_message = SystemMessage(content=system_prompt)

    return {
        "messages": [system_message],
        "access_context_json": access_context_json,
        "query_complexity": complexity,
        "model_name": model_name,
        "max_iterations": max_iterations,
        "iteration_count": 0,
        "tool_call_records": [],
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "final_response": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _denied_response(reason: str) -> dict:
    """Build a QueryResponse dict for access-denied scenarios."""
    return QueryResponse(
        answer=reason,
        sources=[],
        tool_calls=[],
        access_level_applied="none",
        filters_applied=[],
        metadata=QueryMetadata(
            model_used="none",
            total_tokens=0,
            prompt_tokens=0,
            completion_tokens=0,
            duration_ms=0,
            iteration_count=0,
        ),
        error="access_denied",
    ).model_dump()


class _AccessProfileProxy:
    """
    Thin wrapper that provides attribute-style access to the serialized
    access profile dict stored in AgentState.

    This avoids needing to import the AccessProfile dataclass (which lives
    in auth/ and has asyncpg dependencies) into the agent package.
    """

    def __init__(self, d: dict, scoped_trial_ids: list[str] | None = None):
        self._d = d
        self._scoped = set(scoped_trial_ids) if scoped_trial_ids else None

    def _filter(self, ids: list[str]) -> list[str]:
        if self._scoped is None:
            return ids
        return [i for i in ids if i in self._scoped]

    @property
    def has_any_access(self) -> bool:
        return bool(self._d.get("has_any_access")) and bool(self.allowed_trial_ids)

    @property
    def allowed_trial_ids(self) -> list[str]:
        return self._filter(self._d.get("allowed_trial_ids", []))

    @property
    def individual_trial_ids(self) -> list[str]:
        return self._filter(self._d.get("individual_trial_ids", []))

    @property
    def aggregate_trial_ids(self) -> list[str]:
        return self._filter(self._d.get("aggregate_trial_ids", []))

    @property
    def trial_scopes(self) -> dict:
        scopes = self._d.get("trial_scopes", {})
        if self._scoped:
            scopes = {k: v for k, v in scopes.items() if k in self._scoped}
            
        from types import SimpleNamespace
        wrapped = {}
        for k, v in scopes.items():
            if isinstance(v, dict):
                cohorts = [SimpleNamespace(**cs) for cs in v.get("cohort_scopes", [])]
                v_copy = dict(v)
                v_copy["cohort_scopes"] = cohorts
                wrapped[k] = SimpleNamespace(**v_copy)
            else:
                wrapped[k] = v
        return wrapped

    # Passthrough for serialize_access_profile / build_access_summary_for_prompt
    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        val = self._d.get(name)
        if val is None:
            raise AttributeError(f"AccessProfileProxy has no attribute '{name}'")
        return val