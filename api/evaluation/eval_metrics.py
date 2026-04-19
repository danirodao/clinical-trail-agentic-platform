"""
Prometheus metric definitions for the evaluation framework.

These gauges are updated after each evaluation run (on-demand or scheduled).
They are served on the existing /metrics endpoint alongside the operational
metrics from api.metrics and agent.observability, sharing the same default
prometheus_client.REGISTRY — no extra scrape target needed.

Naming convention:
    eval_<metric>_score   — quality scores (gauges, 0.0–1.0)
    eval_run_*            — run metadata (counters, histograms)
    eval_cases_*          — dataset metadata (gauges)
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram


# ══════════════════════════════════════════════════════════════════════════════
# Tier 1 — Core Quality Scores (updated per evaluation run)
# ══════════════════════════════════════════════════════════════════════════════

EVAL_FAITHFULNESS_SCORE = Gauge(
    "eval_faithfulness_score",
    "Latest aggregate faithfulness score across the golden dataset",
    ["dataset_version", "layer"],  # layer: agent | mcp
)

EVAL_ANSWER_RELEVANCY_SCORE = Gauge(
    "eval_answer_relevancy_score",
    "Latest aggregate answer relevancy score",
    ["dataset_version", "layer"],
)

EVAL_HALLUCINATION_SCORE = Gauge(
    "eval_hallucination_score",
    "Latest aggregate hallucination score (lower is better)",
    ["dataset_version", "layer"],
)

EVAL_CONTEXTUAL_RELEVANCY_SCORE = Gauge(
    "eval_contextual_relevancy_score",
    "Latest contextual relevancy score for retrieval quality",
    ["dataset_version", "layer"],
)

# ══════════════════════════════════════════════════════════════════════════════
# Tier 2 — Clinical Domain Scores
# ══════════════════════════════════════════════════════════════════════════════

EVAL_CLINICAL_SAFETY_SCORE = Gauge(
    "eval_clinical_safety_score",
    "Clinical safety score — appropriate disclaimers, no harmful advice",
    ["dataset_version", "layer"],
)

EVAL_ACCESS_COMPLIANCE_SCORE = Gauge(
    "eval_access_compliance_score",
    "Access compliance — did the agent respect access levels?",
    ["dataset_version", "layer"],
)

EVAL_TOOL_CORRECTNESS_SCORE = Gauge(
    "eval_tool_correctness_score",
    "Tool call correctness — right tools for the query type",
    ["dataset_version", "layer"],
)

# ══════════════════════════════════════════════════════════════════════════════
# Tier 3 — Safety & Governance
# ══════════════════════════════════════════════════════════════════════════════

EVAL_TOXICITY_SCORE = Gauge(
    "eval_toxicity_score",
    "Toxicity score — presence of harmful language (lower is better)",
    ["dataset_version", "layer"],
)

EVAL_BIAS_SCORE = Gauge(
    "eval_bias_score",
    "Bias score — demographic bias detection (lower is better)",
    ["dataset_version", "layer"],
)

EVAL_PROMPT_INJECTION_RESISTANCE = Gauge(
    "eval_prompt_injection_resistance_score",
    "Prompt injection resistance score",
    ["dataset_version", "layer"],
)

# ══════════════════════════════════════════════════════════════════════════════
# Run Metadata
# ══════════════════════════════════════════════════════════════════════════════

EVAL_PASS_RATE = Gauge(
    "eval_pass_rate",
    "Percentage of test cases passing all configured thresholds (0.0–1.0)",
    ["dataset_version", "layer"],
)

EVAL_CASES_TOTAL = Gauge(
    "eval_cases_total",
    "Total number of cases in the golden dataset",
    ["dataset_version", "layer"],
)

EVAL_CASES_FAILED = Gauge(
    "eval_cases_failed",
    "Number of cases that failed one or more thresholds",
    ["dataset_version", "layer"],
)

EVAL_RUN_TOTAL = Counter(
    "eval_run_total",
    "Total evaluation runs completed",
    ["trigger", "layer"],  # trigger: api | schedule | ci
)

EVAL_RUN_DURATION = Histogram(
    "eval_run_duration_seconds",
    "Wall-clock duration of a full evaluation run",
    ["layer"],
    buckets=[10, 30, 60, 120, 300, 600, 1200],
)


# ── Helper: update all gauges from an evaluation result dict ─────────────────

def publish_eval_metrics(
    results: dict[str, float],
    dataset_version: str,
    layer: str,
    pass_rate: float,
    total_cases: int,
    failed_cases: int,
) -> None:
    """
    Publish evaluation scores to Prometheus gauges.

    Args:
        results:         Dict of metric_name → score (0.0–1.0)
        dataset_version: Version string for the golden dataset
        layer:           'agent' or 'mcp'
        pass_rate:       Fraction of cases that passed all thresholds
        total_cases:     Total number of test cases
        failed_cases:    Number of cases that failed
    """
    labels = {"dataset_version": dataset_version, "layer": layer}

    _METRIC_MAP = {
        "faithfulness":              EVAL_FAITHFULNESS_SCORE,
        "answer_relevancy":          EVAL_ANSWER_RELEVANCY_SCORE,
        "hallucination":             EVAL_HALLUCINATION_SCORE,
        "contextual_relevancy":      EVAL_CONTEXTUAL_RELEVANCY_SCORE,
        "clinical_safety":           EVAL_CLINICAL_SAFETY_SCORE,
        "access_compliance":         EVAL_ACCESS_COMPLIANCE_SCORE,
        "tool_correctness":          EVAL_TOOL_CORRECTNESS_SCORE,
        "toxicity":                  EVAL_TOXICITY_SCORE,
        "bias":                      EVAL_BIAS_SCORE,
        "prompt_injection_resistance": EVAL_PROMPT_INJECTION_RESISTANCE,
    }

    for metric_name, score in results.items():
        gauge = _METRIC_MAP.get(metric_name)
        if gauge is not None and score is not None:
            gauge.labels(**labels).set(score)

    EVAL_PASS_RATE.labels(**labels).set(pass_rate)
    EVAL_CASES_TOTAL.labels(**labels).set(total_cases)
    EVAL_CASES_FAILED.labels(**labels).set(failed_cases)
