# AI on Microsoft Fabric

A working reference for the **AI capabilities inside Microsoft Fabric** — Copilot across every workload, AI Skills / Data Agents grounded on your Lakehouse, AI functions in Spark notebooks, Azure OpenAI wiring, vector search in Eventhouse, and Auto-ML in Data Science.

Everything below is generic and employer-neutral.

---

## 1. The AI surface area

Fabric embeds AI at three levels — pick the right one per use case.

```
┌───────────────────────────────────────────────────────────────────────┐
│ Level                    │ Who uses it        │ What it does          │
├───────────────────────────────────────────────────────────────────────┤
│ 1. Copilot in Fabric     │ Every persona      │ NL → DAX / SQL / KQL  │
│                          │                    │ / Spark / pipelines   │
│ 2. AI Skills (Data Agnt) │ Business consumer  │ NL Q&A over Lakehouse │
│ 3. Code-level AI         │ Data engineers,    │ ai.* functions,       │
│    (notebooks / SynapMl) │ data scientists    │ Azure OpenAI, vectors │
└───────────────────────────────────────────────────────────────────────┘
```

**Prerequisites:**
- **F64 or higher** SKU (or P1+ Premium) — Copilot is not available on F2–F32.
- Tenant admin has enabled the **"Copilot in Fabric"** tenant setting for the security group.
- Data stays inside the tenant boundary — Fabric Copilot does not send prompts or grounding data to OpenAI's public endpoints; it uses **Azure OpenAI hosted in the same geo** as the capacity.

## 2. Copilot in Fabric — the six surfaces

| Workload | What Copilot does | Example prompt |
|---|---|---|
| **Power BI** | Generate reports, DAX measures, narrative summaries, page-level insights | "Create a page showing YoY revenue by region with a map" |
| **Data Engineering (Notebooks)** | Generate PySpark / T-SQL / Python from NL, explain code, fix errors | "Merge new_invoices into fact_ap on doc_number, updating on match" |
| **Data Science (Notebooks)** | Generate ML training code, exploratory data analysis, feature engineering | "Build a churn classifier on customer_features using LightGBM" |
| **Data Factory** | Generate pipeline expressions, suggest data transformations in Dataflow Gen2 | "Add a column that flags overdue invoices > 60 days" |
| **Data Warehouse** | Generate T-SQL from NL, explain queries, optimize | "Show top 10 vendors by GR/IR balance for company code 1000" |
| **Real-Time Intelligence** | Generate KQL from NL, build queries over Eventhouse | "Count blocked invoices per hour in the last 24 hours" |

### How Copilot grounds

- Power BI Copilot grounds on the **semantic model schema, measures, and description metadata** — Copilot output quality tracks directly with how well you named tables, measures, and columns. Bad naming = bad output.
- Notebook Copilot grounds on **cell context + Lakehouse schema** the notebook is attached to.
- Warehouse Copilot grounds on the **INFORMATION_SCHEMA**.

**Practical rule:** every semantic model table and measure needs a business-friendly `description` filled in via TMDL. Copilot reads it; end-users benefit from tooltips too.

### Copilot chat pane vs inline suggestions

- **Chat pane** — conversational, multi-turn, remembers context inside the session.
- **Inline suggestions** — ghost text in notebook cells / Power BI formula bar. Press Tab to accept.

## 3. AI Skills — grounded conversational agents

**AI Skills** (formerly branded as "Data Agents" in some docs) let a business user ask questions in natural language against a Lakehouse or Warehouse and get back grounded, cited answers.

### Wiring one up

1. In the workspace: **New → AI Skill** → give it a name.
2. **Add data source** — one or more Lakehouses, Warehouses, semantic models.
3. **Add example questions + expected answers** — few-shot examples that steer the agent. 10–20 curated examples raise quality dramatically.
4. **Add instructions** — "You are a finance assistant. Only answer questions about GL, AP, AR. Always cite the table and column."
5. **Test in the playground** — iterate on failing questions, add them as examples.
6. **Publish** — exposes the skill as a REST endpoint + Teams integration.

### What AI Skills do under the hood

```
User question (NL)
    │
    ▼
LLM (Azure OpenAI hosted in Fabric)
    │
    ├── Retrieves schema + descriptions
    ├── Retrieves few-shot examples
    └── Generates T-SQL / KQL / DAX
    │
    ▼
Query runs against the underlying source (RLS/OLS applied as the calling user)
    │
    ▼
Response synthesized in NL, citations attached
```

### RLS is honored

The generated query executes **as the calling user**, so RLS on the semantic model / OneLake data role on the Lakehouse **applies automatically**. A user cannot use an AI Skill to bypass permissions.

### AI Skills over multiple sources

An AI Skill can span **multiple data sources** — the LLM chooses which source per question. Constrain with instructions if you want deterministic routing: "For AR questions use the Finance-Gold warehouse; for supplier events use Finance-Events."

## 4. AI functions in Spark notebooks

Fabric ships built-in `ai.*` functions callable from PySpark DataFrames — no model wiring needed. Backed by the Fabric-hosted Azure OpenAI.

