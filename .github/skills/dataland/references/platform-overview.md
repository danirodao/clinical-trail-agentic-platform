# Dataland — Platform Overview

## 1. What Dataland Is

Dataland is an enterprise data ecosystem built on the **Data Lakehouse paradigm**, combining:
- The **scalability and flexibility** of a data lake (AWS S3)
- The **analytical power and ease-of-use** of a data warehouse (Snowflake)

It follows a **federated, domain-oriented model** where:
- Data ownership is distributed across business domains
- Governance and platform capabilities remain centralised

**Dataland is NOT a single system.** It is the combination of:

| Component | Role |
|---|---|
| **AWS (S3, Glue, Airflow)** | Raw storage, processing, orchestration |
| **Snowflake** | Analytical warehouse, UC & domain databases |
| **SnapLogic + Blueprints** | Data ingestion, low-code integration |
| **dbt** | SQL-based transformation in Snowflake |
| **Microsoft Entra ID (Azure AD)** | Identity, SSO, AD group management |
| **Collibra (EDGC)** | Enterprise governance catalog, access control, data products |

---

## 2. Core Principles

| Principle | Meaning |
|---|---|
| **Domains own the data** | Each domain decides what to ingest, store, curate, and publish |
| **Platform enforces the rules** | Security, orchestration, compute, storage, and automation belong to the Platform |
| **Collibra governs metadata & access** | Data product definitions, ownership, domain→UC sharing workflows |
| **UCs consume governed products** | Use Cases access data through approved, governed channels only |

---

## 3. High-Level Architecture Layers

The architecture has **four logical layers** — two cross-cutting and one central dual layer.

```
┌────────────────────────────────────────────────────────────────┐
│               DATA PLATFORM (Top Cross-Cutting Layer)          │
│  Provisioning · Pipeline lifecycle · Technical catalog         │
│  Blueprints · Asset sharing (Domain ↔ UC)                      │
└───────────────────────────┬────────────────────────────────────┘
                            │
        ┌───────────────────┼────────────────────┐
        │    CENTRAL LAYER — ORGANIZATIONS        │
        │  ┌─────────────────┐  ┌──────────────┐ │
        │  │  FEDERATED      │  │   USE CASES  │ │
        │  │  DOMAINS        │  │  (Consumers) │ │
        │  │  (Producers)    │  │              │ │
        │  └─────────────────┘  └──────────────┘ │
        └───────────────────┬────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────────────┐
│    DATA GOVERNANCE, CATALOG & QUALITY (Bottom Cross-Cutting)   │
│  Collibra (EDGC) · Access Workflows · Data Quality             │
└────────────────────────────────────────────────────────────────┘
```

### 3.1 Top Layer — Data Platform
Responsible for:
- Provisioning and lifecycle management of all resources and pipelines
- **Technical catalog** (distinct from Collibra's enterprise/business catalog)
- Asset sharing automation between Domains and Use Cases
- **Blueprints** — reusable templates that standardise and accelerate ingestion and processing pipelines (batch ingestion, streaming, SCD2, Iceberg writes)

### 3.2 Central Layer — Organizations
Two types of organisations, each self-contained with its own storage and processing:

**Federated Domains (Producers)**
- Ingest, store, and curate their own data
- Domain owners understand their data best
- Responsible for data quality and publishing data products in Collibra
- Each domain uses technologies appropriate to its needs

**Use Cases (Consumers)**
Two broad categories:
- **Analytics / BI / Reporting** — Tableau and Power BI dashboards
- **Data Science / ML** — PySpark, Python, Glue, Databricks for ML and exploration

### 3.3 Bottom Layer — Governance, Catalog & Quality
- **Collibra (EDGC)**: Central metadata repository — owning domain, data owners, sharing terms, permissions, data product definitions
- **Access Control via Collibra**: UCs request access to domain assets through Collibra; upon approval, Collibra triggers platform-side data sharing
- **Data Quality**: Enables domain owners and data stewards to define and run data quality metrics on their datasets (a previously missing feature, now part of the platform)

---

## 4. Medallion Architecture (Bronze → Silver → Gold)

All data within domains flows through three progressive layers:

```
Source Systems
      │
      ▼
  RAW (Bronze) — S3
  Original format, archiving, decoupled from source
      │
      ▼  Airflow orchestrates → Glue / dbt / SnapLogic
  CLEANED (Silver) — Iceberg on S3
  Validated, standardised, historised (SCD2)
      │
      ▼
  CURATED (Gold) — Snowflake (primarily)
  Business-modelled, business rules applied
      │
      ▼
  CONSUMPTION
  BI: Tableau / Power BI (via service accounts)
  DS/ML: PySpark / Python / Glue / Databricks
```

### Layer Details

| Layer | Storage | Purpose | Key Characteristics |
|---|---|---|---|
| **Raw (Bronze)** | Amazon S3 | Staging & archiving | Original format, untouched, decouples from source system — enables reprocessing without fetching from source again |
| **Cleaned (Silver)** | S3 (Iceberg/Parquet) | Validated & standardised | Date normalisation, deduplication, SCD2 historisation, basic quality checks |
| **Curated (Gold)** | Snowflake (primarily) | Business-modelled | Business rules applied, dimensional models, KPI tables, analytical views |

---

## 5. Key Architectural Principles

| Principle | Implementation |
|---|---|
| **Federated Domain Ownership** | Each domain owns its data end-to-end; subject-matter experts manage quality |
| **Data Lakehouse + Medallion** | Raw lake + progressive refinement to warehouse-grade assets |
| **Standardised Blueprints** | Reusable templates for batch, streaming, SCD2 — consistency and faster delivery |
| **Scalability via Decoupling** | Storage decoupled from compute; Raw layer decoupled from source systems |
| **Open Standards** | Parquet + Apache Iceberg across S3 and Snowflake — minimises vendor lock-in |
| **Centralised Governance, Decentralised Execution** | Collibra + Platform govern; Domains execute and own |
| **Self-Service** | Continuous focus on reducing onboarding friction and accelerating platform adoption |
