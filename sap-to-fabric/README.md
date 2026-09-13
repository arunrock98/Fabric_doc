# SAP → Fabric

Practical reference for ingesting **SAP ECC / S/4HANA** into **Microsoft Fabric OneLake**, and modeling it for analytics. Covers the extraction options that actually exist, the SAP-specific traps that break generic pipelines, and the silver-layer transformations that make the data usable.

Everything below is generic — no employer-specific tables, schemas, or code.

---

## 1. Extraction landscape — pick the right path

There is no single "SAP connector." You choose per source system, table size, latency need, and licensing.

| Path | Latency | Best for | Trade-offs |
|---|---|---|---|
| **Fabric Data Factory — SAP CDC connector** | Minutes | Any ODP-enabled source: ECC, S/4HANA, BW, SLT | Requires ODP framework + often SLT. First-class in Fabric. |
| **SAP Table connector** (via SAP .NET Data Provider / SAP HANA ODBC) | Batch | Small dims, config tables, ad-hoc pulls | Full loads only — no delta. Hard on source. |
| **OData V2/V4 from S/4 CDS views** | Batch or near-real-time | Modern S/4 with released CDS views | Public API surface is narrow; private views break without notice. |
| **SAP BW / BW4 extractors** | Batch | Pre-aggregated BW cubes / DSOs | Locked into BW modeling; skip if you want lakehouse-native. |
| **SLT + Fabric mirroring (via SAP Datasphere)** | Seconds | High-volume tables that need CDC | Adds SAP Datasphere as an intermediate; extra license. |
| **SAP Event Mesh / S/4HANA Cloud Events → Eventstream** | Sub-second | Business-object events (AP invoice posted, sales order created) | Event payload is narrow — enrich in silver. |
| **File drop (CSV / IDoc / XML)** | Depends on batch job | Legacy interfaces, third-party feeds | Fragile; use only when nothing else is on the table. |

**Default for greenfield Fabric projects:** SAP CDC connector on ODP, with Event Mesh for the narrow cases where seconds-latency KPIs matter.

## 2. SAP CDC connector — the primary path

Fabric Data Factory's SAP CDC connector uses SAP's **ODP (Operational Data Provisioning)** framework. On the SAP side, ODP is fed by:

- **SLT (Landscape Transformation Replication Server)** — trigger-based row capture; the workhorse.
- **BW extractors** — DataSources with delta support (0FI_GL_14, 2LIS_02_ITM, etc.).
- **ABAP CDS views** with `@Analytics.dataExtraction.enabled: true`.

### Wiring the connector

1. **On-premises data gateway** installed with visibility to the SAP application server.
2. **Linked service** in Fabric Data Factory pointing at the SAP system (SNC/username/password or X.509).
3. **Copy activity** with **CDC = enabled** — the connector manages initial + delta internally, watermark stored in the connector's state.
4. **Sink**: OneLake Lakehouse, Delta table in `bronze/` schema, partitioned by ingest date.

### Initial + delta strategy

- **First run**: full extract, land as `bronze/<table>` with column `_ingest_ts`, `_source_op = 'I'` for every row.
- **Delta runs**: connector emits `I` (insert), `U` (update), `D` (delete/before-image). Land as append; do NOT merge at bronze.
- **Silver**: MERGE from bronze into silver, applying `_source_op` semantics. Keep the append-only bronze for auditability.

### Delta capture caveats

- Some SAP tables have **no reliable change timestamp** (BSEG is the poster child). SLT triggers are how you get true CDC — extractors alone will miss updates outside their audit column.
- Cluster tables (RFBLG, KOCLU) — historical; on HANA these are usually flattened.
- Pooled tables — same story, mostly gone in S/4.

## 3. The MANDT trap

**Every** application table has a `MANDT` (client) column as the first key. If you don't filter it at bronze, you'll happily join across production and test clients on the same appserver.

