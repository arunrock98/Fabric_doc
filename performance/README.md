# Performance & Optimization on Microsoft Fabric

A practical reference for making Fabric workloads **fast and cheap**. Covers every layer that either burns capacity units or blocks a user waiting for a result — Delta storage, Spark, Warehouse, Direct Lake, Data Factory, Eventstream/KQL, and capacity itself.

All examples are generic — patterns I use repeatedly, not any employer's code.

---

## 1. Where Fabric performance is won or lost

```
┌────────────────────────────────────────────────────────────────┐
│ Layer                        │ Primary lever                   │
├────────────────────────────────────────────────────────────────┤
│ 1. Delta / Lakehouse storage │ V-Order, OPTIMIZE, partitioning │
│ 2. Spark compute             │ NEE, AQE, pool sizing, session  │
│ 3. Warehouse (T-SQL)         │ Statistics, result cache, MVs   │
│ 4. Direct Lake semantic mdl  │ Framing, fallback avoidance     │
│ 5. Data Factory pipelines    │ DIUs, parallel copy, staging    │
│ 6. Eventstream / Eventhouse  │ Update policies, materialized v.│
│ 7. Capacity                  │ Smoothing, autoscale, throttle  │
└────────────────────────────────────────────────────────────────┘
```

Rule of thumb: 80% of Fabric performance issues resolve at layer 1 (storage) or layer 4 (Direct Lake framing). Start there, then work down.

## 2. Delta / Lakehouse — the biggest wins

### V-Order

Fabric-specific write-time optimization that produces Parquet files ordered for the VertiPaq scan engine used by Direct Lake and Power BI. **Always on** for Lakehouse writes by default; verify it wasn't disabled at the session or table level.

```python
# PySpark — check and enforce
spark.conf.get("spark.sql.parquet.vorder.enabled")           # 'true'
spark.conf.set("spark.sql.parquet.vorder.enabled", "true")

# T-SQL Warehouse — V-Order applied automatically on writes; no toggle
```

If Direct Lake reports fallback to DirectQuery, non-V-Ordered files are the #1 suspect. Run `OPTIMIZE ... VORDER` to rewrite them.

### OPTIMIZE (bin-packing) and Z-Order

```sql
-- Compact small files into ~1GB files, apply V-Order, and cluster by common filter cols
OPTIMIZE fact_gl_lines VORDER;
OPTIMIZE fact_gl_lines ZORDER BY (posting_date, company_code) VORDER;
```

- Run after **every bulk load** on tables backing semantic models or hot queries.
- Z-Order only helps for **columns with high cardinality** you actually filter on. Do not Z-Order by low-cardinality columns like flags — pure overhead.
- Rewrites are expensive; schedule during off-peak, not during interactive workload.

### Partitioning strategy

- Partition by columns you filter on **every** query — usually `posting_date` (month/year), `company_code`, or `region`.
- **Don't over-partition**: a partition with < 1 GB is wasted overhead. Aim for 100 MB – 1 GB per file, few tens of files per partition.
- **Avoid partitioning on high-cardinality columns** (customer_id, document_number). Kills metadata perf.

### VACUUM and retention

```sql
-- Reclaim space by removing tombstoned files older than retention
VACUUM fact_gl_lines RETAIN 168 HOURS;   -- 7 days
```

Balance:
- **Shorter retention** = smaller storage, faster metadata reads.
- **Longer retention** = deeper time-travel and safer rollback.

Default of 7 days is fine for most fact tables; keep 30 days for regulated data.

### Maintenance job — one notebook, scheduled nightly

```python
tables = ["fact_gl_lines", "fact_ap_open", "fact_ar_open", "fact_grir"]
for t in tables:
    spark.sql(f"OPTIMIZE {t} VORDER")
    spark.sql(f"VACUUM {t} RETAIN 168 HOURS")
```

## 3. Spark — session-level and pool-level

### Native Execution Engine (NEE)

Fabric's **Native Execution Engine** (C++ vectorized Velox-based) replaces the JVM Spark executor path for supported operators. Typical wins: 2–4x on aggregations and scans, larger on wide joins.

```python
spark.conf.set("spark.native.enabled", "true")
```

Enable at session start for every notebook. Confirm at end via `spark.sparkContext.getConf().get("spark.native.enabled")`.

Not every operator falls back to NEE — check the Spark UI query plan for `NativeExecutionEnabled: true` nodes.

### Adaptive Query Execution (AQE)

On by default. Verify:

```python
spark.conf.get("spark.sql.adaptive.enabled")          # true
spark.conf.get("spark.sql.adaptive.skewJoin.enabled") # true
spark.conf.get("spark.sql.adaptive.coalescePartitions.enabled")  # true
```

AQE dynamically re-plans partition counts, coalesces small partitions, and handles skew — do not disable it.

### Spark pools

