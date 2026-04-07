# Clinical Trial Data Mesh Platform — Agent Context Prompt

> **Purpose**: Paste this document at the start of a new agent conversation so it has full context to continue building the platform. It captures architecture, patterns, design decisions, database schema, file layout, and known issues.

---

## 1. Project Overview

This is a **Clinical Trial Research Platform** implementing a **Data Mesh** architecture with fine-grained authorization. It enables pharmaceutical organizations to:

1. **Generate** synthetic clinical trial PDFs (on-demand container)
2. **Ingest & process** PDFs via Kafka → extract entities with OpenAI → store in PostgreSQL, Qdrant (vectors), Neo4j (knowledge graph)
3. **Publish** trial data as "Data Assets" grouped into "Collections" via a Domain Owner dashboard
4. **Discover & Request** access to collections via a Marketplace (Manager role)
5. **Build Cohorts** — define patient subsets using multi-dimensional filters (Manager role)
6. **Assign Researchers** — grant individual or aggregate access to trials/cohorts (Manager role)
7. **Query** data via an Agentic RAG pipeline (Researcher role)

The platform enforces a **ceiling principle**: organizations can only access trials they've been explicitly granted access to. All authorization is enforced at the API level via **OpenFGA** (fine-grained authorization) and **Keycloak** (OAuth2/OIDC identity).

---

## 2. Technology Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| **Identity / AuthN** | Keycloak 25.0 | OIDC, JWT, realm `clinical-trials`, PKCE |
| **Authorization / AuthZ** | OpenFGA v1.8.2 | Relationship-based access control (ReBAC) |
| **API Gateway** | FastAPI (Python 3.12) | `asyncpg` for async PostgreSQL, `python-jose` for JWT |
| **Frontend** | React 18 + TypeScript + Vite | TailwindCSS, `lucide-react` icons, `keycloak-js` |
| **Database** | PostgreSQL 16 (shared instance, 3 databases) | `clinical_trials`, `keycloak`, `openfga` |
| **Vector Store** | Qdrant v1.9.2 | Embeddings for semantic search |
| **Knowledge Graph** | Neo4j 5.19 Community | APOC plugin, Cypher queries |
| **Message Broker** | Confluent Kafka 7.6.0 + Zookeeper | Topics: `pdf-generated`, `pdf-processing-status`, `trial-ingested`, etc. |
| **Object Storage** | MinIO | Bucket: `clinical-trial-pdfs` |
| **Migrations** | Alembic | Revisions prefixed `a001`, `a002`, `a003`, `a004` |
| **Monitoring** | Kafka UI (Provectus) | Port 8080 |

---

## 3. Project File Structure

