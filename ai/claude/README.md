# Enabling Anthropic Claude with Microsoft Fabric

A practical guide to using **Anthropic Claude models** inside **Microsoft Fabric** — from Spark notebooks, Data Factory pipelines, Warehouse UDFs, and AI Skill orchestration.

Fabric's built-in Copilot and `ai.*` functions use Azure OpenAI under the hood. This guide covers when you'd want to bring **Claude** into the mix instead, and the three real paths to do it: **Azure AI Foundry**, **AWS Bedrock**, and the **Anthropic API** directly.

Everything below is generic and employer-neutral.

---

## 1. Why bring Claude into Fabric

Fabric's built-in AI is fine for most workloads. Reach for Claude when:

- **Long-context extraction** — 200k+ token windows for full-document parsing (contracts, filings, long transcripts).
- **Structured output at scale** — Claude's JSON / XML tool-use reliability for schema-strict extraction.
- **Reasoning-heavy tasks** — data quality anomaly explanation, root-cause narratives, complex classification.
- **Multi-provider strategy** — organizations that avoid single-vendor lock-in on foundation models.
- **Existing enterprise agreement** — company already has an Anthropic MSA, Bedrock contract, or Azure Marketplace commit.

If the answer is "just faster Copilot in reports," stay with Fabric-hosted AOAI. This guide is for **code-level** Claude use.

## 2. The three deployment paths

| Path | Where the model runs | Data residency | Auth | Billing |
|---|---|---|---|---|
| **Azure AI Foundry (Models-as-a-Service)** | In an Azure region you pick | Azure tenant, same-geo option | Entra ID / API key | Azure subscription |
| **AWS Bedrock** | AWS region you pick | AWS account | IAM (SigV4) | AWS account |
| **Anthropic API direct** | Anthropic's cloud | Anthropic (US/EU options) | API key | Anthropic invoice |

**Default recommendation for a Fabric shop:** Azure AI Foundry. Data stays in Azure, auth aligns with Entra ID, billing rolls into the existing Azure enterprise agreement.

## 3. Path A — Azure AI Foundry (recommended)

### 3.1 Provision the model

1. Open **Azure AI Foundry** portal (`ai.azure.com`) → your AI hub → **Model catalog**.
2. Filter by publisher = **Anthropic**. Pick a model (e.g., Claude Sonnet or Opus in the latest family).
3. Click **Deploy** → choose **Serverless API** (pay-per-token) or **Provisioned Throughput (PTUs)** for high steady volume.
4. Get the resulting **endpoint URL** + **API key** (or use Entra ID token auth).

### 3.2 Store credentials in Key Vault

Never inline the API key in a notebook.

```
Key Vault: kv-fabric-ai
├── secret: claude-endpoint         → https://<hub>.services.ai.azure.com/…
└── secret: claude-api-key          → <key>
```

Grant the **workspace-managed identity** `Get`/`List` on the Key Vault. Reference in Fabric via workspace-level Key Vault-backed settings (or `mssparkutils` at runtime).

### 3.3 Call from a Spark notebook

```python
import requests
from notebookutils import mssparkutils

endpoint = mssparkutils.credentials.getSecret("kv-fabric-ai", "claude-endpoint")
api_key  = mssparkutils.credentials.getSecret("kv-fabric-ai", "claude-api-key")

def claude_chat(system: str, user: str, model: str = "claude-sonnet-5",
                max_tokens: int = 1024, temperature: float = 0):
    resp = requests.post(
        f"{endpoint}/openai/deployments/{model}/chat/completions?api-version=2024-06-01",
        headers={"api-key": api_key, "Content-Type": "application/json"},
        json={
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature
        },
        timeout=60
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

answer = claude_chat(
    system="Extract invoice fields as strict JSON.",
    user=invoice_text
)
```

Azure AI Foundry exposes Anthropic models behind an **OpenAI-compatible** endpoint shape, so the code style is intentionally familiar.

### 3.4 Native Anthropic SDK against Azure

If you need Claude-specific features (tool use, prompt caching, extended thinking), use the **Anthropic Python SDK** pointed at your Azure endpoint:

```python
from anthropic import AnthropicVertex, Anthropic

# Or the AzureAnthropic client where available
client = Anthropic(
    base_url=f"{endpoint}/anthropic",
    api_key=api_key,
)

msg = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1024,
    system="Extract fields as JSON.",
    messages=[{"role": "user", "content": invoice_text}]
)
```

## 4. Path B — AWS Bedrock

Common when the enterprise data-science stack is already on AWS.

### 4.1 Prerequisites

- Enable the desired Claude models in **Bedrock model access** (per AWS region).
- Create an **IAM user or role** with `bedrock:InvokeModel` on the model ARNs.
- Store the AWS access key + secret in Fabric's Key Vault (or use OIDC federation from Fabric managed identity → AWS role, when set up).

### 4.2 Call from a Spark notebook

