# Metadata-Driven Medallion Ingestion Framework in Microsoft Fabric

> **One pipeline, one control table, any number of sources.**
> A reusable ELT pattern for Microsoft Fabric. Adding a new source means adding a row to a table, not building a new pipeline.

*Illustrative scenario: the company, volumes and results below are realistic examples, not a specific client engagement.*
---

## 1. The business problem

A mid-sized industrial manufacturer runs its reporting on Microsoft Fabric (F64 capacity). Over two years, the data team built **35 hand-crafted pipelines**, one per source table:

| Source system | Examples | Volume |
|---|---|---|
| SAP ECC (replicated via SLT into a staging SQL database) | `VBAK`, `VBAP`, `EKKO`, `EKPO`, `MARA`, `AUFK` | ~120M rows |
| MES (SQL Server) | Production orders, confirmations, scrap | ~40M rows |
| CRM (Salesforce) | Accounts, opportunities | ~2M rows |
| SharePoint / Excel | Plant targets, budget, mappings | small |

**Pain points:**

- **Every table was a full reload** each night, so the nightly refresh took about **4.5 hours** and often ran into the 07:00 business window.
- **Capacity throttling**: the overnight peak pushed the F64 capacity into throttling, which slowed Power BI reports in the morning.
- **Slow onboarding**: adding a new source table took **about 2 weeks** (build, test, deploy and document a new pipeline).
- **Inconsistent logic**: every pipeline handled deduplication, data types and audit columns slightly differently.

---

## 2. The solution: metadata over code

```
┌────────────────────┐     ┌──────────────────────────────────────┐
│ meta.ingestion_    │     │  pl_ingest_master  (ONE pipeline)    │
│ control            │────▶│  Lookup → ForEach → Copy → Notebook   │
│ (Fabric Warehouse) │     │  → update watermark                  │
└────────────────────┘     └──────────────────┬───────────────────┘
                                              ▼
   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
   │ BRONZE       │──▶ │ SILVER       │──▶ │ GOLD         │──▶ │ Power BI         │
   │ raw, append, │    │ typed, dedup,│    │ star schemas,│    │ Direct Lake      │
   │ + audit cols │    │ MERGE (SCD1) │    │ conformed    │    │ semantic model   │
   └──────────────┘    └──────────────┘    │ dimensions   │    └──────────────────┘
       lh_bronze           lh_silver       └──────────────┘
                                              wh_gold
```

### Fabric components

| Layer | Fabric item | Purpose |
|---|---|---|
| Metadata | **Fabric Warehouse** (`wh_meta`) | Control table and watermark procedure (writable through T-SQL) |
| Orchestration | **Data Factory pipeline** | One parameterised master pipeline |
| Bronze | **Lakehouse** `lh_bronze` | Raw data as delivered, append-only, with audit columns |
| Silver | **Lakehouse** `lh_silver` + **Spark notebook** | Cleansed, typed, deduplicated, merged |
| Gold | **Warehouse** `wh_gold` | Business-ready star schemas |
| Consumption | **Power BI (Direct Lake)** | Semantic model with no import refresh |

---

## 3. The control table

```sql
CREATE TABLE meta.ingestion_control
(
    control_id        INT           NOT NULL,
    source_system     VARCHAR(50)   NOT NULL,   -- SAP | MES | CRM | FILES
    source_object     VARCHAR(200)  NOT NULL,   -- e.g. sap.VBAK
    target_table      VARCHAR(200)  NOT NULL,   -- e.g. sap_vbak
    load_type         VARCHAR(10)   NOT NULL,   -- FULL | INCR | CDC
    watermark_column  VARCHAR(100)  NULL,       -- e.g. AEDAT, ModifiedDate
    last_watermark    VARCHAR(50)   NULL,
    primary_keys      VARCHAR(400)  NULL,       -- comma-separated, e.g. MANDT,VBELN
    partition_column  VARCHAR(100)  NULL,
    priority          INT           NOT NULL,   -- load order / dependency group
    is_active         BIT           NOT NULL,
    last_run_utc      DATETIME2(6)  NULL,
    last_run_rows     BIGINT        NULL
);
```

**Example rows:**

| control_id | source_object | target_table | load_type | watermark_column | primary_keys |
|---|---|---|---|---|---|
| 1 | sap.VBAK | sap_vbak | INCR | AEDAT | MANDT,VBELN |
| 2 | sap.VBAP | sap_vbap | INCR | AEDAT | MANDT,VBELN,POSNR |
| 3 | mes.ProductionOrders | mes_prod_orders | CDC | ModifiedDate | OrderId |
| 4 | crm.Account | crm_account | INCR | SystemModstamp | Id |
| 5 | files.PlantTargets | file_plant_targets | FULL | | PlantCode,Year |

**Adding a new source = one `INSERT` statement.**

```sql
CREATE PROCEDURE meta.usp_update_watermark
    @control_id    INT,
    @new_watermark VARCHAR(50),
    @rows_copied   BIGINT
AS
BEGIN
    UPDATE meta.ingestion_control
    SET    last_watermark = @new_watermark,
           last_run_rows  = @rows_copied,
           last_run_utc   = SYSUTCDATETIME()
    WHERE  control_id     = @control_id;
END;
```

