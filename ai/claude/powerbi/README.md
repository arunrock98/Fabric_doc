# Enabling Claude for a Power BI Semantic Model

A step-by-step guide to wiring **Anthropic Claude** into a **Power BI / Fabric semantic model** so that it can:

1. Generate **DAX measures** from natural-language intent, grounded on your model's real schema.
2. Answer **natural-language questions** by generating and executing DAX against the model, returning data + a short explanation.
3. **Scaffold a Power BI report** (semantic model TMDL + report pages) from a prompt, as a Git-friendly **PBIP** project.

Everything below is generic, employer-neutral, and uses only public / documented Fabric and Power BI surfaces (Fabric REST, semantic-link / `sempy`, XMLA endpoint, PBIP, TMDL). For the general Claude-in-Fabric wiring, see [`../README.md`](../README.md).

---

## 0. What you can realistically automate

| Task | Automation level with Claude |
|---|---|
| Generate a DAX measure from NL ("YoY revenue by region") | **High** — reliable when the model has descriptions |
| Answer an NL business question with a dataset + a sentence | **High** — tool-use + execute via `sempy` or REST |
| Generate a semantic model (tables, relationships, measures) from a schema | **Medium-high** — scaffold TMDL, human reviews |
| Generate a full dashboard with multiple pages and visuals | **Medium** — Claude writes PBIP; final layout polish is manual |
| Replace a BI developer | **Low** — Claude accelerates them, does not replace them |

Treat Claude as a **copilot for the semantic-model developer**, not an autonomous dashboard factory.

---

## 1. Prerequisites

- A **Fabric workspace** on an **F64+** capacity (Copilot and workspace-managed identity work above this bar).
- A **Power BI semantic model** in that workspace, published or stored as a PBIP in Git. Tables, columns, and measures have **business-friendly descriptions** — Claude's output quality tracks description quality.
- **Claude access** via one of:
  - **Azure AI Foundry** serverless deployment (recommended for Fabric), or
  - AWS Bedrock, or
  - Anthropic API direct.
- **Azure Key Vault** containing `claude-endpoint` and `claude-api-key` secrets, with the workspace managed identity granted `Get`/`List`.
- **XMLA endpoint read/write** enabled at the tenant level for the workspace's premium capacity (`Admin portal → Capacity settings → Workloads → XMLA Endpoint = Read Write`).
- Python packages inside the Fabric Spark environment: `anthropic`, `semantic-link` (`sempy`), `tenacity`, `pyarrow`.

```bash
# From a Fabric notebook cell
%pip install anthropic sempy tenacity
```

---

## 2. Step-by-step — generate a DAX measure from natural language

### Step 2.1 — Load the model metadata

```python
import sempy.fabric as fabric

dataset = "Finance_Model"            # the semantic model name
workspace = fabric.resolve_workspace_name()

tables   = fabric.list_tables(dataset=dataset, workspace=workspace)
columns  = fabric.list_columns(dataset=dataset, workspace=workspace)
measures = fabric.list_measures(dataset=dataset, workspace=workspace)
rels     = fabric.list_relationships(dataset=dataset, workspace=workspace)
```

### Step 2.2 — Build the grounding block Claude will see

Keep it compact — Claude's output quality drops when you dump 100k tokens of schema. Project only what matters:

```python
import json

def _grounding(tables, columns, measures, rels):
    schema = {
        "tables": [
            {
                "name": t["Name"],
                "description": t.get("Description", ""),
                "columns": [
                    {"name": c["Column"], "type": c["Data Type"],
                     "description": c.get("Description", "")}
                    for c in columns.query(f"`Table Name` == '{t['Name']}'").to_dict("records")
                ],
                "measures": [
                    {"name": m["Measure Name"], "dax": m["Measure Expression"],
                     "description": m.get("Description", "")}
                    for m in measures.query(f"`Table Name` == '{t['Name']}'").to_dict("records")
                ]
            }
            for t in tables.to_dict("records")
        ],
        "relationships": [
            {"from": f"{r['From Table']}[{r['From Column']}]",
             "to":   f"{r['To Table']}[{r['To Column']}]",
             "cardinality": r["Cardinality"]}
            for r in rels.to_dict("records")
        ]
    }
    return json.dumps(schema, indent=2)

grounding = _grounding(tables, columns, measures, rels)
```

