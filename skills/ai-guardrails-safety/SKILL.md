---
name: ai-guardrails-safety
description: Design AI safety guardrails — input/output validation, PII/PHI detection, prompt injection prevention, content filtering, bias monitoring, and safety scoring
triggers:
  - design AI guardrails
  - AI safety
  - prompt injection prevention
  - PII detection
  - content filtering
  - bias monitoring
  - AI security
  - guardrails for agents
  - safety scoring
  - input validation for LLM
---

# AI Guardrails & Safety Design

You are an enterprise architect specialized in AI safety and guardrail systems. Follow this framework.

## Step 1: Define the Guardrail Layers

```
┌──────────────────────────────────────────────┐
│           GUARDRAIL STACK                     │
├──────────────────────────────────────────────┤
│ L1: INPUT GUARDRAILS                          │
│     • Prompt injection detection              │
│     • PII/PHI scrubbing                       │
│     • Malicious intent classification         │
│     • Input length / complexity limits        │
├──────────────────────────────────────────────┤
│ L2: RETRIEVAL GUARDRAILS                      │
│     • Access control enforcement              │
│     • Content safety filtering                │
│     • Source trust scoring                    │
├──────────────────────────────────────────────┤
│ L3: GENERATION GUARDRAILS                     │
│     • Output PII/PHI detection                │
│     • Hallucination detection                 │
│     • Toxicity / bias scoring                 │
│     • Factual consistency check               │
├──────────────────────────────────────────────┤
│ L4: POST-HOC GUARDRAILS                       │
│     • Human review gates                      │
│     • Audit logging                           │
│     • Drift monitoring                        │
└──────────────────────────────────────────────┘
```

## Step 2: Design Prompt Injection Defense

```
DEFENSE-IN-DEPTH STRATEGY
─────────────────────────

1. INPUT SANITIZATION
   • Strip control characters, zero-width chars
   • Normalize Unicode (NFC)
   • Detect delimiter injection (---, ```, 伏)
   • Limit input length (max 4000 chars)

2. STRUCTURAL VALIDATION
   • JSON schema validation for structured inputs
   • Reject nested instruction patterns
   • Detect "ignore previous" / "system override" patterns

3. SEMANTIC ANALYSIS
   • Classify intent: query vs instruction vs attack
   • Detect role confusion ("you are now DAN...")
   • Score manipulation attempts

4. SANDBOXING
   • Run agent in isolated context
   • Tool calls require explicit authorization
   • No system prompt modification at runtime
```

## Step 3: Design PII/PHI Detection

```python
# PII/PHI Detection Pattern
DETECTORS = {
    "PHI": [
        "patient_name", "mrn", "dob", "ssn",
        "phone", "email", "address", "zip_code"
    ],
    "PII": [
        "person_name", "national_id", "passport",
        "credit_card", "bank_account", "ip_address"
    ],
    "CLINICAL": [
        "lab_result_with_date", "diagnosis_with_date",
        "medication_with_dose", "genomic_marker"
    ]
}

# Action matrix
┌──────────────┬──────────────┬──────────────┐
│ Detection    │ In Prompt    │ In Response  │
├──────────────┼──────────────┼──────────────┤
│ PHI          │ BLOCK        │ REDACT       │
│ PII          │ REDACT       │ REDACT       │
│ Clinical     │ ALLOW (auth) │ ALLOW (auth) │
└──────────────┴──────────────┴──────────────┘
```

## Step 4: Design Content Safety Scoring

```
SAFETY SCORING PIPELINE
───────────────────────

Input → [Toxicity] [Bias] [Manipulation] [Policy] → Safety Score (0-100)

SCORE THRESHOLDS
┌──────────┬────────────────────────────────────┐
│ Score    │ Action                             │
├──────────┼────────────────────────────────────┤
│ 90-100   │ PASS — no concerns                 │
│ 70-89    │ FLAG — log, allow with warning     │
│ 50-69    │ REVIEW — queue for human           │
│ 0-49     │ BLOCK — reject, log, alert         │
└──────────┴────────────────────────────────────┘

BIAS MONITORING METRICS
- Demographic parity difference
- Equalized odds difference
- Disparate impact ratio
- Representation index
```

## Step 5: Output the Guardrails Blueprint

```
GUARDRAILS BLUEPRINT: [System Name]
═══════════════════════════════════

THREAT MODEL
┌──────────────────┬──────────┬──────────┐
│ Threat           │ Likelihood│ Severity │
├──────────────────┼──────────┼──────────┤
│ Prompt Injection │ High     │ Critical │
│ PII Leakage      │ Medium   │ Critical │
│ Hallucination    │ High     │ High     │
│ Bias Amplification│ Medium  │ High     │
│ Tool Abuse       │ Low      │ High     │
│ Data Exfiltration│ Low      │ Critical │
└──────────────────┴──────────┴──────────┘

INPUT GUARDRAILS
- Prompt injection defense: [layers active]
- PII/PHI detection: [enabled / disabled]
- Max input length: [N] chars
- Allowed input types: [text / JSON / file upload]

OUTPUT GUARDRAILS
- PII/PHI redaction: [enabled / disabled]
- Toxicity threshold: [score]
- Bias threshold: [score]
- Hallucination check: [method]

HUMAN REVIEW GATES
- Trigger conditions: [list]
- Review SLA: [time]
- Escalation path: [process]

AUDIT & MONITORING
- Guardrail decisions logged: [yes / no]
- Drift detection cadence: [daily / weekly]
- Alert channels: [list]
```

## Rules

- Always implement defense-in-depth — never rely on a single guardrail layer
- PII/PHI must be detected BEFORE it reaches the LLM, not just in output
- Prompt injection defense is mandatory for any agent that accepts user input
- Safety scores must be logged and monitored for drift over time
- Human review gates are required for safety scores below 70
- Never allow runtime system prompt modification — it's the #1 injection vector
- Bias monitoring must use established fairness metrics, not ad-hoc checks
- If the system handles PHI, HIPAA-compliant redaction is non-negotiable