---
name: agent-evaluation-observability
description: Design evaluation frameworks and observability for AI agents — metrics, tracing, logging, dashboards, A/B testing, and continuous improvement pipelines
triggers:
  - evaluate agents
  - agent observability
  - agent metrics
  - LLM tracing
  - agent dashboard
  - A/B test agents
  - agent performance
  - token monitoring
  - agent logging
  - agent evaluation framework
---

# Agent Evaluation & Observability

You are an enterprise architect specialized in AI agent evaluation and observability. Follow this framework.

## Step 1: Define the Evaluation Dimensions

```
┌──────────────────────────────────────────────┐
│         AGENT EVALUATION FRAMEWORK            │
├──────────────────────────────────────────────┤
│ D1: ACCURACY & QUALITY                        │
│     • Task success rate                       │
│     • Answer correctness (human eval)         │
│     • Factual consistency (citation match)    │
│     • Completeness score                      │
├──────────────────────────────────────────────┤
│ D2: EFFICIENCY & COST                         │
│     • Tokens per task (prompt + completion)   │
│     • Tool calls per task                     │
│     • Latency (P50, P95, P99)                 │
│     • Cost per task ($)                       │
├──────────────────────────────────────────────┤
│ D3: SAFETY & COMPLIANCE                       │
│     • Guardrail trigger rate                  │
│     • PII/PHI exposure events                 │
│     • Policy violation rate                   │
│     • Human review escalation rate            │
├──────────────────────────────────────────────┤
│ D4: RELIABILITY & ROBUSTNESS                  │
│     • Error rate (tool failures, timeouts)    │
│     • Recovery rate (self-correction)         │
│     • Degradation under load                  │
│     • Adversarial robustness score            │
└──────────────────────────────────────────────┘
```

## Step 2: Design the Tracing Architecture

```
TRACE STRUCTURE (per task)
═══════════════════════════

trace_id: uuid
session_id: uuid
user_id: string
task_type: "search_trials" | "safety_summary" | ...

SPANS
├─ [1] user_query_received
│   ├─ input_tokens: 450
│   └─ guardrail_score: 95
├─ [2] tool_selection
│   ├─ tools_considered: ["search_trials", "get_trial_details"]
│   └─ tools_selected: ["search_trials"]
├─ [3] tool_execution: search_trials
│   ├─ latency_ms: 320
│   ├─ input_params: {query: "...", limit: 10}
│   ├─ result_count: 8
│   └─ semantic_context_tokens: 180
├─ [4] llm_generation
│   ├─ model: "deepseek-chat"
│   ├─ prompt_tokens: 2340
│   ├─ completion_tokens: 560
│   ├─ latency_ms: 1200
│   └─ finish_reason: "stop"
├─ [5] output_guardrail
│   ├─ toxicity_score: 98
│   ├─ bias_score: 92
│   └─ pii_detected: false
└─ [6] response_delivered
    └─ total_latency_ms: 1840

AGGREGATED METRICS
- tokens_total: 3350
- cost_estimate: $0.0023
- tool_success_rate: 1.0
- safety_score: 95
```

## Step 3: Design the Dashboard

```
OBSERVABILITY DASHBOARD LAYOUT
══════════════════════════════

ROW 1: HIGH-LEVEL KPIs
┌──────────┬──────────┬──────────┬──────────┐
│ Tasks/hr │ Success% │ Avg Tok  │ Avg Cost │
│   1,245  │  94.3%   │  3,200   │ $0.0018  │
└──────────┴──────────┴──────────┴──────────┘

ROW 2: LATENCY & ERRORS
┌────────────────────────────────────────────┐
│ Latency Distribution (P50/P95/P99)          │
│ ████████░░░░░░░░░░░░░░░░░░░░  P50: 1.2s   │
│ ████████████████░░░░░░░░░░░░  P95: 3.8s   │
│ ████████████████████████░░░░  P99: 7.2s   │
├────────────────────────────────────────────┤
│ Error Breakdown                            │
│ Tool Timeout: 2.1% | Auth Fail: 0.3%       │
│ Guardrail Block: 1.4% | Model Err: 0.8%    │
└────────────────────────────────────────────┘

ROW 3: TOKEN EFFICIENCY
┌────────────────────────────────────────────┐
│ Token Composition by Task Type             │
│ search_trials:     ████████ 2,800 avg      │
│ safety_summary:    ██████████████ 5,200 avg│
│ patient_lookup:    ████ 1,400 avg          │
├────────────────────────────────────────────┤
│ Semantic Context Overhead                  │
│ Current: 180 tokens/response (5.6%)        │
│ Target: 50 tokens/response (1.5%)          │
└────────────────────────────────────────────┘

ROW 4: SAFETY & GUARDRAILS
┌────────────────────────────────────────────┐
│ Guardrail Trigger Rate (24h)               │
│ Prompt Injection:  0.02% ▏                 │
│ PII Detected:      0.15% ▎                 │
│ Toxicity Flag:     0.08% ▏                 │
│ Bias Flag:         0.45% ▍                 │
│ Human Review:      1.20% █                 │
└────────────────────────────────────────────┘
```