- **Starter pool** — instant startup (~10s), shared, capped size. Perfect for dev + light ETL.
- **Custom pool** — dedicated nodes, higher ceiling, but ~60–90s startup. Use for large batch jobs where warm-up cost is amortized over minutes of work.
- **High concurrency mode** — one session shared across notebooks, saves warm-up per notebook.

Rule: dev + interactive on starter, nightly ETL on a custom pool sized to the workload, one shared session for the whole pipeline.

### Session configuration cheat sheet

```python
spark.conf.set("spark.native.enabled", "true")
spark.conf.set("spark.sql.shuffle.partitions", "auto")   # let AQE decide
spark.conf.set("spark.sql.parquet.vorder.enabled", "true")
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", "50MB")  # broadcast small dims
```

### Autotune

Fabric's Autotune adjusts session-level config per notebook based on prior runs. Enable at workspace level; leave it on unless you have hand-tuned configs to defend.

## 4. Warehouse (T-SQL) — statistics first

### Statistics

Fabric Warehouse maintains statistics automatically but on a lag. For freshly loaded tables, force an update:

```sql
CREATE STATISTICS stats_fact_gl_posting_date
    ON fact_gl_lines (posting_date);

UPDATE STATISTICS fact_gl_lines;
```

Missing statistics is the #1 cause of Warehouse queries picking a bad join order.

### Result set caching

On by default at the workspace level. Verify:

```sql
SET RESULT_SET_CACHING ON;
```

Repeat queries within cache TTL (default 24 h) return in milliseconds. Cache invalidates on data change.

### Materialized views

For expensive pre-aggregations used by multiple reports:

```sql
CREATE MATERIALIZED VIEW mv_fin_daily_gl
WITH (DISTRIBUTION = HASH(company_code)) AS
SELECT company_code, posting_date, SUM(amount_local) AS amt
FROM fact_gl_lines
GROUP BY company_code, posting_date;
```

- Refreshed automatically by Fabric when base tables change.
- Costs storage + refresh cycles; only worth it when the underlying query runs many times a day.

### Distribution & CTAS

Choose the right distribution when creating fact tables:

- **HASH** on high-cardinality join key — best for large facts joined to smaller dims.
- **ROUND_ROBIN** — default for staging; not for hot facts.
- **REPLICATE** — small (< 2 GB) dims used in most queries.

```sql
CREATE TABLE fact_ap_open
WITH (DISTRIBUTION = HASH(vendor_key)) AS
SELECT ... FROM staging.ap_open_raw;
```

## 5. Direct Lake — framing and fallback

Direct Lake reads Delta directly, skipping the semantic model's data copy. It falls back to DirectQuery (order-of-magnitude slower) when:

- The Delta files are not V-Ordered → `OPTIMIZE ... VORDER`.
- Column dictionary exceeds 3 GB → split hot columns or move to Import.
- More than 1 active relationship path between two tables → collapse.
- A DAX measure uses functions unsupported in the Direct Lake formula engine → rewrite.
- The table has row-group counts beyond the SKU limit for your capacity.

### Framing

"Framing" = the semantic model refreshing its metadata pointer to the latest Delta version. Options:

- **Automatic** on query — first user pays cold-cache cost.
- **Manual reframe** via REST API `POST /refreshes` with `type: full` — warm the cache after gold load.
- **Cache warming with semantic-link** (`sempy`) — issue the top 20 measure/dim combos post-load; 30-line notebook.

```python
import sempy.fabric as fabric

dataset = "Finance_Model"
warmup_dax = [
    ("Total Debit",   ["Dim_Company", "Dim_Date[Year]"]),
    ("AR Open",       ["Dim_Customer", "Dim_Date[Month]"]),
    # ...
]
for measure, groupby in warmup_dax:
    fabric.evaluate_measure(dataset, measure, groupby_columns=groupby)
```

Ship this as a **post-gold notebook step**, chained after `OPTIMIZE ... VORDER`.

### Model-side wins

- **Explicit measures only** — no implicit aggregation on fact columns.
- **Group measures by display folder** in TMDL; hides the 400-measure junk drawer.
- **Field parameters** for dynamic axis / value swapping — one visual instead of five.
- **Calculation groups** for time-intel (YTD / MTD / PY / YoY) — one group, one measure, many contexts.

## 6. Data Factory / Pipelines — throughput levers

### Copy activity

- **Data Integration Units (DIUs)** — parallelism budget for the activity. Default = auto, usually 4. Raise to 32 for large loads. Each DIU costs; benchmark before defaulting to max.
- **Parallel copy** — for partitioned sources, run N partitions concurrently. Set explicitly.
- **Staged copy** — use for network-hop heavy sources (SAP CDC through gateway); reduces round-trips.
- **Fault tolerance** — enable `skipIncompatibleRow` with a log path when source data may have malformed rows.

### Pipeline patterns

