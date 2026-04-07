Clinical Trial Data Mesh Platform — Agentic RAG Context Prompt

Purpose: Paste this document at the start of a new agent conversation to build the Agentic Semantic Query system. It captures the full platform architecture, authorization model, access profile computation, and the data stores that the agent will query.

1. Project Overview

This is a Clinical Trial Research Platform implementing a Data Mesh architecture with fine-grained authorization. The platform is fully built except for the Researcher Agentic RAG pipeline — the final piece that lets researchers query their authorized data using natural language.

What's Built
✅ Synthetic clinical trial PDF generation → Kafka → Processing pipeline
✅ Entity extraction (OpenAI) → PostgreSQL, Qdrant (vectors), Neo4j (knowledge graph)
✅ Domain Owner Dashboard — publish collections, review access requests
✅ Marketplace — browse & request access to collections
✅ Manager Dashboard — grant access, build cohorts, assign researchers
✅ Cohort Builder — multi-dimensional patient filtering
✅ Researcher Dashboard — shows individual & aggregate trial access with cohort filter details
✅ OpenFGA (ReBAC) + Keycloak (OIDC) authorization enforced at API level
✅ Access profile computation (2-layer: OpenFGA trials + PostgreSQL cohort filters)
What Needs to Be Built (This Conversation)
🔲 Agentic Semantic Query System for the Researcher role:

Natural language queries against authorized clinical trial data
Multi-tool agent using OpenAI LLM + MCP tools
Queries across 3 data stores: PostgreSQL (structured), Qdrant (vectors), Neo4j (knowledge graph)
Authorization-aware: filters results based on the researcher's computed access profile
Enforces ceiling principle + cohort-level patient filters
2. Technology Stack

Layer	Technology	Notes
Identity / AuthN	Keycloak 25.0	OIDC, JWT, realm clinical-trials, PKCE
Authorization / AuthZ	OpenFGA v1.8.2	Relationship-based access control (ReBAC)
API Gateway	FastAPI (Python 3.12)	asyncpg for async PostgreSQL, python-jose for JWT
Frontend	React 18 + TypeScript + Vite	TailwindCSS, lucide-react icons, keycloak-js
Database	PostgreSQL 16	clinical_trials database
Vector Store	Qdrant v1.9.2	Embeddings for semantic search
Knowledge Graph	Neo4j 5.19 Community	APOC plugin, Cypher queries
Message Broker	Confluent Kafka 7.6.0	Topics: pdf-generated, trial-ingested, etc.
Object Storage	MinIO	Bucket: clinical-trial-pdfs
LLM	OpenAI GPT-4o / GPT-4o-mini	Entity extraction + agentic queries
3. Project File Structure

text

clinicalTrail/
├── docker-compose.yml
├── .env                        # OPENAI_API_KEY, KEYCLOAK_CLIENT_SECRET, OPENFGA_STORE_ID
│
├── api/                        # FastAPI API Gateway
│   ├── Dockerfile
│   ├── main.py                 # App entrypoint, lifespan, CORS, routers
│   ├── database.py             # asyncpg pool init/close/dependency
│   └── routers/
│       ├── domain_owner.py     # /api/v1/assets/*, /api/v1/access-requests/*, /api/v1/grants/*
│       ├── manager.py          # /api/v1/manager/*, /api/v1/cohorts/*, /api/v1/assignments/*
│       ├── marketplace.py      # /api/v1/marketplace/*
│       └── researcher.py       # /api/v1/research/*  ← THIS IS WHERE THE QUERY ENDPOINT GOES
│
├── auth/                       # Authorization layer
│   ├── middleware.py            # JWT verification, UserContext extraction
│   ├── dependencies.py         # FastAPI deps: CurrentUser, require_role()
│   ├── openfga_client.py       # OpenFGA HTTP wrapper
│   ├── authorization_service.py  # AccessProfile computation (2-layer)
│   ├── cohort_service.py       # Cohort CRUD, patient filtering
│   ├── secure_query_executor.py  # Row-level security query builder
│   └── openfga/
│       ├── model.json          # FGA authorization model
│       └── init_store.py       # Creates FGA store on startup
│
├── frontend/src/
│   ├── api/client.ts           # All API types + endpoint functions
│   └── pages/
│       └── ResearcherDashboard.tsx  # Current dashboard (needs query UI)
│
├── processor/                  # PDF processing pipeline (already built)
│   ├── entity_extractor.py     # OpenAI-based NER
│   ├── embedding_generator.py  # Text chunk embedding → Qdrant
│   └── loaders/                # DB loaders (PostgreSQL, Neo4j)
│
└── shared/
    └── kafka_schemas.py
