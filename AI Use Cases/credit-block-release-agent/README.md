# Credit-Block Release Agent — Full Lifecycle on Microsoft Fabric

An end-to-end implementation blueprint for an AI agent that assists — and, within policy, automates — the **SAP credit-block release** decision.

This doc walks the full lifecycle: business case → discovery → architecture → data ingestion → modeling → policy layer → AI agent → analyst UI → guardrails → deployment → monitoring → roadmap.

Everything below is generic and employer-neutral.

---

## 0. The business case (original post)

> **Most credit blocks get released anyway. So why do they take a day and a half?**
>
> Take a typical SAP credit management setup. Every week, around 400 sales orders get stuck on a credit block. A credit analyst opens each one and checks open items, payment history, credit exposure, any disputes and the external rating, then decides.
>
> At about 12 minutes per order, that's 80 hours a week. The most frustrating part: around 85% of those orders are released without any change. Meanwhile, the customer waits, the shipment waits, and so does the revenue.
>
> The analysts aren't the problem. The process is: it sends every case to a person, whatever the risk.
>
> **This is where an AI agent helps.**
>
> 🤖 It collects the context in seconds. It pulls exposure, overdue items, payment behaviour, dispute status and order value into one summary instead of five SAP screens.
> 🤖 It recommends a decision. Release, hold or escalate, with the reasoning written out so the analyst can check it.
> 🤖 It releases low-risk orders automatically, but only within the policy thresholds you define: good payment history, no overdue items, exposure within the limit.
> 🤖 It escalates the real risks, so analysts spend their time on the 15% of cases that actually need judgement.
> 🤖 It keeps credit assessments current. It flags customers whose payment behaviour has changed and prepares credit limit reviews before a block happens, not after.
>
> **The value**
> ⏱️ If 60% of blocks are released automatically, analysts get back about 48 hours a week.
> 🚚 Release time drops from 1.5 days to minutes for most orders.
> 💶 Shipments and invoices go out faster, and DSO starts earlier.
> 🛡️ Risk control improves, because the attention goes where the risk is.
>
> AI doesn't replace credit judgement. It removes the routine work around it.

---

## 1. Framing — what "the agent" actually does

The agent is not a black box that "decides." It is three layered components:

1. **Context engine** — collects every relevant fact about the order, customer, and exposure from SAP and adjacent systems, into one structured record.
2. **Rules engine (deterministic)** — evaluates hard policy thresholds (credit limit utilization, overdue amount, dispute status). Categorizes the order as `AutoRelease` / `RecommendRelease` / `RecommendHold` / `Escalate`.
3. **AI agent (Claude / AOAI)** — writes the human-readable rationale, surfaces edge cases the rules missed, and produces an audit-ready explanation.

Auto-release only fires when the **rules engine** and the **AI agent** agree, and only within a policy-declared safe zone. Judgment cases always go to a human.

## 2. Discovery — the data every credit decision needs

| Fact | SAP source | Notes |
|---|---|---|
| Sales order value, currency, delivery date | `VBAK`, `VBAP`, `VBEP` | Order-header + item |
| Credit block reason | `VBUK`, `VBAK` (CMGST / CMPS_CM) | Which block type is set |
| Credit limit + exposure | `KNKK` (credit control area) | Limit, used amount, credit horizon |
| Customer master | `KNA1`, `KNB1`, `KNVV` | Blocked flag, payment terms, sales area |
| AR open items | `BSID` (open), `BSAD` (cleared) | Overdue calc feeds from here |
| Payment history | `BSAK` / `BSAD` — cleared docs | Days beyond terms, average |
| Disputes | `SCASE` / `FDM_DCPROC_H` / `UDM_DISPUTE` | Open disputes and amounts |
| External rating | Bureau feed (Bisnode / Coface / D&B) | REST connector |
| Prior overrides | Custom Z-table or workflow log | Last 5 manual releases + reasons |

## 3. Solution architecture on Fabric