### Step 2.3 — Ask Claude for the measure, with tool-use for strict output

```python
from anthropic import Anthropic
from notebookutils import mssparkutils

client = Anthropic(
    base_url=mssparkutils.credentials.getSecret("kv-fabric-ai","claude-endpoint") + "/anthropic",
    api_key=mssparkutils.credentials.getSecret("kv-fabric-ai","claude-api-key")
)

tools = [{
    "name": "propose_measure",
    "description": "Propose a new DAX measure for the semantic model.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name":        {"type": "string"},
            "home_table":  {"type": "string"},
            "format_string":{"type": "string"},
            "display_folder":{"type": "string"},
            "dax":         {"type": "string"},
            "description": {"type": "string"},
            "assumptions": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["name","home_table","dax","description"]
    }
}]

system = [
    {"type": "text",
     "text": "You are a senior Power BI engineer. Produce idiomatic DAX only. "
             "Use explicit DIVIDE, no implicit SUM. Prefer existing measures over "
             "re-aggregating columns. Reject requests you cannot satisfy from the "
             "provided schema; do not invent tables, columns, or measures."},
    {"type": "text",
     "text": "Semantic model schema:\n" + grounding,
     "cache_control": {"type": "ephemeral"}}         # cached across requests
]

def propose_measure(intent: str, model: str = "claude-sonnet-5"):
    msg = client.messages.create(
        model=model, max_tokens=1024,
        system=system,
        tools=tools, tool_choice={"type":"tool","name":"propose_measure"},
        messages=[{"role":"user","content":intent}]
    )
    return next(b.input for b in msg.content if b.type == "tool_use")

m = propose_measure("Year-over-year revenue growth by region, excluding returns")
print(m["name"], "::", m["dax"])
```

**Why tool-use:** you get a dict that already matches the schema, so you can validate/persist without regex scraping of free text.

**Why cache the schema:** every subsequent measure proposal is billed at cache-hit rate — ~90% cheaper, same context.

### Step 2.4 — Validate the proposal before you ship it

Never auto-create measures without validating. At minimum:

```python
import re

def _basic_validate(proposal: dict, grounding_json: str) -> list[str]:
    gr = json.loads(grounding_json)
    known_cols = {f"{t['name']}[{c['name']}]"
                  for t in gr["tables"] for c in t["columns"]}
    known_tabs = {t["name"] for t in gr["tables"]}
    known_meas = {m["name"] for t in gr["tables"] for m in t["measures"]}

    refs = re.findall(r"'?([A-Za-z0-9_ ]+)'?\[([A-Za-z0-9_ ]+)\]", proposal["dax"])
    issues = []
    for tab, col in refs:
        if f"{tab}[{col}]" not in known_cols and col not in known_meas:
            issues.append(f"Unknown reference '{tab}'[{col}]")
    if proposal["home_table"] not in known_tabs:
        issues.append(f"Unknown home table '{proposal['home_table']}'")
    return issues

issues = _basic_validate(m, grounding)
if issues:
    print("REJECTED:", issues)
else:
    print("OK to persist")
```

Pair this with a **DAX linter** (`BPA` best-practice-analyzer in Tabular Editor) wired into CI.

### Step 2.5 — Persist the measure to the model

Two production-safe paths:

**(a) Via `sempy` — hot-add (good for low-stakes measures):**

```python
fabric.add_measure(
    dataset=dataset, workspace=workspace,
    table_name=m["home_table"],
    measure_name=m["name"],
    measure_expression=m["dax"],
    description=m["description"]
)
```