```
clinicalTrail/
├── docker-compose.yml          # All services definition
├── .env                        # OPENAI_API_KEY, KEYCLOAK_CLIENT_SECRET, OPENFGA_STORE_ID
│
├── api/                        # FastAPI API Gateway
│   ├── Dockerfile
│   ├── main.py                 # App entrypoint, lifespan, CORS, routers
│   ├── database.py             # asyncpg pool init/close/dependency
│   ├── collection_consumer.py  # Kafka consumer for collection refresh
│   └── routers/
│       ├── domain_owner.py     # /api/v1/assets/*, /api/v1/access-requests/*, /api/v1/grants/*
│       ├── manager.py          # /api/v1/manager/*, /api/v1/cohorts/*, /api/v1/assignments/*
│       ├── marketplace.py      # /api/v1/marketplace/*
│       └── researcher.py       # /api/v1/research/*
│
├── auth/                       # Authorization layer (mounted read-only into api container)
│   ├── middleware.py            # JWT verification, UserContext extraction
│   ├── dependencies.py         # FastAPI deps: CurrentUser, require_role()
│   ├── openfga_client.py       # OpenFGA HTTP wrapper: check, write_tuple, list_objects
│   ├── asset_service.py        # Collection & asset publishing logic
│   ├── cohort_service.py       # Cohort CRUD, preview, dynamic patient filtering
│   ├── access_request_service.py # Access request/review workflow
│   ├── authorization_service.py  # High-level authz helpers
│   ├── secure_query_executor.py  # Row-level security query builder
│   ├── keycloak/
│   │   └── realm-export.json   # Keycloak realm config (users, roles, clients)
│   └── openfga/
│       ├── model.json          # FGA authorization model
│       └── init_store.py       # Creates FGA store + writes model on startup
│
├── frontend/                   # React SPA
│   ├── Dockerfile              # Node 20 Alpine, serves via Vite dev server
│   ├── nginx.conf              # Reverse proxy to API
│   ├── vite.config.ts          # Proxy /api → http://api-gateway:8000
│   ├── src/
│   │   ├── App.tsx             # Route definitions, Keycloak init
│   │   ├── keycloak.ts         # Keycloak-js configuration
│   │   ├── api/
│   │   │   └── client.ts       # All API types + endpoint functions
│   │   ├── components/
│   │   │   ├── Layout.tsx      # Sidebar nav, role-aware
│   │   │   └── ProtectedRoute.tsx
│   │   ├── pages/
│   │   │   ├── DomainOwnerDashboard.tsx  # Publish collections, manage grants
│   │   │   ├── ManagerDashboard.tsx      # View grants, cohorts, assignments
│   │   │   ├── CohortBuilder.tsx         # Multi-filter cohort builder
│   │   │   ├── Marketplace.tsx           # Browse & request access
│   │   │   └── ResearcherDashboard.tsx   # Query interface
│   │   └── types/
│   │       └── index.ts
│
├── generator/                  # Synthetic data generator (on-demand profile)
│   ├── main.py                 # Entrypoint: generate N trials × M patients
│   ├── synthetic_data.py       # Faker-based clinical data generation
│   ├── pdf_builder.py          # ReportLab PDF construction
│   └── publisher.py            # Kafka producer: publishes to pdf-generated topic
│
├── processor/                  # PDF processing pipeline (always running)
│   ├── main.py                 # Consumer entrypoint
│   ├── consumer.py             # Kafka consumer loop
│   ├── orchestrator.py         # Processing pipeline orchestration
│   ├── pdf_parser.py           # PDF text extraction
│   ├── entity_extractor.py     # OpenAI-based NER for clinical entities
│   ├── embedding_generator.py  # Text chunk embedding → Qdrant
│   └── loaders/                # DB loaders for extracted entities
│
├── migrations/                 # Alembic migrations
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
│       ├── a001_initial.py
│       ├── a002_auth.py
│       ├── a003_collection.py
│       └── a004_fix_unique_constraints.py
│
├── sql/                        # Bootstrap SQL (run by Postgres init)
│   ├── init-databases.sh       # Creates 3 databases + users
│   ├── init.sql                # Clinical data schema (trials, patients, labs, etc.)
│   └── auth_tables.sql         # Auth schema (assets, collections, grants, etc.)
│
├── shared/                     # Shared Python modules
│   └── kafka_schemas.py        # Avro-like schema definitions
│
└── scripts/                    # Utility scripts
```

---

## 4. Database Schema

### Clinical Data Tables (`init.sql`)
- `clinical_trial` — Trial metadata (nct_id, phase, therapeutic_area, regions, countries, etc.)
- `trial_arm` — Treatment arms per trial
- `intervention` — Drugs/interventions per arm
- `eligibility_criteria` — Inclusion/exclusion criteria
- `outcome_measure` — Primary/secondary endpoints
- `patient` — **Core patient table** (subject_id, age, sex, race, ethnicity, country, arm_assigned, disposition_status)
- `patient_trial_enrollment` — Many-to-many: patient ↔ trial (UNIQUE patient_id+trial_id)
- `patient_condition` — Patient conditions (condition_name, icd10_code, mesh_term, snomed_code)
- `patient_medication` — Medications
- `adverse_event` — AEs linked to patient + trial
- `lab_result` — Lab values with LOINC codes
- `vital_sign` — Vital signs
- `cohort` — Cohort definition (filter_criteria JSONB, is_dynamic boolean)
- `cohort_patient` — Cohort ↔ patient membership (currently empty for dynamic cohorts — by design)
- `cohort_trial` — Cohort ↔ trial membership
- `ingestion_log` — PDF processing tracking

### Auth Tables (`auth_tables.sql`)
- `data_asset` — Published trial assets (UNIQUE reference_id+asset_type)
- `data_asset_collection` — Logical groupings of trials with filter criteria
- `collection_asset` — Junction: collection ↔ asset
- `access_request` — Request to access asset or collection
- `access_grant` — Per-trial grants with computed `is_active` column
- `researcher_assignment` — Researcher ↔ trial/cohort access
- `auth_audit_log` — Audit trail

---

## 5. Key Architectural Patterns & Decisions

### 5.1 Identity & Authorization Flow
```
Browser → Keycloak (PKCE login) → JWT with claims:
  - sub (user_id)
  - preferred_username
  - organization_id (custom claim in Keycloak user attributes)
  - organization_name
  - realm_access.roles → [domain_owner | manager | researcher]

JWT → FastAPI middleware (auth/middleware.py) → UserContext
UserContext → require_role() dependency → route handler
Route handler → CohortService / AssetService → OpenFGA check
```

