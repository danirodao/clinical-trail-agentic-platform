# Tradeoff Framework Reference

Architecture is fundamentally about **managing tradeoffs**. There is no perfect architecture — only architectures that are better or worse fits for a specific context. This framework ensures tradeoffs are made explicitly, not accidentally.

---

## The Golden Rule

> **Every architectural advantage purchases a disadvantage elsewhere.**
> Your job is to make the right trade for the context — and to make it consciously.

---

## Core Quality Attributes (ISO 25010 / ATAM)

These are the primary dimensions across which tradeoffs are evaluated:

| Quality Attribute | Definition | Key Metric |
|---|---|---|
| **Functional Correctness** | Does it do what the business needs? | Requirements coverage |
| **Performance** | How fast and efficiently does it respond? | Latency (P50/P95/P99), throughput (RPS) |
| **Scalability** | Can it handle growth in load / data / users? | Traffic headroom, horizontal scale-out |
| **Availability** | Is it reliably accessible when needed? | Uptime %, RTO, RPO |
| **Resilience** | Does it degrade gracefully under failure? | Blast radius, MTTR, cascading failure risk |
| **Security** | Is it protected from unauthorised access and data leaks? | Threat model coverage, compliance posture |
| **Maintainability** | Can teams understand, change, and extend it? | Cognitive load, test coverage, deploy frequency |
| **Observability** | Can you understand what's happening inside? | Log quality, trace coverage, alert accuracy |
| **Portability** | Can it move between environments without rework? | Cloud-agnosticism, containerisation |
| **Cost Efficiency** | Does it deliver value at acceptable cost? | Unit economics, waste %, FinOps score |
| **Developer Experience (DX)** | How easy is it to build and deploy? | Onboarding time, local dev cycle time |
| **Team Autonomy** | Can teams work independently? | Deploy independence, shared coupling |

---

## Tradeoff Spectrums

Architecture decisions typically involve navigating these common spectrums. Neither end is universally right — the right point depends on context.

### Consistency vs Availability
```
Strong Consistency ◄──────────────────────────► High Availability
   (ACID, 2PC, synchronous)            (Eventual, AP, async, cached)

 ╔═══════════════╗                     ╔══════════════════╗
 ║ Financial TX  ║                     ║  Social feed     ║
 ║ Inventory     ║                     ║  Product search  ║
 ║ Auth/Authz    ║                     ║  Analytics views ║
 ╚═══════════════╝                     ╚══════════════════╝
```

### Coupling vs Complexity
```
Tight Coupling ◄───────────────────────────────► Loose Coupling
(Fast, simple, hard to change)           (Flexible, resilient, complex to operate)

 ╔════════════╗                           ╔══════════════════╗
 ║  Monolith  ║                           ║  Event-Driven    ║
 ║  Direct DB ║                           ║  Microservices   ║
 ╚════════════╝                           ╚══════════════════╝
```

### Simplicity vs Capability
```
Simple ◄───────────────────────────────────────► Feature-Rich
(Low ops cost, fast to build)            (High control, high complexity)

 ╔═══════════╗                           ╔══════════════════╗
 ║ SQLite    ║◄── RDS ──── Aurora ──────►║  Multi-region DB ║
 ║ Monolith  ║◄── Modular ─── Micro ────►║  Service Mesh    ║
 ╚═══════════╝                           ╚══════════════════╝
```

### Build vs Buy vs OSS
```
Build ◄──────────────────────────────────────────► Buy (SaaS)
(Full control, full cost)                (Fast, vendor-dependent)

 Build when: Core differentiating IP
 OSS when: Community-supported, self-hostable commodity
 Buy when: Non-differentiating, mature market solution exists
```

---

## Standard Tradeoff Table Template

Use this table structure for every significant architectural decision:

```markdown
### Decision: [What are you deciding?]

| Option | Pros | Cons | When to Choose |
|--------|------|------|----------------|
| **Option A** | - Benefit 1<br>- Benefit 2 | - Cost 1<br>- Cost 2 | Context A fits this |
| **Option B** | - Benefit 1<br>- Benefit 2 | - Cost 1<br>- Cost 2 | Context B fits this |
| **Option C** | - Benefit 1<br>- Benefit 2 | - Cost 1<br>- Cost 2 | Context C fits this |

**Recommendation**: Option X  
**Rationale**: Given [constraint/context], Option X is preferred because [reason].  
**What we're accepting**: [explicit tradeoffs being made — what pain do we take on?]  
**Conditions to revisit**: [when should this decision be re-evaluated?]
```

---

## Common Tradeoff Scenarios

### Scenario 1: Monolith vs Microservices