---

## 4. The master pipeline: `pl_ingest_master`

1. **Lookup `GetActiveObjects`**: reads every active row from `meta.ingestion_control`, ordered by `priority`.
2. **ForEach `ProcessObject`** (parallel, batch count 8): for each row:
   1. **Lookup `GetNewWatermark`** (INCR/CDC only): `SELECT MAX(<watermark_column>)` on the source. This fixes the upper bound of the load window so that rows arriving mid-run aren't lost.
   2. **Copy `SourceToBronze`**: builds the source query dynamically:
      ```sql
      SELECT * FROM <source_object>
      WHERE <watermark_column> >  '<last_watermark>'
        AND <watermark_column> <= '<new_watermark>'
      ```
      For `FULL` it's a plain `SELECT *`. Data lands in `lh_bronze` as Delta with the audit columns `_batch_id`, `_source_system` and `_ingested_at`.
   3. **Notebook `nb_bronze_to_silver`**: parameterised Spark merge (see below).
   4. **Stored procedure `usp_update_watermark`**: runs only if the copy and the notebook succeed, so a failed run is simply repeated the next night.
3. **Failure path**: logs to `meta.ingestion_log` and sends a Teams alert.

---

## 5. Notebook `nb_bronze_to_silver` (PySpark)

```python
# Parameters (passed from the pipeline)
source_table   = "sap_vbak"
target_table   = "sap_vbak"
primary_keys   = "MANDT,VBELN"
watermark_col  = "AEDAT"
load_type      = "INCR"
batch_id       = "20261005_0200"

from delta.tables import DeltaTable
from pyspark.sql import functions as F, Window

pk = [c.strip() for c in primary_keys.split(",")]

df = spark.table(f"lh_bronze.{source_table}")
if load_type != "FULL":
    df = df.filter(F.col("_batch_id") == batch_id)

# Keep the latest version of each business key
if watermark_col:
    w = Window.partitionBy(*pk).orderBy(F.col(watermark_col).desc())
    df = df.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")

df = df.withColumn("_silver_loaded_at", F.current_timestamp())

target = f"lh_silver.{target_table}"
if load_type == "FULL" or not spark.catalog.tableExists(target):
    df.write.mode("overwrite").format("delta").saveAsTable(target)
else:
    cond = " AND ".join(f"t.{c} = s.{c}" for c in pk)
    (DeltaTable.forName(spark, target).alias("t")
        .merge(df.alias("s"), cond)
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute())
```

One notebook serves every source. The business-specific rules (type casts, SAP date conversion, code mappings) live in a small configuration table, not in code.

---

## 6. Performance and cost optimisation

| Technique | Where | Effect |
|---|---|---|
| **Incremental and CDC loads** instead of full reloads | Copy and Silver | Moves only changed rows (a few % of the daily volume) |
| **Watermark window** (`> last AND <= new`) | Copy | No gaps and no duplicates on reruns |
| **V-Order** (Fabric's default Parquet optimisation; check the setting for your runtime) | Silver / Gold | Faster Direct Lake and SQL reads |
| **`OPTIMIZE ... ZORDER BY`** on large tables, run weekly | Silver | Fewer small files, better data skipping |
| **`VACUUM ... RETAIN 168 HOURS`** | Bronze / Silver | Controls storage growth |
| **Partitioning** only on very large tables (for example by year/month) | Bronze | Avoids the small-file problem on small tables |
| **Parallel ForEach** grouped by priority | Pipeline | Short overall runtime while respecting dependencies |
| **Direct Lake** semantic model | Power BI | No separate import refresh |

```sql
OPTIMIZE lh_silver.sap_vbap ZORDER BY (VBELN);
VACUUM   lh_silver.sap_vbap RETAIN 168 HOURS;
```

---

## 7. Results (illustrative)

| KPI | Before | After |
|---|---|---|
| Number of pipelines | 35 | **1** (+1 notebook) |
| Nightly refresh duration | ~4.5 h | **~1.2 h** |
| Capacity (CU) consumption overnight | baseline | **~40 % lower** |
| Time to add a new source table | ~2 weeks | **~30 minutes** (one row plus a test run) |
| Reports ready for the 07:00 business window | often late | **every day** |

---

## 8. Governance and operations

- **Git integration and deployment pipelines** (Dev → Test → Prod). The control table is versioned as SQL scripts.
- **Run logging** in `meta.ingestion_log` (rows copied, duration, status) feeds a small monitoring report in Power BI.
- **OneLake security and workspace roles** separate the Bronze (engineering only) and Gold (business) access.
- **Sensitivity labels and lineage** through Microsoft Purview.

---

## 9. Lessons learned

1. **Fix the watermark upper bound before copying.** Otherwise rows that change during the run are lost.
2. **Update the watermark last**, and only on success. That makes every run safely repeatable.
3. **Don't partition everything.** Small tables plus partitioning means lots of small files and slower reads.
4. **Keep business rules out of the generic notebook.** Put them in configuration, or in Gold.
5. **Monitor CU usage** in the Fabric Capacity Metrics app before and after. That's how you prove the saving.

---

*Author: Arunraj Selvaraj · Microsoft Fabric (DP-600, DP-700) · Data & Analytics Engineering*