4. Database Schema (PostgreSQL)

Clinical Data Tables
SQL

-- Core trial table
clinical_trial (
    trial_id UUID PK,
    nct_id TEXT UNIQUE,
    title TEXT,
    phase TEXT,              -- 'Phase 1', 'Phase 2', 'Phase 3', 'Phase 4'
    therapeutic_area TEXT,
    overall_status TEXT,
    study_type TEXT,
    enrollment_count INT,
    start_date DATE,
    completion_date DATE,
    lead_sponsor TEXT,
    regions TEXT[],
    countries TEXT[]
)

-- Treatment arms
trial_arm (
    arm_id UUID PK,
    trial_id UUID FK → clinical_trial,
    arm_name TEXT,
    arm_type TEXT,           -- 'Experimental', 'Placebo Comparator', 'Active Comparator'
    description TEXT
)

-- Interventions/drugs
intervention (
    intervention_id UUID PK,
    arm_id UUID FK → trial_arm,
    drug_name TEXT,
    intervention_type TEXT,
    dosage TEXT,
    route TEXT
)

-- Core patient table
patient (
    patient_id UUID PK,
    subject_id TEXT,
    age INT,
    sex TEXT,                -- 'M' or 'F'
    race TEXT,
    ethnicity TEXT,
    country TEXT,
    arm_assigned TEXT,
    disposition_status TEXT  -- 'Enrolled', 'Completed', 'Withdrawn', etc.
)

-- Many-to-many: patient ↔ trial
patient_trial_enrollment (
    enrollment_id UUID PK,
    patient_id UUID FK → patient,
    trial_id UUID FK → clinical_trial,
    enrollment_date DATE,
    UNIQUE(patient_id, trial_id)
)

-- Patient conditions
patient_condition (
    condition_id UUID PK,
    patient_id UUID FK → patient,
    condition_name TEXT,
    icd10_code TEXT,
    mesh_term TEXT,
    snomed_code TEXT
)

-- Adverse events
adverse_event (
    event_id UUID PK,
    patient_id UUID FK → patient,
    trial_id UUID FK → clinical_trial,
    event_term TEXT,
    severity TEXT,           -- 'Mild', 'Moderate', 'Severe'
    serious BOOLEAN,
    outcome TEXT,
    onset_date DATE,
    resolution_date DATE
)

-- Lab results
lab_result (
    lab_id UUID PK,
    patient_id UUID FK → patient,
    trial_id UUID FK → clinical_trial,
    test_name TEXT,
    loinc_code TEXT,
    value NUMERIC,
    unit TEXT,
    reference_range TEXT,
    collection_date DATE
)

-- Vital signs
vital_sign (
    vital_id UUID PK,
    patient_id UUID FK → patient,
    trial_id UUID FK → clinical_trial,
    vital_type TEXT,
    value NUMERIC,
    unit TEXT,
    measurement_date DATE
)

-- Patient medications
patient_medication (
    medication_id UUID PK,
    patient_id UUID FK → patient,
    medication_name TEXT,
    dose TEXT,
    route TEXT,
    start_date DATE,
    end_date DATE
)

-- Eligibility criteria
eligibility_criteria (
    criteria_id UUID PK,
    trial_id UUID FK → clinical_trial,
    criteria_type TEXT,      -- 'inclusion' or 'exclusion'
    description TEXT
)

-- Outcome measures
outcome_measure (
    outcome_id UUID PK,
    trial_id UUID FK → clinical_trial,
    measure_type TEXT,       -- 'primary' or 'secondary'
    title TEXT,
    description TEXT,
    time_frame TEXT
)

