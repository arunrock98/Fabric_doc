# 05 · Checklists & sign-off template

## Readiness checklist (before the parallel run)

- [ ] Report and dataset inventory complete, with owners and usage
- [ ] Lineage traced to SAP source tables/fields for every in-scope fact
- [ ] KPI dictionary agreed with Finance (definition, grain, sign, currency)
- [ ] Field mapping ECC → S/4HANA reviewed and stored in Git
- [ ] Leading ledger, currency types and special periods handled explicitly
- [ ] New source layer built in a separate schema; CI/CD in place
- [ ] Validation rules written for every in-scope fact (critical + warning)
- [ ] Results table and Teams alert configured
- [ ] Sign-off periods agreed with Finance

## Parallel run checklist (per period)

- [ ] Both builds loaded for the period, including special periods
- [ ] Validation agent run; report archived
- [ ] Every mismatch triaged: DATA / MAPPING / EXPECTED_CHANGE, with an owner
- [ ] Fixes deployed and the agent re-run
- [ ] Expected changes documented and sent to Finance for approval

## Cut-over checklist

- [ ] All critical rules pass for the sign-off periods
- [ ] Finance sign-off received for each critical KPI area
- [ ] Semantic model source switched to the new views (contract unchanged)
- [ ] Top 10 reports compared visually old vs new
- [ ] Legacy build frozen read-only, kept as fallback through the next quarter-end
- [ ] Users informed: what changed (ideally nothing) and whom to contact
- [ ] Agent re-pointed to permanent SAP control-total checks

---

## Finance sign-off template

| Field | Entry |
|---|---|
| KPI area | e.g. P&L by company and account |
| Periods validated | e.g. 2025-10, 2025-11, 2025-12 incl. 13–16 |
| Validation run ids | e.g. `c3c1cc08`, `9f2a77d1` |
| Critical rules | All pass ☐ / Exceptions listed below ☐ |
| Expected changes approved | Description, impact, approver |
| Open items | None ☐ / Listed with owner and due date ☐ |
| Decision | Approved for go-live ☐ / Not approved ☐ |
| Signed by (Finance / Controlling) | Name, role, date |
| Signed by (BI owner) | Name, role, date |
