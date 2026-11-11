# 04 · AI-assisted migration: where it helps and the guardrails

AI speeds up the repetitive parts of a reporting migration. It does not replace engineering review or the controller's sign-off.

| Use case | What AI does | Who stays accountable |
|---|---|---|
| Code conversion | Drafts the rewrite of ECC-based SQL to S/4HANA structures | Engineer reviews, tests and merges |
| Documentation & lineage | Drafts field mappings, model descriptions, KPI definitions from existing SQL/DAX | Model owner reviews |
| Test case generation | Proposes edge cases: reversals, intercompany, FX, special periods, zero lines | Engineer selects and implements |
| Mismatch explanation | Explains validation findings in business language and proposes the next check | Team triages; Finance approves expected changes |
| Report comparison | Summarises differences between old and new report screenshots/exports | Report owner confirms |

---

## 1. Code conversion

**Workflow**
1. Give the model the legacy SQL, the target field mapping ([02](02-reporting-migration-playbook.md)), and the output contract (column list and grain).
2. Ask for the new SQL **plus** a list of assumptions it made.
3. Engineer reviews, runs it in DEV, and lets the validation agent prove it.

**Prompt template**
```text
You are converting a finance data model from SAP ECC to SAP S/4HANA.
Legacy SQL: <paste>
Field mapping (ECC -> ACDOCA): <paste mapping table>
Output contract: columns <list>, grain <grain>, amounts signed, group currency.
Rules: read actuals only from the leading ledger; keep column names and keys identical;
do not aggregate below/above the stated grain; use inline subqueries.
Return: 1) the new SQL, 2) a numbered list of assumptions and open questions.
```

## 2. Documentation & lineage

Feed existing view definitions and DAX measures to the model and ask for: a one-line business description per column, the source SAP field, and the KPI definition in plain language. This turns knowledge held by one person into documentation the whole team can maintain, which is often the biggest risk in a small BI team.

## 3. Test cases

```text
For a GL actuals fact sourced from the S/4HANA Universal Journal, list the 15 most
likely edge cases that break reporting after migration (e.g. reversals, special
periods, intercompany, currency types, parallel ledgers). For each, give a
reconciliation check that would detect it.
```

## 4. Mismatch explanation (in the validation agent)

The agent's `explain_with_llm()` sends **only**:
- the rule definition (id, description, grain, measures)
- up to 20 aggregated mismatch rows (grain keys and totals)
- the rule-based findings

It asks for a short explanation for a controller, a DATA / MAPPING / EXPECTED CHANGE classification, and the next check to run.

Configure with environment variables:

```bash
export LLM_PROVIDER=anthropic          # or openai
export LLM_API_KEY=...                 # from your company's approved AI platform
export LLM_MODEL=...                   # optional
export LLM_API_URL=...                 # optional: your company's AI gateway / Azure OpenAI endpoint
```

---

## Guardrails

1. **Use the company's approved AI platform** (enterprise agreement, Azure OpenAI in your tenant, or an internal gateway). Never paste finance data into public consumer tools.
2. **Send aggregates, not line items.** No customer, vendor, employee or document-level data.
3. **AI drafts, humans decide.** Every converted query is reviewed and validated; every expected change is approved by Finance.
4. **Log prompts and answers** next to the validation results for auditability.
5. **Never block on AI.** If the model is unavailable, the deterministic checks and rule-based findings still run.