-- Cohort definitions
cohort (
    cohort_id UUID PK,
    cohort_name TEXT,
    description TEXT,
    organization_id TEXT,
    created_by TEXT,
    filter_criteria JSONB,
    is_dynamic BOOLEAN,
    created_at TIMESTAMP
)

cohort_trial (
    cohort_id UUID FK → cohort,
    trial_id UUID FK → clinical_trial
)

cohort_patient (
    cohort_id UUID FK → cohort,
    patient_id UUID FK → patient
    -- NOTE: intentionally empty for dynamic cohorts
)
Auth Tables
SQL

data_asset (asset_id UUID PK, reference_id UUID, asset_type TEXT, ...)
data_asset_collection (collection_id UUID PK, name TEXT, filter_criteria JSONB, ...)
access_grant (grant_id UUID PK, trial_id UUID, organization_id TEXT, is_active GENERATED, ...)
researcher_assignment (
    assignment_id UUID PK,
    researcher_id TEXT,          -- Keycloak username
    organization_id TEXT,
    trial_id UUID NULL,          -- direct trial assignment
    cohort_id UUID NULL,         -- cohort assignment
    access_level TEXT,           -- 'individual' or 'aggregate'
    granted_by TEXT,
    expires_at TIMESTAMP,
    revoked_at TIMESTAMP NULL,
    is_active GENERATED ALWAYS AS (revoked_at IS NULL AND expires_at > NOW()) STORED
)
5. Qdrant Vector Store Schema

The processor stores document chunks as vectors:

Python

# Collection name: "clinical_trials"
# Vector dimension: 1536 (text-embedding-3-small)

# Each point has:
{
    "id": "uuid",
    "vector": [0.123, ...],  # 1536-dim embedding
    "payload": {
        "trial_id": "uuid-string",
        "nct_id": "NCT10000131",
        "chunk_text": "The patient experienced Grade 2 nausea...",
        "chunk_index": 3,
        "section": "adverse_events",  # or "demographics", "results", "eligibility", etc.
        "source_pdf": "trial_NCT10000131.pdf",
        "therapeutic_area": "Oncology",
        "phase": "Phase 3"
    }
}
Qdrant Connection
text

Host: qdrant (docker) / localhost:6333
Collection: clinical_trials
6. Neo4j Knowledge Graph Schema

The processor builds a knowledge graph:

cypher

// Node types
(:Trial {trial_id, nct_id, title, phase, therapeutic_area, status})
(:Patient {patient_id, subject_id, age, sex, race, ethnicity, country})
(:Drug {name, type})
(:Condition {name, icd10_code, mesh_term})
(:AdverseEvent {term, severity, serious})
(:TrialArm {arm_id, name, type})

// Relationships
(Patient)-[:ENROLLED_IN]->(Trial)
(Patient)-[:ASSIGNED_TO]->(TrialArm)
(Patient)-[:HAS_CONDITION]->(Condition)
(Patient)-[:EXPERIENCED]->(AdverseEvent)
(AdverseEvent)-[:OCCURRED_IN]->(Trial)
(Trial)-[:HAS_ARM]->(TrialArm)
(Trial)-[:STUDIES]->(Condition)
(TrialArm)-[:USES_DRUG]->(Drug)
(Drug)-[:TREATS]->(Condition)
Neo4j Connection
text

URI: bolt://neo4j:7687 (docker) / bolt://localhost:7687
Auth: neo4j/password123
Database: neo4j
7. Authorization Model — Access Profile

7.1 UserContext (from JWT)
Python

@dataclass
class UserContext:
    user_id: str            # Keycloak sub (UUID)
    username: str           # preferred_username (e.g., "researcher-jane")
    role: str               # "domain_owner" | "manager" | "researcher"
    organization_id: str    # e.g., "org-pharma-corp"
    organization_name: str
7.2 Access Profile Data Model
Python