| Dimension | Monolith | Microservices |
|---|---|---|
| **Dev Velocity (early)** | ★★★ Fast | ★ Slow (infra overhead) |
| **Dev Velocity (at scale)** | ★ Slow (team coupling) | ★★★ Fast (per team) |
| **Operational Complexity** | ★ Low | ★★★ High |
| **Scalability** | ★★ Scale everything | ★★★ Scale per service |
| **Fault Isolation** | ★ One failure = outage | ★★★ Isolated blast radius |
| **Data Consistency** | ★★★ ACID transactions | ★ Eventual + Sagas needed |
| **Team Autonomy** | ★ Shared codebase | ★★★ Own domain |

**Verdict**: Monolith wins early; Microservices win at organisational scale. **Don't prematurely optimise.**

---

### Scenario 2: REST vs Async Messaging

| Dimension | REST (Sync) | Async Messaging |
|---|---|---|
| **Simplicity** | ★★★ | ★ |
| **Temporal Decoupling** | ★ (both must be up) | ★★★ |
| **Latency** | ★★★ (immediate response) | ★ (polling or callbacks) |
| **Reliability** | ★★ | ★★★ (at-least-once delivery) |
| **Fan-out** | ★ (N calls needed) | ★★★ (single publish, N consumers) |
| **Debugging** | ★★★ (request trace) | ★ (distributed, async trace) |
| **Backpressure** | ★ (cascading failures) | ★★★ (queue absorbs spikes) |

**Verdict**: REST for user-facing interactions; Async for cross-domain integration and high-throughput pipelines.

---

### Scenario 3: SQL vs NoSQL

| Dimension | Relational (SQL) | Document/Key-Value (NoSQL) |
|---|---|---|
| **Schema Flexibility** | ★ (rigid schema) | ★★★ (schemaless) |
| **ACID Transactions** | ★★★ | ★ (limited, varies by DB) |
| **Query Flexibility** | ★★★ (arbitrary JOINs) | ★ (access-pattern-driven) |
| **Horizontal Scale** | ★★ (sharding is hard) | ★★★ (designed for it) |
| **Consistency** | ★★★ | ★★ (tunable) |
| **Operational Maturity** | ★★★ | ★★ (varies) |

**Verdict**: Default to SQL. Use NoSQL when: extreme write throughput, flexible schema, horizontal scale needs, or specific access patterns (graph, time-series, document search) justify it.

---

### Scenario 4: Synchronous Saga (Orchestration) vs Async Saga (Choreography)

| Dimension | Orchestration | Choreography |
|---|---|---|
| **Visibility of flow** | ★★★ (centralised) | ★ (distributed) |
| **Coupling** | ★★ (orchestrator knows all) | ★★★ (services only know events) |
| **Debuggability** | ★★★ | ★ |
| **Resilience** | ★★ (orchestrator SPOF) | ★★★ |
| **Complexity of adding steps** | ★★ (change orchestrator) | ★★★ (add new subscriber) |

**Verdict**: Orchestration for complex, visible workflows; Choreography for simple event reactions with high decoupling requirements.

---

## ATAM — Architecture Tradeoff Analysis Method (Abbreviated)

When performing a formal architecture review:

1. **Present architecture** — Describe the architecture at context + container level.
2. **Identify quality attribute scenarios** — "The system must handle 10,000 concurrent users with P99 < 200ms."
3. **Map scenarios to architectural decisions** — Which component/decision affects each scenario?
4. **Identify sensitivity points** — Which decisions have outsized impact on one quality attribute?
5. **Identify tradeoff points** — Which decisions affect two or more quality attributes in opposing ways?
6. **Identify risks** — Untested assumptions, single points of failure, potential bottlenecks.
7. **Document** — Output: sensitivity points table, risk register, recommended mitigations.

---

## Architecture Risk Register Template

Every architecture review should produce a risk register:

| Risk | Likelihood (H/M/L) | Impact (H/M/L) | Mitigation | Owner |
|---|---|---|---|---|
| Single DB becomes bottleneck at 10x traffic | M | H | Read replicas, caching layer, CQRS | Platform Team |
| Message broker downtime stops order flow | L | H | DLQ + retry, multi-AZ broker | Infra Team |
| Event schema breaking change | M | M | Schema registry, versioning strategy | Domain Team |
| Third-party payment API SLA 99.5% < our 99.9% target | H | H | Circuit breaker, fallback, async retry | Payments Team |

---

## Decision Quality Checklist

Before finalising any architectural decision, verify:

- [ ] At least **two alternatives were considered** and documented.
- [ ] The **tradeoffs are explicitly stated** (not just pros).
- [ ] The decision is **traceable to a specific business/technical requirement**.
- [ ] The decision **does not violate a core architecture principle** (or the violation is justified).
- [ ] The **conditions under which this decision should be revisited** are defined.
- [ ] An **ADR has been written** and stored with the architecture documentation.
- [ ] **NFRs (scalability, security, availability) have been validated** against the decision.
- [ ] **Team can operate this** — org/skill constraints are factored in, not just technical ideals.