### 5.2 Role Hierarchy
| Role | Capabilities |
|------|-------------|
| `domain_owner` | Publish collections, review access requests, manage grants |
| `manager` | Browse marketplace, request access, build cohorts, assign researchers |
| `researcher` | Query data within authorized scope |

### 5.3 Ceiling Principle
- Organizations receive **grants** for specific trials (via collection access requests)
- Grants are stored in `access_grant` table AND as OpenFGA tuples
- `CohortService._verify_ceiling()` checks that all trial_ids in a cohort are accessible to the organization
- OpenFGA relationship: `organization:{org_id}#member` can `viewer` on `trial:{trial_id}`

### 5.4 Dynamic vs Static Cohorts
- **Dynamic cohorts** (`is_dynamic=true`): Patient membership is computed at query time via `_build_patient_filter_query()`. The `cohort_patient` table stays empty — this is by design.
- **Static cohorts**: Would materialize patient IDs into `cohort_patient` (not yet implemented)

### 5.5 Filter Criteria Model
The `filter_criteria` JSONB column on both `cohort` and `data_asset_collection` tables stores query parameters. For cohorts, the current supported filters are:

```json
{
  "trial_ids": ["uuid1", "uuid2"],
  "therapeutic_areas": ["Oncology"],
  "conditions": ["Non-Small Cell Lung Cancer"],
  "age_min": 18,
  "age_max": 65,
  "sex": ["M", "F"],
  "phases": ["Phase 1", "Phase 2"],
  "country": ["Argentina", "Spain"],
  "ethnicity": ["Hispanic or Latino"],
  "disposition_status": ["Completed", "Enrolled"],
  "arm_assigned": ["Placebo Arm", "Nivolumab Treatment Arm"]
}
```

### 5.6 Dynamic Filter Options
- The backend exposes `GET /api/v1/manager/cohorts/filter-options` which queries **distinct values from the database** for conditions, country, ethnicity, disposition_status, and arm_assigned.
- The CohortBuilder frontend fetches these on mount via `Promise.all` and renders toggle buttons dynamically.
- **IMPORTANT**: `asyncpg` does NOT support concurrent queries on a single connection. Use sequential `await` calls within `async with pool.acquire() as conn:`, never `asyncio.gather()` on the same connection.

### 5.7 asset_id vs trial_id
- `data_asset.asset_id` is the marketplace/grant-level identifier
- `data_asset.reference_id` is the actual `clinical_trial.trial_id`
- The manager grants endpoint returns `da.reference_id AS trial_id`
- **Always use `trial_id` (reference_id)** for cohort filtering and ceiling checks, never `asset_id`

### 5.8 Frontend API Client Pattern
All API calls go through `frontend/src/api/client.ts`:
- `request<T>(path, options)` — adds JWT auth headers via `keycloak.updateToken()`
- API namespaces: `domainOwnerApi`, `managerApi`, `researcherApi`
- Types are co-located in the same file

### 5.9 Frontend Routing
```
/owner/*        → DomainOwnerDashboard (domain_owner only)
/manager/*      → ManagerDashboard (manager only)
/marketplace    → Marketplace (manager only)
/cohorts/new    → CohortBuilder (manager only)
/researcher/*   → ResearcherDashboard (researcher only)
```

---

## 6. Keycloak Configuration

- **Realm**: `clinical-trials`
- **Client**: `research-platform` (public, PKCE) for frontend
- **Client**: `research-platform-api` (confidential) for backend
- **Users** have custom attributes: `organization_id`, `organization_name`
- **Roles**: `domain_owner`, `manager`, `researcher` (realm-level)
- Realm export is at `auth/keycloak/realm-export.json`
- Keycloak URL: `http://localhost:8180`

---

## 7. OpenFGA Configuration

- **Store** is auto-created by `auth/openfga/init_store.py` on startup
- Model defines types: `organization`, `trial`, `cohort`, `asset`
- Key relationships:
  - `organization:{org_id}#member` → `trial:{trial_id}#viewer`
  - `organization:{org_id}#member` → `cohort:{cohort_id}#owner`
- **Fail-closed**: If OpenFGA is unreachable, access is denied
- OpenFGA Playground: `http://localhost:8082/playground`

---

## 8. Data Pipeline

```
Generator (on-demand) → Kafka (pdf-generated) → Processor (always-on)
    │                                                │
    ├── Generates synthetic PDFs                     ├── Parses PDF text
    ├── Uploads to MinIO                             ├── Extracts entities (OpenAI)
    └── Publishes Kafka event                        ├── Loads to PostgreSQL
                                                     ├── Generates embeddings → Qdrant
                                                     └── Builds knowledge graph → Neo4j
```

Run generator: `docker compose run --rm generator`

---

## 9. Running the System