- **Fail fast** — put small validation activities up front, skip the copy if inputs are wrong.
- **Fan-out** — pipeline processes N tables in parallel via `ForEach` with `isSequential = false`, batch count sized to the DIU budget.
- **Idempotent runs** — pass `run_id` down to notebooks; every write partitioned by `run_id`.

### Dataflow Gen2

- Enable **staging** for any Dataflow feeding a Lakehouse or Warehouse — otherwise transformations spill to the mashup engine and slow to a crawl.
- Use **fast copy connectors** (marked in the connector list) where available — skip the mashup engine entirely.

## 7. Eventstream / Eventhouse — streaming perf

### Materialized views in KQL

For hot aggregations queried by dashboards:

```kusto
.create materialized-view AP_Invoice_Blocked_Hourly on table AP_Events
{
    AP_Events
    | where EventType == "InvoiceBlocked"
    | summarize count(), sum(Amount) by bin(EventTime, 1h), CompanyCode
}
```

Refreshes incrementally as data arrives. Direct-query cost approaches zero.

### Update policies

Ingestion-time transformation from raw table → curated table:

```kusto
.alter table AP_Events_Curated policy update
@'[{"IsEnabled": true, "Source": "AP_Events_Raw",
    "Query": "AP_Events_Raw | project EventTime, CompanyCode, Amount, EventType",
    "IsTransactional": true}]'
```

Cheaper than nightly ETL for streaming pipelines.

### Cache and retention

Eventhouse tables have two knobs:
- **Cache policy** — how much data stays hot-SSD (fast queries).
- **Retention policy** — how long data is kept before deletion.

Keep 30d cache for AP/AR monitoring dashboards; 90d retention for audit.

## 8. Shortcut vs Copy — decision rule

- **Shortcut** — zero storage duplication, source of truth stays put, permissions inherit. Best for shared dims across domains and cross-workspace reads.
- **Copy** — data physically moves; you own its lifecycle. Best when the read pattern is heavy and the source is on a slower storage tier (ADLS Gen2 non-Fabric, S3, GCS).

If in doubt: shortcut first, promote to copy only when profiling shows a cold-read tax you can't accept.

## 9. Capacity — the money layer

### Smoothing, burst, throttle

Fabric doesn't fail interactive queries when you exceed capacity — it **smooths** the CU spike over the next 24 hours. If sustained overage continues, it enters **throttling**:

- **Overage 10 min – 60 min**: interactive delays.
- **Overage > 60 min**: interactive rejections; background jobs continue but delayed.
- **Overage > 24 h**: full throttle, everything rejected.

Consequence: an oversized nightly ETL run can silently degrade tomorrow's Power BI reports.

### Autoscale billing

Enable **pay-as-you-go autoscale** on capacities you can't right-size. Sets a max spend ceiling and only bills for exceeded CUs by the second.

### Pause & schedule

For dev/test capacities, schedule **auto-pause** outside working hours. Even F2 SKU adds up over a month.

### Monitoring

- **Fabric Capacity Metrics app** — free, install from AppSource; visualizes CU consumption, throttling risk, top-consuming items.
- **Log Analytics export** for tenant-level trends and alerting.

## 10. Anti-patterns I avoid

- ❌ Running `OPTIMIZE` during interactive hours on production tables — kills user queries.
- ❌ Partitioning by `customer_id` or `document_number` — millions of tiny partitions, metadata death.
- ❌ Setting `spark.sql.shuffle.partitions = 200` and forgetting — let AQE decide.
- ❌ Materialized views over queries that run twice a week — refresh cost exceeds the savings.
- ❌ Storing amounts pre-aggregated in bronze — bronze is raw, always.
- ❌ Bi-directional relationships in a semantic model for cross-filter — Direct Lake fallback trap.
- ❌ Direct Lake with 1 GB dictionaries and no `OPTIMIZE ... VORDER` scheduled — guaranteed DirectQuery fallback.
- ❌ Copy activity with default DIUs on a 100 GB SAP load — leaves capacity + wall-clock on the table.

---

**Optimization checklist for a new pipeline going to production:**

- [ ] `OPTIMIZE ... VORDER` scheduled after every bulk gold load
- [ ] `VACUUM` scheduled weekly with defensible retention
- [ ] Partitioning validated against actual query filters
- [ ] Spark session sets `spark.native.enabled = true`
- [ ] AQE and Autotune on
- [ ] Semantic model reframe / cache warm-up notebook chained after gold
- [ ] Direct Lake fallback rate = 0 % in the perf analyzer
- [ ] Statistics fresh on warehouse fact tables
- [ ] Copy activity DIUs sized to actual throughput, not defaults
- [ ] Capacity Metrics app installed and dashboard bookmarked
- [ ] Autoscale ceiling set (if applicable)
- [ ] Dev/test capacities on scheduled pause