@dataclass
class CohortScope:
    """One cohort's patient filter criteria."""
    cohort_id: str
    cohort_name: str
    filter_criteria: dict   # Same structure as cohort.filter_criteria JSONB

@dataclass
class TrialAccessScope:
    """Access scope for a single trial."""
    trial_id: str
    access_level: str       # 'individual' or 'aggregate'
    cohort_scopes: list[CohortScope] = field(default_factory=list)

    @property
    def has_patient_filter(self) -> bool:
        return len(self.cohort_scopes) > 0

    @property
    def is_unrestricted(self) -> bool:
        return len(self.cohort_scopes) == 0

@dataclass
class AccessProfile:
    user_id: str
    role: str
    organization_id: str

    # Layer 1: Trial-level access (from OpenFGA)
    allowed_trial_ids: list[str] = field(default_factory=list)
    aggregate_trial_ids: list[str] = field(default_factory=list)
    individual_trial_ids: list[str] = field(default_factory=list)

    # Layer 2: Patient-level scopes (from PostgreSQL)
    trial_scopes: dict[str, TrialAccessScope] = field(default_factory=dict)

    # Computed
    has_any_access: bool = False
    has_individual_access: bool = False
    aggregate_only: bool = True
    sql_trial_filter: str = "1=0"  # Safe default: deny all
7.3 Access Profile Computation
Python

async def compute_access_profile(self, user: UserContext) -> AccessProfile:
    """
    Two-layer access computation:
    Layer 1: OpenFGA → which trials can this user access?
    Layer 2: PostgreSQL → which patients within those trials?
    """
    profile = AccessProfile(
        user_id=user.username,
        role=user.role,
        organization_id=user.organization_id,
    )

    if user.role == "domain_owner":
        profile.has_any_access = True
        profile.has_individual_access = True
        profile.aggregate_only = False
        profile.sql_trial_filter = "1=1"
        return profile

    # Layer 1: OpenFGA
    aggregate_ids = await self.fga.get_accessible_trial_ids(
        profile.user_id, access_level="aggregate"
    )
    individual_ids = await self.fga.get_accessible_trial_ids(
        profile.user_id, access_level="individual"
    )

    profile.aggregate_trial_ids = aggregate_ids
    profile.individual_trial_ids = individual_ids
    profile.allowed_trial_ids = list(set(aggregate_ids + individual_ids))
    profile.has_any_access = len(profile.allowed_trial_ids) > 0
    profile.has_individual_access = len(individual_ids) > 0
    profile.aggregate_only = len(individual_ids) == 0

    if profile.allowed_trial_ids:
        ids = ", ".join(f"'{t}'" for t in profile.allowed_trial_ids)
        profile.sql_trial_filter = f"trial_id IN ({ids})"

    # Layer 2: Cohort scopes for ALL trials
    if profile.allowed_trial_ids:
        await self._load_trial_scopes(profile, user)

    return profile
7.4 Trial Scope Loading
Python

async def _load_trial_scopes(self, profile: AccessProfile, user: UserContext):
    individual_set = set(profile.individual_trial_ids)

    cohort_assignments = await self.db.fetch("""
        SELECT ra.cohort_id, ra.access_level,
               c.name AS cohort_name, c.filter_criteria,
               ct.trial_id
        FROM researcher_assignment ra
        JOIN cohort c ON ra.cohort_id = c.cohort_id
        JOIN cohort_trial ct ON c.cohort_id = ct.cohort_id
        WHERE ra.researcher_id = $1
          AND ra.organization_id = $2
          AND ra.cohort_id IS NOT NULL
          AND ra.revoked_at IS NULL
          AND ra.expires_at > NOW()
    """, profile.user_id, profile.organization_id)

    direct_assignments = await self.db.fetch("""
        SELECT ra.trial_id, ra.access_level
        FROM researcher_assignment ra
        WHERE ra.researcher_id = $1
          AND ra.organization_id = $2
          AND ra.trial_id IS NOT NULL
          AND ra.cohort_id IS NULL
          AND ra.revoked_at IS NULL
          AND ra.expires_at > NOW()
    """, profile.user_id, profile.organization_id)

    direct_trial_ids = {str(d["trial_id"]) for d in direct_assignments}

    cohort_by_trial: dict[str, list] = {}
    for ca in cohort_assignments:
        tid = str(ca["trial_id"])
        cohort_by_trial.setdefault(tid, []).append(ca)

    for trial_id in profile.allowed_trial_ids:
        is_individual = trial_id in individual_set
        has_direct = trial_id in direct_trial_ids
        trial_cohorts = cohort_by_trial.get(trial_id, [])

        if has_direct:
            scopes = []
        elif trial_cohorts:
            scopes = [
                CohortScope(
                    cohort_id=str(ca["cohort_id"]),
                    cohort_name=ca["cohort_name"],
                    filter_criteria=ca["filter_criteria"] or {},
                )
                for ca in trial_cohorts
            ]
        else:
            scopes = []

        profile.trial_scopes[trial_id] = TrialAccessScope(
            trial_id=trial_id,
            access_level="individual" if is_individual else "aggregate",
            cohort_scopes=scopes,
        )
