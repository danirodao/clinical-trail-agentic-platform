# Dataland — Governance & Access (Collibra / EDGC)

## 1. What EDGC (Collibra) Is in Dataland

The **Enterprise Data Governance Catalog (EDGC)** — implemented in Collibra — is the **single source of truth** for:
- All dataset metadata (technical and business)
- Data product definitions and publishing
- Data ownership and stewardship
- Domain-to-Use Case access workflows and approvals
- Governance audit trail

> **Collibra is the governance layer.** The Platform enforces what Collibra approves.

---

## 2. What EDGC Currently Contains

### ✅ Domain Data Products

Each registered data product contains:

| Field | Description |
|---|---|
| **Dataset definition** | What the dataset contains, its scope |
| **Technical & business metadata** | Schema, column descriptions, business glossary linkage |
| **Data Owner** | Person accountable for the data |
| **Data Steward** | Person responsible for day-to-day quality management |
| **Data quality expectations** | Quality rules, when provided by the domain |
| **Sharing terms / contract** | Conditions under which a UC may access this product |
| **Approval workflow configuration** | Who must approve, any conditions |
| **Storage location** | Snowflake database/schema/table or S3 path |
| **Access guidance** | Instructions for how to use the asset |

### ✅ Use Case Registrations

Each registered UC contains:

| Field | Description |
|---|---|
| **UC Owner** | Person accountable for the UC |
| **Business purpose** | Why this UC exists |
| **Justification for data usage** | Why this UC needs specific domain data |
| **Associated AD groups** | AD groups that map to this UC |
| **UC Snowflake database** | The UC's dedicated Snowflake database |

### ✅ Domain → UC Access Workflows

Collibra governs:
- **Request**: UC Manager submits access request
- **Approval**: Domain Owner reviews and approves/denies
- **Audit trail**: Record of what was approved, when, by whom
- **Trigger**: Approved → Collibra triggers Snowflake grant via Platform

### ❌ What EDGC Does NOT Yet Contain

| Missing | Impact |
|---|---|
| **End-user level entitlements** | End-user access within a UC is managed via ServiceNow/AD, not Collibra |
| **BI row-level security definitions** | RLS for Tableau/Power BI is not yet implemented or catalogued |
| **Custom UC roles** | `AD_<UC>_ROLE_*` groups exist in AD but are not registered in Collibra |
| **Cross-domain lineage at Curated layer** | Not consistently tracked; an open gap |

---

## 3. Who Can Publish in EDGC

> ⚠️ **Only Domains can publish data products in EDGC. Use Cases cannot.**

This is a hard governance rule. The enterprise catalog reflects the federated ownership model — domains are the authoritative publishers of data products. Use Cases build on top of governed products but do not produce enterprise-level assets.

---

## 4. Access Request Flows (End-to-End)

### 4.1 Domain → UC Access (Governed by Collibra)

This is the **only governed data access path** for domain data in Dataland.

```
1. UC Manager submits access request in Collibra
        │
        ▼
2. Domain Owner reviews request in Collibra
        │ Approve / Deny
        ▼ (if approved)
3. Collibra passes approval signal → Platform
        │
        ▼
4. Platform executes data-sharing automation in Snowflake
   (grants UC functional role SELECT on domain tables/views)
        │
        ▼
5. Collibra records approval → governance audit trail
```

**Key facts:**
- The UC receives **database/table-level access**, not row-level filtering
- No manual Snowflake `GRANT` is ever executed — all is automated
- Domain Owner has full authority to deny

### 4.2 End User → UC Role Access (via ServiceNow — outside Collibra)

This flow manages **end users within a UC** accessing UC-level Snowflake roles.

```
1. UC Manager creates AD_<UC>_ROLE_* group via ServiceNow
        │
        ▼
2. End user requests membership via ServiceNow
        │
        ▼
3. UC Manager approves (no Collibra involvement)
        │
        ▼
4. Platform configures the new AD group in the Snowflake Enterprise App in Entra ID
        │
        ▼
5. SCIM syncs group → Snowflake role (automatic, account-wide)
        │
        ▼
6. Snowflake role gets UC-level privileges
```

**Key facts:**
- Collibra is **not involved** in end-user entitlement management
- UC Managers have autonomy to define roles within their UC without governance approval
- SCIM sync is global — the group appears in the shared Snowflake account

---

## 5. Three Permission Layers (Must All Be Aligned)

All access in Dataland requires **exact alignment across three layers**:

| Layer | Managed By | Purpose |
|---|---|---|
| **Identity (AD)** | ServiceNow + Entra ID | Who the user is and what AD groups they belong to |
| **Governance (Collibra)** | Domain Owners + UC Managers | Who should get access and why (business justification, approval) |
| **Platform (Snowflake / AWS)** | Platform Team | Technical enforcement of privileges |

> If any layer is misaligned, access will not work correctly. For example: Collibra approval without Platform automation = no actual access. AD group without Collibra approval = no domain data access.

---

## 6. Governance Clarifications (Frequently Misunderstood)

| Misconception | Correct Understanding |
|---|---|
| "UCs can publish in EDGC" | ❌ Only Domains publish enterprise data products |
| "Collibra manages all access" | ❌ Collibra manages Domain → UC access only. End-user access is via ServiceNow |
| "I can grant access manually in Snowflake" | ❌ All grants are Platform-automated. No manual GRANTs |
| "BI users connect to Snowflake as themselves" | ❌ BI tools use service accounts. End-user identity never reaches Snowflake |
| "Collibra governs RLS for BI" | ❌ End-user RLS is not yet implemented anywhere |
| "SCIM only syncs my UC's groups" | ❌ SCIM is account-wide — all Dataland AD groups sync into the shared Snowflake account |
| "A domain can share data directly without Collibra" | ❌ All domain-to-UC sharing must go through the Collibra governed path |

---

## 7. Types of Assets Shared in EDGC by Maturity

Not all domains publish all layers. Maturity varies:

| Tier | Typical Assets | Notes |
|---|---|---|
| **Fully mature domain** | Raw + Cleaned + Curated products | Complete medallion implementation; rich metadata; SLAs defined |
| **Intermediate domain** | Cleaned + some Curated | Raw exists but not published; some business models |
| **Early-stage domain** | Raw only | Minimal transformation; limited metadata; data quality not yet defined |