```bash
# Start all infrastructure + services
docker compose up -d

# Generate synthetic data (10 trials × 20 patients)
docker compose run --rm generator

# Rebuild specific containers after code changes
docker compose up -d --build api frontend

# View API logs
docker compose logs -f api-gateway

# Access PostgreSQL
docker compose exec postgres psql -U ctuser -d clinical_trials

# Keycloak admin: http://localhost:8180 (admin/admin)
# Frontend: http://localhost:3001
# API: http://localhost:8000
# Kafka UI: http://localhost:8080
# Neo4j Browser: http://localhost:7474
# MinIO Console: http://localhost:9001
```

---

## 10. Known Issues & Gotchas

1. **asyncpg single-connection concurrency**: A single `asyncpg` connection does NOT support parallel queries. Always use sequential `await` inside a `conn` context. Use `asyncio.gather` only with separate connections from the pool.

2. **Sex field values**: The database stores `M` and `F`, not `Male`/`Female`. The frontend CohortBuilder uses `['M', 'F']`.

3. **Phase naming**: Database stores phases as `Phase 1`, `Phase 2`, etc. (not `Phase I`, `Phase II`).

4. **Computed `is_active` columns**: Both `access_grant.is_active` and `researcher_assignment.is_active` are `GENERATED ALWAYS AS (revoked_at IS NULL AND expires_at > NOW()) STORED`. You cannot INSERT or UPDATE these columns directly.

5. **CORS**: Only `http://localhost:3001` is allowed. Update `api/main.py` if the frontend port changes.

6. **Unused imports lint warnings**: `Shield`, `Plus` in ManagerDashboard; `FlaskConical`, `Users`, `user` in CohortBuilder — these are cosmetic warnings, not blocking.

7. **Dynamic cohorts**: `cohort_patient` table is intentionally empty for dynamic cohorts. Patient membership is computed at query time.

8. **Collection refresh**: The API gateway runs a Kafka consumer (`CollectionRefreshConsumer`) that listens for `trial-ingested` events and auto-updates dynamic collections.

---

## 11. What Has Been Built So Far

### Fully Implemented
- ✅ Synthetic data generation pipeline (Generator → Kafka → Processor → all stores)
- ✅ Domain Owner Dashboard (publish collections, review requests, manage grants)
- ✅ Marketplace (browse collections, request access)
- ✅ Manager Dashboard (view grants, cohorts with filter detail tags, assignments)
- ✅ Cohort Builder with dynamic multi-dimensional filtering (trial, condition, age, sex, phase, country, ethnicity, disposition_status, arm_assigned)
- ✅ Dynamic filter options loaded from database (not hardcoded)
- ✅ Researcher assignment workflow
- ✅ OpenFGA integration for ceiling enforcement
- ✅ Keycloak authentication with role-based routing
- ✅ Alembic migrations
- ✅ Filter criteria detail display on created cohorts

### Partially Implemented / Next Steps
- 🔲 **Researcher Agentic RAG**: The `ResearcherDashboard.tsx` has a basic query interface but the backend RAG pipeline (`/research/query`) needs Qdrant + Neo4j integration for semantic search
- 🔲 **Static cohort materialization**: Insert patient IDs into `cohort_patient` for static cohorts
- 🔲 **Cohort refresh**: Periodic re-evaluation of dynamic cohort counts
- 🔲 **Audit logging**: `auth_audit_log` table exists but is not populated
- 🔲 **Grant revocation UI**: Backend exists but no frontend controls
- 🔲 **Pagination**: API endpoints return all results; add cursor/offset pagination
- 🔲 **Search/filter in Marketplace**: Currently shows all collections
- 🔲 **Researcher access enforcement in RAG**: Ensure queries only return data from authorized trials
- 🔲 **OpenAI function calling / tool use**: For the agentic researcher query interface
- 🔲 **Observability**: Phoenix/Arize tracing for LLM calls, Argilla for human feedback

---

## 12. Development Workflow

1. Make code changes to `api/`, `auth/`, or `frontend/src/`
2. Rebuild: `docker compose up -d --build api frontend`
3. The `auth/` directory is mounted read-only into the API container, so changes there require rebuilding `api`
4. Frontend changes require rebuilding `frontend`
5. Database schema changes: create a new Alembic migration in `migrations/versions/`
6. Test API manually via the frontend at `http://localhost:3001`
7. Check API logs: `docker compose logs -f api-gateway`

---

## 13. Environment Variables

```env
OPENAI_API_KEY=sk-...
KEYCLOAK_CLIENT_SECRET=research-platform-secret
OPENFGA_STORE_ID=<auto-set by init script>
NUM_TRIALS=10
PATIENTS_PER_TRIAL=20
SEED=42
```