```
┌──────────────┐   SAP CDC       ┌────────────────────────────────────────────┐
│ SAP S/4HANA  │────────────────►│ Fabric — bronze_lakehouse (Finance)        │
│  (SD + FI)   │                 │   Tables/sap_s4_vbak, vbap, knkk, bsid…    │
└──────────────┘                 └───────────────┬────────────────────────────┘
                                                 │  Spark notebooks (silver)
                                                 ▼
┌──────────────┐  REST / files   ┌────────────────────────────────────────────┐
│  Credit      │────────────────►│ Fabric — silver_lakehouse                  │
│  bureau      │                 │   dim_customer  (SCD2 with rating)         │
└──────────────┘                 │   fact_ar_open, fact_ar_history            │
                                 │   fact_orders_blocked                      │
                                 │   fact_disputes                            │
                                 └───────────────┬────────────────────────────┘
                                                 │  Notebook + policy engine
                                                 ▼
                                 ┌────────────────────────────────────────────┐
                                 │  gold_warehouse — vw_credit_case (per-order│
                                 │  row with 40 features, ready for the agent)│
                                 └───────────────┬────────────────────────────┘
                                                 │
                     ┌───────────────────────────┼─────────────────────────────┐
                     ▼                           ▼                             ▼
        ┌─────────────────────┐    ┌────────────────────────┐    ┌──────────────────────────┐
        │  Rules engine       │    │  AI agent (Claude via  │    │  Direct Lake semantic    │
        │  (SQL / Python)     │    │  Azure AI Foundry)     │    │  model — analyst report  │
        │  Deterministic tier │    │  Structured tool-use   │    │  Certified, RLS enforced │
        └──────────┬──────────┘    └────────────┬───────────┘    └──────────────────────────┘
                   │                            │
                   └───────────┬────────────────┘
                               ▼
              ┌──────────────────────────────────┐
              │  Decision log Delta table        │
              │  (rules verdict + agent rationale│
              │  + final decision + human review)│
              └──────────────┬───────────────────┘
                             ▼
              ┌──────────────────────────────────┐
              │  SAP release write-back          │
              │  (BAPI_SALESORDER_RELEASE via    │
              │  Function App or OData)          │
              └──────────────────────────────────┘
```

## 4. Data ingestion — SAP → OneLake

- **SAP CDC connector** on ODP for `VBAK`, `VBAP`, `VBUK`, `BSID`, `BSAD`, `BSAK`, `KNA1`, `KNB1`, `KNVV`, `KNKK`, `UDM_DISPUTE`.
- Delta interval: **15 min** — credit blocks are near-real-time actionable.
- Bureau feed: nightly REST pull with fallback to CSV drop; land as `bronze/bureau/<provider>/…`.
- Manual override log (SAP Z-table or SolMan workflow export): daily.

**MANDT filter enforced at bronze.** All the SAP-side pitfalls covered in `sap-to-fabric/` apply — trailing-minus amounts, `YYYYMMDD` dates, timezone normalization, `X`-as-boolean.

## 5. Silver — one row per business object

- `dim_customer` (SCD2) — credit limit history, rating history, payment terms, blocked flag.
- `fact_ar_open` — every open AR item with `days_overdue`, `amount_local`, `amount_group`.
- `fact_ar_history` — cleared docs with `days_beyond_terms` per invoice.
- `fact_orders_blocked` — every currently-blocked SO with block type, order value, requested delivery.
- `fact_disputes` — open disputes with amount, days-open, category.

Currency + unit conversion done here at silver, not at query time.

## 6. Gold — `vw_credit_case`, one row per credit decision

The feature table the rules engine and AI agent both consume. **~40 columns**, keyed by `sales_order_number` + `credit_control_area`. Examples:

| Column | Type | Source |
|---|---|---|
| `so_number` | string | vbak |
| `customer_id` | string | vbak → knvv |
| `order_value_group_ccy` | decimal | vbap + tcurr |
| `block_type` | string | vbuk |
| `block_reason_text` | string | vbuk (semantic lookup) |
| `credit_limit` | decimal | knkk |
| `credit_exposure` | decimal | knkk + fact_ar_open |
| `exposure_pct` | decimal | derived |
| `overdue_amount_group_ccy` | decimal | fact_ar_open |
| `overdue_count_30_60_90_plus` | struct | fact_ar_open |
| `avg_days_beyond_terms_12m` | decimal | fact_ar_history |
| `worst_days_beyond_terms_12m` | int | fact_ar_history |
| `open_dispute_count` | int | fact_disputes |
| `open_dispute_amount` | decimal | fact_disputes |
| `external_rating` | string | dim_customer |
| `external_rating_changed_90d` | bool | dim_customer history |
| `last_manual_release_days_ago` | int | override log |
| `last_manual_release_reason` | string | override log |
| `is_strategic_account` | bool | account master |
| `country_risk_tier` | string | derived |
| `time_since_block_hours` | decimal | derived at read time |

## 7. Policy — the deterministic rules tier