8. Filter Criteria Structure

The filter_criteria JSONB on cohorts uses this structure:

JSON

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
The existing CohortService._build_patient_filter_query() constructs a SQL WHERE clause from this:

Python

async def _build_patient_filter_query(self, criteria: dict, trial_ids: list[str]) -> tuple[str, list]:
    """Build SQL query to find patients matching filter criteria within allowed trials."""
    conditions = ["pte.trial_id = ANY($1::uuid[])"]
    params = [trial_ids]
    idx = 2

    if criteria.get("age_min") is not None:
        conditions.append(f"p.age >= ${idx}")
        params.append(criteria["age_min"])
        idx += 1

    if criteria.get("age_max") is not None:
        conditions.append(f"p.age <= ${idx}")
        params.append(criteria["age_max"])
        idx += 1

    if criteria.get("sex"):
        conditions.append(f"p.sex = ANY(${idx}::text[])")
        params.append(criteria["sex"])
        idx += 1

    if criteria.get("ethnicity"):
        conditions.append(f"p.ethnicity = ANY(${idx}::text[])")
        params.append(criteria["ethnicity"])
        idx += 1

    if criteria.get("country"):
        conditions.append(f"p.country = ANY(${idx}::text[])")
        params.append(criteria["country"])
        idx += 1

    if criteria.get("disposition_status"):
        conditions.append(f"p.disposition_status = ANY(${idx}::text[])")
        params.append(criteria["disposition_status"])
        idx += 1

    if criteria.get("arm_assigned"):
        conditions.append(f"p.arm_assigned = ANY(${idx}::text[])")
        params.append(criteria["arm_assigned"])
        idx += 1

    if criteria.get("conditions"):
        conditions.append(f"""
            EXISTS (
                SELECT 1 FROM patient_condition pc
                WHERE pc.patient_id = p.patient_id
                AND pc.condition_name = ANY(${idx}::text[])
            )
        """)
        params.append(criteria["conditions"])
        idx += 1

    where_clause = " AND ".join(conditions)

    query = f"""
        SELECT DISTINCT p.patient_id
        FROM patient p
        JOIN patient_trial_enrollment pte ON p.patient_id = pte.patient_id
        WHERE {where_clause}
    """

    return query, params

10. Current Frontend Types (client.ts)

TypeScript

export interface CohortFilterCriteria {
  sex?: string[];
  phases?: string[];
  age_max?: number;
  age_min?: number;
  ethnicity?: string[];
  trial_ids?: string[];
  conditions?: string[];
  therapeutic_areas?: string[];
  country?: string[];
  disposition_status?: string[];
  arm_assigned?: string[];
}

export interface TrialCohortFilter {
  cohort_id: string;
  cohort_name: string;
  filter_criteria: CohortFilterCriteria;
}

export interface TrialAccess {
  trial_id: string;
  nct_id: string;
  title: string;
  phase: string;
  therapeutic_area: string;
  overall_status: string;
  enrollment_count: number;
  patient_count: number;
  access_level: 'individual' | 'aggregate';
  is_unrestricted: boolean;
  cohort_filters: TrialCohortFilter[];
}

