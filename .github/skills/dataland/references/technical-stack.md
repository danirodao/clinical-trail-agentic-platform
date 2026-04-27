# Dataland — Technical Stack

## 1. Technology Stack Summary

| Concern | Technology | Details |
|---|---|---|
| **Object Storage (Data Lake)** | Amazon S3 | Raw layer; virtually unlimited capacity; structured and unstructured data at lowest cost |
| **Data Warehouse** | Snowflake | Analytics performance and ease of use; most heavy transformations happen here because most UCs consume via BI tools |
| **Table Format** | Apache Iceberg / Parquet | Open-standard formats in both S3 and Snowflake; avoids vendor lock-in |
| **Orchestration** | Apache Airflow | Coordinates end-to-end workflows across all tools; delegates execution to SnapLogic, Glue, or dbt |
| **Ingestion / Integration** | SnapLogic + Dataland Blueprints | Low-code / automated integration of diverse sources |
| **Transformation** | dbt (Snowflake), AWS Glue, SnapLogic | dbt for SQL-based Snowflake transforms; Glue for AWS-native processing |
| **Consumption / BI** | Tableau, Power BI | Dashboard and reporting; connect via service accounts |
| **Data Science / ML** | PySpark, Python, Glue Jobs, Databricks | Advanced analytics, ML training, ad-hoc exploration |
| **Governance Catalog** | Collibra (EDGC) | Enterprise metadata, data products, access permissions, sharing terms |
| **Identity & Access** | Microsoft Entra ID (Azure AD) | Foundation for all security; AD groups mapped to IAM roles (AWS) and Snowflake functional roles |

---

## 2. Storage Strategy

Two storage technologies serve complementary roles:

| Storage | Strengths | Primary Use |
|---|---|---|
| **Amazon S3** | Unlimited scalability, low cost, unstructured + structured data | Raw (Bronze) layer; big-data workloads; Data Science |
| **Snowflake** | Query performance, SQL ergonomics, ease of use for analytics | Cleaned (Silver) and Curated (Gold) layers; BI consumption |

**Open Standards Mandate**: All assets are stored in **Apache Parquet** or **Apache Iceberg** format — whether on S3 or in Snowflake — to minimise vendor lock-in.

---

## 3. Snowflake Account Structure

```
One Snowflake Account (shared by all)
├── DOMAIN_FINANCE_DB          (Domain: Finance)
├── DOMAIN_MANUFACTURING_DB    (Domain: Manufacturing)
├── DOMAIN_SALES_DB            (Domain: Sales)
│   ├── RAW schema
│   ├── CLEANED schema
│   └── CURATED schema
│
├── UC_SALES_REPORTING_DB      (Use Case: Sales Reporting)
├── UC_MARKETING_DB            (Use Case: Marketing)
└── UC_SUPPLY_CHAIN_ML_DB      (Use Case: Supply Chain ML)
```

- **One Snowflake account** — all domains and use cases coexist
- **Per-organisation databases** — each domain and each UC has its own isolated database
- Cross-database access is only granted via the Platform (after Collibra approval) using Snowflake RBAC

---

## 4. Blueprints

Blueprints are **reusable, standardised pipeline templates** managed by the Platform team. They encode best practices for common ingestion and processing patterns.

### Supported Blueprint Patterns

| Blueprint Type | Description |
|---|---|
| **Batch Ingestion** | Full or incremental extraction from relational databases, files, SaaS apps |
| **Streaming Ingestion** | Near-real-time event ingestion from streaming sources |
| **SCD2 (Slow Changing Dimension Type 2)** | Historised table generation — tracks changes over time |
| **Iceberg Write** | Writing data into Apache Iceberg format in S3 or Snowflake |

### Why Blueprints Matter
- Enforces architectural consistency across all domain pipelines
- Reduces implementation time significantly (not starting from scratch)
- Ensures new pipelines automatically align with platform standards
- Easier to maintain — upgrades to a blueprint benefit all consumers