```python
from synapse.ml.services.openai import OpenAIDefaults
import pyspark.sql.functions as F
from synapse.ml.services.ai import ai   # Fabric AI functions

# Summarize free-text comments
df_summarized = df.withColumn(
    "summary",
    ai.summarize(F.col("customer_comment"), max_length=50)
)

# Classify support tickets
df_classified = df.withColumn(
    "category",
    ai.classify(
        F.col("ticket_text"),
        labels=["billing", "technical", "sales", "other"]
    )
)

# Extract structured data from unstructured text
df_extracted = df.withColumn(
    "extracted",
    ai.extract(
        F.col("email_body"),
        labels=["invoice_number", "amount", "due_date"]
    )
)

# Translate free text
df_translated = df.withColumn(
    "text_en",
    ai.translate(F.col("comment_de"), to_language="en")
)

# Similarity between two text columns
df_sim = df.withColumn(
    "similarity",
    ai.similarity(F.col("query"), F.col("document"))
)

# Fix grammar / spelling
df_clean = df.withColumn(
    "clean_text",
    ai.fix_grammar(F.col("raw_text"))
)

# Freeform prompt
df_gen = df.withColumn(
    "response",
    ai.generate_response(
        F.concat_ws(" ", F.lit("Explain this SAP posting:"), F.col("bktxt"))
    )
)
```

### Cost model

`ai.*` functions consume **CU** from your Fabric capacity, priced per 1M tokens. Log column volumes before mass invocation — a 100M-row scan against `ai.summarize` will spike CU consumption significantly. Guardrails:

- **Sample first** — validate on 1000 rows, then scale.
- **Cache results** — persist AI-column output as its own Delta table so re-runs don't re-invoke.
- **Batch by content hash** — if 40 % of your text values are duplicates, dedupe before calling.

## 5. Azure OpenAI direct — SynapseML or REST

When AI functions aren't flexible enough, call Azure OpenAI directly. Two paths:

### SynapseML

```python
from synapse.ml.services.openai import OpenAIChatCompletion

chat = (
    OpenAIChatCompletion()
    .setSubscriptionKey("<key>")     # or use workspace-managed identity
    .setDeploymentName("gpt-4o")
    .setCustomServiceName("<your-aoai>")
    .setMessagesCol("messages")
    .setOutputCol("response")
)

messages_df = df.withColumn(
    "messages",
    F.array(
        F.struct(F.lit("system").alias("role"),
                 F.lit("You are a financial data extractor.").alias("content")),
        F.struct(F.lit("user").alias("role"),
                 F.col("invoice_text").alias("content"))
    )
)

result = chat.transform(messages_df)
```

### Native REST (any workload)

```python
import requests, os

resp = requests.post(
    f"{os.environ['AOAI_ENDPOINT']}/openai/deployments/gpt-4o/chat/completions?api-version=2024-06-01",
    headers={"api-key": os.environ["AOAI_KEY"]},
    json={
        "messages": [
            {"role": "system", "content": "Extract invoice fields as JSON."},
            {"role": "user", "content": text}
        ],
        "response_format": {"type": "json_object"}
    }
)
```

**Auth:** prefer **workspace-managed identity** over API keys. Store secrets in **Azure Key Vault** and reference via the Key Vault-backed workspace configuration, never inline.

## 6. Vector search in Eventhouse (KQL)

Eventhouse supports vector storage and cosine similarity — the fastest path to a lakehouse-native RAG store in Fabric.

```kusto
// Table with embedded column
.create table docs (id: string, text: string, embedding: dynamic)

// Ingest — compute embeddings in a Spark notebook first, then upsert
.set-or-append docs <|
    print id="doc1", text="…", embedding=dynamic([0.12, -0.44, …])

// Query: top 5 nearest neighbors to a query embedding
let q_emb = dynamic([0.11, -0.42, …]);
docs
| extend sim = series_cosine_similarity(embedding, q_emb)
| top 5 by sim desc
```

Pair with **`ai.generate_response`** on the retrieved chunks to build end-to-end RAG without leaving Fabric.

### Alternative — Azure AI Search shortcut

For high-scale or hybrid (keyword + vector) search, keep the corpus in **Azure AI Search** and shortcut its index into a Fabric Lakehouse for reporting/lineage. Queries hit AI Search; Fabric owns the ETL and analytics side.

## 7. Prebuilt AI services (SynapseML)

For classic ML use cases, SynapseML wraps Azure AI Services:

| Service | Class | Use |
|---|---|---|
| Language | `AnalyzeText`, `TranslatorText`, `LanguageDetector` | Sentiment, key phrases, PII detection, translation |
| Vision | `AnalyzeImage`, `ReadImage` | OCR, tagging, dense captions |
| Document Intelligence | `AnalyzeDocument` | Invoice / receipt / form parsing |
| Speech | `SpeechToText` | Transcription |
| Anomaly Detector | `SimpleDetectAnomalies` | Time-series anomaly flagging |

```python
from synapse.ml.services.language import AnalyzeText

analyzer = (
    AnalyzeText()
    .setKind("SentimentAnalysis")
    .setTextCol("comment")
    .setOutputCol("sentiment")
)

df_sentiment = analyzer.transform(df)
```