export interface AccessSummary {
  user_id: string;
  username: string;
  role: string;
  organization_id: string;
  access_summary: {
    has_any_access: boolean;
    aggregate_only: boolean;
    aggregate_trial_count: number;
    individual_trial_count: number;
    aggregate_trial_ids: string[];
    individual_trial_ids: string[];
  };
  trial_access: TrialAccess[];
}

export const researcherApi = {
  getMyAccess: () =>
    request<AccessSummary>('/research/my-access'),
  query: (q: string, trialIds?: string[]) =>
    request('/research/query', {
      method: 'POST',
      body: JSON.stringify({ query: q, trial_ids: trialIds }),
    }),
};
11. Current Researcher Dashboard (Summary)

The ResearcherDashboard.tsx currently shows:

Access mode banner (individual + aggregate / aggregate only)
4 stat cards: Total Trials, Patient-Level, Statistics Only, Cohort Filters
Individual Access Section: expandable trial rows with cohort filter tags
Aggregate-Only Section: expandable rows with trial metadata + "available queries" chips + restriction notice
What needs to be added: A query interface panel where the researcher types natural language questions and gets responses from an agentic system.

12. Sample API Responses

researcher-jane (individual access to 4 trials via cohort)
JSON

{
  "access_summary": {
    "has_any_access": true,
    "aggregate_only": false,
    "aggregate_trial_count": 4,
    "individual_trial_count": 4,
    "aggregate_trial_ids": ["2a22...", "737b...", "6500...", "e852..."],
    "individual_trial_ids": ["2a22...", "737b...", "6500...", "e852..."]
  },
  "trial_access": [
    {
      "trial_id": "2a22...",
      "nct_id": "NCT10000133",
      "title": "A Phase 4 Study of Nivolumab in Patients with Melanoma",
      "phase": "Phase 4",
      "therapeutic_area": "Oncology",
      "access_level": "individual",
      "cohort_filters": [
        {
          "cohort_name": "Hispanic CT",
          "filter_criteria": { "ethnicity": ["Hispanic or Latino"], "age_min": 10, "age_max": 100 }
        }
      ]
    }
  ]
}
researcher-dani (mixed: 1 individual + 3 aggregate-only)
JSON

{
  "access_summary": {
    "has_any_access": true,
    "aggregate_only": false,
    "aggregate_trial_count": 4,
    "individual_trial_count": 1,
    "individual_trial_ids": ["737b..."]
  },
  "trial_access": [
    {
      "trial_id": "737b...",
      "nct_id": "NCT10000131",
      "title": "A Phase 2 Study of Semaglutide...",
      "access_level": "individual",
      "cohort_filters": [{ "cohort_name": "Diabetes Clinical Trials", "filter_criteria": { "conditions": ["Type 2 Diabetes Mellitus"] } }]
    },
    { "trial_id": "2a22...", "nct_id": "NCT10000133", "access_level": "aggregate", "cohort_filters": [] },
    { "trial_id": "6500...", "nct_id": "NCT10000384", "access_level": "aggregate", "cohort_filters": [] },
    { "trial_id": "e852...", "nct_id": "NCT10000121", "access_level": "aggregate", "cohort_filters": [] }
  ]
}
13. What the Agentic System Must Do

13.1 Query Flow
text

Researcher types: "How many Hispanic patients experienced severe adverse events in the melanoma trial?"
    │
    ▼
Frontend → POST /api/v1/research/query { query, trial_ids? }
    │
    ▼
Backend:
  1. Compute AccessProfile (trials + cohort filters)
  2. Pass to Agent with tools + access constraints
  3. Agent decides which tools to call.
     3.1 the mpc servers/tools do not need to be per technology like ( sql_query_tool , vector_search_tool ,graph_query_tool ), maybe it makes more sense to add tools per functionalities or content oriented and delegate to the tool itself how fetch this data (db, vector db or knowledge graph) this way we abstract /hid the techonology What is important is to consider the user filters in order to not expose any data that user does not have access to
  
  4. Agent synthesizes response from tool results
  5. Return natural language answer + sources + data
    │
    ▼