## Step 4: Design A/B Testing for Agents

```
A/B TESTING FRAMEWORK
─────────────────────

VARIANT A (Control)          VARIANT B (Treatment)
- Model: deepseek-chat       - Model: deepseek-chat
- Semantic: full context     - Semantic: minimal context
- Tools: all registered      - Tools: lazy-loaded
- Chunk size: 500 tokens     - Chunk size: 300 tokens

METRICS COMPARED
┌──────────────────┬─────────┬─────────┬────────┐
│ Metric           │ A       │ B       │ Δ      │
├──────────────────┼─────────┼─────────┼────────┤
│ Task Success     │ 94.3%   │ 93.8%   │ -0.5%  │
│ Avg Tokens       │ 3,200   │ 2,100   │ -34%   │
│ Avg Cost         │ $0.0018 │ $0.0012 │ -33%   │
│ P95 Latency      │ 3.8s    │ 2.9s    │ -24%   │
│ User Satisfaction│ 4.2/5   │ 4.1/5   │ -0.1   │
└──────────────────┴─────────┴─────────┴────────┘

DECISION: Promote B if success stays within 2% of A
          AND cost/latency improvement > 20%
```

## Step 5: Output the Observability Blueprint

```
OBSERVABILITY BLUEPRINT: [System Name]
═══════════════════════════════════════

TRACING
- Framework: [OpenTelemetry / LangSmith / Custom]
- Span types: [list]
- Sampling rate: [100% / 10% / adaptive]
- Retention: [N days]

METRICS STORE
- Engine: [Prometheus / Datadog / CloudWatch / Custom]
- Key metrics: [list with targets]
- Alert thresholds: [list]

LOGGING
- Level: [DEBUG / INFO / WARN / ERROR]
- Structured: [JSON / Logfmt]
- PII in logs: [NEVER / Redacted / Allowed with auth]
- Retention: [N days]

DASHBOARD
- Tool: [Grafana / Datadog / Custom]
- Refresh: [real-time / 1min / 5min]
- Audiences: [Engineering / Product / Compliance]

EVALUATION CADENCE
- Automated eval: [per-task / daily / weekly]
- Human eval: [weekly / monthly / quarterly]
- A/B test duration: [N days minimum]
- Statistical significance: [p < 0.05]

ALERTS
┌──────────────────────┬──────────┬──────────┐
│ Condition            │ Threshold │ Channel  │
├──────────────────────┼──────────┼──────────┤
│ Success rate drop    │ <90%     │ PagerDuty│
│ P95 latency spike    │ >5s      │ Slack    │
│ Guardrail block spike│ >5%      │ Slack    │
│ Cost anomaly         │ >2x avg  │ Email    │
└──────────────────────┴──────────┴──────────┘
```

## Rules

- Every agent task must produce a trace — no untraced executions
- Token counting is mandatory: prompt tokens, completion tokens, and semantic overhead separately
- PII/PHI must never appear in logs or traces — redact before writing
- Dashboard must show token composition, not just totals — semantic overhead is a key metric
- A/B tests must run for at least N=100 tasks per variant before deciding
- Human evaluation is required for accuracy metrics; automated metrics are proxies
- Alert on guardrail trigger rate changes — spikes indicate attacks or model drift
- Cost per task must be tracked and budgeted; token waste is real money at scale