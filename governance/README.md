# Data Governance & Access Control on Microsoft Fabric

A working reference for setting up **governance, security, and access control** on Microsoft Fabric — the layers, the trade-offs, and the patterns that hold up in enterprise deployments.

Everything below is generic and employer-neutral.

---

## 1. The seven layers of Fabric access

Access on Fabric is enforced at **seven distinct layers**. Miss any one and you have a hole.

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Tenant settings (Fabric admin portal)                    │
│ 2. Capacity assignment (F-SKU / P-SKU)                      │
│ 3. Domain membership                                        │
│ 4. Workspace roles (Admin / Member / Contributor / Viewer)  │
│ 5. Item permissions (Lakehouse / Warehouse / Model / Report)│
│ 6. OneLake data-level (folder / table RBAC, shortcuts)      │
│ 7. In-model security (RLS / OLS / CLS + dynamic masking)    │
└─────────────────────────────────────────────────────────────┘
```

Model the whole stack top-down. Skipping a layer means the tightest control below is invisible when the layer above is too permissive.

## 2. Domains — the top-level organizing principle

**Domain** is the modern grouping construct: a business area (Finance, Supply Chain, HR, R&D) that owns one or more workspaces.

- Domain **admins** are business data leaders (not the central platform team). They approve endorsements inside the domain, own naming conventions, and decide which workspaces belong.
- **Sub-domains** allow finer grouping (e.g., Finance → AR, AP, Controlling).
- Every workspace belongs to **zero or one domain**. Cross-domain reads happen via **shortcuts** — governed at the destination.

Recommended layout for an enterprise:

```
Finance (domain)
├── Finance-Bronze         (raw SAP CDC land)     — restricted
├── Finance-Silver         (cleansed facts/dims)  — engineers write
├── Finance-Gold-AR        (business marts)       — analysts read
├── Finance-Gold-AP        (business marts)       — analysts read
└── Finance-Reports        (Power BI reports)     — consumers read

Platform (domain)
├── Platform-Shared-Dims   (calendar, currency, org) — read by all domains
└── Platform-Ops           (monitoring, catalog)
```

## 3. Capacity — where the compute (and the license boundary) lives

Every workspace is assigned to a **Fabric capacity** (F-SKU) or a Premium capacity (P-SKU). Capacity governs:

- **Who can consume** — items in an F64+ capacity can be shared with any user (including free Fabric users). Below F64, consumers need Pro licenses.
- **Compute isolation** — noisy neighbours on the same capacity throttle each other. Isolate high-value production workloads on their own capacity.
- **Billing chargeback** — one capacity per business unit maps neatly to internal cost centers.

**Capacity admins** control who can assign workspaces. Restrict this to the platform team — otherwise anyone with a workspace admin role can move it to a hot capacity and blow your budget.

## 4. Workspace roles

Four built-in roles, applied to Microsoft Entra ID users, groups, or service principals:

| Role | Can | Typical assignee |
|---|---|---|
| **Admin** | Everything, incl. delete workspace, manage roles | 2–3 platform engineers |
| **Member** | Read/write all items, share, but not delete workspace | Senior engineers |
| **Contributor** | Read/write items, cannot re-share | Engineers, analysts building content |
| **Viewer** | Read only | Business consumers |

**Best practice:** never assign roles to individual users. Bind roles to **Entra ID security groups** — `sg-finance-fabric-admins`, `sg-finance-fabric-viewers`. Membership becomes an HR/IAM concern, not a Fabric-admin concern.

## 5. Item-level permissions

Each item (Lakehouse, Warehouse, Semantic Model, Report, Notebook, Pipeline) exposes its own permission surface **on top of** workspace roles. Grant these when someone needs access to one item without the whole workspace.

Common patterns:

- **Lakehouse** — grant `ReadData` to allow SQL endpoint queries without Fabric UI access.
- **Warehouse** — grant `T-SQL` roles like `db_datareader` on schemas, not the whole warehouse.
- **Semantic model** — `Build` permission is what a report author needs; a report **viewer** never needs Build.
- **Report** — `Reshare` off by default for external audiences.

## 6. OneLake data-level security

OneLake now supports **role-based access control (RBAC) at the folder and table level** — granular below the item level.

- Create a **OneLake data role** on a Lakehouse that grants read to a specific subfolder or table.
- Assign the role to an Entra ID group.
- The role applies to **all** compute engines that read OneLake (Spark, T-SQL, semantic models via Direct Lake, external tools via ADLS Gen2 API).

Example:

```
Finance-Silver / Lakehouse / Tables /
├── fact_gl_lines       — role "silver-readers"
├── fact_ap_open        — role "silver-readers"
└── dim_employee        — role "silver-readers-hr-only"   ← narrower
```

**Shortcut security** — a shortcut inherits the source's permissions, not the destination's. That is the point (single source of truth) but it means a shortcut into Finance-Gold to Platform-Shared-Dims does NOT let Finance readers see everything in Platform-Shared-Dims — only what they already had access to at the source.

## 7. RLS / OLS / CLS — in-model security

For semantic models and warehouses, three complementary controls:

### Row-Level Security (RLS)

Filters rows dynamically per user. Defined as DAX filter expressions on dimension tables.

```dax
-- Role: "Finance-Controller-DE"
[CompanyCode] IN { "1000", "1010", "1020" }