```sql
-- Bronze bronze/bkpf filter (SLT/CDC usually does this for you; verify)
SELECT * FROM bkpf WHERE MANDT = '100'
```

Enforce at bronze land time, not silver. Once wrong client data is in bronze, downstream de-duplication is expensive.

## 4. Data-type & encoding pitfalls

| SAP behavior | Trap | Fix at bronze/silver |
|---|---|---|
| Numeric amounts stored as **DEC 13,2** but exposed as string with trailing minus (`123.45-`) | Silent negation loss | Cast in silver: `regexp_replace + cast to decimal(23,4)`; preserve sign |
| Dates as `CHAR(8)` `YYYYMMDD` | Sorts fine, but date functions fail | `to_date(col, 'yyyyMMdd')` in silver |
| Times as `CHAR(6)` `HHMMSS` | Same | `to_timestamp(concat(date, time), 'yyyyMMddHHmmss')` |
| Timezone: SAP stores in **system TZ**, usually UTC in S/4, application server local in ECC | Off-by-hours joins with event data | Convert to UTC once in silver, keep both `posting_ts_utc` and `posting_ts_local` |
| Boolean as `X` / blank | `case when col = 'X' then true else false end` | Do it once in silver |
| Numeric fields stored right-justified with leading zeros (`0000012345`) | String join to non-padded source fails | `cast(int(col) as string)` or trim leading zeros in silver |
| Language-dependent texts | Missing `SPRAS = 'E'` gets you German texts | Filter texts to your target language in silver |

## 5. Currency & unit conversion — do it once, at silver

Never convert currency in DAX or at query time. In silver:

1. Load `TCURR` (exchange rates) as a slowly-updated dim.
2. For each amount field, produce `amt_local`, `amt_group` (e.g., EUR), `amt_reporting` (e.g., USD) columns.
3. Rate selection rule: posting-date rate for realized, valuation-date rate for open items. Codify it — every ex-SAP consultant has a different opinion.

For quantities: `MSEG` / `EKPO` amounts are in **material base UoM**, not order UoM. Join `MARA` for conversion factors or you'll ship inventory reports with impossible numbers.

## 6. Header + item stitching

SAP is header/item everywhere. Silver combines them:

| Document | Header | Item | Notes |
|---|---|---|---|
| Accounting | `BKPF` | `BSEG` | Join on `BUKRS + BELNR + GJAHR`. BSEG has ~350 columns — cherry-pick. |
| Purchasing | `EKKO` | `EKPO` (+ `EKBE` history) | Join on `EBELN`. Use `EKBE` for GR/IR events. |
| Sales | `VBAK` | `VBAP` | Join on `VBELN`. |
| Material doc | `MKPF` | `MSEG` | Join on `MBLNR + MJAHR`. |
| Inbound delivery | `LIKP` | `LIPS` | |
| Billing | `VBRK` | `VBRP` | |

In silver, produce **one flat fact per business event** — `fact_gl_lines`, `fact_po_lines`, `fact_so_lines` — with header attributes already joined in. Downstream DAX gets simple, model gets Direct-Lake-friendly.

## 7. Master data & SCD Type 2

Most SAP masters need Type 2 tracking for accurate historical reporting:

| Master | Table(s) | SCD 2 columns worth tracking |
|---|---|---|
| Vendor | `LFA1`, `LFB1`, `LFM1` | Payment terms, blocked status, account group |
| Customer | `KNA1`, `KNB1`, `KNVV` | Credit limit, blocked status, sales area |
| Material | `MARA`, `MARC`, `MBEW` | MRP controller, valuation class, standard price |
| GL Account | `SKA1`, `SKB1` | Account group, P&L type |
| Cost Center | `CSKS`, `CSKT` (texts) | Responsible person, hierarchy node |
| Cost Element | `CSKA`, `CSKB` | Type (primary/secondary) |
| Profit Center | `CEPC`, `CEPCT` | Segment, hierarchy |
| Company Code | `T001` | Currency, chart of accounts |
| Plant | `T001W` | Company code assignment |

