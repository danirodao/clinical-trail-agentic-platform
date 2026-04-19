# Clinical Trial Agentic Platform 🧬

[![Python: 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green.svg)](https://fastapi.tiangolo.com/)
[![MCP](https://img.shields.io/badge/MCP-Protocol-orange.svg)](https://modelcontextprotocol.io/)
[![LangGraph](https://img.shields.io/badge/LangGraph-ReAct-purple.svg)](https://langchain-ai.github.io/langgraph/)
[![Keycloak](https://img.shields.io/badge/Keycloak-25.0-red.svg)](https://www.keycloak.org/)
[![OpenFGA](https://img.shields.io/badge/OpenFGA-v1.8-teal.svg)](https://openfga.dev/)

A secure, agentic platform for clinical trial data analysis that combines **LLM-powered reasoning** with a **multi-modal Data Mesh** (Relational + Graph + Vector) and **fine-grained authorization** (Keycloak + OpenFGA). Researchers ask natural language questions; the system autonomously selects tools, queries authorized data, and synthesizes clinically precise answers — all while enforcing the **Access Level Ceiling Principle** to prevent data leakage.

---

## Table of Contents

1. [High-Level Architecture](#-high-level-architecture)
2. [Architecture Patterns & Best Practices](#-architecture-patterns--best-practices)
3. [Synthetic Data Generation](#-synthetic-data-generation)
4. [Data Ingestion Pipeline](#-data-ingestion-pipeline)
5. [Authentication — Keycloak OIDC](#-authentication--keycloak-oidc)
6. [Fine-Grained Authorization — OpenFGA](#-fine-grained-authorization--openfga)
7. [The Access Level Ceiling Principle](#-the-access-level-ceiling-principle)
8. [MCP Server — Tool Hub](#-mcp-server--tool-hub)
9. [Agentic Reasoning — LangGraph ReAct](#-agentic-reasoning--langgraph-react)
10. [Frontend — React + Keycloak SPA](#-frontend--react--keycloak-spa)
11. [Evaluation Framework](#-evaluation-framework)
12. [Project Structure](#-project-structure)
13. [Getting Started](#-getting-started)
14. [Common Commands](#-common-commands)
15. [Dashboards & Exploration](#-dashboards--exploration)
16. [Testing & Validation](#-testing--validation)
17. [Topics Covered](#-topics-covered)

---

## 🏗️ High-Level Architecture

The platform follows a **microservices architecture** coordinated via Docker Compose. Every service runs in an isolated container, communicating over a shared Docker network (`clinical-net`).

```mermaid
graph TD
    User([Researcher]) <-->|OIDC Login| Frontend[React SPA]
    Frontend <-->|JWT Bearer| API[FastAPI Gateway]
    API <-->|Validate JWT| KC[Keycloak OIDC]
    API <-->|Check Tuples| FGA[OpenFGA]
    API <-->|SSE + JSON-RPC| MCP[MCP Server]

    subgraph "Intelligent Data Mesh"
        MCP <-->|SQL| PG[(PostgreSQL)]
        MCP <-->|Cosine Similarity| QD[(Qdrant)]
        MCP <-->|Cypher| NEO[(Neo4j)]
    end

    subgraph "Async Data Pipeline"
        GEN[Generator] -->|pdf.generated| KFK((Kafka))
        KFK -->|consume| PROC[Processor]
        PROC --> PG
        PROC --> QD
        PROC --> NEO
        PROC --> MINIO[(MinIO S3)]
    end
```

### Component Summary

| Service | Role | Port |
|:---|:---|:---|
| **Frontend** | React SPA with Keycloak SSO, chat UI, role-based dashboards | `3001` |
| **API Gateway** | FastAPI — orchestrates LangGraph agent, computes access profiles | `8000` |
| **MCP Server** | FastMCP — 15 clinical tools exposed via SSE/JSON-RPC | `8001` |
| **Keycloak** | OIDC Identity Provider — JWT issuance, realm roles, PKCE | `8180` |
| **OpenFGA** | Zanzibar-style ReBAC engine — trial/patient/cohort tuples | `8082` |
| **PostgreSQL** | Relational store — trials, patients, labs, AEs, auth tables | `5432` |
| **Neo4j** | Knowledge graph — Drug→Condition, Patient→AE, comorbidities | `7474` |
| **Qdrant** | Vector DB — `text-embedding-3-large` (3072-dim) embeddings | `6333` |
| **Kafka** | Event bus — `pdf-generated`, `trial-ingested` topics | `9092` |
| **MinIO** | S3-compatible object store for generated PDF reports | `9001` |
| **Phoenix** | OTLP trace collector for LLM/Agent spans | `6006` |
| **Prometheus** | Metrics scraping from API + MCP + evaluation framework | `9090` |
| **Grafana** | Dashboards — Agent Performance + Semantic Layer Quality | `3010` |
| **Argilla** | Human-in-the-loop evaluation review | `6900` |
| **Elasticsearch** | Backend store for Argilla annotations | `9200` |

---

## 🧩 Architecture Patterns & Best Practices

### Data Mesh

Each data domain (Relational, Graph, Vector) is treated as an independent product with its own access surface. The MCP Server acts as the **data product API**, exposing tools that abstract away the underlying store.

### Event-Driven Architecture (EDA)

The Generator and Processor communicate exclusively via Kafka events. The Generator never writes to databases directly — it publishes a lightweight `PDFGeneratedEvent` to Kafka with a reference to the PDF stored in MinIO. This decouples generation from ingestion and allows horizontal scaling.

```mermaid
sequenceDiagram
    participant Gen as Generator
    participant MinIO as MinIO (S3)
    participant Kafka as Kafka
    participant Proc as Processor
    participant PG as PostgreSQL
    participant QD as Qdrant
    participant Neo as Neo4j

    Gen->>MinIO: Upload PDF
    Gen->>Kafka: Publish PDFGeneratedEvent
    Note over Kafka: topic: pdf-generated<br/>key: NCT ID (partition ordering)
    Kafka->>Proc: Consume event
    Proc->>MinIO: Download PDF
    Proc->>Proc: Parse PDF (pdfplumber)
    Proc->>Proc: Extract entities (regex + GPT-4o)
    Proc->>Proc: Generate embeddings (text-embedding-3-large)
    par Parallel Loading
        Proc->>PG: Ingest trial + patients
        Proc->>QD: Upsert embedding chunks
        Proc->>Neo: Create graph nodes + relationships
    end
    Proc->>Kafka: Publish TrialIngestedEvent
```

### Idempotent Producer

The Kafka producer is configured with `enable.idempotence=True`, `acks=all`, and `max.in.flight.requests.per.connection=1` to guarantee exactly-once delivery semantics. Messages are LZ4-compressed for efficiency.

### Claim-Check Pattern

Large PDF payloads are stored in MinIO (the "claim"), and only a lightweight reference (`bucket` + `object_key`) is published to Kafka (the "check"). This keeps Kafka messages under 1MB and avoids broker memory pressure.

### ReAct (Reason + Act) Loop

The LangGraph agent follows the ReAct pattern: at each step, the LLM either (a) selects a tool to call, or (b) outputs a final answer. The loop continues until the LLM has enough data to synthesize a response.

### Fail-Closed Security

The OpenFGA client defaults to `FAIL_CLOSED=true`: if the authorization service is unreachable, all access checks return `denied`. This prevents accidental data exposure during infrastructure outages.

### Two-Layer Access Control

- **Layer 1 (OpenFGA)**: Which trials can the user access? (binary gate)
- **Layer 2 (PostgreSQL)**: Which patients within those trials? (cohort filters)

The `AccessProfile` carries both layers through the entire pipeline.

### Hybrid Entity Extraction

The Processor uses a three-stage extraction pipeline:
1. **Regex-based** for structured fields (NCT IDs, LOINC codes, dates)
2. **Table parsing** with robust classification (conditions vs. labs vs. AEs)
3. **LLM-assisted** (`GPT-4o`) for unstructured narrative sections

---

## 🧪 Synthetic Data Generation

The Generator service creates realistic clinical trial data using `Faker` and domain-specific medical reference tables. All data is reproducible via a configurable seed.

### Therapeutic Areas & Reference Data

The generator ships with curated medical data across **3 therapeutic areas**:

| Area | Conditions | Drugs | Lab Tests | Adverse Events |
|:---|:---|:---|:---|:---|
| **Oncology** | NSCLC, Breast Cancer, Melanoma, Colorectal, Pancreatic | Pembrolizumab, Nivolumab, Atezolizumab, Paclitaxel | WBC, ANC, Hemoglobin, Platelets, ALT, AST, Creatinine, TSH | Nausea, Neutropenia, Pneumonitis, Hepatitis |
| **Cardiology** | Heart Failure, AFib, Hypertension, ACS | Sacubitril/Valsartan, Empagliflozin, Apixaban | BNP, Troponin I, Potassium, eGFR, INR | Hypotension, Bleeding, Hyperkalemia |
| **Endocrinology** | T2DM, T1DM, Obesity, Diabetic Nephropathy | Semaglutide, Tirzepatide, Metformin | HbA1c, Fasting Glucose, Lipase | Hypoglycemia, Pancreatitis |

All entries include **medical coding**: ICD-10, MeSH, SNOMED CT, RxNorm, LOINC, MedDRA PT/SOC.

### What Gets Generated Per Trial

```mermaid
graph LR
    Trial[ClinicalTrial] --> Arms[2-3 Arms]
    Trial --> Interventions[Drug Interventions]
    Trial --> Eligibility[8 Criteria]
    Trial --> Outcomes[3-5 Endpoints]
    Trial --> Sites[Multi-region Sites]
    Trial --> Patients[10-30 Patients]

    Patients --> Demographics[Age, Sex, Race, Ethnicity]
    Patients --> Conditions[1-4 Conditions w/ ICD-10]
    Patients --> Meds[1-4 Medications w/ RxNorm]
    Patients --> AEs[0-5 Adverse Events w/ MedDRA]
    Patients --> Labs[3-8 Visits × 5-8 Lab Tests]
    Patients --> Vitals[3-6 Visits × 5 Vital Signs]
```

### Generated PDF Format

Each trial produces a multi-page PDF built with **ReportLab** that mimics a real ClinicalTrials.gov protocol document:

1. **Title Page**: NCT ID, sponsor, phase, status, therapeutic area, confidentiality notice
2. **Study Identification**: NCT number, sponsor ID, titles, collaborators
3. **Study Overview**: Brief summary, detailed description (LLM-parseable narratives)
4. **Study Design**: Phase, allocation, masking, intervention model, primary purpose
5. **Arms & Interventions**: Treatment vs. placebo arms, dosage, route, frequency, RxNorm codes
6. **Eligibility Criteria**: Numbered inclusion/exclusion criteria
7. **Outcome Measures**: Primary and secondary endpoints with time frames
8. **Study Locations**: Multi-region facility table
9. **Patient Data Summary**: Aggregate demographic statistics
10. **Individual Case Reports**: First 5 patients with full detail (demographics, conditions, medications, AEs, labs)
11. **Summary Table**: Remaining patients in a compact tabular format

### Lab Value Realism

Lab results follow Gaussian distributions centered on the normal range, with a **15% probability of abnormal values**. Each result carries an abnormal flag (`H`/`L`/`N`) computed against reference ranges.

---

## 🔄 Data Ingestion Pipeline

The Processor is a long-lived Kafka consumer that converts raw PDFs into structured, searchable data across all three stores.

### Pipeline Steps

```text
Download PDF (MinIO) → Parse (pdfplumber) → Extract (Regex + LLM) → Embed (OpenAI) → Load (PG + Qdrant + Neo4j)
```

### Embedding Strategy

The `ClinicalTrialEmbeddingGenerator` creates multiple chunk types per trial:

| Chunk Type | Content | Use Case |
|:---|:---|:---|
| `trial_summary` | Title, phase, condition, sponsor, brief summary | "Find trials for NSCLC" |
| `trial_design` | Study type, allocation, masking, model | "Which trials are double-blind?" |
| `eligibility_criterion` | One criterion per chunk | "Trials accepting patients over 65" |
| `intervention` | Drug name, dose, route, RxNorm code | "Trials using Pembrolizumab" |
| `outcome_measures` | All endpoints concatenated | "Trials measuring PFS" |
| `patient_narrative` | Natural language patient summary | "Patients with severe neutropenia" |
| `serious_adverse_event` | Individual SAE detail with MedDRA | "Serious hepatitis events" |

**Model**: `text-embedding-3-large` — **3072 dimensions** — with validation that every embedding matches the expected dimensionality before upserting to Qdrant.

### Neo4j Knowledge Graph Schema

```mermaid
graph LR
    T[ClinicalTrial] -->|TESTS_INTERVENTION| D[Drug]
    T -->|STUDIES| C[Condition]
    P[Patient] -->|ENROLLED_IN| T
    P -->|HAS_CONDITION| C
    P -->|TAKES_MEDICATION| D
    P -->|EXPERIENCED| AE[AdverseEvent]
    D -->|MAY_CAUSE| AE
    C -->|COMORBID_WITH| C2[Condition]
```

**Node Types**: `ClinicalTrial`, `Patient`, `Drug` (RxNorm), `Condition` (ICD-10), `AdverseEvent` (MedDRA), `LabTest` (LOINC)

**Uniqueness Constraints**: `trial_id`, `nct_id`, `patient_id`, `icd10_code`, `rxnorm_code`, `meddra_pt`, `loinc_code`

---

## 🔑 Authentication — Keycloak OIDC

Keycloak serves as the **OpenID Connect (OIDC) Identity Provider**. It issues **RS256-signed JWTs** that carry user identity, roles, and custom claims.

### Token Issuance Flow

```mermaid
sequenceDiagram
    participant Browser as React SPA
    participant KC as Keycloak
    participant API as FastAPI Gateway

    Browser->>KC: 1. Redirect to /auth (PKCE + code_challenge)
    KC->>Browser: 2. Login form
    Browser->>KC: 3. Submit credentials
    KC->>Browser: 4. Authorization code
    Browser->>KC: 5. Exchange code + code_verifier for tokens
    KC->>Browser: 6. Access Token (JWT) + Refresh Token

    Note over Browser: JWT contains:<br/>sub, preferred_username,<br/>realm_roles, organization_id

    Browser->>API: 7. GET /api/researcher/query<br/>Authorization: Bearer {JWT}
    API->>KC: 8. Fetch JWKS (cached 5 min)
    API->>API: 9. Verify JWT signature (RS256)
    API->>API: 10. Extract UserContext
```

### JWT Claims Used

| Claim | Source | Purpose |
|:---|:---|:---|
| `sub` | Keycloak standard | User ID (UUID) |
| `preferred_username` | Keycloak standard | Display name |
| `realm_access.roles` | Keycloak realm roles | Role determination (`domain_owner` > `manager` > `researcher`) |
| `organization_id` | Custom attribute mapper | Organization scoping for multi-tenancy |

### Realm Configuration

The `clinical-trials` realm is auto-imported via `realm-export.json` mounted into Keycloak. It defines:

- **Client**: `research-platform-api` (confidential client)
- **Roles**: `domain_owner`, `manager`, `researcher`
- **Attribute Mappers**: `organization_id` → JWT claim
- **Authentication**: PKCE (S256) required

---

## 🛡️ Fine-Grained Authorization — OpenFGA

OpenFGA implements **Google Zanzibar-style Relationship-Based Access Control (ReBAC)**. It answers questions like "Can user X view individual data for trial Y?" by evaluating a graph of relationship tuples.

### Authorization Model

```text
type user

type organization
  relations
    define member: [user]
    define manager: [user] and member
    define domain_owner: [user]

type clinical_trial
  relations
    define owner: [user]                              # Domain owner who published
    define granted_org: [organization]                 # Org-level access ceiling
    define assigned_researcher: [user]                 # Direct individual assignment
    define can_view_aggregate: member from granted_org  # COMPUTED
    define can_view_individual: assigned_researcher or owner  # COMPUTED

type patient
  relations
    define enrolled_in_trial: [clinical_trial]
    define can_view_individual: can_view_individual from enrolled_in_trial  # DERIVED

type cohort
  relations
    define assigned_researcher: [user]
    define includes_trial: [clinical_trial]
    define can_access: assigned_researcher or creator
```

### How Access Is Evaluated

```mermaid
flowchart TD
    A[User Request] --> B{Role = domain_owner?}
    B -->|Yes| C[Full Access - 1=1]
    B -->|No| D[Layer 1: OpenFGA]
    D --> E[ListObjects: can_view_aggregate]
    D --> F[ListObjects: can_view_individual]
    E --> G[Aggregate Trial IDs]
    F --> H[Individual Trial IDs]
    G --> I[Layer 2: PostgreSQL]
    H --> I
    I --> J[Load Cohort Scopes]
    J --> K{Has Cohort Filter?}
    K -->|Yes| L[Build patient WHERE clause<br/>age, sex, ethnicity, country, conditions]
    K -->|No| M[Unrestricted - all patients]
    L --> N[AccessProfile]
    M --> N
    N --> O[Inject into every tool call]
```

### Access Grant Chain

1. **Domain Owner** publishes a trial → writes `owner` tuple
2. **Domain Owner** approves organization access request → writes `granted_org` tuple → all org members get `can_view_aggregate`
3. **Manager** assigns a researcher to a trial → writes `assigned_researcher` tuple → researcher gets `can_view_individual`
4. **Manager** assigns a researcher to a cohort → cohort's `filter_criteria` are loaded from PostgreSQL and applied as patient-level WHERE clauses

---

## 🚧 The Access Level Ceiling Principle

This is the platform's core security mechanism for preventing data leakage in cross-trial queries.

```mermaid
flowchart LR
    subgraph "Researcher Dani's Access"
        T1["Trial A<br/>🟢 Individual"]
        T2["Trial B<br/>🟡 Aggregate"]
    end

    Q["Query: Compare AEs across<br/>Trial A and Trial B"] --> CEIL

    CEIL{Ceiling Principle} --> AGG["Force ALL results<br/>to Aggregate mode"]

    AGG --> R["Response:<br/>✅ Count of AEs per trial<br/>✅ Average severity<br/>❌ No individual patient rows"]
```

**Rule**: If a query spans trials where the researcher has **mixed access levels** (some individual, some aggregate), the entire response is forced to **aggregate level**. This prevents an attacker from correlating individual data from Trial A with aggregate counts from Trial B.

### Where It's Enforced

| Layer | Component | Enforcement |
|:---|:---|:---|
| **MCP Server** | `access_control.py` | `AccessContext.validate_trial_access()` resolves NCT↔UUID, checks access per trial |
| **MCP Tools** | Each of the 15 tool modules | Defensively calls `ctx.validate_trial_access()` before every query |
| **Agent Synthesizer** | `synthesizer.py` | Emits `⚠️ AGGREGATE-CEILING` warning if individual rows appear in aggregate context |
| **System Prompt** | `prompts.py` | Instructs the LLM: "if a query spans trials with mixed access, present ALL data at aggregate level" |

---

## 🔧 MCP Server — Tool Hub

The MCP Server exposes **15 clinical data tools** via the [Model Context Protocol](https://modelcontextprotocol.io/) using **Server-Sent Events (SSE)** + **JSON-RPC 2.0**.

### Registered Tools

| # | Tool | Data Source | Description |
|:---|:---|:---|:---|
| 1 | `search_trials` | PostgreSQL | Full-text search by condition, drug, phase, sponsor |
| 2 | `get_trial_details` | PostgreSQL | Complete trial metadata by UUID |
| 3 | `get_eligibility_criteria` | PostgreSQL | Inclusion/exclusion criteria |
| 4 | `get_outcome_measures` | PostgreSQL | Primary/secondary endpoints |
| 5 | `get_trial_interventions` | PostgreSQL | Drug, dose, route, RxNorm |
| 6 | `count_patients` | PostgreSQL | Counts with `group_by` (sex, age, arm, country, disposition) |
| 7 | `get_patient_demographics` | PostgreSQL | Individual rows or aggregate breakdown |
| 8 | `get_patient_disposition` | PostgreSQL | Completion/withdrawal rates |
| 9 | `get_adverse_events` | PostgreSQL | Safety data; filters: severity, serious, event_term |
| 10 | `get_lab_results` | PostgreSQL | Lab values by test, visit, abnormal flag |
| 11 | `get_vital_signs` | PostgreSQL | SYSBP, DIABP, HR, TEMP, WEIGHT over time |
| 12 | `get_concomitant_medications` | PostgreSQL | Concomitant medication data |
| 13 | `compare_treatment_arms` | PostgreSQL | Cross-arm statistical comparison |
| 14 | `find_drug_condition_relationships` | Neo4j (+PG fallback) | Graph traversal: Drug→MAY_CAUSE→AE, COMORBID_WITH |
| 15 | `search_documents` | Qdrant | Semantic vector search across protocol text |

### Dynamic Tool Discovery

The API agent does **not** maintain a static list of tool wrappers. Instead, `tool_wrappers.py` connects to the MCP Server at startup, discovers all available tools, and dynamically generates Pydantic schemas using `create_model()`. The `access_context` parameter is excluded from the LLM's view and injected transparently during execution.

### Keycloak Auth Middleware on MCP

The MCP Server has its own `KeycloakAuthMiddleware` that validates JWT tokens on all `/sse` endpoints. Only the API Gateway (which has a service-account JWT) can connect.

---

## 🤖 Agentic Reasoning — LangGraph ReAct

The agent is built as a **LangGraph StateGraph** with 4 nodes:

```mermaid
stateDiagram-v2
    [*] --> Guardrails
    Guardrails --> Agent: Access OK
    Guardrails --> Synthesizer: Access Denied
    Agent --> Tools: tool_calls present
    Agent --> Synthesizer: No tool_calls (final answer)
    Tools --> Agent: Tool results
    Synthesizer --> [*]
```

### Node Responsibilities

| Node | Module | Role |
|:---|:---|:---|
| **Guardrails** | `guardrails.py` | Validates prompt injection, checks user has any access |
| **Agent** | `agent_node.py` | GPT-4o with function calling; selects tools from 15 options |
| **Tools** | `tool_node.py` | Executes MCP tool calls, injects `access_context`, returns results |
| **Synthesizer** | `synthesizer.py` | Formats final response, applies ceiling warnings, extracts sources |

### Query Complexity Routing

Queries are classified as `simple` or `complex` based on keyword heuristics (e.g., "compare", "trend", "across"). Simple queries use `GPT-4o-mini` for cost efficiency; complex queries use `GPT-4o`.

### System Prompt Engineering

The system prompt is assembled **per-query** by injecting:
- The researcher's access summary (which trials, which level)
- Active cohort filters in human-readable form
- Security guardrails (anti-prompt-injection directives, anti-chatter rules)
- CDISC domain knowledge (phases, sex values, severity levels)

---

## 💻 Frontend — React + Keycloak SPA

The frontend is a **React + TypeScript** SPA that authenticates via **Keycloak PKCE** and provides role-based dashboards.

### Role-Based Routing

| Role | Dashboard | Features |
|:---|:---|:---|
| `domain_owner` | `/owner` | Publish trials, approve organization access requests, manage data assets |
| `manager` | `/manager` | Assign researchers to trials/cohorts, build cohort filters, browse marketplace |
| `researcher` | `/researcher` | Natural language query interface, view accessible trials, chat history |

### Query Interface Features

- **Multi-turn Chat**: Session-based conversation with history stored in `localStorage` and backend checkpointing
- **Trial Scope Selector**: Sidebar with checkboxes to narrow queries to specific trials; each trial shows its access level badge (`individual` / `aggregate`)
- **Live Tool Visualization**: Real-time display of which MCP tools are being called, their execution duration (ms), and success/error status
- **Streaming Responses**: SSE-based token streaming with a blinking cursor animation
- **Access Level Footer**: Every response displays the access level applied, the LLM model used, and active cohort filters
- **Suggested Queries**: Pre-composed example queries as clickable pills

### Keycloak Integration

```typescript
// keycloak.ts
keycloak.init({
    onLoad: 'login-required',     // Force login before app renders
    checkLoginIframe: false,       // Avoid CORS issues
    pkceMethod: 'S256'            // PKCE for public client security
});
```

---

## 📊 Evaluation Framework

The platform includes a production-grade evaluation framework for continuous quality monitoring of the semantic layer — covering both the **agent layer** (end-to-end query quality) and the **MCP tool layer** (individual tool correctness).

#### The Evaluation Flywheel (HITL Loop)

The framework implements a **Continuous Quality Flywheel** to evolve the system based on real-world feedback:

1.  **Automated Monitoring**: Nightly evaluations run against the static `golden_dataset.json`. Any failure (score < 0.7) is automatically pushed to **Argilla** for expert triage.
2.  **Production Sampling**: Managers can sample production traffic from Arize Phoenix and push it to Argilla for curation via `POST /eval/build-dataset`.
3.  **Human Curation**: Domain experts review flagged records in Argilla, providing corrected `expected_answer` values.
4.  **Dataset Sync**: Running `POST /api/v1/eval/import-reviewed` pulls all human-reviewed corrections from Argilla back into the repository's `golden_dataset.json`.
5.  **Quality Gate**: The updated dataset is used for all future deployments and nightly tests, preventing regressions.

#### Key Components

- **`offline_evaluator.py`**: The core engine that replays test cases and computes DeepEval metrics.
- **`golden_dataset_builder.py`**: Samples production traces from Arize Phoenix to grow the test suite.
- **`argilla_client.py`**: Pushes failures to Argilla for expert triage and exports corrected answers.
- **`eval_metrics.py`**: Bridges evaluation scores to Prometheus/Grafana gauges.

### Architecture

```mermaid
graph TB
    subgraph "Production Traffic"
        API["FastAPI Gateway"]
        Agent["LangGraph Agent"]
        MCP["MCP Server (15 tools)"]
    end

    subgraph "Tracing & Observability"
        Phoenix["Arize Phoenix (OTLP)"]
        Prom["Prometheus"]
        Grafana["Grafana"]
    end

    subgraph "Evaluation Pipeline"
        Sampler["Phoenix Trace Sampler"]
        GoldenDS["Golden Dataset (JSON)"]
        Evaluator["Offline Evaluator (DeepEval)"]
        Argilla["Argilla (HITL Review)"]
    end

    API --> Agent --> MCP
    Agent -->|OTLP spans| Phoenix
    API -->|/metrics| Prom

    Phoenix -->|export spans| Sampler
    Sampler -->|curate| GoldenDS
    GoldenDS --> Evaluator
    Evaluator -->|scores| Phoenix
    Evaluator -->|eval_* gauges| Prom
    Evaluator -->|failed cases| Argilla
    Prom --> Grafana
```

### Evaluation Metrics (15 metrics across 4 tiers)

| Tier | Metric | Type | Threshold |
|:---|:---|:---|:---|
| **Core Quality** | Faithfulness | DeepEval | ≥ 0.7 |
| | Answer Relevancy | DeepEval | ≥ 0.7 |
| | Hallucination | DeepEval | ≤ 0.3 |
| | Contextual Relevancy | DeepEval | ≥ 0.6 |
| **Clinical Domain** | Clinical Safety | GEval | ≥ 0.8 |
| | Access Compliance | Custom | = 1.0 |
| | Tool Call Correctness | Custom | ≥ 0.8 |
| | Data Completeness | Custom | ≥ 0.7 |
| **Safety & Governance** | Toxicity | DeepEval | ≤ 0.1 |
| | Bias | DeepEval | ≤ 0.2 |
| | PII Leakage | Custom | = 0 |
| | Prompt Injection Resistance | GEval | ≥ 0.9 |
| **Operational** | Latency p50/p90 | Prometheus | — |
| | Token efficiency | Prometheus | — |
| | Tool error rate | Prometheus | — |

### Execution Modes

| Mode | Trigger | Use Case | Command |
|:---|:---|:---|:---|
| **On-demand** | `POST /api/v1/eval/run` | Pre-deploy validation | API call (requires manager role) |
| **Nightly** | APScheduler (2 AM UTC) | Regression detection | Automatic |
| **CI/CD** | GitHub Actions | Block deploys below threshold | `python -m api.evaluation.offline_evaluator --ci --threshold 0.85` |

### Golden Dataset

The golden dataset (`api/evaluation/golden_dataset.json`) contains curated test cases for both layers:

- **Agent layer** (15 cases): counts, demographics, AEs, lab results, cross-trial comparisons, knowledge graph queries, access denial, prompt injection, empty results
- **MCP tool layer** (8 cases): individual tool invocation correctness, data completeness, authorization enforcement

New golden records can be extracted from production traces via Phoenix:

```bash
docker compose exec api python -m api.evaluation.golden_dataset_builder --sample-pct 10
```

### Human-in-the-Loop (Argilla)

Failed evaluation cases are automatically pushed to **Argilla** for expert review. Reviewers can:
- Rate response correctness (1–5)
- Classify failure type (hallucination, irrelevant, incomplete, access violation, etc.)
- Provide the expected correct answer
- Export validated corrections back to the golden dataset

### Grafana Dashboard

The **"Semantic Layer Quality"** dashboard provides real-time visibility into:
- Overall pass rate (agent + MCP)
- Core quality score trends (faithfulness, relevancy, hallucination)
- Clinical safety and access compliance gauges
- Prompt injection resistance monitoring
- Evaluation run history and duration

---

## 📂 Project Structure

```text
clinical-trial/
├── api/                          # FastAPI + LangGraph Agent
│   ├── agent/
│   │   ├── graph.py              # LangGraph StateGraph builder (4 nodes)
│   │   ├── prompts.py            # Dynamic system prompt assembly
│   │   ├── tool_wrappers.py      # Dynamic MCP tool discovery + Pydantic schema generation
│   │   ├── access_context.py     # AccessContext serialization (UUID ↔ NCT mapping)
│   │   ├── service.py            # Agent service (entry point per query)
│   │   ├── models.py             # AgentState TypedDict
│   │   ├── observability.py      # Prometheus metrics + Phoenix OTLP tracing
│   │   └── nodes/
│   │       ├── guardrails.py     # Prompt injection detection, access gate
│   │       ├── agent_node.py     # GPT-4o function calling
│   │       ├── tool_node.py      # MCP tool execution with access_context injection
│   │       └── synthesizer.py    # Response formatting, ceiling warnings, source extraction
│   ├── evaluation/               # Evaluation Framework
│   │   ├── eval_metrics.py       # Prometheus gauges for eval scores
│   │   ├── golden_dataset.json   # Seed golden dataset (23 test cases)
│   │   ├── golden_dataset_builder.py  # Phoenix trace sampler + stratified sampling
│   │   ├── offline_evaluator.py  # DeepEval runner + Phoenix annotations + CI gate
│   │   └── argilla_client.py     # Argilla HITL integration
│   ├── routers/                  # FastAPI routers per role
│   │   ├── researcher.py         # /api/researcher/query (streaming)
│   │   ├── manager.py            # /api/manager/assign, /api/manager/cohorts
│   │   ├── domain_owner.py       # /api/owner/trials, /api/owner/access-requests
│   │   └── eval_router.py        # /api/eval/run, /api/eval/status, /api/eval/build-dataset
│   └── main.py                   # FastAPI app with lifespan, CORS, middleware, eval scheduler
│
├── mcp_server/                   # FastMCP Tool Server
│   ├── server.py                 # Starlette app, tool registration, Keycloak middleware
│   ├── access_control.py         # AccessContext class, ceiling principle, NCT↔UUID resolution
│   ├── tools/
│   │   ├── trial_discovery.py    # search_trials
│   │   ├── trial_metadata.py     # get_trial_details, eligibility, outcomes, interventions
│   │   ├── patient_analytics.py  # demographics, disposition, count, medications, vitals
│   │   ├── clinical_analysis.py  # adverse_events, lab_results, compare_arms
│   │   └── knowledge_discovery.py # find_drug_condition_relationships, search_documents
│   ├── db/                       # Database client wrappers
│   │   ├── postgres.py           # asyncpg connection pool
│   │   ├── qdrant_client.py      # Qdrant async client
│   │   └── neo4j_client.py       # Neo4j async driver
│   └── test_tools.py             # Diagnostic tool registration tests
│
├── generator/                    # Synthetic Data Engine (Kafka Producer)
│   ├── synthetic_data.py         # ClinicalTrialGenerator class, THERAPEUTIC_AREAS reference data
│   ├── pdf_builder.py            # ReportLab PDF generation (mimics ClinicalTrials.gov)
│   ├── publisher.py              # Idempotent Kafka producer + MinIO upload (claim-check)
│   └── main.py                   # Entry point: generate batch → build PDFs → publish events
│
├── processor/                    # Data Ingestion Pipeline (Kafka Consumer)
│   ├── orchestrator.py           # 5-step pipeline: Download → Parse → Extract → Embed → Load
│   ├── pdf_parser.py             # pdfplumber-based section extraction
│   ├── entity_extractor.py       # Hybrid extraction: regex + table classification + GPT-4o
│   ├── embedding_generator.py    # text-embedding-3-large chunking with dimension validation
│   ├── loaders/
│   │   ├── postgres_loader.py    # Relational ingestion (trials, patients, AEs, labs)
│   │   ├── qdrant_loader.py      # Vector upsert with UUID5 chunk IDs
│   │   └── neo4j_loader.py       # Graph node/relationship creation with constraints
│   └── consumer.py               # Kafka consumer loop with error handling
│
├── auth/                         # Security Layer
│   ├── middleware.py              # JWT verification, JWKS caching, UserContext extraction
│   ├── authorization_service.py   # Two-layer AccessProfile computation (OpenFGA + PG cohorts)
│   ├── openfga_client.py          # Async OpenFGA client (check, list-objects, write/delete tuples)
│   ├── openfga/
│   │   ├── model.fga              # OpenFGA authorization model (DSL)
│   │   ├── model.json             # Compiled model for API upload
│   │   └── init_store.py          # Store + model bootstrap script
│   ├── cohort_service.py          # Cohort CRUD, filter criteria, trial linkage
│   ├── asset_service.py           # Data asset management, dynamic collections
│   ├── access_request_service.py  # Organization access request workflow
│   └── secure_query_executor.py   # SQL query executor with access profile injection
│
├── shared/                       # Shared Code (mounted as volume)
│   ├── models.py                 # 20+ Pydantic models (CDISC enums, ClinicalTrial, Patient, etc.)
│   ├── config.py                 # Centralized configuration from environment
│   ├── kafka_schemas.py          # Kafka event schemas (PDFGeneratedEvent, TrialIngestedEvent)
│   └── storage.py                # MinIO client wrapper
│
├── frontend/                     # React + TypeScript SPA
│   ├── src/
│   │   ├── App.tsx               # Keycloak init, role-based routing
│   │   ├── keycloak.ts           # Keycloak-js adapter config (PKCE S256)
│   │   ├── components/
│   │   │   └── researcher/
│   │   │       └── QueryInterface.tsx  # Chat UI with streaming, tool visualization
│   │   ├── hooks/
│   │   │   └── useStreamingQuery.ts    # SSE streaming hook for real-time responses
│   │   └── pages/
│   │       ├── ResearcherDashboard.tsx
│   │       ├── ManagerDashboard.tsx
│   │       ├── DomainOwnerDashboard.tsx
│   │       ├── Marketplace.tsx
│   │       └── CohortBuilder.tsx
│   └── Dockerfile                # Multi-stage build (Vite → Nginx)
│
├── observability/                # Monitoring & Tracing
│   ├── prometheus/
│   │   └── prometheus.yml        # Scrape config (API + MCP)
│   └── grafana/provisioning/
│       └── dashboards/
│           ├── agent-dashboard.json     # Agent Performance Dashboard
│           └── eval-dashboard.json      # Semantic Layer Quality Dashboard
│
├── migrations/                   # Alembic Database Migrations
│   └── versions/
│       ├── a001_initial.py       # Core clinical trial schema
│       ├── a002_auth.py          # Auth tables (researcher_assignment, access_request)
│       ├── a003_collection.py    # Dynamic collection tables
│       └── a004_fix_unique_constraints.py
│
├── sql/
│   ├── init.sql                  # Full PostgreSQL schema (clinical_trial, patient, adverse_event, lab_result, etc.)
│   ├── auth_tables.sql           # researcher_assignment, cohort, cohort_trial tables
│   └── init-databases.sh         # Creates 3 databases: clinical_trials, keycloak, openfga
│
├── scripts/
│   ├── trigger_generation.sh     # Shell script: generate trials → wait for processing → show stats
│   ├── create_kafka_topics.sh    # Create topics with retention/partition config
│   └── bootstrap_auth.sh         # Initialize Keycloak realm + OpenFGA store
│
├── docker-compose.yml            # 17+ services with health checks and dependency ordering
├── Makefile                      # Common command shortcuts
└── .env                          # Environment variables (OPENAI_API_KEY, secrets)
```

---

## 🚀 Getting Started

### Prerequisites

- **Docker Desktop** (latest)
- **Python 3.12+** (for local scripts)
- **OpenAI API Key** (embeddings + agent reasoning)

### 1. Configure Environment

```bash
cp .env.example .env
# Edit .env with your OPENAI_API_KEY
```

### 2. Build & Launch

```bash
make build
make up
```

### 3. Bootstrap Auth

```bash
# Setup Keycloak realm (auto-imports realm-export.json)
./scripts/bootstrap_auth.sh

# Create Kafka topics
./scripts/create_kafka_topics.sh
```

### 4. Generate Synthetic Data

```bash
# Generate 5 trials across Oncology/Cardiology/Endocrinology
./scripts/trigger_generation.sh --trials 5 --patients 20 --wait

# Options:
#   --trials, -t NUM      Number of trials (default: 10)
#   --patients, -p NUM    Patients per trial (default: 20)
#   --seed, -s NUM        Reproducibility seed (default: 42)
#   --wait, -w            Wait for processor to finish all events
```

The `--wait` flag blocks until all Kafka events are consumed and the `show_statistics` function reports record counts across PostgreSQL, Qdrant, Neo4j, and MinIO.

---

## 🛠️ Common Commands

| Command | Description |
|:---|:---|
| `make up` | Start all services in detached mode |
| `make down` | Stop all services |
| `make build` | Rebuild all Docker images |
| `make test-agent` | Run agent tests as `researcher-jane` (full access) |
| `make test-agent-dani` | Run agent tests as `researcher-dani` (mixed access — tests ceiling principle) |
| `make test-agent-query Q="..."` | Ask a single custom question |
| `make test_mcp` | Run MCP tool registration diagnostics |
| `make logs-agent` | Tail API logs filtered to agent/tool/MCP activity |
| `make logs-mcp` | Tail MCP server logs |
| `make health-check` | Verify PostgreSQL, MCP, and API are reachable |

### Useful Docker Commands

```bash
# Check record counts in PostgreSQL
docker compose exec -T postgres psql -U ctuser -d clinical_trials -c \
  "SELECT COUNT(*) AS trials FROM clinical_trial;
   SELECT COUNT(*) AS patients FROM patient;
   SELECT COUNT(*) AS adverse_events FROM adverse_event;"

# Reset Kafka consumer offset to reprocess all PDFs
docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:29092 \
  --group pdf-processor-group \
  --reset-offsets --to-earliest \
  --topic pdf-generated --execute

# Clear all data (PostgreSQL)
docker compose exec -T postgres psql -U ctuser -d clinical_trials -c \
  "TRUNCATE patient CASCADE; TRUNCATE clinical_trial CASCADE; TRUNCATE ingestion_log;"

# Clear Neo4j graph
docker compose exec neo4j cypher-shell -u neo4j -p neo4jpassword \
  "MATCH (n) DETACH DELETE n"

# Check Qdrant vector count
curl -s http://localhost:6333/collections/clinical_trial_embeddings | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['result']['points_count'])"
```

---

## 🖥️ Dashboards & Exploration

| Service | URL | Credentials |
|:---|:---|:---|
| **Frontend (React)** | `http://localhost:3001` | Keycloak login |
| **Agent API** | `http://localhost:8000/docs` | Swagger UI |
| **MCP Server** | `http://localhost:8001/health` | Service-to-service |
| **Keycloak Admin** | `http://localhost:8180/admin` | `admin` / `admin` |
| **OpenFGA Playground** | `http://localhost:3000/playground` | N/A |
| **Neo4j Browser** | `http://localhost:7474/browser` | `neo4j` / `neo4jpassword` |
| **Qdrant Dashboard** | `http://localhost:6333/dashboard` | N/A |
| **Kafka UI** | `http://localhost:8080` | N/A |
| **MinIO Console** | `http://localhost:9001` | `minioadmin` / `minioadmin123` |
| **Phoenix (Traces)** | `http://localhost:6006` | N/A |
| **Prometheus** | `http://localhost:9090` | N/A |
| **Grafana** | `http://localhost:3010` | `admin` / `admin` |
| **Argilla (Eval Review)** | `http://localhost:6900` | `argilla` / `1234` |

---

## 🧪 Testing & Validation

### End-to-End Agent Testing

```bash
# Full-access researcher (should see individual patient rows)
make test-agent

# Mixed-access researcher (should trigger aggregate ceiling)
make test-agent-dani

# Custom question
make test-agent-query Q="What are the serious adverse events in the oncology trials?"
```

### MCP Tool Validation

```bash
# Verify all 15 tools are registered and callable
make test_mcp
```

### Evaluation Framework

```bash
# Dry run — validate golden dataset structure
docker compose exec api python -m api.evaluation.offline_evaluator --dry-run

# Full evaluation (both agent + MCP layers)
docker compose exec api python -m api.evaluation.offline_evaluator

# Agent layer only
docker compose exec api python -m api.evaluation.offline_evaluator --layer agent

# MCP tool layer only
docker compose exec api python -m api.evaluation.offline_evaluator --layer mcp

# CI gate (exit code 1 if pass rate < 85%)
docker compose exec api python -m api.evaluation.offline_evaluator --ci --threshold 0.85

# Build golden dataset from Phoenix traces
docker compose exec api python -m api.evaluation.golden_dataset_builder --sample-pct 10

# Build + push ambiguous cases to Argilla for human review
docker compose exec api python -m api.evaluation.golden_dataset_builder --sample-pct 10 --push-to-argilla

# On-demand evaluation via API (requires manager JWT)
curl -X POST http://localhost:8000/api/v1/eval/run \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"layer": null, "push_failures_to_argilla": true}'

# Check latest evaluation status
curl http://localhost:8000/api/v1/eval/status \
  -H "Authorization: Bearer $JWT"

# Verify evaluation metrics in Prometheus
curl -s http://localhost:8000/metrics | grep eval_
```

### Access Control Verification

Test that `researcher-dani` (who has individual access to Trial A and aggregate access to Trial B) gets **aggregate-only** responses when querying across both trials:

```bash
make test-agent-dani
# Expected: No individual patient rows in the response
# Expected: Aggregate statistics only (counts, averages)
```

---

## 📜 Topics Covered

| Category | Topics |
|:---|:---|
| **Distributed Systems** | Kafka event streaming, idempotent producers, consumer groups, dead-letter queues, partition ordering by NCT ID |
| **Security Engineering** | OIDC/Keycloak JWT (RS256), PKCE S256, JWKS caching, Zanzibar-style ReBAC (OpenFGA), fail-closed defaults |
| **Data Mesh** | Multi-modal data products (Relational + Graph + Vector), domain-oriented ownership, self-serve data platform |
| **Knowledge Graphs** | Neo4j Cypher, labeled property graphs, drug-condition-AE relationships, comorbidity inference, full-text indexes |
| **Vector Search** | OpenAI `text-embedding-3-large` (3072-dim), semantic document retrieval, chunk typing, dimension validation |
| **LLM Orchestration** | LangGraph StateGraph, ReAct loop, function calling, query complexity routing, dynamic tool discovery, prompt injection guardrails |
| **LLM Evaluation** | DeepEval metrics (faithfulness, hallucination, relevancy), GEval custom criteria, golden dataset management, Phoenix span annotations, Argilla HITL |
| **Clinical Informatics** | CDISC-like modeling (SDTM/CDASH patterns), ICD-10, MeSH, SNOMED CT, RxNorm, LOINC, MedDRA PT/SOC |
| **Modern Auth Patterns** | Two-layer access control, access level ceiling principle, cohort-based patient filtering, organization-scoped multi-tenancy |
| **Event-Driven Architecture** | Claim-check pattern (MinIO + Kafka), asynchronous ingestion, event sourcing (TrialIngestedEvent) |
| **Document Processing** | PDF generation (ReportLab), PDF parsing (pdfplumber), hybrid entity extraction (regex + LLM), table classification |
| **Observability** | Prometheus metrics, Grafana dashboards, Arize Phoenix OTLP tracing, APScheduler cron evaluation, CI/CD quality gates |
| **Frontend Engineering** | React + TypeScript, Keycloak-js PKCE, SSE streaming, role-based routing, real-time tool visualization |
| **Infrastructure** | Docker Compose orchestration (17+ services), health checks, Alembic migrations, multi-database PostgreSQL |

---

> [!IMPORTANT]
> This platform uses **synthetic data only**. Do not use with real PHI (Protected Health Information) without proper HIPAA/GDPR compliance audits of the underlying infrastructure.

---

## 🏗️ High-Level Architecture

The platform follows a **microservices architecture** coordinated via Docker Compose. Every service runs in an isolated container, communicating over a shared Docker network (`clinical-net`).

```mermaid
graph TD
    User([Researcher]) <-->|OIDC Login| Frontend[React SPA]
    Frontend <-->|JWT Bearer| API[FastAPI Gateway]
    API <-->|Validate JWT| KC[Keycloak OIDC]
    API <-->|Check Tuples| FGA[OpenFGA]
    API <-->|SSE + JSON-RPC| MCP[MCP Server]

    subgraph "Intelligent Data Mesh"
        MCP <-->|SQL| PG[(PostgreSQL)]
        MCP <-->|Cosine Similarity| QD[(Qdrant)]
        MCP <-->|Cypher| NEO[(Neo4j)]
    end

    subgraph "Async Data Pipeline"
        GEN[Generator] -->|pdf.generated| KFK((Kafka))
        KFK -->|consume| PROC[Processor]
        PROC --> PG
        PROC --> QD
        PROC --> NEO
        PROC --> MINIO[(MinIO S3)]
    end
```

### Component Summary

| Service | Role | Port |
|:---|:---|:---|
| **Frontend** | React SPA with Keycloak SSO, chat UI, role-based dashboards | `3001` |
| **API Gateway** | FastAPI — orchestrates LangGraph agent, computes access profiles | `8000` |
| **MCP Server** | FastMCP — 15 clinical tools exposed via SSE/JSON-RPC | `8001` |
| **Keycloak** | OIDC Identity Provider — JWT issuance, realm roles, PKCE | `8180` |
| **OpenFGA** | Zanzibar-style ReBAC engine — trial/patient/cohort tuples | `8082` |
| **PostgreSQL** | Relational store — trials, patients, labs, AEs, auth tables | `5432` |
| **Neo4j** | Knowledge graph — Drug→Condition, Patient→AE, comorbidities | `7474` |
| **Qdrant** | Vector DB — `text-embedding-3-large` (3072-dim) embeddings | `6333` |
| **Kafka** | Event bus — `pdf-generated`, `trial-ingested` topics | `9092` |
| **MinIO** | S3-compatible object store for generated PDF reports | `9001` |

---

## 🧩 Architecture Patterns & Best Practices

### Data Mesh

Each data domain (Relational, Graph, Vector) is treated as an independent product with its own access surface. The MCP Server acts as the **data product API**, exposing tools that abstract away the underlying store.

### Event-Driven Architecture (EDA)

The Generator and Processor communicate exclusively via Kafka events. The Generator never writes to databases directly — it publishes a lightweight `PDFGeneratedEvent` to Kafka with a reference to the PDF stored in MinIO. This decouples generation from ingestion and allows horizontal scaling.

```mermaid
sequenceDiagram
    participant Gen as Generator
    participant MinIO as MinIO (S3)
    participant Kafka as Kafka
    participant Proc as Processor
    participant PG as PostgreSQL
    participant QD as Qdrant
    participant Neo as Neo4j

    Gen->>MinIO: Upload PDF
    Gen->>Kafka: Publish PDFGeneratedEvent
    Note over Kafka: topic: pdf-generated<br/>key: NCT ID (partition ordering)
    Kafka->>Proc: Consume event
    Proc->>MinIO: Download PDF
    Proc->>Proc: Parse PDF (pdfplumber)
    Proc->>Proc: Extract entities (regex + GPT-4o)
    Proc->>Proc: Generate embeddings (text-embedding-3-large)
    par Parallel Loading
        Proc->>PG: Ingest trial + patients
        Proc->>QD: Upsert embedding chunks
        Proc->>Neo: Create graph nodes + relationships
    end
    Proc->>Kafka: Publish TrialIngestedEvent
```

### Idempotent Producer

The Kafka producer is configured with `enable.idempotence=True`, `acks=all`, and `max.in.flight.requests.per.connection=1` to guarantee exactly-once delivery semantics. Messages are LZ4-compressed for efficiency.

### Claim-Check Pattern

Large PDF payloads are stored in MinIO (the "claim"), and only a lightweight reference (`bucket` + `object_key`) is published to Kafka (the "check"). This keeps Kafka messages under 1MB and avoids broker memory pressure.

### ReAct (Reason + Act) Loop

The LangGraph agent follows the ReAct pattern: at each step, the LLM either (a) selects a tool to call, or (b) outputs a final answer. The loop continues until the LLM has enough data to synthesize a response.

### Fail-Closed Security

The OpenFGA client defaults to `FAIL_CLOSED=true`: if the authorization service is unreachable, all access checks return `denied`. This prevents accidental data exposure during infrastructure outages.

### Two-Layer Access Control

- **Layer 1 (OpenFGA)**: Which trials can the user access? (binary gate)
- **Layer 2 (PostgreSQL)**: Which patients within those trials? (cohort filters)

The `AccessProfile` carries both layers through the entire pipeline.

### Hybrid Entity Extraction

The Processor uses a three-stage extraction pipeline:
1. **Regex-based** for structured fields (NCT IDs, LOINC codes, dates)
2. **Table parsing** with robust classification (conditions vs. labs vs. AEs)
3. **LLM-assisted** (`GPT-4o`) for unstructured narrative sections

---

## 🧪 Synthetic Data Generation

The Generator service creates realistic clinical trial data using `Faker` and domain-specific medical reference tables. All data is reproducible via a configurable seed.

### Therapeutic Areas & Reference Data

The generator ships with curated medical data across **3 therapeutic areas**:

| Area | Conditions | Drugs | Lab Tests | Adverse Events |
|:---|:---|:---|:---|:---|
| **Oncology** | NSCLC, Breast Cancer, Melanoma, Colorectal, Pancreatic | Pembrolizumab, Nivolumab, Atezolizumab, Paclitaxel | WBC, ANC, Hemoglobin, Platelets, ALT, AST, Creatinine, TSH | Nausea, Neutropenia, Pneumonitis, Hepatitis |
| **Cardiology** | Heart Failure, AFib, Hypertension, ACS | Sacubitril/Valsartan, Empagliflozin, Apixaban | BNP, Troponin I, Potassium, eGFR, INR | Hypotension, Bleeding, Hyperkalemia |
| **Endocrinology** | T2DM, T1DM, Obesity, Diabetic Nephropathy | Semaglutide, Tirzepatide, Metformin | HbA1c, Fasting Glucose, Lipase | Hypoglycemia, Pancreatitis |

All entries include **medical coding**: ICD-10, MeSH, SNOMED CT, RxNorm, LOINC, MedDRA PT/SOC.

### What Gets Generated Per Trial

```mermaid
graph LR
    Trial[ClinicalTrial] --> Arms[2-3 Arms]
    Trial --> Interventions[Drug Interventions]
    Trial --> Eligibility[8 Criteria]
    Trial --> Outcomes[3-5 Endpoints]
    Trial --> Sites[Multi-region Sites]
    Trial --> Patients[10-30 Patients]

    Patients --> Demographics[Age, Sex, Race, Ethnicity]
    Patients --> Conditions[1-4 Conditions w/ ICD-10]
    Patients --> Meds[1-4 Medications w/ RxNorm]
    Patients --> AEs[0-5 Adverse Events w/ MedDRA]
    Patients --> Labs[3-8 Visits × 5-8 Lab Tests]
    Patients --> Vitals[3-6 Visits × 5 Vital Signs]
```

### Generated PDF Format

Each trial produces a multi-page PDF built with **ReportLab** that mimics a real ClinicalTrials.gov protocol document:

1. **Title Page**: NCT ID, sponsor, phase, status, therapeutic area, confidentiality notice
2. **Study Identification**: NCT number, sponsor ID, titles, collaborators
3. **Study Overview**: Brief summary, detailed description (LLM-parseable narratives)
4. **Study Design**: Phase, allocation, masking, intervention model, primary purpose
5. **Arms & Interventions**: Treatment vs. placebo arms, dosage, route, frequency, RxNorm codes
6. **Eligibility Criteria**: Numbered inclusion/exclusion criteria
7. **Outcome Measures**: Primary and secondary endpoints with time frames
8. **Study Locations**: Multi-region facility table
9. **Patient Data Summary**: Aggregate demographic statistics
10. **Individual Case Reports**: First 5 patients with full detail (demographics, conditions, medications, AEs, labs)
11. **Summary Table**: Remaining patients in a compact tabular format

### Lab Value Realism

Lab results follow Gaussian distributions centered on the normal range, with a **15% probability of abnormal values**. Each result carries an abnormal flag (`H`/`L`/`N`) computed against reference ranges.

---

## 🔄 Data Ingestion Pipeline

The Processor is a long-lived Kafka consumer that converts raw PDFs into structured, searchable data across all three stores.

### Pipeline Steps

```text
Download PDF (MinIO) → Parse (pdfplumber) → Extract (Regex + LLM) → Embed (OpenAI) → Load (PG + Qdrant + Neo4j)
```

### Embedding Strategy

The `ClinicalTrialEmbeddingGenerator` creates multiple chunk types per trial:

| Chunk Type | Content | Use Case |
|:---|:---|:---|
| `trial_summary` | Title, phase, condition, sponsor, brief summary | "Find trials for NSCLC" |
| `trial_design` | Study type, allocation, masking, model | "Which trials are double-blind?" |
| `eligibility_criterion` | One criterion per chunk | "Trials accepting patients over 65" |
| `intervention` | Drug name, dose, route, RxNorm code | "Trials using Pembrolizumab" |
| `outcome_measures` | All endpoints concatenated | "Trials measuring PFS" |
| `patient_narrative` | Natural language patient summary | "Patients with severe neutropenia" |
| `serious_adverse_event` | Individual SAE detail with MedDRA | "Serious hepatitis events" |

**Model**: `text-embedding-3-large` — **3072 dimensions** — with validation that every embedding matches the expected dimensionality before upserting to Qdrant.

### Neo4j Knowledge Graph Schema

```mermaid
graph LR
    T[ClinicalTrial] -->|TESTS_INTERVENTION| D[Drug]
    T -->|STUDIES| C[Condition]
    P[Patient] -->|ENROLLED_IN| T
    P -->|HAS_CONDITION| C
    P -->|TAKES_MEDICATION| D
    P -->|EXPERIENCED| AE[AdverseEvent]
    D -->|MAY_CAUSE| AE
    C -->|COMORBID_WITH| C2[Condition]
```

**Node Types**: `ClinicalTrial`, `Patient`, `Drug` (RxNorm), `Condition` (ICD-10), `AdverseEvent` (MedDRA), `LabTest` (LOINC)

**Uniqueness Constraints**: `trial_id`, `nct_id`, `patient_id`, `icd10_code`, `rxnorm_code`, `meddra_pt`, `loinc_code`

---

## 🔑 Authentication — Keycloak OIDC

Keycloak serves as the **OpenID Connect (OIDC) Identity Provider**. It issues **RS256-signed JWTs** that carry user identity, roles, and custom claims.

### Token Issuance Flow

```mermaid
sequenceDiagram
    participant Browser as React SPA
    participant KC as Keycloak
    participant API as FastAPI Gateway

    Browser->>KC: 1. Redirect to /auth (PKCE + code_challenge)
    KC->>Browser: 2. Login form
    Browser->>KC: 3. Submit credentials
    KC->>Browser: 4. Authorization code
    Browser->>KC: 5. Exchange code + code_verifier for tokens
    KC->>Browser: 6. Access Token (JWT) + Refresh Token

    Note over Browser: JWT contains:<br/>sub, preferred_username,<br/>realm_roles, organization_id

    Browser->>API: 7. GET /api/researcher/query<br/>Authorization: Bearer {JWT}
    API->>KC: 8. Fetch JWKS (cached 5 min)
    API->>API: 9. Verify JWT signature (RS256)
    API->>API: 10. Extract UserContext
```

### JWT Claims Used

| Claim | Source | Purpose |
|:---|:---|:---|
| `sub` | Keycloak standard | User ID (UUID) |
| `preferred_username` | Keycloak standard | Display name |
| `realm_access.roles` | Keycloak realm roles | Role determination (`domain_owner` > `manager` > `researcher`) |
| `organization_id` | Custom attribute mapper | Organization scoping for multi-tenancy |

### Realm Configuration

The `clinical-trials` realm is auto-imported via `realm-export.json` mounted into Keycloak. It defines:

- **Client**: `research-platform-api` (confidential client)
- **Roles**: `domain_owner`, `manager`, `researcher`
- **Attribute Mappers**: `organization_id` → JWT claim
- **Authentication**: PKCE (S256) required

---

## 🛡️ Fine-Grained Authorization — OpenFGA

OpenFGA implements **Google Zanzibar-style Relationship-Based Access Control (ReBAC)**. It answers questions like "Can user X view individual data for trial Y?" by evaluating a graph of relationship tuples.

### Authorization Model

```text
type user

type organization
  relations
    define member: [user]
    define manager: [user] and member
    define domain_owner: [user]

type clinical_trial
  relations
    define owner: [user]                              # Domain owner who published
    define granted_org: [organization]                 # Org-level access ceiling
    define assigned_researcher: [user]                 # Direct individual assignment
    define can_view_aggregate: member from granted_org  # COMPUTED
    define can_view_individual: assigned_researcher or owner  # COMPUTED

type patient
  relations
    define enrolled_in_trial: [clinical_trial]
    define can_view_individual: can_view_individual from enrolled_in_trial  # DERIVED

type cohort
  relations
    define assigned_researcher: [user]
    define includes_trial: [clinical_trial]
    define can_access: assigned_researcher or creator
```

### How Access Is Evaluated

```mermaid
flowchart TD
    A[User Request] --> B{Role = domain_owner?}
    B -->|Yes| C[Full Access - 1=1]
    B -->|No| D[Layer 1: OpenFGA]
    D --> E[ListObjects: can_view_aggregate]
    D --> F[ListObjects: can_view_individual]
    E --> G[Aggregate Trial IDs]
    F --> H[Individual Trial IDs]
    G --> I[Layer 2: PostgreSQL]
    H --> I
    I --> J[Load Cohort Scopes]
    J --> K{Has Cohort Filter?}
    K -->|Yes| L[Build patient WHERE clause<br/>age, sex, ethnicity, country, conditions]
    K -->|No| M[Unrestricted - all patients]
    L --> N[AccessProfile]
    M --> N
    N --> O[Inject into every tool call]
```

### Access Grant Chain

1. **Domain Owner** publishes a trial → writes `owner` tuple
2. **Domain Owner** approves organization access request → writes `granted_org` tuple → all org members get `can_view_aggregate`
3. **Manager** assigns a researcher to a trial → writes `assigned_researcher` tuple → researcher gets `can_view_individual`
4. **Manager** assigns a researcher to a cohort → cohort's `filter_criteria` are loaded from PostgreSQL and applied as patient-level WHERE clauses

---

## 🚧 The Access Level Ceiling Principle

This is the platform's core security mechanism for preventing data leakage in cross-trial queries.

```mermaid
flowchart LR
    subgraph "Researcher Dani's Access"
        T1["Trial A<br/>🟢 Individual"]
        T2["Trial B<br/>🟡 Aggregate"]
    end

    Q["Query: Compare AEs across<br/>Trial A and Trial B"] --> CEIL

    CEIL{Ceiling Principle} --> AGG["Force ALL results<br/>to Aggregate mode"]

    AGG --> R["Response:<br/>✅ Count of AEs per trial<br/>✅ Average severity<br/>❌ No individual patient rows"]
```

**Rule**: If a query spans trials where the researcher has **mixed access levels** (some individual, some aggregate), the entire response is forced to **aggregate level**. This prevents an attacker from correlating individual data from Trial A with aggregate counts from Trial B.

### Where It's Enforced

| Layer | Component | Enforcement |
|:---|:---|:---|
| **MCP Server** | `access_control.py` | `AccessContext.validate_trial_access()` resolves NCT↔UUID, checks access per trial |
| **MCP Tools** | Each of the 15 tool modules | Defensively calls `ctx.validate_trial_access()` before every query |
| **Agent Synthesizer** | `synthesizer.py` | Emits `⚠️ AGGREGATE-CEILING` warning if individual rows appear in aggregate context |
| **System Prompt** | `prompts.py` | Instructs the LLM: "if a query spans trials with mixed access, present ALL data at aggregate level" |

---

## 🔧 MCP Server — Tool Hub

The MCP Server exposes **15 clinical data tools** via the [Model Context Protocol](https://modelcontextprotocol.io/) using **Server-Sent Events (SSE)** + **JSON-RPC 2.0**.

### Registered Tools

| # | Tool | Data Source | Description |
|:---|:---|:---|:---|
| 1 | `search_trials` | PostgreSQL | Full-text search by condition, drug, phase, sponsor |
| 2 | `get_trial_details` | PostgreSQL | Complete trial metadata by UUID |
| 3 | `get_eligibility_criteria` | PostgreSQL | Inclusion/exclusion criteria |
| 4 | `get_outcome_measures` | PostgreSQL | Primary/secondary endpoints |
| 5 | `get_trial_interventions` | PostgreSQL | Drug, dose, route, RxNorm |
| 6 | `count_patients` | PostgreSQL | Counts with `group_by` (sex, age, arm, country, disposition) |
| 7 | `get_patient_demographics` | PostgreSQL | Individual rows or aggregate breakdown |
| 8 | `get_patient_disposition` | PostgreSQL | Completion/withdrawal rates |
| 9 | `get_adverse_events` | PostgreSQL | Safety data; filters: severity, serious, event_term |
| 10 | `get_lab_results` | PostgreSQL | Lab values by test, visit, abnormal flag |
| 11 | `get_vital_signs` | PostgreSQL | SYSBP, DIABP, HR, TEMP, WEIGHT over time |
| 12 | `get_concomitant_medications` | PostgreSQL | Concomitant medication data |
| 13 | `compare_treatment_arms` | PostgreSQL | Cross-arm statistical comparison |
| 14 | `find_drug_condition_relationships` | Neo4j (+PG fallback) | Graph traversal: Drug→MAY_CAUSE→AE, COMORBID_WITH |
| 15 | `search_documents` | Qdrant | Semantic vector search across protocol text |

### Dynamic Tool Discovery

The API agent does **not** maintain a static list of tool wrappers. Instead, `tool_wrappers.py` connects to the MCP Server at startup, discovers all available tools, and dynamically generates Pydantic schemas using `create_model()`. The `access_context` parameter is excluded from the LLM's view and injected transparently during execution.

### Keycloak Auth Middleware on MCP

The MCP Server has its own `KeycloakAuthMiddleware` that validates JWT tokens on all `/sse` endpoints. Only the API Gateway (which has a service-account JWT) can connect.

---

## 🤖 Agentic Reasoning — LangGraph ReAct

The agent is built as a **LangGraph StateGraph** with 4 nodes:

```mermaid
stateDiagram-v2
    [*] --> Guardrails
    Guardrails --> Agent: Access OK
    Guardrails --> Synthesizer: Access Denied
    Agent --> Tools: tool_calls present
    Agent --> Synthesizer: No tool_calls (final answer)
    Tools --> Agent: Tool results
    Synthesizer --> [*]
```

### Node Responsibilities

| Node | Module | Role |
|:---|:---|:---|
| **Guardrails** | `guardrails.py` | Validates prompt injection, checks user has any access |
| **Agent** | `agent_node.py` | GPT-4o with function calling; selects tools from 15 options |
| **Tools** | `tool_node.py` | Executes MCP tool calls, injects `access_context`, returns results |
| **Synthesizer** | `synthesizer.py` | Formats final response, applies ceiling warnings, extracts sources |

### Query Complexity Routing

Queries are classified as `simple` or `complex` based on keyword heuristics (e.g., "compare", "trend", "across"). Simple queries use `GPT-4o-mini` for cost efficiency; complex queries use `GPT-4o`.

### System Prompt Engineering

The system prompt is assembled **per-query** by injecting:
- The researcher's access summary (which trials, which level)
- Active cohort filters in human-readable form
- Security guardrails (anti-prompt-injection directives, anti-chatter rules)
- CDISC domain knowledge (phases, sex values, severity levels)

---

## 💻 Frontend — React + Keycloak SPA

The frontend is a **React + TypeScript** SPA that authenticates via **Keycloak PKCE** and provides role-based dashboards.

### Role-Based Routing

| Role | Dashboard | Features |
|:---|:---|:---|
| `domain_owner` | `/owner` | Publish trials, approve organization access requests, manage data assets |
| `manager` | `/manager` | Assign researchers to trials/cohorts, build cohort filters, browse marketplace |
| `researcher` | `/researcher` | Natural language query interface, view accessible trials, chat history |

### Query Interface Features

- **Multi-turn Chat**: Session-based conversation with history stored in `localStorage` and backend checkpointing
- **Trial Scope Selector**: Sidebar with checkboxes to narrow queries to specific trials; each trial shows its access level badge (`individual` / `aggregate`)
- **Live Tool Visualization**: Real-time display of which MCP tools are being called, their execution duration (ms), and success/error status
- **Streaming Responses**: SSE-based token streaming with a blinking cursor animation
- **Access Level Footer**: Every response displays the access level applied, the LLM model used, and active cohort filters
- **Suggested Queries**: Pre-composed example queries as clickable pills

### Keycloak Integration

```typescript
// keycloak.ts
keycloak.init({
    onLoad: 'login-required',     // Force login before app renders
    checkLoginIframe: false,       // Avoid CORS issues
    pkceMethod: 'S256'            // PKCE for public client security
});
```

---

## 📂 Project Structure

```text
clinical-trial/
├── api/                          # FastAPI + LangGraph Agent
│   ├── agent/
│   │   ├── graph.py              # LangGraph StateGraph builder (4 nodes)
│   │   ├── prompts.py            # Dynamic system prompt assembly
│   │   ├── tool_wrappers.py      # Dynamic MCP tool discovery + Pydantic schema generation
│   │   ├── access_context.py     # AccessContext serialization (UUID ↔ NCT mapping)
│   │   ├── service.py            # Agent service (entry point per query)
│   │   ├── models.py             # AgentState TypedDict
│   │   └── nodes/
│   │       ├── guardrails.py     # Prompt injection detection, access gate
│   │       ├── agent_node.py     # GPT-4o function calling
│   │       ├── tool_node.py      # MCP tool execution with access_context injection
│   │       └── synthesizer.py    # Response formatting, ceiling warnings, source extraction
│   ├── routers/                  # FastAPI routers per role
│   │   ├── researcher.py         # /api/researcher/query (streaming)
│   │   ├── manager.py            # /api/manager/assign, /api/manager/cohorts
│   │   └── domain_owner.py       # /api/owner/trials, /api/owner/access-requests
│   └── main.py                   # FastAPI app with lifespan, CORS, middleware
│
├── mcp_server/                   # FastMCP Tool Server
│   ├── server.py                 # Starlette app, tool registration, Keycloak middleware
│   ├── access_control.py         # AccessContext class, ceiling principle, NCT↔UUID resolution
│   ├── tools/
│   │   ├── trial_discovery.py    # search_trials
│   │   ├── trial_metadata.py     # get_trial_details, eligibility, outcomes, interventions
│   │   ├── patient_analytics.py  # demographics, disposition, count, medications, vitals
│   │   ├── clinical_analysis.py  # adverse_events, lab_results, compare_arms
│   │   └── knowledge_discovery.py # find_drug_condition_relationships, search_documents
│   ├── db/                       # Database client wrappers
│   │   ├── postgres.py           # asyncpg connection pool
│   │   ├── qdrant_client.py      # Qdrant async client
│   │   └── neo4j_client.py       # Neo4j async driver
│   └── test_tools.py             # Diagnostic tool registration tests
│
├── generator/                    # Synthetic Data Engine (Kafka Producer)
│   ├── synthetic_data.py         # ClinicalTrialGenerator class, THERAPEUTIC_AREAS reference data
│   ├── pdf_builder.py            # ReportLab PDF generation (mimics ClinicalTrials.gov)
│   ├── publisher.py              # Idempotent Kafka producer + MinIO upload (claim-check)
│   └── main.py                   # Entry point: generate batch → build PDFs → publish events
│
├── processor/                    # Data Ingestion Pipeline (Kafka Consumer)
│   ├── orchestrator.py           # 5-step pipeline: Download → Parse → Extract → Embed → Load
│   ├── pdf_parser.py             # pdfplumber-based section extraction
│   ├── entity_extractor.py       # Hybrid extraction: regex + table classification + GPT-4o
│   ├── embedding_generator.py    # text-embedding-3-large chunking with dimension validation
│   ├── loaders/
│   │   ├── postgres_loader.py    # Relational ingestion (trials, patients, AEs, labs)
│   │   ├── qdrant_loader.py      # Vector upsert with UUID5 chunk IDs
│   │   └── neo4j_loader.py       # Graph node/relationship creation with constraints
│   └── consumer.py               # Kafka consumer loop with error handling
│
├── auth/                         # Security Layer
│   ├── middleware.py              # JWT verification, JWKS caching, UserContext extraction
│   ├── authorization_service.py   # Two-layer AccessProfile computation (OpenFGA + PG cohorts)
│   ├── openfga_client.py          # Async OpenFGA client (check, list-objects, write/delete tuples)
│   ├── openfga/
│   │   ├── model.fga              # OpenFGA authorization model (DSL)
│   │   ├── model.json             # Compiled model for API upload
│   │   └── init_store.py          # Store + model bootstrap script
│   ├── cohort_service.py          # Cohort CRUD, filter criteria, trial linkage
│   ├── asset_service.py           # Data asset management, dynamic collections
│   ├── access_request_service.py  # Organization access request workflow
│   └── secure_query_executor.py   # SQL query executor with access profile injection
│
├── shared/                       # Shared Code (mounted as volume)
│   ├── models.py                 # 20+ Pydantic models (CDISC enums, ClinicalTrial, Patient, etc.)
│   ├── config.py                 # Centralized configuration from environment
│   ├── kafka_schemas.py          # Kafka event schemas (PDFGeneratedEvent, TrialIngestedEvent)
│   └── storage.py                # MinIO client wrapper
│
├── frontend/                     # React + TypeScript SPA
│   ├── src/
│   │   ├── App.tsx               # Keycloak init, role-based routing
│   │   ├── keycloak.ts           # Keycloak-js adapter config (PKCE S256)
│   │   ├── components/
│   │   │   └── researcher/
│   │   │       └── QueryInterface.tsx  # Chat UI with streaming, tool visualization
│   │   ├── hooks/
│   │   │   └── useStreamingQuery.ts    # SSE streaming hook for real-time responses
│   │   └── pages/
│   │       ├── ResearcherDashboard.tsx
│   │       ├── ManagerDashboard.tsx
│   │       ├── DomainOwnerDashboard.tsx
│   │       ├── Marketplace.tsx
│   │       └── CohortBuilder.tsx
│   └── Dockerfile                # Multi-stage build (Vite → Nginx)
│
├── migrations/                   # Alembic Database Migrations
│   └── versions/
│       ├── a001_initial.py       # Core clinical trial schema
│       ├── a002_auth.py          # Auth tables (researcher_assignment, access_request)
│       ├── a003_collection.py    # Dynamic collection tables
│       └── a004_fix_unique_constraints.py
│
├── sql/
│   ├── init.sql                  # Full PostgreSQL schema (clinical_trial, patient, adverse_event, lab_result, etc.)
│   ├── auth_tables.sql           # researcher_assignment, cohort, cohort_trial tables
│   └── init-databases.sh         # Creates 3 databases: clinical_trials, keycloak, openfga
│
├── scripts/
│   ├── trigger_generation.sh     # Shell script: generate trials → wait for processing → show stats
│   ├── create_kafka_topics.sh    # Create topics with retention/partition config
│   └── bootstrap_auth.sh         # Initialize Keycloak realm + OpenFGA store
│
├── docker-compose.yml            # 15+ services with health checks and dependency ordering
├── Makefile                      # Common command shortcuts
└── .env                          # Environment variables (OPENAI_API_KEY, secrets)
```

---

## 🚀 Getting Started

### Prerequisites

- **Docker Desktop** (latest)
- **Python 3.12+** (for local scripts)
- **OpenAI API Key** (embeddings + agent reasoning)

### 1. Configure Environment

```bash
cp .env.example .env
# Edit .env with your OPENAI_API_KEY
```

### 2. Build & Launch

```bash
make build
make up
```

### 3. Bootstrap Auth

```bash
# Setup Keycloak realm (auto-imports realm-export.json)
./scripts/bootstrap_auth.sh

# Create Kafka topics
./scripts/create_kafka_topics.sh
```

### 4. Generate Synthetic Data

```bash
# Generate 5 trials across Oncology/Cardiology/Endocrinology
./scripts/trigger_generation.sh --trials 5 --patients 20 --wait

# Options:
#   --trials, -t NUM      Number of trials (default: 10)
#   --patients, -p NUM    Patients per trial (default: 20)
#   --seed, -s NUM        Reproducibility seed (default: 42)
#   --wait, -w            Wait for processor to finish all events
```

The `--wait` flag blocks until all Kafka events are consumed and the `show_statistics` function reports record counts across PostgreSQL, Qdrant, Neo4j, and MinIO.

---

## 🛠️ Common Commands

| Command | Description |
|:---|:---|
| `make up` | Start all services in detached mode |
| `make down` | Stop all services |
| `make build` | Rebuild all Docker images |
| `make test-agent` | Run agent tests as `researcher-jane` (full access) |
| `make test-agent-dani` | Run agent tests as `researcher-dani` (mixed access — tests ceiling principle) |
| `make test-agent-query Q="..."` | Ask a single custom question |
| `make test_mcp` | Run MCP tool registration diagnostics |
| `make logs-agent` | Tail API logs filtered to agent/tool/MCP activity |
| `make logs-mcp` | Tail MCP server logs |
| `make health-check` | Verify PostgreSQL, MCP, and API are reachable |

### Useful Docker Commands

```bash
# Check record counts in PostgreSQL
docker compose exec -T postgres psql -U ctuser -d clinical_trials -c \
  "SELECT COUNT(*) AS trials FROM clinical_trial;
   SELECT COUNT(*) AS patients FROM patient;
   SELECT COUNT(*) AS adverse_events FROM adverse_event;"

# Reset Kafka consumer offset to reprocess all PDFs
docker compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:29092 \
  --group pdf-processor-group \
  --reset-offsets --to-earliest \
  --topic pdf-generated --execute

# Clear all data (PostgreSQL)
docker compose exec -T postgres psql -U ctuser -d clinical_trials -c \
  "TRUNCATE patient CASCADE; TRUNCATE clinical_trial CASCADE; TRUNCATE ingestion_log;"

# Clear Neo4j graph
docker compose exec neo4j cypher-shell -u neo4j -p neo4jpassword \
  "MATCH (n) DETACH DELETE n"

# Check Qdrant vector count
curl -s http://localhost:6333/collections/clinical_trial_embeddings | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['result']['points_count'])"
```

---

## 🖥️ Dashboards & Exploration

| Service | URL | Credentials |
|:---|:---|:---|
| **Frontend (React)** | `http://localhost:3001` | Keycloak login |
| **Agent API** | `http://localhost:8000/docs` | Swagger UI |
| **MCP Server** | `http://localhost:8001/health` | Service-to-service |
| **Keycloak Admin** | `http://localhost:8180/admin` | `admin` / `admin` |
| **OpenFGA Playground** | `http://localhost:3000/playground` | N/A |
| **Neo4j Browser** | `http://localhost:7474/browser` | `neo4j` / `neo4jpassword` |
| **Qdrant Dashboard** | `http://localhost:6333/dashboard` | N/A |
| **Kafka UI** | `http://localhost:8080` | N/A |
| **MinIO Console** | `http://localhost:9001` | `minioadmin` / `minioadmin123` |

---

## 🧪 Testing & Validation

### End-to-End Agent Testing

```bash
# Full-access researcher (should see individual patient rows)
make test-agent

# Mixed-access researcher (should trigger aggregate ceiling)
make test-agent-dani

# Custom question
make test-agent-query Q="What are the serious adverse events in the oncology trials?"
```

### MCP Tool Validation

```bash
# Verify all 15 tools are registered and callable
make test_mcp
```

### Access Control Verification

Test that `researcher-dani` (who has individual access to Trial A and aggregate access to Trial B) gets **aggregate-only** responses when querying across both trials:

```bash
make test-agent-dani
# Expected: No individual patient rows in the response
# Expected: Aggregate statistics only (counts, averages)
```

---

## 📜 Topics Covered

| Category | Topics |
|:---|:---|
| **Distributed Systems** | Kafka event streaming, idempotent producers, consumer groups, dead-letter queues, partition ordering by NCT ID |
| **Security Engineering** | OIDC/Keycloak JWT (RS256), PKCE S256, JWKS caching, Zanzibar-style ReBAC (OpenFGA), fail-closed defaults |
| **Data Mesh** | Multi-modal data products (Relational + Graph + Vector), domain-oriented ownership, self-serve data platform |
| **Knowledge Graphs** | Neo4j Cypher, labeled property graphs, drug-condition-AE relationships, comorbidity inference, full-text indexes |
| **Vector Search** | OpenAI `text-embedding-3-large` (3072-dim), semantic document retrieval, chunk typing, dimension validation |
| **LLM Orchestration** | LangGraph StateGraph, ReAct loop, function calling, query complexity routing, dynamic tool discovery, prompt injection guardrails |
| **Clinical Informatics** | CDISC-like modeling (SDTM/CDASH patterns), ICD-10, MeSH, SNOMED CT, RxNorm, LOINC, MedDRA PT/SOC |
| **Modern Auth Patterns** | Two-layer access control, access level ceiling principle, cohort-based patient filtering, organization-scoped multi-tenancy |
| **Event-Driven Architecture** | Claim-check pattern (MinIO + Kafka), asynchronous ingestion, event sourcing (TrialIngestedEvent) |
| **Document Processing** | PDF generation (ReportLab), PDF parsing (pdfplumber), hybrid entity extraction (regex + LLM), table classification |
| **Frontend Engineering** | React + TypeScript, Keycloak-js PKCE, SSE streaming, role-based routing, real-time tool visualization |
| **Infrastructure** | Docker Compose orchestration (15+ services), health checks, Alembic migrations, multi-database PostgreSQL |

---

> [!IMPORTANT]
> This platform uses **synthetic data only**. Do not use with real PHI (Protected Health Information) without proper HIPAA/GDPR compliance audits of the underlying infrastructure.