```python
import boto3, json
from notebookutils import mssparkutils

aws_key    = mssparkutils.credentials.getSecret("kv-fabric-ai", "aws-access-key")
aws_secret = mssparkutils.credentials.getSecret("kv-fabric-ai", "aws-secret-key")

bedrock = boto3.client(
    "bedrock-runtime",
    region_name="eu-central-1",
    aws_access_key_id=aws_key,
    aws_secret_access_key=aws_secret,
)

def claude_bedrock(prompt: str, model_id: str = "anthropic.claude-sonnet-5-v1:0"):
    resp = bedrock.invoke_model(
        modelId=model_id,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}]
        })
    )
    return json.loads(resp["body"].read())["content"][0]["text"]
```

## 5. Path C — Anthropic API direct

Simplest, but data leaves Azure/AWS. Fine for prototyping, dev workspaces, or when residency is not a blocker.

```python
from anthropic import Anthropic
from notebookutils import mssparkutils

client = Anthropic(
    api_key=mssparkutils.credentials.getSecret("kv-fabric-ai", "anthropic-api-key")
)

msg = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": prompt}]
)
```

## 6. Batch processing pattern — Claude over a DataFrame

Naive `.withColumn` invokes Claude row-by-row on the driver — slow and rate-limit prone. Use a **Pandas UDF with async fan-out**:

```python
import asyncio, aiohttp
import pandas as pd
from pyspark.sql.functions import pandas_udf, col
from pyspark.sql.types import StringType

SEM = 8   # concurrent in-flight requests per executor

async def _one(session, text):
    async with session.post(
        f"{endpoint}/openai/deployments/claude-sonnet-5/chat/completions?api-version=2024-06-01",
        headers={"api-key": api_key, "Content-Type": "application/json"},
        json={"messages": [{"role": "user", "content": text}], "max_tokens": 512}
    ) as r:
        j = await r.json()
        return j["choices"][0]["message"]["content"]

async def _many(texts):
    sem = asyncio.Semaphore(SEM)
    async with aiohttp.ClientSession() as session:
        async def bound(t):
            async with sem:
                return await _one(session, t)
        return await asyncio.gather(*[bound(t) for t in texts])

@pandas_udf(StringType())
def claude_batch(texts: pd.Series) -> pd.Series:
    results = asyncio.run(_many(texts.tolist()))
    return pd.Series(results)

df_scored = df.withColumn("claude_output", claude_batch(col("input_text")))
```

Combine with **partition-level checkpoints** so a rerun after a rate-limit blip resumes rather than restarts:

```python
(df_scored
    .write
    .mode("append")
    .partitionBy("run_id", "batch_id")
    .format("delta")
    .saveAsTable("silver.claude_extractions"))
```

## 7. Structured output — tool use for schema-strict extraction

For invoice / contract / support-ticket extraction, do **not** parse free text — use Anthropic's tool-use protocol.

```python
tools = [{
    "name": "record_invoice",
    "description": "Persist extracted invoice fields.",
    "input_schema": {
        "type": "object",
        "properties": {
            "invoice_number": {"type": "string"},
            "vendor_name":    {"type": "string"},
            "total_amount":   {"type": "number"},
            "currency":       {"type": "string"},
            "due_date":       {"type": "string", "format": "date"}
        },
        "required": ["invoice_number", "vendor_name", "total_amount"]
    }
}]

msg = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1024,
    tools=tools,
    tool_choice={"type": "tool", "name": "record_invoice"},
    messages=[{"role": "user", "content": invoice_text}]
)

extracted = next(b.input for b in msg.content if b.type == "tool_use")
```

You get a dict that already matches your Delta schema — no `try/except json.loads` scaffolding.

## 8. Prompt caching — cut costs 90% on repeated context

When you call Claude many times against the **same long document / schema / instructions**, mark the reusable portion for caching:

```python
msg = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1024,
    system=[
        {"type": "text", "text": SHORT_INSTRUCTIONS},
        {"type": "text", "text": LARGE_SCHEMA_OR_DOC,
         "cache_control": {"type": "ephemeral"}}
    ],
    messages=[{"role": "user", "content": query}]
)
```

Cached tokens are billed at a fraction of input rate. High-value patterns in Fabric:
- Long semantic-model / DAX docs cached; query changes per user.
- Full SAP field catalog cached; per-row question changes.

## 9. Wiring Claude into Data Factory

Two patterns:

### 9.1 Web activity (simple, low volume)

Copy activity or ForEach → **Web activity** hitting the Foundry / Bedrock / Anthropic endpoint. Store the API key in a **Fabric-managed Key Vault** linked service. Rate-limit-friendly for < 100 calls per pipeline run.

### 9.2 Notebook activity (production)

Wrap the batch pattern from §6 in a parameterized notebook. Pipeline passes source table, target table, and prompt template as parameters. This is the production pattern — retries, checkpoints, and CU visibility all land where they should.

## 10. AI Skill / RAG pattern with Claude

Fabric's built-in AI Skills use Azure OpenAI. For a **Claude-backed grounded agent**, build it in a notebook + a semantic-link REST publish:

```
User question
    │
    ▼
Retrieval:
    - Eventhouse vector search  (semantic hits)
    - Direct Lake / Warehouse   (structured lookups via NL→SQL with Claude)
    │
    ▼
Compose prompt: instructions + retrieved chunks + user question
    │
    ▼
Claude (tool use, JSON response schema)
    │
    ▼
Response + citations returned as JSON
```

Publish as a Fabric REST endpoint via a **user data function** (public preview) or as a Function App fronted by Fabric.

**RLS reminder:** unlike Fabric AI Skills, this custom path does **not** automatically run queries as the calling user. Enforce user identity yourself: propagate the caller's OID, generate SQL against a view that filters `WHERE user_oid = @caller`, or query the semantic model with `sempy` while impersonating.

## 11. Governance for Claude usage

| Control | Do |
|---|---|
| Data residency | Prefer Azure AI Foundry in the tenant's geo; document data flow if using Bedrock or direct Anthropic |
| Secrets | Key Vault + managed identity only. Never inline. Rotate every 90 days |
| PII / sensitive data | Strip or hash before sending; apply sensitivity labels; log the call, not the payload |
| Audit | Log every Claude call to a `ai_calls` Delta table (user, model, tokens_in, tokens_out, cost_estimate, correlation_id) |
| Purview | Add Claude outputs as classified assets; sensitivity labels inherit from source tables |
| Model choice | Pin the model version; do not track "latest" for reproducibility. Upgrade deliberately |
| Prompt injection | Treat retrieved content as untrusted; keep system instructions above user content; use tool use over free-form output |
| Cross-border | Sensitivity-label DLP rules should block sending `Confidential` or higher to non-Azure Claude endpoints |

## 12. Cost model

- **Azure AI Foundry pay-per-token** — billed against Azure subscription; falls under enterprise agreement discount.
- **Azure AI Foundry PTUs** — predictable cost, best for high steady volume (> ~50 requests/sec).
- **Bedrock on-demand** — per-token, per-region.
- **Bedrock Provisioned Throughput** — like Azure PTUs, hourly commit.
- **Anthropic direct** — per-token, invoiced separately.

Whichever path, **log every call** with token counts and dollar estimate — a Delta `ai_calls` table plus a Power BI report is 30 minutes of work and saves quarterly fire drills.

```sql
CREATE TABLE ai_calls (
    call_id            STRING,
    called_at          TIMESTAMP,
    caller_upn         STRING,
    workspace          STRING,
    model              STRING,
    tokens_input       INT,
    tokens_output      INT,
    cache_hit_tokens   INT,
    cost_estimate_usd  DECIMAL(10,4),
    latency_ms         INT,
    outcome            STRING,     -- success | rate_limited | error
    correlation_id     STRING
) USING DELTA;
```

## 13. When to pick Claude vs Fabric-hosted AOAI vs BYO AOAI

| Scenario | Pick |
|---|---|
| Copilot in Power BI / Notebooks | Stay with built-in (AOAI) |
| Simple `ai.summarize` / `ai.classify` at row level | Built-in `ai.*` |
| High-volume streaming AI on your capacity | Built-in `ai.*` or BYO AOAI |
| Long-document extraction (contracts, filings) | **Claude** |
| Complex reasoning / classification with citations | **Claude** |
| JSON tool-use schema strictness matters | **Claude** |
| You already run on AWS | **Claude via Bedrock** |
| Multi-model routing (fallback / A-B) | Claude for one arm, AOAI for the other |

## 14. Anti-patterns

- ❌ Inlining `sk_ant_…` or `sk-…` keys in a notebook cell. Key Vault, always.
- ❌ Sending raw PII to any Claude endpoint before sensitivity-label + DLP review.
- ❌ Parsing free-text JSON from Claude with regex — use **tool use** for structured outputs.
- ❌ Row-by-row `.withColumn` invocation of Claude — always batch async.
- ❌ Storing prompt text in notebook cells instead of versioning prompts in Git alongside the notebook.
- ❌ Skipping prompt caching on high-volume long-context patterns — leaves 90%+ savings on the table.
- ❌ Assuming Fabric AI Skills RLS applies to a custom Claude-based skill — enforce identity yourself.
- ❌ No `ai_calls` audit log — you will not be able to answer "who called what and what did it cost."

---

**Claude-in-Fabric enablement checklist:**

- [ ] Deployment path chosen (Foundry / Bedrock / Anthropic direct) with data-residency rationale documented
- [ ] Credentials in Key Vault, workspace managed identity granted access
- [ ] Sample notebook validates a single call end-to-end
- [ ] Batch async pattern implemented with partition-level checkpoints
- [ ] Tool-use / structured output schema defined for each extraction task
- [ ] Prompt caching enabled for repeated long-context patterns
- [ ] `ai_calls` audit table created + Power BI report bookmarked
- [ ] Sensitivity labels + DLP rules cover Claude payloads
- [ ] Model version pinned; upgrade cadence documented
- [ ] Cost alerts set on the subscription