**(b) Via PBIP + Git (recommended):**

Emit a TMDL fragment, add it to the PBIP project, open a pull request. The approver reviews DAX, format string, display folder, and description.

```python
tmdl_snippet = f"""
measure '{m['name']}' = ```
    {m['dax']}
    ```
    description = ```{m['description']}```
    displayFolder = {m.get('display_folder','AI-proposed')}
    formatString = "{m.get('format_string','0.00')}"
"""
```

Append to `<pbip>/<model>.SemanticModel/definition/tables/<home_table>.tmdl` and open a PR.

---

## 3. Step-by-step — natural-language Q&A over the model

Users ask free-text questions; Claude generates DAX, you run it, the user gets a result.

### Step 3.1 — Define the "ask" tool

```python
tools_qa = [{
    "name": "answer_with_dax",
    "description": "Produce a DAX query to answer the user and a short explanation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "dax":          {"type": "string",
                             "description": "Full EVALUATE statement."},
            "explanation":  {"type": "string",
                             "description": "One sentence for the end user."},
            "confidence":   {"type": "number", "minimum": 0, "maximum": 1}
        },
        "required": ["dax","explanation","confidence"]
    }
}]
```

### Step 3.2 — Ask + execute

```python
def ask(question: str):
    msg = client.messages.create(
        model="claude-sonnet-5", max_tokens=1024,
        system=system,     # same cached grounding as §2
        tools=tools_qa, tool_choice={"type":"tool","name":"answer_with_dax"},
        messages=[{"role":"user","content":question}]
    )
    answer = next(b.input for b in msg.content if b.type == "tool_use")

    # Execute against the model
    df = fabric.evaluate_dax(dataset=dataset, workspace=workspace,
                             dax_string=answer["dax"])
    return df, answer

df, meta = ask("What are the top 10 vendors by GR/IR balance in company code 1000, this year?")
display(df)
print(meta["explanation"])
```

### Step 3.3 — Enforce guardrails on the generated DAX

Simple rules that prevent most hallucination damage:

- Must start with `EVALUATE` (reject anything that isn't a query).
- Must not include destructive constructs — not relevant in DAX, but guard anyway.
- Reject if `confidence < 0.6`; return "I'm not confident — please rephrase."
- Append `TOPN(1000, …)` wrapping to cap row counts.
- Timeout the query at ~30 s.

### Step 3.4 — Honor RLS

`fabric.evaluate_dax` runs **as the calling user** when invoked from a Fabric notebook / data-function with their identity. RLS defined on the model applies automatically. For an external service calling XMLA, use **effective identity** on the connection string:

```
Data Source=powerbi://api.powerbi.com/v1.0/myorg/<Workspace>;
Initial Catalog=<Dataset>;
EffectiveUserName=user@tenant.com;
```

### Step 3.5 — Expose as a REST endpoint

Package Steps 3.1–3.4 into a **Fabric user data function** (preview) or an Azure Function App fronted by the Fabric workspace. Teams / Power Apps / a custom UI calls that endpoint with `{ "question": "…" }` and gets `{ "data": [...], "explanation": "...", "dax": "..." }` back.

The DAX is returned to the caller too — displayed as "How I answered this" so the user can verify.

---

## 4. Step-by-step — scaffold a Power BI report automatically

This section targets **PBIP** (Power BI Project format), which stores a model as **TMDL** and reports as JSON files on disk. Both are plain text — perfect for Claude to generate and for Git to review.

### Step 4.1 — Start from an empty PBIP project

In Power BI Desktop: **File → Save As → Power BI Project (`.pbip`)**. You get:

```
Report.pbip
Report/
    definition.pbir
    report.json
Report.SemanticModel/
    definition.pbism
    definition/
        tables/
            Dim_Date.tmdl
            Fact_GL.tmdl
        model.tmdl
        relationships.tmdl
```

Check this into Git.

### Step 4.2 — Define the brief for Claude

```python
brief = """
Build a Finance monthly-close report. Pages:
  1. Overview — KPI cards (Total Revenue, Total Expense, Net Income, AR Open),
     revenue trend by month, top 5 cost centers over plan.
  2. GR/IR — balance by vendor, aging buckets, oldest open items.
  3. AR Aging — overdue amount by customer with 0-30 / 31-60 / 61-90 / 90+ buckets.

Use existing measures where available; propose new ones only when required.
"""
```

### Step 4.3 — Ask Claude for a report plan + the files to write

```python
report_tools = [{
    "name": "emit_pbip_files",
    "description": "Emit the files to create or update in the PBIP project.",
    "input_schema": {
        "type": "object",
        "properties": {
            "new_measures": {
                "type": "array",
                "items": {"type":"object","properties":{
                    "home_table":{"type":"string"},
                    "name":{"type":"string"},
                    "dax":{"type":"string"},
                    "format_string":{"type":"string"},
                    "description":{"type":"string"}}}},
            "report_pages": {
                "type": "array",
                "items": {"type":"object","properties":{
                    "name":{"type":"string"},
                    "layout":{"type":"string","description":"Short description — visual by visual, with position (row,col) on a 12-col grid."},
                    "visuals":{"type":"array","items":{"type":"object","properties":{
                        "type":{"type":"string","enum":["kpi","line","bar","table","matrix","card","slicer"]},
                        "title":{"type":"string"},
                        "fields":{"type":"array","items":{"type":"string"}},
                        "position":{"type":"object","properties":{
                            "row":{"type":"integer"},"col":{"type":"integer"},
                            "width":{"type":"integer"},"height":{"type":"integer"}}}
                    }}}
                }}
            }
        },
        "required": ["report_pages"]
    }
}]

msg = client.messages.create(
    model="claude-sonnet-5", max_tokens=4096,
    system=system,
    tools=report_tools, tool_choice={"type":"tool","name":"emit_pbip_files"},
    messages=[{"role":"user","content":brief}]
)
plan = next(b.input for b in msg.content if b.type == "tool_use")
```

### Step 4.4 — Render the plan into actual PBIP files

A thin renderer:

```python
from pathlib import Path

project = Path("Report.SemanticModel/definition")

# 4.4.1 — append any new measures as TMDL fragments
for mz in plan.get("new_measures", []):
    tmdl = f'\nmeasure {mz["name"]} = ```\n{mz["dax"]}\n```\n' \
           f'    formatString = "{mz.get("format_string","0.00")}"\n' \
           f'    description = ```{mz.get("description","")}```\n'
    (project / "tables" / f"{mz['home_table']}.tmdl").open("a").write(tmdl)

# 4.4.2 — emit a report page JSON per plan.report_pages
# This generates minimal valid report.json; polish in Desktop.
import json
pages = []
for p in plan["report_pages"]:
    page = {
        "name": p["name"],
        "displayName": p["name"],
        "visuals": [
            {
                "name": v["title"],
                "visualType": {"kpi":"card","line":"lineChart","bar":"barChart",
                               "table":"tableEx","matrix":"pivotTable",
                               "card":"card","slicer":"slicer"}[v["type"]],
                "position": v.get("position", {"row":0,"col":0,"width":4,"height":3}),
                "fields": v.get("fields", [])
            }
            for v in p["visuals"]
        ]
    }
    pages.append(page)

Path("Report/report.json").write_text(json.dumps({"pages": pages}, indent=2))
```

The emitted `report.json` is a **minimal skeleton**. Power BI Desktop re-opens it and layouts snap to the grid.

### Step 4.5 — Open in Power BI Desktop, polish, commit

Open `.pbip` in Desktop:
- Verify the measures are present and compile.
- Walk each page; tune colors, formatting, filters.
- Commit the diff to Git.

### Step 4.6 — Deploy via Fabric Deployment Pipelines

Promote Dev → Test → Prod via Fabric Deployment Pipelines, or publish directly with `fabric-cli`:

```bash
fab import --path ./Report.pbip --workspace Finance-Reports-Prod
```

---

## 5. Prompt caching — the money saver

Any of the three use cases above calls Claude many times with the **same large grounding block** (schema + instructions). Mark that block as cached:

```python
system = [
    {"type":"text", "text": SHORT_INSTRUCTIONS},
    {"type":"text", "text": grounding, "cache_control":{"type":"ephemeral"}}
]
```

Cached input tokens bill at roughly 10 % of normal. For a Q&A endpoint doing 10 000 questions / day on a 20 k-token grounding, cache turns an unaffordable workload into a routine one.

---

## 6. Governance & controls

- **RLS** — always propagated via `sempy` or XMLA effective identity. Never execute DAX as a service principal with elevated rights.
- **Model edits** — route via PBIP + PR, not hot-add, for anything above low-stakes measures.
- **Audit** — append every call to an `ai_calls` Delta table (model, intent, generated DAX, approved y/n, caller UPN, tokens, cost). See the DDL in [`../README.md`](../README.md#12-cost-model).
- **Sensitivity labels** — the semantic model's label propagates into Claude's response envelope; honor it when returning data (block "Export to CSV" on `Confidential` labels).
- **Prompt injection** — treat NL questions as untrusted; keep system instructions above the user content; use tool-use rather than free-form output.
- **Kill switch** — a workspace parameter `claude_enabled = false` halts calls without redeploying.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Claude invents a table or column | Grounding too big and model skipped it, or descriptions missing | Prune schema to relevant tables; add descriptions |
| DAX compiles but returns wrong number | Measure uses implicit SUM or wrong relationship | Add negative examples to the system prompt; add linter check |
| Query times out via `sempy` | Measure fans out across millions of rows | Wrap in `TOPN`, add a `SUMMARIZECOLUMNS` with required filters, add `KEEPFILTERS` |
| `sempy.evaluate_dax` throws permission error | Workspace managed identity lacks model Build permission | Grant Build on the semantic model |
| XMLA write refused | XMLA endpoint not Read/Write for the capacity | Tenant admin flips the capacity setting |
| Cost per Q&A spikes | Prompt caching not applied, or schema regenerated per call | Verify `cache_control` present; pre-compute grounding once per session |
| Report page opens empty in Desktop | `report.json` missing a required field | Use Desktop to save, see the diff, backport to the renderer |

---

## 8. Anti-patterns

- ❌ Letting Claude write DAX directly into Prod without validation + review.
- ❌ Dumping the entire 400-table model into the grounding block. Prune.
- ❌ Skipping prompt caching on the schema. 90 % of cost gone.
- ❌ Treating the generated report as "done." It is a scaffold; a BI developer polishes.
- ❌ Running NL Q&A as a shared service principal. RLS silently bypassed.
- ❌ No `ai_calls` log. You will not be able to prove or disprove anything.
- ❌ Hiding the DAX from the end user. Show it; it builds trust and catches errors.
- ❌ Hot-adding measures and not committing the TMDL to Git. The model drifts from the source of truth.

---

**Enablement checklist:**

- [ ] F64+ capacity confirmed
- [ ] Semantic model has descriptions on all tables, columns, measures
- [ ] XMLA endpoint = Read/Write at the capacity level
- [ ] Key Vault secrets created; workspace managed identity granted access
- [ ] `anthropic` + `sempy` installed in the Fabric Spark environment
- [ ] Grounding builder (`_grounding`) tested against the real model
- [ ] Measure-generator notebook validated end-to-end, including linter
- [ ] NL-Q&A notebook validated with RLS honoring confirmed
- [ ] PBIP scaffold renderer validated on an empty PBIP project
- [ ] `ai_calls` Delta log created and dashboard bookmarked
- [ ] Sensitivity label flows preserved on Claude outputs
- [ ] Deployment pipeline (Dev → Test → Prod) handles PBIP + TMDL diffs