Rules live in **YAML checked into Git** — not scattered in code:

```yaml
version: 2026-09
credit_block_release_policy:

  auto_release:
    all_of:
      - overdue_amount_group_ccy: { op: "<=", value: 0 }
      - exposure_pct: { op: "<=", value: 0.85 }
      - open_dispute_count: { op: "==", value: 0 }
      - avg_days_beyond_terms_12m: { op: "<=", value: 5 }
      - block_type: { op: "in", value: ["A", "B"] }        # only soft blocks
      - order_value_group_ccy: { op: "<=", value: 50000 }
      - external_rating_changed_90d: { op: "==", value: false }
      - country_risk_tier: { op: "in", value: ["low", "medium"] }

  escalate_immediately:
    any_of:
      - overdue_amount_group_ccy: { op: ">", value: 100000 }
      - exposure_pct: { op: ">", value: 1.20 }
      - open_dispute_amount: { op: ">", value: 25000 }
      - country_risk_tier: { op: "==", value: "high" }
      - external_rating: { op: "in", value: ["D", "SD"] }

  recommend_release_default: true
```

Applied by a Spark notebook against `vw_credit_case`:

```python
import yaml
from pyspark.sql import functions as F

policy = yaml.safe_load(open("/lakehouse/default/Files/policy/credit_release.yaml"))

def apply_rule(df, ruleset, verdict):
    cond = None
    combiner = F.expr("true") if ruleset.get("all_of") else F.expr("false")
    clauses = ruleset.get("all_of", []) + ruleset.get("any_of", [])
    for clause in clauses:
        (col, spec), = clause.items()
        c = F.col(col)
        op = spec["op"]; v = spec["value"]
        expr = {
            "<=": c <= v, ">=": c >= v, "<": c < v, ">": c > v,
            "==": c == v, "!=": c != v,
            "in": c.isin(v), "not_in": ~c.isin(v)
        }[op]
        cond = expr if cond is None else (
            cond & expr if "all_of" in ruleset else cond | expr
        )
    return df.withColumn("rules_verdict",
        F.when(cond, F.lit(verdict)).otherwise(F.col("rules_verdict")))

cases = (spark.table("gold.vw_credit_case")
    .withColumn("rules_verdict", F.lit("RecommendRelease")))

cases = apply_rule(cases, policy["credit_block_release_policy"]["escalate_immediately"], "Escalate")
cases = apply_rule(cases, policy["credit_block_release_policy"]["auto_release"], "AutoRelease")
```

The policy YAML is versioned in Git; every change is reviewed, and every case row stores the **policy version** it was evaluated against.

## 8. The AI agent — Claude via Azure AI Foundry

**Model:** Claude Sonnet in the latest family, deployed on Azure AI Foundry in the tenant's geo. See [`../../ai/claude/`](../../ai/claude/README.md) for the enablement pattern.

### 8.1 System prompt (versioned in Git)

```
You are a credit-analyst assistant for Company X. You review a structured
"credit case" record and produce:
  1. A one-paragraph rationale a controller can read in under 30 seconds.
  2. A decision recommendation: AutoRelease | RecommendRelease | RecommendHold | Escalate.
  3. Any red flags the rules engine may not have caught (e.g., pattern in
     the customer's payment behaviour, dispute clustering, order timing).

Cite specific fields from the case (e.g., "exposure_pct = 0.92"). Never
invent facts not in the record. Never override a rules_verdict of "Escalate"
with a lower risk verdict.

Output ONLY via the `record_decision` tool.
```

### 8.2 Tool-use schema — strict output

```python
tools = [{
    "name": "record_decision",
    "description": "Persist the agent's decision and rationale.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision":  {"type": "string",
                          "enum": ["AutoRelease", "RecommendRelease",
                                   "RecommendHold", "Escalate"]},
            "rationale": {"type": "string", "maxLength": 800},
            "red_flags": {"type": "array", "items": {"type": "string"}},
            "cited_fields": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1}
        },
        "required": ["decision", "rationale", "confidence"]
    }
}]
```

### 8.3 Prompt caching

The system prompt + policy summary block is **cached** — the per-case query changes, so the reusable ~4k-token context is billed at cached-rate:

```python
system = [
    {"type": "text", "text": FIXED_INSTRUCTIONS},
    {"type": "text", "text": FULL_POLICY_YAML_SUMMARY,
     "cache_control": {"type": "ephemeral"}}
]
```

