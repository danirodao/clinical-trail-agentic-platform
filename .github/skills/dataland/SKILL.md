---
name: dataland
description: "Provides deep context about the Dataland enterprise data platform ecosystem. Use when: answering questions about Dataland architecture, data domains, use cases, Collibra/EDGC governance, Snowflake account structure, access request workflows, AD groups, SCIM, security model, SnapLogic, blueprints, dbt, Airflow, S3, Iceberg, medallion architecture, domain data products, BI connectivity, end-user entitlements, data sharing, federation, data lakehouse, Dataland platform."
argument-hint: "Describe the Dataland topic or problem (e.g. 'how does domain-to-UC access work?' or 'explain the security model')"
---

# Dataland Platform — Context Skill

This skill provides authoritative context about the **Dataland enterprise data platform ecosystem**. Load the relevant reference files based on what is being asked.

---

## What Dataland Is (One-Paragraph Summary)

Dataland is the enterprise-wide data ecosystem built on a **federated domain model** and a **Data Lakehouse architecture**. Domains own, ingest, and curate their data. The Platform (AWS + Snowflake + Airflow + SnapLogic + dbt) provides infrastructure, automation, and orchestration. Collibra (EDGC) centralises governance, metadata, data product registration, and domain-to-use-case access workflows. Use Cases consume governed data products through BI (Tableau/Power BI) or Data Science/ML tooling. It is not a monolith — it is the **combination of AWS, Snowflake, SnapLogic, Airflow, dbt, Entra ID, and Collibra** operating under a strict governance model.

---

## Quick Reference Index

| Topic | Reference File |
|---|---|
| What Dataland is, architecture layers, key principles | [Platform Overview](./references/platform-overview.md) |
| Domains vs Use Cases — what they can and cannot do | [Organizations](./references/organizations.md) |
| Technology stack, Medallion layers, storage, orchestration | [Technical Stack](./references/technical-stack.md) |
| Collibra/EDGC, data products, access workflows, governance | [Governance & Access](./references/governance-access.md) |
| AD groups, SCIM, Snowflake RBAC, permission layers, BI connectivity | [Security Model](./references/security-model.md) |

---

## When to Load Each Reference

- Questions about **what Dataland is**, its philosophy, or architectural layers → load [Platform Overview](./references/platform-overview.md)
- Questions about **domain responsibilities, use case types, org model** → load [Organizations](./references/organizations.md)
- Questions about **technology choices, data flow, storage strategy, Airflow, dbt, blueprints, Iceberg** → load [Technical Stack](./references/technical-stack.md)
- Questions about **Collibra, data products, access requests, governance workflows, EDGC** → load [Governance & Access](./references/governance-access.md)
- Questions about **AD groups, SCIM, Snowflake roles, end-user security, BI service accounts, RLS** → load [Security Model](./references/security-model.md)

---

## Critical Facts — Always in Context

These facts are frequently misunderstood and must always be kept in mind:

1. **Only Domains publish data products in EDGC.** Use Cases cannot.
2. **Collibra governs Domain → UC access only.** End-user access within a UC is managed via ServiceNow + AD groups, outside Collibra.
3. **All data sharing is platform-automated.** No manual `GRANT` statements in Snowflake.
4. **BI tools (Tableau/Power BI) connect via service accounts** using RSA keys. End-user identity never reaches Snowflake. Row-Level Security in Snowflake is therefore ineffective for BI users today.
5. **End-user RLS is not yet implemented** — neither at the Snowflake level nor at the BI layer.
6. **SCIM is account-wide.** Every Entra ID AD group flows into the single shared Snowflake account.
7. **Single Snowflake account, single Entra ID tenant** — all domains and use cases coexist.
8. **No direct cross-service DB access** — domain data is accessed by UCs only via granted roles after Collibra approval.
