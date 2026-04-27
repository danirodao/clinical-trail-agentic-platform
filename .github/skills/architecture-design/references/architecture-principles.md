# Architecture Principles Reference

These principles must be explicitly applied and cited in every architecture design. When a decision conflicts with a principle, document the **exception and rationale**.

---

## Foundational Software Principles

### 1. Separation of Concerns (SoC)
Each architectural unit (service, module, layer) should have a **single, well-defined responsibility**. Changes to one concern should not ripple into unrelated areas.
- **Apply at**: Service boundaries, layer design, data ownership.
- **Violation signal**: A service that owns business logic, data persistence, AND orchestrates other services.

### 2. Single Responsibility Principle (SRP)
A component should have **only one reason to change**. Derived from SoC, applied at a finer granularity.
- **Apply at**: Microservice scope definition, class/module design.
- **Violation signal**: "God services" or "ball-of-mud" modules.

### 3. Loose Coupling / High Cohesion
- **Loose Coupling**: Components should depend on abstractions, not concretions. Changes to one component should not require changes in others.
- **High Cohesion**: Related functionality should live together.
- **Apply at**: API contracts, event schema design, service mesh configuration.
- **Violation signal**: Chatty inter-service communication; services that share databases.

### 4. Don't Repeat Yourself (DRY)
Avoid duplication of logic and data. **However**, in distributed systems, **some duplication in data is acceptable** to avoid coupling (e.g., read models in CQRS).
- **Nuance**: DRY applies to logic, not necessarily data copies in microservices.

### 5. YAGNI — You Aren't Gonna Need It
Do not build for hypothetical future requirements. Build the simplest thing that works today, designed to be extended.
- **Violation signal**: A system with 12 microservices for an MVP that serves 100 users.

### 6. KISS — Keep It Simple, Stupid
Prefer simple solutions. Complexity is a liability. Every layer of abstraction has a cost.
- **Heuristic**: If you need 3 slides to explain a component to a senior engineer, it is too complex.

### 7. Open/Closed Principle (OCP)
Systems should be **open for extension, closed for modification**. Add behaviour by adding new components, not editing existing ones.
- **Apply at**: Plugin architectures, event-driven systems, strategy patterns.

### 8. Dependency Inversion Principle (DIP)
High-level modules should not depend on low-level modules. Both should depend on abstractions (interfaces/contracts).
- **Apply at**: Hexagonal/Ports & Adapters architecture, service contracts.

---

## Distributed Systems Principles

### 9. Design for Failure
**Assume everything will fail.** Design systems to degrade gracefully, not catastrophically.
- Implement: Circuit Breakers, Retries with Exponential Backoff, Timeouts, Fallbacks, Bulkheads.
- **Key insight**: Cascading failures are often caused by missing timeouts and lack of bulkheads.

### 10. Idempotency
Operations that can be retried should produce the same result regardless of how many times they are executed.
- **Mandatory for**: All message consumers, payment operations, state mutations over unreliable networks.
- **Pattern**: Idempotency keys, deduplication tables.

### 11. Eventual Consistency (Accept It Where Appropriate)
In distributed systems, **strong consistency is expensive**. Accept eventual consistency where business rules permit.
- **Use strong consistency for**: Financial ledgers, inventory decrements, authorisation decisions.
- **Accept eventual consistency for**: Search indexes, recommendation feeds, analytics dashboards.

### 12. CAP Theorem Awareness
A distributed system can only guarantee **two of three**: Consistency, Availability, Partition Tolerance.
Since network partitions are unavoidable, the real choice is **CP vs AP**:
- **CP systems**: Consistent under partition, may become unavailable (e.g., ZooKeeper, etcd, traditional RDBMS clusters).
- **AP systems**: Available under partition, may return stale data (e.g., Cassandra, DynamoDB with eventual consistency, DNS).

---

## Enterprise Architecture Principles

### 13. Business-IT Alignment
Architecture must serve **business capabilities**, not technology preferences. Every architectural component should map to a business capability.
- **Tool**: Business Capability Map → Service decomposition.

### 14. Build vs Buy vs Integrate
Default decision order: **Buy (SaaS) → Integrate (existing internal) → Build**.
- Build only for **core differentiating capabilities**.
- Buy for commodities (auth, email, monitoring, CI/CD).

### 15. Data Ownership & Sovereignty
Each bounded context/service **owns its data**. No direct cross-service database access.
- Data shared across boundaries must flow through **APIs or events**, never shared schemas.
- **Compliance**: Know where data lives (data residency), especially for PII and regulated data.

### 16. Security by Design (Shift Left Security)
Security is not an afterthought. Every architecture decision has a security implication.
- Apply **Zero Trust**: "Never trust, always verify" — authenticate and authorise every request, assume breach.
- Classify data: Public → Internal → Confidential → Restricted, and enforce controls accordingly.
- Apply **Least Privilege** at every layer.

### 17. Observability as a First-Class Concern
You cannot manage what you cannot measure.
- The **three pillars of observability**: Logs, Metrics, Distributed Traces.
- Design emit-points into the architecture, not retrofitted.

### 18. Evolutionary Architecture
Architecture must support change. The ability to **safely evolve** the system is a primary quality attribute.
- Use: Fitness functions (automated architectural tests), strangler fig for migrations, branch-by-abstraction.
- Avoid tight API coupling that makes change expensive.

### 19. Platform Thinking
Prefer building **platforms that enable teams** over point solutions that serve one product.
- Internal Developer Platforms (IDPs): Golden paths, self-service infrastructure.
- API-first design enables internal and external consumers.

### 20. Cost as an Architectural Constraint
Cloud costs are an architectural concern, not just an operations concern.
- Design with cost models in mind: data transfer costs, per-request pricing, storage tiers.
- Apply FinOps principles: **right-size**, auto-scale, use spot/preemptible where appropriate.

---

## Principle Application Matrix

| Principle | Microservices | Monolith | Event-Driven | Serverless |
|-----------|:---:|:---:|:---:|:---:|
| SoC / SRP | ★★★ | ★★ | ★★★ | ★★★ |
| Loose Coupling | ★★★ | ★ | ★★★ | ★★ |
| Design for Failure | ★★★ | ★★ | ★★★ | ★★ |
| YAGNI / KISS | ★ | ★★★ | ★★ | ★★ |
| Eventual Consistency | ★★★ | ★ | ★★★ | ★★ |
| Observability | ★★★ | ★★ | ★★★ | ★★★ |

★★★ = Critical  ★★ = Important  ★ = Consider