### 8.4 Batch invocation over the case table

Async Pandas UDF from [`../../ai/claude/`](../../ai/claude/README.md) §6, sized to ~50 concurrent calls per executor. For 400 cases/week this runs in seconds; scales to the tens of thousands.

### 8.5 Final decision fusion

```python
def fuse(rules_verdict, agent_decision, agent_confidence):
    # Never auto-release if either side rejects.
    if rules_verdict == "Escalate" or agent_decision == "Escalate":
        return "Escalate"
    if rules_verdict == "AutoRelease" and agent_decision == "AutoRelease" \
       and agent_confidence >= 0.85:
        return "AutoRelease"
    if rules_verdict == "AutoRelease" and agent_decision in ("RecommendRelease",):
        return "RecommendRelease"
    return "RecommendHold" if "Hold" in (rules_verdict, agent_decision) \
                            else "RecommendRelease"
```

## 9. Analyst workbench — human-in-the-loop UI

Not everything ships to SAP. Analysts need to see the queue, the reasoning, and one-click override.

Options, cheapest to richest:

| Surface | Fit |
|---|---|
| **Power BI report** (Direct Lake) with drill-through to a case page | Good for triage + escalation |
| **Power Apps** on the same Direct Lake dataset | Adds one-click Approve/Reject buttons |
| **Fabric AI Skill** — analysts ask NL questions across the case queue | Add-on for exploration |
| **Custom React app** (Function App backend) | When approval workflow is complex |

Every override — approve/reject/edit — is written to the **decision log**.

## 10. Decision log — the audit source of truth

```sql
CREATE TABLE decision_log (
    case_id                 STRING,
    so_number               STRING,
    customer_id             STRING,
    evaluated_at            TIMESTAMP,
    policy_version          STRING,
    rules_verdict           STRING,
    agent_model             STRING,
    agent_prompt_version    STRING,
    agent_decision          STRING,
    agent_confidence        DECIMAL(4,3),
    agent_rationale         STRING,
    fused_decision          STRING,
    analyst_override        STRING,        -- null if not overridden
    analyst_upn             STRING,
    override_reason         STRING,
    sap_release_status      STRING,        -- pending | success | failed
    tokens_input            INT,
    tokens_output           INT,
    cost_estimate_usd       DECIMAL(10,4),
    correlation_id          STRING
) USING DELTA
PARTITIONED BY (DATE(evaluated_at));
```

The single source of truth for compliance, ML retraining data, and business-value reporting.

## 11. Write-back to SAP

Two production-safe paths:

- **BAPI_SALESORDER_RELEASE** wrapped in an **Azure Function** with SAP RFC SDK, called from a Fabric pipeline **Web activity** at `AutoRelease` fusion result.
- **OData V4 service** on S/4HANA exposing a release action — cleaner in modern S/4 landscapes.

Never write directly from the notebook. Route through a dedicated service with:
- Idempotency key = `case_id`
- Retry with exponential backoff
- Compensating action if SAP rejects (mark `sap_release_status = 'failed'`, requeue for analyst)

## 12. Governance & controls

Refer to [`../../governance/`](../../governance/README.md) for the full framework. Specific to this use case:

- **Sensitivity label:** `Confidential — Finance` on all lakehouses and semantic models.
- **RLS on the analyst report:** by `sales_organisation` and `credit_control_area`.
- **OLS:** hide `customer_bank_account` and `external_rating_raw_score` from the analyst role; show only the derived tier.
- **DLP:** block export of the case queue to external emails.
- **Audit log:** every AutoRelease writes to Purview via the standard Fabric audit stream.
- **Model card:** a versioned document describing the AI agent's prompt, tools, allowed decisions, evaluation results, and known limitations. Referenced from the decision log.
- **Kill switch:** a tenant-level workspace parameter `auto_release_enabled = false` halts every AutoRelease within one refresh cycle without redeploying.

## 13. Deployment — Dev → Test → Prod

```
Git repo: fabric-credit-agent
├── notebooks/
│   ├── 01_ingest_sap_cdc.ipynb          (Data Factory-triggered)
│   ├── 02_silver_transforms.ipynb
│   ├── 03_gold_credit_case_view.ipynb
│   ├── 04_apply_policy.ipynb
│   ├── 05_run_agent.ipynb
│   └── 06_fuse_and_write_log.ipynb
├── pipelines/
│   ├── pl_credit_agent_hourly.json
│   └── pl_credit_agent_bureau_nightly.json
├── policy/
│   └── credit_release.yaml              (versioned, reviewed via PR)
├── prompts/
│   ├── system_prompt.md                 (versioned)
│   └── tool_schema.json
├── semantic-model/
│   └── analyst_report.tmdl
└── azure-devops/
    └── deploy.yaml                      (fabric-cli + PowerShell)
```