Frontend: Render answer, sources, charts
13.2 Authorization Rules for Agent Tools
Access Level	Allowed Operations
individual + unrestricted	Full patient-level queries, no filter
individual + filtered	Patient-level queries with cohort filter WHERE clause injected
aggregate	Only COUNT, AVG, GROUP BY — no individual patient records
no access	Query rejected

14. Known Issues & Gotchas

asyncpg single-connection concurrency: Never use asyncio.gather() on the same connection. Use sequential await or separate pool connections.
Sex values: Database stores M/F, not Male/Female.
Phase naming: Phase 1, Phase 2, etc. (not Roman numerals).
Computed is_active: Generated column on access_grant and researcher_assignment — cannot INSERT/UPDATE directly.
is_unrestricted is a @property: Computed from len(cohort_scopes) == 0. Cannot be set directly.
Dynamic cohorts: cohort_patient table is intentionally empty. Patient membership computed at query time.
CORS: Only http://localhost:3001 allowed.
15. Environment & Ports

text

Frontend:        http://localhost:3001
API:             http://localhost:8000
Keycloak:        http://localhost:8180  (admin/admin)
Neo4j Browser:   http://localhost:7474  (neo4j/password123)
Qdrant:          http://localhost:6333
PostgreSQL:      localhost:5432         (ctuser/ctpassword, db: clinical_trials)
MinIO Console:   http://localhost:9001
Kafka UI:        http://localhost:8080
OpenFGA:         http://localhost:8082/playground
env

OPENAI_API_KEY=sk-...
KEYCLOAK_CLIENT_SECRET=research-platform-secret
OPENFGA_STORE_ID=<auto-set>
16. Goal for This Conversation

Build a complete Agentic Semantic Query System with:

Backend Agent Service (auth/agent_service.py or api/agent/):

OpenAI-powered agent with function calling / tool use
MCP tools for the different features related to clinical trails and its associated data stored in different data stores (PostgreSQL, Qdrant, Neo4j)
Authorization-aware: injects trial filters + cohort patient filters into every tool call
Respects individual vs aggregate access levels
Streaming response support
API Endpoint (POST /api/v1/research/query):

Accepts { query: string, trial_ids?: string[] }
Computes access profile
Runs agent
Returns { answer, sources, tool_calls, patient_count? }
Frontend Query UI (added to ResearcherDashboard.tsx):

Chat-like interface
Trial or cohort selection (checkboxes from accessible trials)
Shows agent thinking / tool calls
Renders structured data (tables, counts) alongside natural language
Suggested questions
Observability: Log all LLM calls, tool invocations, and results for debugging


I would like to reuse the same frontend so the researchers can use It to interact with the agent (through new tab or section in the app)
I would like to use frameworks like langchain and langraph, and fastmcp for mpc servers.
agents to mcp communication must be secure
we need to identify which tools we will create in order to expose the functionality to the agent and semantic app,
When it comes to agents, what is the best agent strategy? use chain of thought, ReAct, plan-and-execute, ReWOO, supervised and specialized agents (using a2a)?

consideration: 
- SSE endpoint with FastAPI
- remote fastmcp server (docker)
- agent and mcp servers must be secure (use keycloak) SSE transport and bearer token authentication
- For the MCP tools, I should design them around clinical trial concepts rather than generic database operations—things like trial discovery using semantic search and structured filters, patient demographics queries, adverse event analysis with relationship tracking, and lab results retrieval. intervention information, drug-condition relationships, and treatment details 7. explore_relationships - Navigate connections between entities like drugs, conditions, and patients across trials 8. search_documents - Semantic search across trial documentation and research materials
 focus on analytical capabilities: searching trials, analyzing patient cohorts, examining adverse events and lab data, exploring entity relationships, and searching documents semantically.
- single consolidated MCP server in its own container, 

About observability, ∫plase add some frameworks like arguilla, arize phoenix, graphana, kibana, prometheus, etc to monitor the agent and the system