**Hierarchies** (cost center / profit center / product) live in `SETHEADER` / `SETNODE` / `SETLEAF`. Ugly to unpack — build a recursive CTE in silver, flatten to L1..L6 columns in gold.

## 8. Event-driven ingestion (near-real-time)

For the narrow cases where minutes-latency isn't enough:

```
S/4HANA (Event Mesh)                          Fabric
────────────────────                          ─────────────────────────────
Business event topic  ──►  Event grid  ──►    Eventstream
  e.g. sap.s4.beh.                              │
  supplierinvoice.v1.Created                    ▼
                                              Eventhouse (KQL)
                                                │
                                                ▼
                                              Data Activator
                                                │
                                                ▼
                                              Teams / email / webhook
```

Two patterns I use:

- **AP invoice monitoring** — event fires on `SupplierInvoice.Created` and `.Blocked`; Data Activator alerts controller when blocked count crosses a threshold.
- **Sales order backlog** — event fires on `SalesOrder.Created` and status changes; KQL rolls up open backlog by division in near real time.

Events carry business keys, not full payload. Enrich in KQL with a lookup against a small hot dim table pinned in Eventhouse.

## 9. Ingestion layout in OneLake

Recommended layout — makes governance, retention, and lineage tractable:

```
OneLake / <domain-workspace>
├── bronze_lakehouse/
│   ├── Files/sap/<system>/<table>/date=YYYY-MM-DD/*.parquet
│   └── Tables/sap_<system>_<table>            (append-only, raw CDC)
├── silver_lakehouse/
│   └── Tables/
│       ├── dim_vendor  dim_customer  dim_material  dim_costcenter …
│       └── fact_gl_lines  fact_ap_open  fact_ar_open  fact_grir …
└── gold_warehouse/                            (T-SQL views / marts)
    └── vw_finance_ar_aging  vw_finance_grir  …
```

Bronze is a workspace of its own — governed, restricted, retained for audit. Silver and gold live in domain workspaces (Finance, Supply Chain, HR).

## 10. Anti-patterns I avoid

- ❌ Pulling BSEG in full every night — trigger a full only when you have a schema change, otherwise CDC.
- ❌ Cherry-picking columns at the source RFC then discovering you need more later — extract wide, model narrow.
- ❌ Doing currency conversion in Power BI — kills Direct Lake and hides audit trail.
- ❌ Ignoring `MANDT` because "we only have one client" — until someone connects a QA system.
- ❌ Modeling SAP tables 1:1 in the lakehouse — analysts drown. Flatten to business facts and dims.
- ❌ Trusting CDS view names to be stable across S/4 releases — private views (`P_*`, `C_*`) can and do change.
- ❌ Using OData for large facts — page-based pagination, chokes at scale.

---

**Reference — SAP tables every finance/logistics Fabric project touches:**

| Domain | Tables |
|---|---|
| GL | `BKPF`, `BSEG`, `SKA1`, `SKB1`, `T001`, `T003` |
| AP | `LFA1`, `LFB1`, `LFC1`, `BSAK`, `BSIK`, `RBKP`, `RSEG` |
| AR | `KNA1`, `KNB1`, `KNC1`, `BSAD`, `BSID`, `VBRK`, `VBRP` |
| Purchasing | `EKKO`, `EKPO`, `EKBE`, `EKKN`, `EKET` |
| Sales | `VBAK`, `VBAP`, `VBEP`, `VBFA`, `LIKP`, `LIPS` |
| Inventory | `MARA`, `MARC`, `MBEW`, `MSEG`, `MKPF` |
| Controlling | `CSKS`, `CSKA`, `CSKB`, `CEPC`, `COEP`, `COSS`, `COSP` |
| GR/IR | `EKBE`, `RSEG`, `MSEG` (movement types 101/102/122/123) |
| Config / rates | `T001`, `T001W`, `T024E`, `TCURR`, `TCURV` |
