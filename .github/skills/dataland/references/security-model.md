# Dataland — Security Model

## 1. Security Foundation

All security in Dataland is grounded in **Microsoft Entra ID (Azure AD) groups**. AD groups are the identity primitive that map to:
- **IAM Roles in AWS** (for S3 access)
- **Functional Roles in Snowflake** (for query access)

Every permission in Dataland ultimately traces back to an AD group membership.

---

## 2. Three Permission Layers

| Layer | Managed By | What It Controls |
|---|---|---|
| **Identity (Entra ID / AD)** | ServiceNow + Entra ID | Who the user is; which AD groups they belong to |
| **Governance (Collibra)** | Domains + UC Managers | Which access is approved and why (business justification, audit trail) |
| **Platform (Snowflake / AWS)** | Platform Team | Actual enforcement via RBAC roles and IAM policies |

> All three layers must be aligned for access to work correctly.

---

## 3. Domain-Level AD Groups

Each domain has **three standard AD groups**:

| AD Group Pattern | Role | Permissions |
|---|---|---|
| `AD_<DOMAIN>_TEAM` | Team Member | Day-to-day work within the domain; read/write to domain's Snowflake DB and S3 |
| `AD_<DOMAIN>_MANAGER` | Manager | Administrative responsibilities; approval authority for access requests |
| `AD_<DOMAIN>_STAKEHOLDER` | Stakeholder | Visibility / read-only access to domain assets |

These groups are mapped to:
- **AWS IAM roles** → S3 bucket access for the domain's Raw layer
- **Snowflake functional roles** → domain database access (via SCIM)

---

## 4. Use Case-Level AD Groups

Each use case has the same three-group model:

| AD Group Pattern | Role | Key Behaviour |
|---|---|---|
| `AD_<UC>_TEAM` | Team Member | Can query data granted to the UC via domain approvals |
| `AD_<UC>_MANAGER` | Manager | Submits domain access requests via Collibra; creates custom role groups via ServiceNow |
| `AD_<UC>_STAKEHOLDER` | Stakeholder | Read-only visibility into UC assets |

In Snowflake:
- Each UC has its own database
- The UC's `_TEAM` functional role is granted `SELECT` on domain tables/views **after Collibra approval**
- Domain tables are never directly accessible — access is mediated through the Platform's sharing automation

---

## 5. End-User Custom Roles (Partially Implemented)

For end users needing more granular access within a UC, custom AD groups can be created:

| AD Group Pattern | Purpose |
|---|---|
| `AD_<UC>_ROLE_<ROLENAME>` | Custom end-user role within a UC |

**Example**: `AD_UC_SALES_RPT_ROLE_EU_ANALYST`

### Lifecycle Flow for Custom Roles

```
1. UC Manager creates AD_<UC>_ROLE_* group via ServiceNow
2. End user requests group membership via ServiceNow
3. UC Manager approves (no Collibra involvement)
4. Platform configures the group in the Snowflake Enterprise App (Entra ID)
5. SCIM syncs group members → Snowflake functional role
6. Platform assigns UC-level Snowflake privileges to the role
```

### Current Status

| Capability | Status |
|---|---|
| Custom AD groups for UC roles | ✅ Implemented — via ServiceNow |
| SCIM sync of custom roles into Snowflake | ✅ Implemented |
| Row-level security (RLS) by end-user role | ❌ Not yet implemented |
| Column-level security by end-user role | ❌ Not yet implemented |
| BI-layer RLS (Tableau/Power BI `USERNAME()`) | ❌ Not yet implemented |
| Entitlement management UI | ❌ Does not exist |
| Collibra governance of end-user entitlements | ❌ Out of scope for Collibra today |

---

## 6. SCIM Integration (Entra ID → Snowflake)

| Property | Detail |
|---|---|
| **SCIM scope** | Account-wide — all AD groups from all domains and UCs |
| **Trigger** | Adding/removing a user from an AD group in Entra ID |
| **Effect** | Automatically creates/disables user in Snowflake; grants/revokes corresponding Snowflake role |
| **Single Enterprise App** | One Entra ID Enterprise Application configured for Snowflake, shared by all of Dataland |
| **Implication** | Per-UC isolation must be handled explicitly through Snowflake RBAC — SCIM itself does not isolate |

---

## 7. BI Tool Connectivity & Identity Gap

This is one of the most important architectural constraints for security design:

```
End User
    │  Authenticates via Entra ID SSO (SAML)
    ▼
Tableau Server / Power BI Service
    │  BI tool knows end user identity
    │  Connects to Snowflake using SERVICE ACCOUNT (RSA key/secret)
    ▼
Snowflake
    │  Sees service account identity — NOT end user
    ▼
Data
```

| Consequence | Detail |
|---|---|
| **Snowflake RLS is ineffective for BI users** | `CURRENT_USER()` returns the service account, not the end user |
| **BI-layer RLS required** | `USERNAME()` / `USERPRINCIPALNAME()` in Tableau/Power BI — can identify the end user at the BI layer |
| **Not yet implemented** | BI-layer RLS has not been built for any use case |
| **No per-user audit trail in Snowflake** | Query logs show service account activity, not individual user actions |

---

## 8. Data Sharing Security Flow (Domain → UC)

```
Collibra Approval
        │
        ▼
Platform automation executes:
  GRANT SELECT ON DOMAIN_DB.CURATED.TABLE
  TO ROLE UC_SALES_RPT_TEAM_ROLE;
        │
        ▼
UC _TEAM members (via SCIM-synced role) can query the table
```

- The UC functional role is granted at the **table or view level**, not at a row-filtered level
- Domain data is never copied into the UC database by default — it is accessed via cross-database reference with granted roles

---

## 9. Open Security Design Questions (Not Yet Decided)

| Topic | Status |
|---|---|
| **Per-end-user RLS mechanism** | Architecture not finalised — could be entitlement tables, AD role-based, or hybrid |
| **Column-level security** | Not implemented; requirements gathering in progress |
| **BI-layer RLS implementation** | Tableau `Username()` / Power BI `USERPRINCIPALNAME()` approach being evaluated |
| **Entitlement definition & storage** | Where entitlements live (AD, Collibra, entitlement tables, or combination) is undecided |
| **Audit trail for end-user BI access** | Currently no per-user query audit — only BI tool logs |

---

## 10. Security Architecture Summary Diagram

```
┌──────────────────────────────────────────────────────┐
│                 Microsoft Entra ID                    │
│  AD_DOMAIN_FINANCE_TEAM                               │
│  AD_DOMAIN_FINANCE_MANAGER                            │
│  AD_UC_SALES_RPT_TEAM                                 │
│  AD_UC_SALES_RPT_MANAGER                              │
│  AD_UC_SALES_RPT_ROLE_EU_ANALYST    (custom)          │
└──────────────┬───────────────────────────────────────┘
               │ SCIM (account-wide)
               ▼
┌──────────────────────────────────────────────────────┐
│             Snowflake (Single Account)                │
│  DOMAIN_FINANCE_DB                                    │
│    └── DOMAIN_FINANCE_TEAM_ROLE  → AD group mapped    │
│  UC_SALES_RPT_DB                                      │
│    └── UC_SALES_RPT_TEAM_ROLE   → AD group mapped    │
│    └── (domain tables granted after Collibra approval)│
└──────────────────────────────────────────────────────┘
               ▲
               │ Service Account (RSA key)
┌──────────────┴───────────────────────────────────────┐
│         Tableau Server / Power BI Service             │
│   End users authenticate via Entra ID SSO             │
│   BI tool knows end user — Snowflake does NOT         │
└──────────────────────────────────────────────────────┘
```