## 8. Data Science — Auto-ML + MLflow

### Auto-ML (`flaml` in Fabric)

```python
from flaml import AutoML

automl = AutoML()
automl.fit(
    X_train, y_train,
    task="classification",
    time_budget=600,          # 10 minutes
    metric="roc_auc",
    log_file_name="churn_automl.log"
)
```

Logs every trial into the workspace's MLflow tracking server — automatically. No wiring.

### MLflow integration

Every Fabric workspace comes with an **MLflow tracking server** and **model registry** built in.

```python
import mlflow

mlflow.set_experiment("churn-model")
with mlflow.start_run():
    mlflow.log_param("max_depth", 7)
    mlflow.sklearn.log_model(model, artifact_path="model",
                             registered_model_name="churn_v1")
```

Registered models can be:
- **Applied inline** with `PREDICT` T-SQL against a Warehouse.
- **Scored batch** in a notebook via `mlflow.pyfunc.load_model(...)`.
- **Deployed as a real-time endpoint** on Azure ML for online scoring.

### T-SQL PREDICT

```sql
SELECT customer_id, PREDICT(MODEL 'churn_v1', *) AS churn_score
FROM dim_customer;
```

Skips the notebook round-trip for batch scoring inside the warehouse.

## 9. Semantic link + `sempy` — AI-assisted analysis

`sempy` (semantic-link) bridges Fabric semantic models and Python. Useful patterns:

- **Programmatic measure eval** — run 100 DAX measures across dim slices in a notebook.
- **Semantic model documentation** — auto-generate a description of every measure with `ai.summarize`.
- **Best-practice analyzer** — programmatically lint TMDL for anti-patterns.

```python
import sempy.fabric as fabric

datasets = fabric.list_datasets()
measures = fabric.list_measures("Finance_Model")

# Bulk evaluate for anomaly detection
df = fabric.evaluate_dax(
    "Finance_Model",
    """EVALUATE
       SUMMARIZECOLUMNS(
           Dim_Company[CompanyCode],
           Dim_Date[YearMonth],
           "Revenue", [Total Revenue])"""
)
```

## 10. Governance & prerequisites for AI

| Control | Where set |
|---|---|
| Copilot on/off per group | Fabric admin portal → Tenant settings → "Copilot in Fabric" |
| Cross-geo processing | Off by default (data stays in geo); toggle per compliance |
| Bring-your-own Azure OpenAI | Configure at capacity level; overrides Fabric-hosted model |
| Data used for training | Never — Fabric Copilot data is **not** used to train foundation models |
| AI Skill audit | Purview + M365 unified audit log — every prompt + generated query logged |
| RLS/OLS enforcement | Automatic; AI Skills run queries as the calling user |
| Sensitivity labels | Inherit into Copilot-generated content; blocked from external Copilot chats |

## 11. Cost — plan before you spec

- **Copilot** — bundled into F64+ SKU cost; no per-token line item. But heavy inline notebook use consumes CU.
- **AI functions** (`ai.*`) — billed as CU per 1M tokens processed.
- **AI Skills** — billed per query (tokens in + out).
- **Direct Azure OpenAI** (BYO or SynapseML) — billed by your **Azure OpenAI subscription**, separate from Fabric CU. Cheaper at scale, more governance overhead.
- **Auto-ML** — regular Spark CU consumption; no premium tier.

For consistent, high-volume AI workloads, **bring your own Azure OpenAI** with a **PTU (provisioned throughput unit)** deployment — predictable cost, no throttling.

## 12. Anti-patterns I avoid

- ❌ Enabling Copilot for the whole tenant on day one — pilot with one domain first.
- ❌ Poor semantic-model naming and empty descriptions — Copilot output degrades in lockstep.
- ❌ Running `ai.summarize` over a raw 100M-row bronze table — sample first, cache results.
- ❌ AI Skills without few-shot examples — accuracy is coin-flip until you add 10–20.
- ❌ Embedding API keys inline instead of Key Vault + managed identity.
- ❌ Building a RAG store in a separate Azure resource when Eventhouse vector search suffices.
- ❌ Assuming Copilot handles complex multi-hop joins in a single prompt — decompose or provide a canonical view.
- ❌ No sensitivity label on the Lakehouse feeding an AI Skill — leaks classification into generated answers.

---

**AI readiness checklist for a Fabric workspace:**

- [ ] Capacity is F64+ (Copilot prerequisite)
- [ ] Copilot enabled for the workspace's security group
- [ ] Semantic model tables and measures have descriptions
- [ ] AI Skill has ≥ 10 few-shot examples
- [ ] AI Skill instructions specify scope + citation format
- [ ] RLS validated end-to-end on grounding sources
- [ ] Sensitivity labels applied on all Lakehouses / Warehouses used by AI Skills
- [ ] Azure OpenAI keys stored in Key Vault (if BYO)
- [ ] AI function output cached to persistent Delta tables
- [ ] Purview audit stream configured for AI Skill prompts
- [ ] Capacity Metrics dashboard bookmarked for AI CU tracking
