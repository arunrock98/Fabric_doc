# Real-Time Sensor Analytics & AI Hybrid Search — Tiger Cloud (PostgreSQL + TimescaleDB)

A step-by-step orchestration guide for building a **real-time IoT sensor analytics platform** with an **AI hybrid-search finale**, on **Tiger Cloud** (Tiger Data's managed PostgreSQL with the TimescaleDB extension), driven by an **AI coding agent over MCP**.

Source walkthrough: Krish Naik — *Real-Time Sensor Data Analytics & AI Implementation with Tiger Cloud* (YouTube, Nov 2026) — <https://www.youtube.com/watch?v=69LqL2g9sr8>. Figures quoted below are the ones observed in that demo; SQL is a clean reference implementation written for this repo (verified against the Tiger Data docs), not a copy of the demo notebook.

Everything below is generic and employer-neutral. Section 12 maps every step to its **Microsoft Fabric Real-Time Intelligence** equivalent.

---

## 0. TL;DR — what gets built

| # | Capability | Tiger Cloud feature | Demo result |
|---|---|---|---|
| 1 | Provision a managed Postgres from an AI agent | Tiger CLI + Tiger MCP (`service_create`) | Service up in seconds, `.env` downloadable |
| 2 | Ingest ~1.7 M sensor readings | Hypertable (auto time partitioning) | 20 sensors × 1/min × 60 days = **1,728,000 rows**, 9 chunks |
| 3 | Agent-reviewed schema | MCP "best practices" review | FK, NOT NULL, `CHECK humidity 0–100`, one-reading-per-sensor-per-timestamp |
| 4 | Catch faulty sensors | Seasonality-adjusted z-score (hour-of-day baseline) | 3 planted faults found, threshold **\|z\| > 3.5** |
| 5 | Fast dashboards | Continuous aggregate (hourly rollup) | Reads ~**60× fewer rows** than the raw hypertable for the same answer |
| 6 | Cheap history | Columnstore compression, `segmentby sensor_id` | **249 MB → 52 MB** (console: ~71 % savings), queries unchanged |
| 7 | Safe destructive tests | Service **fork** + retention policy on the fork only | Fork in ~1 min on the trial tier; original untouched |
| 8 | AI search over free text | BM25 (`pg_textsearch`) + dense vectors (pgvector HNSW) + **RRF** | 685 EV-charger maintenance reports, query "animals chewing wires" |

---

## 1. Why this pattern — the problem it solves

Modern apps (IoT fleets, AI agents calling LLMs, token/usage logs) produce **millions to billions of time-stamped rows**. Plain OLTP tables slow down on three fronts at once:

- **Write volume** — continuous appends, never-ending growth.
- **Aggregate reads** — dashboards re-scan raw rows for the same hourly/daily rollups over and over.
- **Storage cost** — old data is rarely updated, but kept for months.

Tiger Cloud's pitch: it is **plain PostgreSQL** (same SQL, drivers, tools — no new database, no lock-in fork) with the **TimescaleDB** extension adding time-series "superpowers": hypertables, continuous aggregates, columnar compression, retention policies, plus platform features (forks, MCP). *If you know SQL you know ~90 % of it* — the remaining 10 % is what this guide documents.

## 2. Special use cases — where this fits

| Use case | Why the pattern fits | Key features used |
|---|---|---|
| **IoT / sensor monitoring** (cold chain, HVAC, freezers, factory lines) | 1-per-minute (or faster) readings, daily cycles, fault detection | Hypertable, z-score anomalies, hourly cagg |
| **Predictive maintenance** | Long history to train models + real-time alerting on the same store | Compression for history, cagg for features, retention |
| **EV charging / field-service ops** | Telemetry *and* free-text technician reports about the same assets | Time series + hybrid search in one Postgres |
| **AI-agent observability** | Agents emit logs, tool calls, token counts per call | Hypertable on `event_time`, cagg on cost/tokens per agent per hour |
| **Energy & utilities / smart meters** | Huge row counts, strong seasonality (hour-of-day, weekday) | Seasonality-adjusted baselines, compression |
| **RAG over operational documents** | Keyword precision (part numbers, error codes) + semantic recall (paraphrases) | BM25 + vectors fused with RRF |
| **Safe experimentation** | Test retention, schema migrations, heavy backfills without touching prod | Service fork |

**When *not* to use it:** low-volume transactional data with no time dimension; workloads already standardised on a lakehouse where Fabric Eventhouse / Delta covers the need (see §12).

## 3. Architecture

```
┌──────────────────────┐  prompts   ┌──────────────────┐  MCP tools (service_create,
│ AI coding agent      │──────────►│ Tiger MCP server │  service_list, service_fork,
│ (Devin / Claude Code │           │ (bundled w/ CLI) │  docs search, best practices)
│  / Cursor / Codex)   │◄──────────└────────┬─────────┘
└─────────┬────────────┘                    │ Tiger Cloud API
          │ runs scripts / psql             ▼
          │                 ┌──────────────────────────────────────────────┐
          │  .env (PG*)     │ Tiger Cloud service (PostgreSQL + TimescaleDB)│
          ├────────────────►│                                              │
┌─────────┴────────────┐    │  sensors (dim)        sensor_readings (HYPERTABLE)
│ Jupyter notebook     │    │                        └─ chunks by time      │
│ (Python, psycopg,    │    │  sensor_hourly  (CONTINUOUS AGGREGATE)        │
│  fastembed)          │    │  columnstore policy  (compress > 7 days)      │
└──────────────────────┘    │  maintenance_reports + BM25 idx + HNSW idx    │
                            └──────────────┬───────────────────────────────┘
                                           │ tiger service fork
                                           ▼
                            ┌──────────────────────────────────────────────┐
                            │ Fork (e.g. retention-experiment-db)          │
                            │  add_retention_policy(... 30 days) — isolated│
                            └──────────────────────────────────────────────┘
```

---

## 4. Step-by-step orchestration

The whole build is 14 steps in five phases. Each step lists **who does it** (Console / CLI / Agent / Notebook), **what to run**, and **how to verify**.

```
Phase A  Provision        1 Account → 2 Service → 3 CLI → 4 MCP → 5 Agent smoke test
Phase B  Data & schema    6 Generate data → 7 Agent schema review → 8 Load + hypertable
Phase C  Analytics        9 Plant faults → 10 Anomaly detection → 11 Continuous aggregate benchmark
Phase D  Storage & safety 12 Columnstore compression → 13 Fork + retention experiment
Phase E  AI search        14 Hybrid search (BM25 + vectors + RRF)
```

### Phase A — Provision

#### Step 1 · Create a Tiger Cloud account (Console)
- `tigerdata.com/cloud` → **Start a free trial**. At the time of the demo, new accounts got **$1,000 credit for 30 days, no credit card** (check current terms).

#### Step 2 · Create a service (Console or CLI)
Console wizard choices used in the demo:

| Setting | Choice | Why |
|---|---|---|
| Compute | 0.5 CPU / 2 GB RAM | Enough for ~2 M rows; billed per hour against credits |
| Cloud / region | AWS or Azure, nearest region | Network round-trip dominates small-query latency (see Step 11) |
| Environment | **Development** | Ad-hoc analytics, testing. Use *Production* for HA live workloads |
| Replica / VPC | None / not connected | Demo only |
| Name | e.g. `tiger-demo` | — |

Then **Download config** → `.env` (or `.ini`) containing `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`. Load it in Python with `python-dotenv`. **Never commit this file.**

CLI equivalent:
```bash
tiger service create --name tiger-demo --region us-east-1
```

#### Step 3 · Install and authenticate Tiger CLI (CLI)
```bash
curl -fsSL https://cli.tigerdata.com | sh     # or the Homebrew / Windows method from the docs
tiger auth login                              # opens browser → Authorize
tiger auth status                             # shows project ID + logged-in state
tiger service list                            # services in the project
tiger service get <service-id>                # type, env, CPU, connection string
tiger db connect                              # psql session
```
If the CLI asks, run `tiger upgrade` first.

#### Step 4 · Install Tiger MCP for your coding agent (CLI)
```bash
tiger mcp install            # interactive: pick Claude Code, Codex, Cursor, Devin, VS Code, Gemini…
# restart the agent so it loads the new MCP config
```
**Guardrail:** for anything close to production, run MCP read-only:
```bash
tiger config set read_only true   # refuses create/fork/start/stop/resize/delete
```

#### Step 5 · Smoke-test the agent (Agent)
Prompts that worked in the demo:
1. *"List my Tiger Cloud services."* → agent calls `service_list`.
2. *"Create a Tiger service called tiger-demo-123 and generate the sample data by running generate_sensor_data.sql."* → agent proposes region, asks for confirmation, creates the service (status **ready**, type TimescaleDB).

✅ Verify: new service appears in Console and in `tiger service list`.

### Phase B — Data & schema

#### Step 6 · Generate the synthetic datasets (Agent or Notebook)

**Dataset 1 — sensor readings (`data.csv`) + metadata (`sensors.csv`)**
- 20 sensors (`sensor_id`, `model`, `location`).
- One reading per sensor per minute for 60 days → 86,400 timestamps × 20 = **1,728,000 rows** (~71 MB CSV).
- Baseline per sensor: temperature uniform **18–24 °C**, humidity **40–60 %**, plus a **daily cycle** (warm afternoons, cool nights).

**Dataset 2 — EV charging-station maintenance reports (`ev_maintenance_reports.csv`)**
- **685** free-text reports: `id`, `station`, `date`, `description`, `status`. Used only in Step 14.

> Gotcha from the demo: the agent first generated the CSVs **locally only**. Ask explicitly: *"Upload the data into the Tiger service."* Then check the Console **Explorer** shows non-zero objects.

#### Step 7 · Let the agent review the schema (Agent)
Prompt: *"Examine data.csv and sensors.csv. Use Tiger MCP best practices and propose an optimized schema."* → review → *"Apply the schema and load the data."*

Recommendations the agent applied:
- Preserve existing column names (no surprise renames downstream).
- `NOT NULL` on required fields.
- **Foreign key** `sensor_readings.sensor_id → sensors.sensor_id`.
- **`CHECK (humidity BETWEEN 0 AND 100)`**.
- **One reading per sensor per timestamp** (unique `(sensor_id, time)`).

#### Step 8 · Create the hypertable and load (Notebook / SQL)

A **hypertable** is a normal Postgres table automatically partitioned into **chunks** by time (optionally by another dimension). Queries touching a time range only scan the matching chunks — "chunk exclusion" — instead of the whole table.

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE sensors (
    sensor_id  TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    location   TEXT NOT NULL
);

CREATE TABLE sensor_readings (
    time         TIMESTAMPTZ       NOT NULL,
    sensor_id    TEXT              NOT NULL REFERENCES sensors (sensor_id),
    temperature  DOUBLE PRECISION  NOT NULL,
    humidity     DOUBLE PRECISION  NOT NULL CHECK (humidity BETWEEN 0 AND 100),
    UNIQUE (sensor_id, time)                       -- one reading per sensor per timestamp
) WITH (
    tsdb.hypertable,                               -- TimescaleDB 2.20+ direct-create syntax
    tsdb.partition_column = 'time',
    tsdb.segmentby        = 'sensor_id',           -- columnstore grouping (Step 12)
    tsdb.orderby          = 'time DESC'
);

-- bulk load (psql)
\copy sensors         FROM 'sensors.csv' CSV HEADER
\copy sensor_readings FROM 'data.csv'    CSV HEADER
```
Older TimescaleDB: create a plain table, then `SELECT create_hypertable('sensor_readings', by_range('time'));`.

✅ Verify:
```sql
SELECT count(*) FROM sensor_readings;                                   -- 1,728,000
SELECT count(*) FROM timescaledb_information.chunks
 WHERE hypertable_name = 'sensor_readings';                             -- demo: 9 chunks
SELECT extname, extversion FROM pg_extension;                           -- timescaledb, pg_textsearch, vector…
```

### Phase C — Analytics

#### Step 9 · Plant known faults (Notebook)
Testing a detector needs ground truth. The demo planted three faults:

| Sensor | Fault | Signature |
|---|---|---|
| `sensor_007` | Heater stuck at **48 °C for 30 min**, 5 days ago | Short, sharp temperature spike |
| `sensor_012` | Humidity sensor malfunction | Humidity out of its normal band |
| `sensor_017` | **Freezer door left open overnight** | Hours-long temperature rise at night (when it should be coldest) |

```sql
-- example: sensor_007 heater stuck at 48 °C for 30 minutes, 5 days ago
UPDATE sensor_readings
   SET temperature = 48
 WHERE sensor_id = 'sensor_007'
   AND time >= date_trunc('minute', now() - INTERVAL '5 days')
   AND time <  date_trunc('minute', now() - INTERVAL '5 days') + INTERVAL '30 minutes';
```
After planting, refresh the hourly rollup (Step 11): `CALL refresh_continuous_aggregate('sensor_hourly', NULL, NULL);`

#### Step 10 · Seasonality-adjusted anomaly detection (Notebook / SQL)
**Why not a fixed threshold?** Every sensor follows a daily cycle; a global "alert above 26 °C" floods you with false alarms every warm afternoon and misses a 15 °C night that should have been 10 °C.

**Method:** for each sensor and each **hour of day**, learn the expected mean and standard deviation from history; flag hours that deviate by more than **3.5 σ**.

```sql
SELECT h.sensor_id,
       h.bucket,
       h.max_temp,
       b.mu,
       b.sigma,
       ROUND(((h.max_temp - b.mu) / NULLIF(b.sigma, 0))::numeric, 2) AS z_score
FROM sensor_hourly h
LEFT JOIN (
        SELECT sensor_id,
               EXTRACT(HOUR FROM bucket) AS hod,
               AVG(avg_temp)             AS mu,
               STDDEV_SAMP(avg_temp)     AS sigma
        FROM sensor_hourly
        GROUP BY sensor_id, EXTRACT(HOUR FROM bucket)
     ) b
       ON b.sensor_id = h.sensor_id
      AND b.hod       = EXTRACT(HOUR FROM h.bucket)
WHERE ABS((h.max_temp - b.mu) / NULLIF(b.sigma, 0)) > 3.5
ORDER BY ABS((h.max_temp - b.mu) / NULLIF(b.sigma, 0)) DESC;
```
Design notes:
- Score on **`max_temp`** (not `avg_temp`) so a 30-minute spike isn't diluted by the other 30 minutes of the hour.
- Run the same query on humidity to catch `sensor_012`.
- For production, compute the baseline on a trailing window that **excludes** the period being scored, so a long fault doesn't poison its own baseline.

✅ Verify: the three planted sensors are at the top of the result; nothing else crosses 3.5 σ.

#### Step 11 · Continuous aggregate + benchmark (Notebook / SQL)

| Aggregation type | What it is | Refresh |
|---|---|---|
| Materialized view | Standard Postgres cached query | Manual, full recompute |
| **Continuous aggregate** | TimescaleDB-only; like a materialized view over a hypertable | **Incremental, background, policy-driven** |
| Real-time aggregate | Continuous aggregate + un-materialized recent rows unioned in | Always up to date |
| Cagg on cagg | Hierarchical rollups (minute → hour → day) | Incremental |

```sql
CREATE MATERIALIZED VIEW sensor_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
       sensor_id,
       AVG(temperature) AS avg_temp,
       MAX(temperature) AS max_temp,
       MIN(temperature) AS min_temp,
       AVG(humidity)    AS avg_humidity,
       MAX(humidity)    AS max_humidity,
       COUNT(*)         AS n_readings
FROM sensor_readings
GROUP BY bucket, sensor_id;

SELECT add_continuous_aggregate_policy('sensor_hourly',
       start_offset      => INTERVAL '3 hours',
       end_offset        => INTERVAL '1 hour',
       schedule_interval => INTERVAL '1 hour');
```
Demo: ~**28,820** materialized buckets (20 sensors × ~1,441 hours).

**Benchmark — same answer two ways** (average temperature per sensor, last 30 days):
```sql
-- raw hypertable: scans ~864,000 rows
EXPLAIN ANALYZE
SELECT sensor_id, AVG(temperature)
FROM sensor_readings
WHERE time > now() - INTERVAL '30 days'
GROUP BY sensor_id;

-- continuous aggregate: scans ~14,400 summary rows (~60× fewer)
EXPLAIN ANALYZE
SELECT sensor_id, SUM(avg_temp * n_readings) / SUM(n_readings) AS avg_temp
FROM sensor_hourly
WHERE bucket > now() - INTERVAL '30 days'
GROUP BY sensor_id;
```
Reading the numbers: in the demo the client-side timing was dominated by a **~267 ms network round trip** to a far-away region; compare **server-side** `Execution Time` from `EXPLAIN ANALYZE`, not wall-clock from the notebook. (Weight averages by `n_readings` — an average of hourly averages is only correct when every hour has the same count.)

### Phase D — Storage & safety

#### Step 12 · Columnar compression for historical data (Notebook / SQL)
Old time-series data is rarely updated but often scanned in aggregate — ideal for columnar storage. TimescaleDB converts chunks to a compressed **columnstore**, grouped by `segmentby` (here `sensor_id`) and ordered by `orderby`.

```sql
SELECT pg_size_pretty(hypertable_size('sensor_readings'));     -- before (demo ~249 MB)

-- automatic: convert chunks older than 7 days
CALL add_columnstore_policy('sensor_readings', after => INTERVAL '7 days');

-- or one-off for the demo
SELECT convert_to_columnstore(c)
FROM show_chunks('sensor_readings', older_than => INTERVAL '7 days') c;

SELECT pg_size_pretty(hypertable_size('sensor_readings'));     -- after  (demo ~52 MB)
```
- Classic API equivalent (pre-2.18): `ALTER TABLE … SET (timescaledb.compress, timescaledb.compress_segmentby='sensor_id')` + `add_compression_policy`.
- On TimescaleDB 2.23+, `CREATE TABLE … WITH (tsdb.hypertable …)` may already create a 7-day columnstore policy — check `timescaledb_information.jobs` before adding another.
- Compression is **transparent**: the Step 10/11 queries run unmodified over compressed chunks.

#### Step 13 · Fork the service and run a destructive experiment (CLI + SQL)
A **fork** is an instant, independent copy of a whole Tiger Cloud service — data, schema, compression state. It's the sandbox for anything destructive (retention, migrations, backfills). It is a **platform** operation, not SQL.

```bash
tiger service fork <service-id> --name retention-experiment-db   # ~1 min on the trial tier
tiger service get  retention-experiment-db                      # grab the fork's connection string
```
Then, connected to the **fork only**:
```sql
SELECT count(*) FROM sensor_readings;                                 -- before
SELECT add_retention_policy('sensor_readings', drop_after => INTERVAL '30 days');
CALL run_job(<job_id>);              -- job id from timescaledb_information.jobs
SELECT count(*) FROM sensor_readings;                                 -- after: ~half the rows
```
✅ Verify on the **original** service: row count unchanged. Delete the fork when done (it costs credits). Re-runs: drop a stale fork from a previous run first, then fork fresh.

### Phase E — AI hybrid search

#### Step 14 · Hybrid search over maintenance reports (Notebook / SQL)

| Retriever | How it ranks | Good at | Misses |
|---|---|---|---|
| **Sparse / BM25** (`pg_textsearch`) | Exact term overlap, weighted by corpus statistics | Precision: part numbers, codes, exact words ("chewed") | Paraphrases ("animals" ≠ "rodents") |
| **Dense / semantic** (pgvector) | Cosine distance between embeddings | Recall: meaning, synonyms | Exact rare terms |
| **Hybrid (RRF)** | `Σ 1 / (k + rank)` across both lists, k = 60 | Both | — |

Embeddings were generated **locally** with **FastEmbed** using **`BAAI/bge-small-en-v1.5` (384 dimensions)** — no API key, no data leaving the machine.

```sql
CREATE EXTENSION IF NOT EXISTS pg_textsearch;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE maintenance_reports (
    id          INTEGER PRIMARY KEY,
    station     TEXT,
    report_date DATE,
    description TEXT NOT NULL,
    status      TEXT,
    embedding   vector(384)
);

CREATE INDEX mr_bm25_idx ON maintenance_reports USING bm25 (description) WITH (text_config = 'english');
CREATE INDEX mr_vec_idx  ON maintenance_reports USING hnsw (embedding vector_cosine_ops);
```
Embed and write back (Python):
```python
from fastembed import TextEmbedding
model = TextEmbedding("BAAI/bge-small-en-v1.5")
rows = cur.execute("SELECT id, description FROM maintenance_reports ORDER BY id").fetchall()
for (id_, _), vec in zip(rows, model.embed([d for _, d in rows])):
    cur.execute("UPDATE maintenance_reports SET embedding = %s WHERE id = %s", (vec.tolist(), id_))
conn.commit()
```
Queries — `:q` is the text, `:qv` its embedding from the same model:
```sql
-- 1. keyword (BM25). <@> returns a NEGATIVE score: lower = better
SELECT id, description, description <@> :q AS bm25
FROM maintenance_reports ORDER BY description <@> :q LIMIT 10;

-- 2. semantic (cosine distance)
SELECT id, description, embedding <=> :qv AS dist
FROM maintenance_reports ORDER BY embedding <=> :qv LIMIT 10;

-- 3. hybrid — Reciprocal Rank Fusion
SELECT d.id,
       d.description,
       COALESCE(1.0 / (60 + b.rnk), 0) + COALESCE(1.0 / (60 + v.rnk), 0) AS rrf_score
FROM maintenance_reports d
LEFT JOIN (SELECT id, ROW_NUMBER() OVER (ORDER BY description <@> :q) AS rnk
           FROM maintenance_reports ORDER BY description <@> :q LIMIT 20) b ON b.id = d.id
LEFT JOIN (SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> :qv) AS rnk
           FROM maintenance_reports ORDER BY embedding <=> :qv LIMIT 20) v ON v.id = d.id
