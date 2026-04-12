"""
Phase 6: Context-aware suggested question generator.

Generates question chips for the frontend based on the researcher's
actual trial access — individual vs. aggregate, therapeutic areas,
phases, and active cohort filters.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from auth.authorization_service import AccessProfile


@dataclass
class SuggestedQuestion:
    text: str
    category: str          # "demographics", "safety", "efficacy", "comparison", "documents"
    trial_ids: list[str]   # Pre-scoped trial IDs for this question
    access_level: str      # "individual" or "aggregate"


# Maximum questions to return
MAX_SUGGESTIONS = 8


def generate_suggested_questions(
    access_profile: AccessProfile,
    trial_metadata: list[dict],   # [{trial_id, nct_id, title, phase, therapeutic_area}, ...]
) -> list[SuggestedQuestion]:
    """
    Generates up to MAX_SUGGESTIONS contextual question suggestions.

    Strategy:
    1. Individual-access trials → rich patient-level questions
    2. Aggregate-only trials → count/rate-based questions
    3. Multi-trial access → cross-trial comparison questions
    4. Condition/area-specific questions from trial metadata
    5. Cohort-filter-aware questions when filters are active
    """
    suggestions: list[SuggestedQuestion] = []
    trial_map = {t["trial_id"]: t for t in trial_metadata}

    individual_ids = set(access_profile.individual_trial_ids)
    aggregate_ids  = set(access_profile.aggregate_trial_ids) - individual_ids

    # -----------------------------------------------------------------------
    # Per-trial questions
    # -----------------------------------------------------------------------
    for trial_id, scope in access_profile.trial_scopes.items():
        meta = trial_map.get(trial_id, {})
        nct_id = meta.get("nct_id", "this trial")
        area   = meta.get("therapeutic_area", "")
        phase  = meta.get("phase", "")

        if trial_id in individual_ids:
            # Individual access — patient-level questions
            cohort_suffix = ""
            if scope.cohort_scopes:
                cohort_name = scope.cohort_scopes[0].cohort_name
                cohort_suffix = f" in the {cohort_name} cohort"

            suggestions.extend([
                SuggestedQuestion(
                    text=f"What are the demographics of patients enrolled in {nct_id}{cohort_suffix}?",
                    category="demographics",
                    trial_ids=[trial_id],
                    access_level="individual",
                ),
                SuggestedQuestion(
                    text=f"What adverse events occurred in {nct_id}{cohort_suffix}?",
                    category="safety",
                    trial_ids=[trial_id],
                    access_level="individual",
                ),
                SuggestedQuestion(
                    text=f"Compare treatment arms in {nct_id} by completion rate and adverse events.",
                    category="efficacy",
                    trial_ids=[trial_id],
                    access_level="individual",
                ),
            ])

            # Cohort-specific questions
            for cohort_scope in scope.cohort_scopes:
                fc = cohort_scope.filter_criteria
                if fc.get("ethnicity"):
                    eth = ", ".join(fc["ethnicity"])
                    suggestions.append(SuggestedQuestion(
                        text=f"How many {eth} patients completed {nct_id}?",
                        category="demographics",
                        trial_ids=[trial_id],
                        access_level="individual",
                    ))
                if fc.get("conditions"):
                    cond = fc["conditions"][0]
                    suggestions.append(SuggestedQuestion(
                        text=f"What lab results are available for {cond} patients in {nct_id}?",
                        category="efficacy",
                        trial_ids=[trial_id],
                        access_level="individual",
                    ))

        else:
            # Aggregate-only — count and rate questions
            suggestions.extend([
                SuggestedQuestion(
                    text=f"How many patients are enrolled in {nct_id}?",
                    category="demographics",
                    trial_ids=[trial_id],
                    access_level="aggregate",
                ),
                SuggestedQuestion(
                    text=f"What is the completion rate for {nct_id}?",
                    category="efficacy",
                    trial_ids=[trial_id],
                    access_level="aggregate",
                ),
                SuggestedQuestion(
                    text=f"What serious adverse events have been reported in {nct_id}?",
                    category="safety",
                    trial_ids=[trial_id],
                    access_level="aggregate",
                ),
            ])

        # Area-specific questions
        if area:
            suggestions.append(SuggestedQuestion(
                text=f"What drugs are being studied in the {area} trial {nct_id}?",
                category="documents",
                trial_ids=[trial_id],
                access_level=scope.access_level,
            ))

    # -----------------------------------------------------------------------
    # Cross-trial comparison questions (if multiple trials)
    # -----------------------------------------------------------------------
    all_ids = access_profile.allowed_trial_ids
    if len(all_ids) >= 2:
        suggestions.append(SuggestedQuestion(
            text="Compare adverse event rates across all my trials.",
            category="comparison",
            trial_ids=all_ids,
            access_level="aggregate",
        ))
        suggestions.append(SuggestedQuestion(
            text="What conditions are studied across all my authorized trials?",
            category="comparison",
            trial_ids=all_ids,
            access_level="aggregate",
        ))

    if len(individual_ids) >= 2:
        ind_ids = list(individual_ids)[:3]
        suggestions.append(SuggestedQuestion(
            text="Compare patient demographics across my individual-access trials.",
            category="comparison",
            trial_ids=ind_ids,
            access_level="individual",
        ))

    # -----------------------------------------------------------------------
    # Document search question
    # -----------------------------------------------------------------------
    if all_ids:
        suggestions.append(SuggestedQuestion(
            text="What are the eligibility criteria for my trials?",
            category="documents",
            trial_ids=all_ids,
            access_level="aggregate",
        ))

    # -----------------------------------------------------------------------
    # Deduplicate, shuffle, and cap
    # -----------------------------------------------------------------------
    seen: set[str] = set()
    unique: list[SuggestedQuestion] = []
    for s in suggestions:
        if s.text not in seen:
            seen.add(s.text)
            unique.append(s)

    random.shuffle(unique)
    return unique[:MAX_SUGGESTIONS]