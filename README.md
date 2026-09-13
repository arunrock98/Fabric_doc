# Fabric_doc — Arunraj Selvaraj

**Senior Data Engineer · Microsoft Fabric · Azure Synapse · SAP → OneLake**

Portfolio and working notes for building enterprise data platforms on Microsoft Fabric. Based in Mannheim, DE — open to Germany and US roles.

---

## Certifications

- **DP-700** — Microsoft Certified: Fabric Data Engineer Associate
- **DP-600** — Microsoft Certified: Fabric Analytics Engineer Associate

## About

10+ years in data, 4+ years hands-on with Microsoft Fabric and Azure Synapse. Currently Senior Data Engineer at ABB AG (Mannheim), building the R&D unified OneLake program and event-driven Fabric workloads on top of SAP S/4HANA. Deep SAP FI-CO / CO-PA / CFIN background — I bridge the finance-domain-to-lakehouse gap that most Fabric projects underestimate.

- 🇩🇪 Germany permanent resident · 🇺🇸 US Green Card (EAD)
- Languages: English (native), German (B1)

## Microsoft Fabric — what I build

| Area | Stack |
|---|---|
| Ingestion | Data Factory pipelines, Dataflows Gen2, mirroring, shortcuts, SAP CDC events |
| Storage | OneLake, Lakehouse (Delta), Warehouse, medallion (bronze/silver/gold) |
| Compute | Spark / PySpark notebooks, T-SQL warehouse, KQL over Eventhouse |
| Streaming | Eventstream, Eventhouse, Data Activator (reflex rules) |
| Semantic | Direct Lake semantic models, DAX, RLS/OLS, Power BI reports |
| Governance | Domains, workspace roles, deployment pipelines, Purview lineage |
| DevOps | Git integration, deployment pipelines, Azure DevOps YAML, fabric-cli |

## Signature work

- **Unified OneLake for R&D at ABB** — domain design, workspace layout, shortcut strategy across sources; replaces siloed Synapse warehouses.
- **8 financial data models** — GR/IR, Credit, Dispute, AP, AR, Controlling / Cost Accounting, OTD, Inventory — designed on Fabric medallion with Direct Lake serving Power BI.
- **Event-driven Fabric POCs** — AP/AR invoice monitoring and IoT telemetry via SAP CDC events → Eventstream → Data Activator alerts.

## What's in this repo

This is a living reference — patterns and code snippets I use often on Fabric projects.

- `notebooks/` — Spark/PySpark patterns (medallion transforms, incremental merges, deduplication, SCD Type 2)
- `pipelines/` — Data Factory pipeline JSON exports and orchestration patterns
- `warehouse/` — T-SQL patterns for Fabric Warehouse (windowed loads, PK enforcement workarounds)
- `semantic-models/` — DAX measures, Direct Lake vs Import trade-offs, RLS patterns
- `eventstream/` — Eventstream + Eventhouse + Data Activator wiring examples
- `governance/` — Domain / workspace / capacity layout templates
- `sap-to-fabric/` — SAP CDC → OneLake ingestion notes, common pitfalls
- `dp-600-700/` — Study notes and exam preparation references

> Content is added as I extract reusable pieces from real work. Everything here is generic — no employer-specific data, schemas, or code.

## Contact

- **Email:** write2arunrajs@gmail.com
- **LinkedIn:** _add your LinkedIn URL here_
- **Location:** Mannheim, DE (open to relocation within DE / to US)

---

_This repository is a personal knowledge base. Views expressed here are my own and do not represent my employer._
