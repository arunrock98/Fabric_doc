# 02 · Reporting migration playbook: rebuilding the source layer

The rule: **the semantic model must not notice the migration.** Every fact and dimension keeps its columns, keys and meaning; only the SQL that feeds it changes.

---

## 1. The big simplification: the Universal Journal

In ECC, a finance fact is typically stitched together from several tables. In S/4HANA most actual postings (FI, CO, asset accounting, material ledger, margin analysis) land in one line-item table, **ACDOCA**, with company code, account, cost center, profit center, segment, functional area and many more attributes on every line.

| Reporting need | SAP ECC sources | SAP S/4HANA source | Notes |
|---|---|---|---|
| GL actuals | BKPF (header) + BSEG (line) + FAGLFLEXA (new-GL line items) | **ACDOCA** (+ BKPF for header fields) | One row per journal entry line |
| GL totals (control) | FAGLFLEXT | ACDOCA aggregated | Use for reconciliation |
| CO actuals | COBK + COEP | **ACDOCA** | CO object on the same line as FI |
| CO plan | COSP / COSS (plan value type, version) | **ACDOCP** or the planning tool | Decide the plan source in Phase 2 |
| Profitability | CE1xxxx / CE4xxxx (costing-based) | **ACDOCA** (account-based margin analysis) | Values may differ legitimately |
| Material ledger / actual cost | CKMLHD, CKMLCR | CKMLHD, CKMLCR, MLDOC | |
| Masters (company, account, cost/profit center) | T001, SKA1/SKAT, CSKS/CSKT, CEPC/CEPCT | Unchanged | Hierarchies stay in set tables / FSV |

---

## 2. Field mapping: the usual suspects

Map columns explicitly and keep the mapping in Git (`mapping/fact_gl_actual.csv` is a good pattern).

| Target column (unchanged) | ECC origin (example) | S/4HANA ACDOCA field | Watch out |
|---|---|---|---|
| company_code | BUKRS | RBUKRS | |
| fiscal_year | GJAHR | GJAHR | |
| period | MONAT / POPER | POPER | Special periods 13–16 live here too |
| gl_account | HKONT / RACCT | RACCT | |
| cost_center | KOSTL | RCNTR | |
| profit_center | PRCTR | PRCTR | Now derived on more lines: expect *more* assignment, not less |
| document_number | BELNR | BELNR | |
| line_item | BUZEI | DOCLN | Different numbering; never join old and new on line number |
| amount_local | DMBTR (with debit/credit indicator) | HSL | ACDOCA amounts are **signed**; ECC needs SHKZG to apply the sign |
| amount_group | (group currency field) | KSL | Check which currency type is "group" in your ledger setup |
| amount_doc | WRBTR | WSL | |
| quantity | MENGE | MSL | |

**Sign convention:** in ECC line items, many amount fields are unsigned and the debit/credit indicator (SHKZG: S/H) gives the sign. ACDOCA amounts are already signed. A classic migration bug is applying the sign twice, which flips every credit.

---

## 3. Design rules

1. **One view per fact as the contract.** The semantic model reads `rpt.fact_gl_actual`. Underneath, `rpt.fact_gl_actual` is a view that points first to `legacy.fact_gl_actual`, later to `s4.fact_gl_actual`. Cut-over is a one-line change.
2. **Same grain, same keys.** If the legacy fact was at line-item grain, the new one is too. Don't silently aggregate.
3. **Surrogate keys from business keys,** not from row numbers, so legacy and new builds produce identical keys for the same dimension members.
4. **Keep the ledger explicit.** Filter ACDOCA to the leading ledger (`RLDNR = '0L'` in most setups) unless the report needs a parallel ledger. Forgetting this duplicates amounts.
5. **Handle history deliberately.** Either migrate history into the new build (simplest for users), or union `legacy` history before the cut-over date with `s4` after it, with a reconciliation rule on the boundary period.
6. **Document every expected change** (for example new profit center derivation) in the KPI dictionary, approved by Finance.

---

## 4. Common pitfalls (and how the agent catches them)

| Pitfall | Symptom | Agent rule that catches it |
|---|---|---|
| Special periods 13–16 not extracted | Year-end totals short, months 1–12 fine | Totals by period, plus full-year total |
| Sign applied twice | Differences equal to **−2 × legacy amount** | Amount totals by account |
| Wrong currency field (local vs group) | Constant ratio between old and new per company | Totals by company and currency |
| Non-leading ledger included | New amounts ≈ 2 × legacy | Totals by company/period |
| Profit center derivation changed | Totals match by account, but not by profit center | Totals by account × profit center |
| Line-item duplication from a bad join | Line counts higher than legacy | Line counts by company/period |
| Missing new company codes or accounts in dimensions | Facts with unknown keys | Orphan-key check |

---

## 5. Semantic layer & reports

- No DAX changes should be needed. If one is, it's a signal the contract changed; review with Finance.
- Re-point datasets in a **branch** (Git / deployment pipeline), test against the validation report, then deploy.
- Run a visual comparison of the top 10 reports (old vs new workspace) as a final smoke test.