---

## 5. Orchestration & Data Flow

**Airflow** is the orchestration backbone — it coordinates all workflows but **does not process data itself**. It delegates execution to:

| Processor | When Used |
|---|---|
| **SnapLogic** | Source integration, low-code ingestion, Raw layer loading |
| **AWS Glue** | AWS-native processing, Data Science workloads, Python/PySpark jobs |
| **dbt** | SQL-based transformation within Snowflake (Cleaned → Curated) |

### Most Common Data Flow

```
Source Systems (DB, Files, SaaS, Streaming)
        │
        ▼  SnapLogic / Blueprints
   RAW Layer (S3 — Bronze)
   Original format, archiving
        │
        ▼  Airflow → Glue or SnapLogic
   CLEANED Layer (S3 — Iceberg — Silver)
   Validated, standardised, SCD2
        │
        ▼  Airflow → dbt (Snowflake)
   CURATED Layer (Snowflake — Gold)
   Business-modelled, KPIs, dimensional models
        │
        ▼
   CONSUMPTION
   BI: Tableau / Power BI (service account → Snowflake)
   DS/ML: PySpark / Python / Glue / Databricks
```

> **Why Snowflake for most transformations?**
> Most use cases consume data via BI tools (Tableau/Power BI), and Snowflake's SQL engine is optimised for this path. Heavy transformation in Snowflake (via dbt) is the most efficient for BI-oriented workloads. AWS-heavy paths exist for Data Science / ML workloads.

---

## 6. Consumption Patterns

### BI & Reporting
- **Tools**: Tableau Server, Power BI Service
- **Authentication**: End users authenticate to BI tools via **Entra ID SSO (SAML)**
- **Snowflake Connection**: BI tools connect to Snowflake via **service accounts** (RSA keys/secrets)
- **Key constraint**: End-user identity does NOT reach Snowflake. Snowflake sees the service account, not the individual user.

### Data Science / ML
- **Tools**: Python, PySpark, Databricks, AWS Glue Jobs
- **Authentication**: AD-backed functional roles via SCIM-synced Snowflake roles
- **Access**: Direct Snowflake + S3 access (depending on UC type and what was granted via Collibra)

---

## 7. Entra ID (Azure AD) Structure

```
One Entra ID Tenant (SSO for all of Dataland)
└── One Enterprise Application → Snowflake
    └── SCIM integration → Snowflake (account-wide)
        ├── AD_DOMAIN_FINANCE_TEAM        → Snowflake functional role
        ├── AD_DOMAIN_FINANCE_MANAGER     → Snowflake functional role
        ├── AD_UC_SALES_RPT_TEAM          → Snowflake functional role
        ├── AD_UC_SALES_RPT_MANAGER       → Snowflake functional role
        └── AD_UC_SALES_RPT_ROLE_EU_ANALYST → Snowflake functional role (custom)
```

- **One tenant, one Enterprise App, one SCIM integration** — global scope
- All AD groups from all organisations are synced into the single Snowflake account
- SCIM automatically provisions/deprovisions users and role memberships when AD group membership changes

---

## 8. Key Technical Constraints

| Constraint | Detail |
|---|---|
| **Single Snowflake account** | All domains and UCs share one account; each has its own database |
| **Single Entra ID tenant** | One SSO, one Enterprise App for Snowflake, one SCIM integration |
| **SCIM is account-wide** | All AD groups sync into the same Snowflake account; per-UC isolation must be handled through explicit RBAC |
| **No live BI → Snowflake connection** | Tableau and Power BI use service accounts; end-user identity does not reach Snowflake |
| **No manual GRANTs** | All Snowflake privilege grants are platform-automated through Collibra approval workflows |
| **Open format mandate** | Parquet / Iceberg everywhere — S3 and Snowflake |
| **ServiceNow for AD group lifecycle** | Creation of AD groups and user membership requests go through ServiceNow |