-- Role: "Cost Center Manager" (dynamic)
[ResponsiblePerson] = USERPRINCIPALNAME()
```

**Rule:** filter on the **dimension**, not the fact. Filtering directly on fact tables tanks Direct Lake performance.

### Object-Level Security (OLS)

Hides entire tables or columns from unauthorized roles — they don't appear in the field list.

Applied in **TMDL / Tabular Editor**:

```
column BankAccount
    objectLevelSecurity =
        {
            "OLS-NoSensitive": None
        }
```

Common use: hide salary, bank account, national ID columns from analyst roles.

### Column-Level Security in Warehouse (T-SQL)

For Fabric Warehouse, use standard T-SQL:

```sql
DENY SELECT ON dim_employee(salary, national_id)
    TO [sg-finance-analysts];
```

For **dynamic data masking** (obfuscate but don't deny):

```sql
ALTER TABLE dim_employee
ALTER COLUMN email
    ADD MASKED WITH (FUNCTION = 'email()');
```

## 8. Sensitivity labels

**Microsoft Purview Information Protection** labels flow through Fabric end-to-end:

1. Author applies a label to a Lakehouse table (`Confidential — Finance`).
2. Label **inherits** into downstream semantic models, reports, and exports to Excel/PPT.
3. Label drives DLP policies (block external sharing of `Confidential` content).

Enable **mandatory labeling** in the Fabric admin portal — every item published must carry a label.

Suggested label hierarchy for enterprise:

- `Public`
- `Internal`
- `Confidential — Finance` / `Confidential — HR` / `Confidential — R&D`
- `Highly Confidential — PII`
- `Highly Confidential — Legal Hold`

## 9. Data Loss Prevention (DLP)

DLP policies in the Microsoft Purview compliance portal act on labeled Fabric content:

- Block downloads of `Highly Confidential — PII` semantic models.
- Alert admin when a report labeled `Confidential — Finance` is shared to an external tenant.
- Prevent export-to-Excel on tables containing detected credit-card patterns.

DLP is **detective + preventive**. Pair it with sensitivity labels; alone it can only match content patterns.

## 10. Endorsements — Promoted / Certified

Two content quality signals visible to users when they search Fabric:

- **Promoted** — a workspace member vouches for the item ("this is ready for use").
- **Certified** — a designated authority within the domain formally certifies. Only **certification admins** (set per domain) can grant this.

Certification requires: naming convention passed, sensitivity label set, lineage complete, RLS validated, deployment pipeline in place, owner group named.

Reports **should** be Certified before executive rollout. Ad-hoc / experimental content stays uncertified — that is the honest signal for consumers.

## 11. Purview integration — catalog & lineage

Connect the Fabric tenant to a **Purview account** in the admin portal. What you get:

- **Data catalog** — every Lakehouse table, Warehouse table, semantic model, report shows up as a searchable Purview asset.
- **Lineage** — Purview draws the end-to-end graph (SAP source → bronze → silver → gold → model → report). Column-level lineage where the compute exposes it.
- **Classifications** — auto-scan detects PII (email, phone, IBAN, national ID) and applies classifications.
- **Business glossary** — link technical assets to business terms owned by the domain.
- **Access requests** — Purview surfaces a "Request access" button on assets the user cannot open; approval flow goes to the data owner.

This is the layer that makes governance discoverable, not just enforced.

## 12. Service principals & automation identities

CI/CD, orchestration, and monitoring should never run as a human.

- Use **service principals** (or **managed identities** on Azure resources) for pipelines, Fabric REST API calls, `fabric-cli` in Azure DevOps, and semantic-model refreshes.
- Grant the SP the **least role** — a refresh SP needs semantic-model contributor, not workspace admin.
- Enable **service principal support** at the tenant level for the specific security groups you use; keep it off by default.
- Rotate secrets every 90 days; store in Azure Key Vault, not in pipeline variables.

## 13. Guest access & B2B

External partners consume Fabric content via Entra B2B guest accounts.

- Guests can be **assigned Fabric roles**, but the tenant setting **"Guests can access Fabric"** must be enabled explicitly.
- Restrict guests to specific security groups (`sg-fabric-external-<partner>`), never the whole tenant.
- Sensitivity labels block external sharing of `Confidential` content — enable that DLP rule before allowing guest access.

## 14. Audit & monitoring

Fabric activity logs land in the **Microsoft 365 Unified Audit Log** and can be exported to Log Analytics / Sentinel.

Alerts I always set up:

- New workspace created outside standard domains.
- New capacity created (finance-billing signal).
- Sensitivity label downgraded on any Confidential asset.
- Sharing of Certified item outside the tenant.
- Failed refresh on a certified semantic model.
- Data role change on any Lakehouse.

Feed the log into a **Fabric Eventhouse** and put a KQL dashboard on top — closes the governance loop inside the same platform.

## 15. Access request flow — how a request should look

```
User needs data
    │
    ▼
