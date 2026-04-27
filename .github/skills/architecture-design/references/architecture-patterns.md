# Architecture Patterns Reference

Comprehensive catalogue of architecture patterns. For each pattern, the tradeoffs are explicitly documented. Always present at least two alternatives before recommending one.

---

## Application Structure Patterns

### 1. Monolithic Architecture

**What it is**: All application logic, modules, and data access in a single deployable unit.

| | Detail |
|---|---|
| **Pros** | Simple to develop, test, and debug locally; low operational overhead; straightforward transactions; low latency (in-process calls); easy refactoring across modules |
| **Cons** | Scales as a unit (can't scale hot components independently); long build/test cycles at scale; technology lock-in; deployment risk per release; team coupling |
| **Best Fit** | Early-stage products, small teams (<10 engineers), low complexity domains, clear bounded contexts that haven't been identified yet |
| **Avoid When** | Multiple teams need independent deployment; drastically different scaling needs per component; diverse technology requirements |

---

### 2. Modular Monolith

**What it is**: A single deployable unit with **strongly enforced internal module boundaries**, making it ready for eventual extraction.

| | Detail |
|---|---|
| **Pros** | Simple deployment; enforced boundaries prevent Big Ball of Mud; single transaction boundary; easier to migrate to microservices later |
| **Cons** | Still scales as a unit; team coordination on shared codebase; still language/platform homogeneous |
| **Best Fit** | Teams that want microservice-level structure without the operational complexity; migration stepping stone |
| **Key Pattern** | Enforce boundaries via package-by-feature, access modifiers, or ArchUnit/fitness functions |

---

### 3. Microservices Architecture

**What it is**: System decomposed into small, independently deployable services, each owning its data and communicating over network APIs or events.

| | Detail |
|---|---|
| **Pros** | Independent deployment and scaling; polyglot technology; team autonomy; fault isolation; small codebase per service |
| **Cons** | Distributed systems complexity (network latency, partial failure, eventual consistency); high operational overhead (CI/CD per service, service mesh, distributed tracing); data consistency challenges; over-decomposition risk |
| **Best Fit** | Multiple autonomous teams; well-understood domain boundaries (DDD Bounded Contexts); mature DevOps culture and platform; scale requirements differ per component |
| **Golden Rule** | **Do not start with microservices.** Start with a modular monolith; extract when team/scaling pain is real, not hypothetical. |
| **Decomposition Heuristic** | 1 service = 1 Bounded Context. The team that owns the service owns the data. |

---

### 4. Serverless / Function as a Service (FaaS)

**What it is**: Stateless functions triggered by events, scaled and managed entirely by the cloud platform.

| | Detail |
|---|---|
| **Pros** | Zero infrastructure management; automatic scaling to zero; pay-per-execution; fast experimentation |
| **Cons** | Cold start latency; stateless (state must be externalised); vendor lock-in; hard to test locally; long-running workloads are expensive; limited concurrency control; observability challenges |
| **Best Fit** | Event-driven workloads, webhooks, scheduled tasks, lightweight APIs with spiky traffic, data processing pipelines |
| **Avoid When** | Long-running processes; requires consistent latency; complex local dev/test workflows |

---

## Communication Patterns

### 5. Synchronous REST / HTTP

| | Detail |
|---|---|
| **Pros** | Simple, universal, human-readable; request-response semantics are intuitive; easy debugging |
| **Cons** | Temporal coupling (both parties must be available); cascading failures if downstream is slow; each caller blocked waiting |
| **Best Fit** | CRUD operations, query responses, user-facing APIs, when immediate consistency is required |

### 6. gRPC / Protocol Buffers

| | Detail |
|---|---|
| **Pros** | Strongly typed contracts; ~10x faster than REST for high-throughput internal services; bidirectional streaming; schema evolution via field numbers |
| **Cons** | Binary format (not human-readable); requires HTTP/2; harder to debug without tooling; less universal browser support |
| **Best Fit** | Internal service-to-service communication with high throughput or streaming requirements |

### 7. Asynchronous Messaging / Event-Driven Architecture (EDA)

**What it is**: Services communicate by publishing and consuming events via a message broker (Kafka, RabbitMQ, SNS/SQS).

| | Detail |
|---|---|
| **Pros** | Temporal decoupling (producers and consumers don't need to be up simultaneously); resilience; high throughput; enables event sourcing, audit logs, replay |
| **Cons** | Eventual consistency; harder to debug (no request trace by default); message ordering complexity; idempotency required; schema governance burden |
| **Best Fit** | Cross-domain integration; workflows that can tolerate eventual consistency; audit/compliance requirements; fan-out notifications |

### 8. GraphQL

| | Detail |
|---|---|
| **Pros** | Client-driven queries (no over/under-fetching); single endpoint; strong typing and introspection; excellent for federated graphs (Apollo Federation) |
| **Cons** | Complex caching (no HTTP caching by default); N+1 query problem (requires DataLoader); security risk (deeply nested queries); steeper learning curve |
| **Best Fit** | BFF layer for mobile/web clients; aggregation over multiple backend services; public APIs with diverse consumer needs |

---

## Data Patterns

### 9. CQRS — Command Query Responsibility Segregation

**What it is**: Separate the write model (Commands) from the read model (Queries). Different schemas, potentially different stores.

| | Detail |
|---|---|
| **Pros** | Read and write models optimised independently; scales read side separately; enables event sourcing; cleaner domain model |
| **Cons** | Added complexity; eventual consistency between write and read models; more code to maintain |
| **Best Fit** | High read/write asymmetry; complex query requirements; event sourcing systems; audit requirements |

### 10. Event Sourcing

**What it is**: Store state as an **immutable sequence of events** rather than the current state snapshot.

| | Detail |
|---|---|
| **Pros** | Complete audit log; time travel / replay; naturally enables CQRS; decouples state from projections |
| **Cons** | Steep learning curve; schema evolution is hard (events are immutable contracts); storage growth; eventual consistency for projections; not suited for all domains |
| **Best Fit** | Financial systems, booking systems, compliance-heavy domains, systems where "what happened" matters as much as "what is the state" |

### 11. Saga Pattern (Distributed Transactions)

**What it is**: A sequence of local transactions, each publishing events/messages to trigger the next step. No distributed 2PC.

| Orchestration Saga | Choreography Saga |
|---|---|
| Central orchestrator manages the flow | Services listen for events and react |
| Easier to reason about flow and debug | More decoupled, no central coordinator |
| Orchestrator becomes a bottleneck/SPOF risk | Harder to track overall saga state |

| | Detail |
|---|---|
| **Pros** | Avoids distributed transactions; works across service boundaries |
| **Cons** | Compensating transactions must be designed for every failure path; eventual consistency; complex to test |
| **Best Fit** | Multi-service business processes (e.g., order placement → payment → inventory → shipping) |

### 12. Outbox Pattern

**What it is**: Write to database AND publish an event atomically by writing the event to an **outbox table** in the same DB transaction, then a relay process publishes it.

| | Detail |
|---|---|
| **Pros** | Solves dual write problem (guaranteed event publication with DB commit); at-least-once delivery |
| **Cons** | Additional table and relay process; slight latency (relay polling or CDC); idempotency still required on consumer side |
| **Best Fit** | Any service that must reliably emit events after a state change |

---

## Integration Patterns

### 13. API Gateway

**What it is**: Single entry point for all client requests, handling routing, auth, rate limiting, SSL termination.

| | Detail |
|---|---|
| **Pros** | Centralises cross-cutting concerns; client talks to one endpoint; enables versioning; observability |
| **Cons** | Potential SPOF if not HA; can become a bottleneck; risk of business logic leaking into gateway |
| **Best Fit** | Public-facing APIs; mobile/web clients; multi-backend systems |

### 14. Backend for Frontend (BFF)

**What it is**: A dedicated backend API tailored to a specific client type (web, mobile, 3rd party).

| | Detail |
|---|---|
| **Pros** | Optimised payloads per client; independent evolution; shields downstream services from client-specific logic |
| **Cons** | Code duplication across BFFs; more services to maintain |
| **Best Fit** | When web and mobile have significantly different data needs; when API Gateway aggregation becomes too complex |

### 15. Service Mesh (Istio, Linkerd, Consul Connect)

**What it is**: Infrastructure layer that handles service-to-service communication (mTLS, load balancing, observability, retries) via sidecar proxies.

| | Detail |
|---|---|
| **Pros** | Decouples resilience/observability from application code; automatic mTLS; traffic management (canary) |
| **Cons** | Significant operational complexity; sidecar overhead (CPU/memory); steep learning curve; Kubernetes-native |
| **Best Fit** | Large microservice deployments on Kubernetes with mature platform teams |

---

## Resilience Patterns

### 16. Circuit Breaker
Prevents calls to a failing service from cascading. After N failures, "opens" the circuit and fast-fails requests.
- **Library examples**: Resilience4j, Polly, Hystrix (deprecated).
- **States**: Closed (normal) → Open (failing, short-circuit) → Half-Open (probe).

### 17. Bulkhead
Isolate resource pools per consumer to prevent one failing call from exhausting all threads/connections.
- **Analogy**: Watertight compartments in a ship hull.

### 18. Retry with Exponential Backoff + Jitter
Retry transient failures with increasing delays to avoid thundering herds.
- **Always combine with**: Idempotency, circuit breaker, and a max retry limit.

### 19. Timeout
Every remote call **must have a timeout**. Without timeouts, a slow downstream will hold threads indefinitely.
- **Guideline**: Set timeouts at P99 latency + buffer. Never use infinite timeouts.

---

## Deployment Patterns

### 20. Blue/Green Deployment

| | Detail |
|---|---|
| **Pros** | Instant rollback; zero downtime; full production traffic test before cutover |
| **Cons** | Double infrastructure cost while both environments are live |

### 21. Canary Release

| | Detail |
|---|---|
| **Pros** | Gradual traffic shift; real production validation; limit blast radius of bad releases |
| **Cons** | Requires traffic splitting infrastructure; running two versions simultaneously adds complexity |

### 22. Strangler Fig Pattern
Incrementally replace a legacy system by routing new requests to the new system while the legacy handles the rest. Over time, the legacy "strangles" away.
- **Best Fit**: Legacy modernisation projects; avoid big-bang rewrites.

---

## Cloud Architecture Patterns

### 23. Cell-Based Architecture
Decompose a system into independent, identically-configured "cells" (e.g., regional or tenant-partitioned). A failure in one cell does not affect others.
- **Pros**: Blast radius containment; natural multi-tenancy isolation.
- **Best Fit**: SaaS platforms with strict tenant isolation requirements.

### 24. Data Mesh
Decentralised data ownership where **domain teams own and publish their data as a product** via self-serve data platform.
- **Pros**: Removes data team bottleneck; domain-aligned data ownership.
- **Cons**: Governance complexity; requires data product thinking across all teams; significant cultural shift.
- **Best Fit**: Large organisations with many data-producing domains.

---

## Pattern Selection Decision Tree

```
Is this a new product/MVP?
 ├─ YES → Start with Modular Monolith
 └─ NO → Are teams blocked deploying independently?
           ├─ YES → Consider Microservices decomposition
           └─ NO → Do you have high read/write asymmetry or audit needs?
                     ├─ YES → Consider CQRS / Event Sourcing
                     └─ NO → Is the main challenge integration across systems?
                               ├─ YES → Event-Driven Architecture
                               └─ NO → Refine the Monolith's internal structure
```
