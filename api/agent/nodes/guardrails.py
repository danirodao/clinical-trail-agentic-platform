"""
Guardrails node — first node in the LangGraph workflow.

Responsibilities:
  - Validate that the researcher has ANY authorized trial access
  - Apply optional trial_ids scoping (subset of authorized trials)
  - Classify query complexity → select LLM model
  - Build access_context_json for tool injection
  - Construct the system message with access summary
  - Initialize iteration counter and token tracking

Phase 5 additions:
  - OTel span wrapping the full node
  - AGENT_ACCESS_DENIED_TOTAL counter with reason label
  - Structured debug log on success path
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from langchain_core.messages import SystemMessage
from opentelemetry import trace

from ..observability import AGENT_ACCESS_DENIED_TOTAL, get_tracer
from ..access_context import (
    build_access_summary_for_prompt,
    describe_filters,
    serialize_access_profile,
)
from ..config import agent_config
from ..models import AgentState, QueryMetadata, QueryResponse
from ..prompts import build_system_prompt, classify_query_complexity

logger = logging.getLogger(__name__)
tracer = get_tracer()


def guardrails_node(state: AgentState) -> dict:
    """
    Pre-flight validation and state initialization.

    Returns a state update dict. If access is denied, sets final_response
    so the graph routes to the synthesizer immediately (no LLM call).
    """
    with tracer.start_as_current_span("guardrails") as span:
        profile = _AccessProfileProxy(state["access_profile_dict"])
        user_query: str = state.get("user_query", "")

        span.set_attribute("user.id", profile._d.get("user_id", "unknown"))
        span.set_attribute("user.org", profile._d.get("organization_id", "unknown"))
        span.set_attribute("query.length", len(user_query))
        span.set_attribute("trials.total", len(profile.allowed_trial_ids))

        # ── Guard 1: Basic access check ───────────────────────────────────
        if not profile.has_any_access:
            logger.warning("Access denied: user has no authorized trials")
            AGENT_ACCESS_DENIED_TOTAL.labels(reason="no_access").inc()
            span.set_attribute("guardrails.result", "denied")
            span.set_attribute("guardrails.reason", "no_access")
            return {
                "final_response": _denied_response(
                    "You do not have access to any clinical trials. "
                    "Please contact your manager to request access."
                ),
                "iteration_count": 0,
                "tool_call_records": [],
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
            }

        # ── Guard 2: Scope trial_ids if provided ──────────────────────────
        requested_ids: list[str] | None = state.get("requested_trial_ids")
        if requested_ids:
            unauthorized = [
                tid for tid in requested_ids
                if tid not in profile.allowed_trial_ids
            ]
            if unauthorized:
                logger.warning(
                    f"Scope request includes unauthorized trials: {unauthorized}"
                )
                AGENT_ACCESS_DENIED_TOTAL.labels(
                    reason="unauthorized_trial_scope"
                ).inc()
                span.set_attribute("guardrails.result", "denied")
                span.set_attribute("guardrails.reason", "unauthorized_trial_scope")
                return {
                    "final_response": _denied_response(
                        f"The following trial IDs are not in your access profile: "
                        f"{unauthorized}. Please select only trials you are "
                        "authorized to access."
                    ),
                    "iteration_count": 0,
                    "tool_call_records": [],
                    "total_prompt_tokens": 0,
                    "total_completion_tokens": 0,
                }

            # Rebuild profile scoped to the requested subset
            profile = _AccessProfileProxy(
                state["access_profile_dict"],
                scoped_trial_ids=requested_ids,
            )

        # ── Guard 3: Classify complexity, select model ────────────────────
        complexity = classify_query_complexity(user_query, agent_config)
        model_name = (
            agent_config.complex_model
            if complexity == "complex"
            else agent_config.simple_model
        )
        max_iterations = (
            agent_config.max_iterations
            if complexity == "complex"
            else agent_config.simple_query_max_iterations
        )

        span.set_attribute("query.complexity", complexity)
        span.set_attribute("llm.model", model_name)
        span.set_attribute("guardrails.result", "passed")

        logger.info(
            f"Query classified: complexity={complexity}, model={model_name}, "
            f"trials={len(profile.allowed_trial_ids)}, "
            f"individual={len(profile.individual_trial_ids)}"
        )

        # ── Build access context JSON for tool injection ──────────────────
        access_context_json = serialize_access_profile(profile)

        # ── Build system message ──────────────────────────────────────────
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

    Avoids needing to import the AccessProfile dataclass (which lives in
    auth/ and has asyncpg dependencies) into the agent package.
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

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        val = self._d.get(name)
        if val is None:
            raise AttributeError(f"AccessProfileProxy has no attribute '{name}'")
        return val