- **Fabric Deployment Pipelines** (Dev → Test → Prod) for workspaces.
- **Azure DevOps YAML pipeline** deploys notebooks, semantic model TMDL, and policy YAML.
- **fabric-cli** for headless deployment.

## 14. Evaluation — did the agent actually work?

Before switching auto-release on, run **shadow mode for 6–8 weeks**:

- Every case goes through the agent, but only human decisions execute.
- Log agent decision alongside human decision.
- Weekly review: agreement rate, disagreement categories, false-positive-release rate (would have auto-released but human held), false-negative-release rate (would have held but human released).

Auto-release goes live only when:
- Agreement rate ≥ 95% on the auto-release band
- Zero false-positive-releases on cases where `exposure_pct > 1.0`
- Analyst team signs off in writing

## 15. Monitoring

KPIs in a dedicated Power BI report on the decision log:

- Case volume / day + block-type mix
- Auto-release rate (target: 55–65 %)
- Escalation rate (target: 10–15 %)
- Median time-to-decision (target: < 5 min for AutoRelease, < 2 h for RecommendRelease)
- Analyst override rate on AutoRelease (must stay < 2 %; higher = drift, retune)
- Cost per case (agent tokens + CU)
- SAP write-back failure rate
- DSO impact (measured against a matched historical baseline)

Alerts (via Data Activator on the log table):
- `analyst_override_rate_24h > 0.05` → notify data-eng lead
- `sap_release_status = failed` for any single case → notify integration owner
- `agent_confidence < 0.6` AND `fused_decision = AutoRelease` (should be zero) → immediate kill switch

## 16. Change management

- **Analyst training** — the tool is a helper, not a judge. Show worked examples on both sides (agent-right, agent-wrong).
- **Policy review board** — monthly review of the YAML thresholds with credit, sales, and finance-ops.
- **Prompt & model changes** — PR + shadow-mode retest before production rollout. Never hot-swap.

## 17. Roadmap after go-live

- **Predictive block avoidance** — forecast which orders will block *before* they enter the pipe, propose credit-limit review one week ahead.
- **Cross-sell of Working Capital tools** — feed the same features into DSO-driver dashboards and payment-behaviour scorecards.
- **Customer-facing self-service** — allow strategic customers to see their current exposure and pending blocks via a portal, backed by the same gold table.
- **Multi-language rationale** — Claude generates the analyst rationale in the analyst's preferred language.

## 18. Anti-patterns

- ❌ Sending the AI decision straight to SAP without a rules-engine gate — one hallucination becomes a shipped order.
- ❌ Storing policy thresholds inside the notebook — impossible to review, impossible to audit.
- ❌ Skipping shadow mode — you will not know the false-release rate until it is too late.
- ❌ Un-versioned prompts and models — reproducibility gone.
- ❌ No cost-per-case metric — Copilot / Claude costs scale with case volume; leadership will ask.
- ❌ Global auto-release for every block type — soft blocks and hard blocks need different bars.
- ❌ Assuming analysts will trust the agent because it "usually works" — show reasoning, always. Confidence without explanation is a black box.
- ❌ Building this as a one-off script — this is a product; treat it like one.

---

**Go-live readiness checklist:**

- [ ] All SAP source tables landing in bronze on 15-min cadence
- [ ] Silver + gold transforms idempotent, checkpointed, run-id partitioned
- [ ] `vw_credit_case` has all 40 features, validated on 3 months of history
- [ ] Policy YAML reviewed and signed off by credit-ops
- [ ] Agent prompt + tool schema versioned in Git; shadow-mode results attached
- [ ] Rules-agent agreement rate ≥ 95% in the auto-release band
- [ ] Sensitivity labels applied end-to-end
- [ ] RLS + OLS validated on analyst report
- [ ] Decision log Delta table live; Power BI report certified
- [ ] Alerts wired in Data Activator + on-call rota named
- [ ] Kill switch tested; workspace parameter halts auto-release within one cycle
- [ ] SAP write-back service deployed with idempotency + retry
- [ ] Analyst training session delivered; feedback loop defined
- [ ] Cost per case + DSO impact baseline captured pre-launch