Search Purview catalog  ──►  "Request access"
    │
    ▼
Purview creates request  ──►  routed to domain data owner
    │
    ▼
Owner approves           ──►  Purview writes back:
                                 - add user to sg-<workspace>-viewers
                                 - assign RLS role in semantic model
                                 - notify user
    │
    ▼
User can now consume; access grant logged in Purview + M365 audit
```

Automate the write-back with **Logic App / Power Automate** triggered by the Purview request event. The manual click-through in Fabric itself is a source of drift.

## 16. Anti-patterns I avoid

- ❌ Individual users on workspace role lists — use security groups always.
- ❌ Sharing a report to "the whole organization" — kills your labeling story.
- ❌ Certifying a report before RLS is validated — the fastest way to leak.
- ❌ Bronze workspaces open to analysts — bronze is engineer-only, always.
- ❌ Sensitivity labels applied inconsistently across bronze/silver/gold — label at the source and let inheritance propagate.
- ❌ RLS filters on fact tables — always filter through the dim.
- ❌ Service principals with workspace admin rights "just to make it work" — pin to the minimum role, always.
- ❌ Skipping Purview because "the catalog is empty" — it stays empty until you connect it.

---

**Governance checklist for a new Fabric workspace:**

- [ ] Assigned to correct domain
- [ ] Assigned to correct capacity
- [ ] Roles bound to Entra ID groups only
- [ ] Sensitivity label applied (workspace default + item overrides)
- [ ] OneLake data roles created for restricted tables
- [ ] RLS/OLS/CLS validated for each semantic model
- [ ] Deployment pipeline (Dev → Test → Prod) configured
- [ ] Purview scan enabled and successful
- [ ] Audit log flowing to Log Analytics / Sentinel
- [ ] Owner group named in workspace description
- [ ] Certification requested (if production)
