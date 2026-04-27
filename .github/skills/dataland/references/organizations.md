# Dataland — Organizations: Domains & Use Cases

## Overview

There are two types of organisations in Dataland:
- **Domains** — data producers
- **Use Cases (UCs)** — data consumers

They operate under strict governance rules that define what each is permitted to do and through which channels.

---

## 1. Domains (Producers)

Domains are the **only organisations allowed to produce data** in Dataland. They ingest data from source systems, refine it through the medallion layers, and publish it as governed data products.

### What Domains CAN Do

| Permission | How |
|---|---|
| Ingest data from source systems | Via SnapLogic, Blueprints, or AWS Glue |
| Create Raw / Cleaned / Curated layers | Within their own S3 prefix and Snowflake database |
| Publish data products in EDGC (Collibra) | Domain owners register assets in the enterprise catalog |
| Define data-sharing terms | In the data product's sharing contract in Collibra |
| Approve or deny UC access requests | Via Collibra approval workflow |
| Maintain business metadata and data ownership | In Collibra |

### What Domains CANNOT Do

| Forbidden Action | Reason |
|---|---|
| Grant access directly in Snowflake | All grants are platform-automated via Collibra workflow |
| Share data to a UC bypassing Collibra | Governance requires the Collibra approval trail |
| Manage AD groups | AD lifecycle is managed via ServiceNow + Entra ID |
| Overwrite governance metadata in Collibra | Governed fields must follow the approval process |

### Typical Domain Data Products Published in EDGC

**Raw Assets**
- Archived extracts
- Incremental feeds
- CDC (Change Data Capture) logs

**Cleaned Assets**
- Standardised tables
- SCD2 historised tables
- Cleaned reference datasets

**Curated Assets / Data Products**
- Domain business models
- KPI tables
- Dimensional models
- Analytical views

**Cross-domain Reference Tables** (in some cases)
- Calendar tables
- Supplier lists
- Material master
- Organisational hierarchies

> ⚠️ **Not all domains are equally mature.** The depth of what a domain publishes (Raw only vs. full Curated) varies by domain.

---

## 2. Use Cases (Consumers)

Use Cases are the **consumers of domain data products**. They access data after going through the Collibra-governed access request process.

### What UCs CAN Do

| Permission | How |
|---|---|
| Request access to domain assets | Only through Collibra (governed path) |
| Build their own transformations | In their own UC Snowflake database |
| Create custom AD groups for end-user roles | Via ServiceNow — naming convention: `AD_<UC>_ROLE_<ROLENAME>` |
| Publish UC-level datasets | Within their UC database (not enterprise data products) |

### What UCs CANNOT Do

| Forbidden Action | Reason |
|---|---|
| Publish enterprise data products in EDGC | Only Domains are permitted to do so |
| Approve their own access requests | Approval belongs to the Domain Owner |
| Directly request Snowflake privileges | All access flows through Collibra → Platform automation |
| Circumvent domain ownership | Data sharing must always follow the governed path |
| Access another UC's database directly | Each UC is isolated within its own Snowflake database |

---

## 3. Types of Use Cases

There are three major UC types, each with different technical requirements:

### 3.1 BI & Reporting
- **Tools**: Tableau, Power BI
- **Characteristics**: Heavy Snowflake consumption; often require denormalised gold-layer models; connect via **service accounts** (not end-user SSO to Snowflake)
- **Access pattern**: Service account connects to Snowflake → extract → BI cache → end user

### 3.2 Data Science / ML
- **Tools**: PySpark, Python, Databricks, AWS Glue Jobs
- **Characteristics**: Often require large data volumes; frequently need Raw or Cleaned layers (not just Curated); direct data access for model training and ad-hoc exploration
- **Access pattern**: Python/PySpark code connects to Snowflake or S3 directly using AD-backed functional roles

### 3.3 Operational / API-Driven (less common)
- **Tools**: SnapLogic, Snowflake Tasks, AWS Glue
- **Characteristics**: Trigger-based workflows, Reverse ETL, system-to-system data sharing
- **Access pattern**: Automated pipeline → Snowflake or S3 asset

---

## 4. Domain vs Use Case — Quick Comparison

| Dimension | Domain | Use Case |
|---|---|---|
| Role | Producer | Consumer |
| Publishes in EDGC | ✅ Yes | ❌ No |
| Owns data products | ✅ Yes | ❌ No |
| Approves access | ✅ Yes (for their data) | ❌ No |
| Manages ingestion pipelines | ✅ Yes | ❌ No |
| Builds own transformations | ✅ Yes | ✅ Yes (within UC DB only) |
| Requests domain data | ❌ N/A | ✅ Via Collibra |
| Creates end-user AD groups | ✅ (Domain roles) | ✅ (UC roles via ServiceNow) |
| Has own Snowflake DB | ✅ Yes | ✅ Yes |
| Has own S3 storage | ✅ Yes | Depends on UC type |