WHERE b.id IS NOT NULL OR v.id IS NOT NULL
ORDER BY rrf_score DESC
LIMIT 10;
```
What the demo showed for **"animals chewing wires"**:
- **BM25** surfaced reports containing the literal word *chewed/chewing* (mice chewing payment-system wiring, rat-chewed wiring).
- **Vector** additionally found *"power fluctuations caused by rat damage to an underground cable"* and *"wiring damage by rodents in the main control unit"* — no word overlap, same meaning.
- **Hybrid** put reports both retrievers agreed on first, then the best of each.

Alternative vector index on Tiger Cloud: `pgvectorscale`'s StreamingDiskANN (`CREATE EXTENSION vectorscale CASCADE; … USING diskann (embedding vector_cosine_ops)`) for larger corpora.

---

## 5. Prompts used with the agent (copy-paste)

```text
List my Tiger Cloud services.
Create a Tiger service called <name> and generate the sample data by running generate_sensor_data.sql.
Upload the data into the Tiger service.
Examine data.csv and sensors.csv. Use Tiger MCP best practices and propose an optimized schema.
Apply the schema and load the data.
Fork service <id> so I can test a retention policy.
```
Always review the agent's plan (region, schema changes) before saying "proceed".

## 6. Validation checklist

| Check | Query / action | Expected |
|---|---|---|
| Row count | `SELECT count(*) FROM sensor_readings` | 1,728,000 |
| Chunks | `timescaledb_information.chunks` | > 1 (demo: 9) |
| Constraints | Insert humidity = 120 | Rejected by CHECK |
| Uniqueness | Insert duplicate `(sensor_id, time)` | Rejected |
| Anomalies | Step 10 query | Exactly sensors 007, 012, 017 |
| Cagg freshness | `max(bucket)` in `sensor_hourly` | Within the policy's end offset |
| Compression | `hypertable_size` before/after | ~70–80 % reduction |
| Fork isolation | Count on original after retention on fork | Unchanged |
| Search | Hybrid top-10 for a paraphrased query | Contains semantic-only hits |

## 7. Cost & operations notes
- Compute is billed per hour while the service runs — **pause/stop** idle dev services and **delete forks** after experiments.
- Keep `.env` / connection strings out of Git (add to `.gitignore`).
- Use **read-only MCP** (`tiger config set read_only true`) for agents pointed at shared or prod projects.
- Pick the region closest to the client; small analytical queries are latency-bound, not CPU-bound.

## 8. Anti-patterns
- **`DELETE … WHERE time < …` in a cron job** — use `add_retention_policy` (drops whole chunks, no bloat).
- **`REFRESH MATERIALIZED VIEW` on a schedule over a hypertable** — use a continuous aggregate.
- **Global fixed thresholds on cyclic signals** — use hour-of-day (and weekday) baselines.
- **Averaging averages** without weighting by count.
- **Benchmarking wall-clock from a notebook across continents** — use `EXPLAIN ANALYZE` server time.
- **Vector-only RAG over technical text** — misses exact codes; add BM25 and fuse.
- **Destructive experiments on the primary** — fork first.

## 9. Glossary
- **Hypertable** — Postgres table auto-partitioned into time **chunks**.
- **Chunk exclusion** — planner skips chunks outside the query's time range.
- **Continuous aggregate (cagg)** — incrementally refreshed materialized rollup.
- **Columnstore / Hypercore** — TimescaleDB's compressed columnar chunk format.
- **Fork** — instant independent copy of a Tiger Cloud service.
- **BM25** — keyword relevance ranking (term frequency × inverse document frequency, length-normalised).
- **HNSW** — graph-based approximate nearest-neighbour vector index.
- **RRF** — Reciprocal Rank Fusion, merges ranked lists by `1/(k + rank)`.
- **MCP** — Model Context Protocol; lets an AI agent call Tiger Cloud tools directly.

## 10. Prerequisites
- Tiger Cloud account (trial is enough), Tiger CLI, an MCP-capable coding agent.
- Python 3.11+: `psycopg`, `python-dotenv`, `pandas`, `fastembed`, Jupyter.
- PostgreSQL 17/18 service with `timescaledb`, `pg_textsearch`, `vector` available (default on Tiger Cloud).

## 11. References
- Video: <https://www.youtube.com/watch?v=69LqL2g9sr8>
- Tiger CLI & MCP: <https://www.tigerdata.com/docs/get-started/quickstart/mcp-cli>
- Tiger CLI reference: <https://www.tigerdata.com/docs/reference/tiger-cloud/tiger-cli>
- Hypertables (`CREATE TABLE … WITH tsdb.hypertable`): <https://www.tigerdata.com/docs/reference/timescaledb/hypertables/create_table/>
- `pg_textsearch` (BM25): <https://www.tigerdata.com/docs/deploy/tiger-cloud/tiger-cloud-extensions/pg-textsearch>
- Hybrid search tutorial (BM25 + vectors + RRF): <https://www.tigerdata.com/docs/learn/tutorials/hybrid-search>

---

## 12. Mapping to Microsoft Fabric Real-Time Intelligence

Same use case, Fabric-native — for when the platform decision is already Fabric.

| Step | Tiger Cloud | Microsoft Fabric equivalent |
|---|---|---|
| Ingest stream | `\copy` / app inserts into hypertable | **Eventstream** → **Eventhouse** (KQL DB) table |
| Time partitioning | Hypertable chunks | Eventhouse extents (auto, by ingestion time); Delta partitioning in Lakehouse |
| Rollups | Continuous aggregate | **KQL materialized view** (`.create materialized-view … summarize … by bin(Timestamp, 1h)`) |
| Seasonal anomalies | Hour-of-day z-score SQL | KQL `make-series` + `series_decompose_anomalies()` |
| Compression | Columnstore policy | Columnar by default in Eventhouse; Delta + V-Order in Lakehouse |
| Retention | `add_retention_policy` | Eventhouse table **retention policy**; Delta `VACUUM` + lifecycle |
| Hot/cold tiers | Columnstore + tiered storage | Eventhouse **caching policy** (hot cache window) + OneLake |
| Safe experiments | Service fork | Warehouse **zero-copy table clone** (`CREATE TABLE … AS CLONE OF`); separate dev workspace via Git integration |
| Alerts | App logic on anomaly query | **Data Activator** (Reflex) rules on Eventstream / KQL results |
| Hybrid search | `pg_textsearch` + pgvector + RRF | Eventhouse vector columns + `series_cosine_similarity()`; or Azure AI Search hybrid (BM25 + vector, RRF built-in) |
| Agent access | Tiger MCP | Fabric Data Agent / Copilot over the KQL DB |

**Decision rule:** pick Tiger Cloud when the app team wants **one Postgres** for transactional + time-series + search and agent-driven provisioning; pick Fabric RTI when the data must land in **OneLake** next to finance/ERP models and Power BI Direct Lake.
