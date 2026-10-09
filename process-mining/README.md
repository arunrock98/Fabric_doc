# Process Mining for AI Agent Operations

A practical reference for using **process mining** — specifically on the **OCEL 2.0 (Object-Centric Event Log) standard** — to make AI-agent operations observable, auditable, and verifiable.

The sharpest fit today is at the intersection of two emerging categories:

- **Agent operations platforms** such as [Sophia (gosophia.ai)](https://www.gosophia.ai/?lang=en) — autonomous agents that read emails and act on ERP systems.
- **Agent verification platforms** such as [Verified (beverified.ai)](https://beverified.ai/) — independent verification that an agent actually completed the work it claims to have completed.

Process mining supplies both with the same thing from different angles: a reference model of what the business process *should* look like, plus the ability to replay what *actually* happened against it.

> **A note on vendor claims.** The product descriptions below are drawn from each vendor's own public website on the date this document was written. KPIs each vendor states on its page are summarized as stated — they are not independently validated by this document.

---

## 1. What process mining actually is

Process mining = **reconstructing the real execution of a business process from event data**, then comparing it to a reference model.

Three canonical techniques:

| Technique | Question answered |
|---|---|
| **Discovery** | What does my process look like, based on the data? |
| **Conformance checking** | Where does reality deviate from the agreed model? |
| **Enhancement** | How can we improve the model with insights from reality? |

Historical use (Celonis, Signavio, Fluxicon Disco) focused on back-office processes: order-to-cash, procure-to-pay, record-to-report, incident management. **The new use case is agent oversight** — the same math, applied to the actions of autonomous AI agents.

## 2. OCEL 2.0 — why object-centric matters now

Traditional event logs (XES, flat CSV) force every event to belong to **one case ID**. That works for simple processes but breaks the moment one event touches multiple business objects — exactly how AI agents operate.

A single "Sophia drafted a reply and updated the ERP" action may touch:

- an **Email**
- a **Customer**
- a **Sales Order**
- a **Material**
- a **Quotation**

In a traditional log you'd pick one object as "the case" and either lose the others or duplicate rows. In **OCEL 2.0** events relate to **sets of objects**, each with a qualifier describing the relationship. Objects also have state over time, and relationships between objects can evolve.

### Why OCEL 2.0 is the right standard for AI agent operations

- Agents operate across objects. One event = many objects.
- Audit trails (needed by platforms like Verified) must link an agent action to every business record it touched. OCEL 2.0 makes that native.
- Modern process-mining libraries (pm4py 2.7+, PromptML, newer commercial tools) use OCEL 2.0 as a first-class input.
- JSON serialization makes it trivial to emit from any agent framework.

OCEL 2.0 ships with two canonical formats: **OCEL2-JSON** and **OCEL2-SQLite**. Pick JSON for streaming/append, SQLite for analytical replay.

## 3. Why process mining helps Sophia-style agent platforms

[Sophia (gosophia.ai)](https://www.gosophia.ai/?lang=en) describes itself as "The Autonomous Operations Platform" — AI agents that work from a company's email inbox, check the ERP, and act. Native integrations with 20+ ERP systems (SAP, Dynamics, NetSuite, Sage, …). Vendor claims on its page include 70 hours saved per back-office employee per month and 80% of routine emails handled.

Where process mining materially moves the needle for this type of platform:

### 3.1 Before deployment — pick where the agent lands first

Mine the as-is **email + ERP** process over the last 6–12 months:

- Count of inbound emails per topic (RFQ, order change, delivery delay, reminder, dispute).
- End-to-end lead time for each process variant.
- Rework rate (how often the same order is touched more than N times).
- Which process variants concentrate volume vs variance.

**High-volume + low-variance** process variants are the first candidates for agent automation. **High-variance + low-volume** stays with humans. Without the mine, the pilot is a hunch.

### 3.2 Define the reference model the agent must follow

Discover a reference process (BPMN or Petri net) from historical events of successful cases. This becomes the **expected trace** against which the agent's live actions are later compared. Approving a new agent use case = approving its reference model.

### 3.3 During operation — conformance in production

Every agent action emits an OCEL event (`AgentAction`, `AgentDraftedReply`, `AgentUpdatedERP`). Compare the live trace against the reference:

- **Fitness** below threshold → the agent is skipping or inventing steps.
- **Precision** low → the agent is doing things not described in the model.
- **Trace deviations** → surface in a dashboard, route to a human.

This is **real-time governance** on autonomous actions, driven from the same event log the agent emits.

### 3.4 After deployment — prove the value

Compare the mined process **before** and **after** Sophia goes live. Hard metrics instead of marketing claims:

| Metric | Before | After | Source |
|---|---|---|---|
| Median lead time (RFQ → quote) | 36 h | 4 h | mined |
| Touch count per RFQ | 4.1 | 1.3 | mined |
| Rework rate | 18 % | 6 % | mined |
| Autonomy rate | — | 72 % | agent telemetry |

Signed OCEL evidence is independent of the vendor dashboard.

### 3.5 Where Sophia (as described on its site) specifically benefits

- **Material checks** — mine how long analysts take today, where they go wrong, which SKUs are hardest.
- **Order processing** — surface the variants where human overrides happen most, so the agent's confidence threshold can be tuned per variant.
- **Payables** — conformance against three-way-match reference catches silent agent errors on invoices.
- **Delay management** — variant mining on delay patterns tells the agent which escalation branch to pick.
- **Human-in-the-loop oversight** — Sophia says "every action can be reviewed, approved, or overridden"; a mined deviation dashboard is the natural interface for that.

## 4. Why process mining helps Verified-style verification platforms

[Verified (beverified.ai)](https://beverified.ai/) describes itself as "Independent verification for enterprise work" — "Agree on what done means. Then check it." It builds **Work Contracts** that define completion for a task, then verifies an agent's completion claim against evidence from the source system. The page lists the product as **live and in use at five enterprises**, with the invoice-matching ERP connector live and LIMS/QMS/TMS/WMS/ticketing connectors planned.

Process mining and OCEL 2.0 map directly onto this model:

### 4.1 Work Contracts = reference process models

A Work Contract that defines "invoice is matched to its PO" is a mini reference process:

```
Create PO ──► Receive Goods ──► Post Invoice ──► Match (3-way) ──► Approved
```

Formalize that as a Petri net or BPMN model. The agent's claim ("I matched invoice X") is then verifiable by replaying its OCEL events against the reference. **Conformance = verification.**

### 4.2 Signed OCEL events = tamper-evident evidence

Verified says "raw data stays in the customer's systems, and anyone can re-run the check without trusting Verified." OCEL 2.0 fits that model natively — the log is just data, signed per event with the source system's identity. Verification is deterministic re-replay.

### 4.3 Per-record billing on a Work Contract

Verified mentions per-invoice billing on outsourced work. Each OCEL event tied to a Work Contract becomes a billable unit of verified completion. The mined log is both the invoice backup and the audit trail.

### 4.4 Vendor comparison on the same "done"

Different agent vendors can be compared on the **same reference model**: fitness, precision, deviation rate, cost per verified completion. The mined log is the only objectively comparable artefact across vendors.

### 4.5 Dispute settlement

When a bill is contested, the OCEL log is the shared fact base. The reference model is agreed before the work starts; the actual trace is what shipped. Deviation reports settle most disputes.

### 4.6 Specific fits to Verified's roadmap

Verified's site lists these use cases live or planned:

- **Invoice matching (live)** — classical PM territory, 3-way-match conformance.
- **Case resolution (design partners)** — ITSM-style discovery + conformance.
- **Batch release (next)** — regulated GxP processes; OCEL replay = inspection-ready evidence.
- **Shipment delivery (next)** — multi-object (shipment, PO, invoice, delivery) — the OCEL 2.0 sweet spot.
- **Warehouse movements (horizon)** — physical AI; event-driven ingestion from WMS/IoT → OCEL.

## 5. OCEL 2.0 demo — build an event log for invoice matching

A working example in Python with **pm4py 2.7+**. Models the **invoice-matching** flow described by Verified's live connector.

### 5.1 Objects and events

```
Objects:
  PurchaseOrder       po-1001, po-1002 …
  GoodsReceipt        gr-5001 …
  Invoice             inv-9001 …
  Agent               agent-sophia-v3

Events:
  CreatePO            touches PurchaseOrder, Agent (if agent created)
  ReceiveGoods        touches PurchaseOrder, GoodsReceipt
  PostInvoice         touches Invoice, PurchaseOrder
  ClaimMatch          touches Invoice, PurchaseOrder, GoodsReceipt, Agent
  VerifyMatch         touches Invoice, PurchaseOrder, GoodsReceipt
  Approve / Reject    touches Invoice
```

### 5.2 Minimal OCEL 2.0 JSON

```json
{
  "objectTypes": [
    { "name": "PurchaseOrder", "attributes": [{"name":"vendor","type":"string"},{"name":"amount","type":"double"}] },
    { "name": "GoodsReceipt",  "attributes": [{"name":"qty","type":"integer"}] },
    { "name": "Invoice",       "attributes": [{"name":"amount","type":"double"}] },
    { "name": "Agent",         "attributes": [{"name":"version","type":"string"}] }
  ],
  "eventTypes": [
    { "name": "CreatePO" }, { "name": "ReceiveGoods" }, { "name": "PostInvoice" },
    { "name": "ClaimMatch" }, { "name": "VerifyMatch" },
    { "name": "Approve" }, { "name": "Reject" }
  ],
  "objects": [
    { "id": "po-1001", "type": "PurchaseOrder", "attributes": [{"name":"vendor","value":"ACME","time":"2026-10-01T08:00:00Z"},{"name":"amount","value":1200.00,"time":"2026-10-01T08:00:00Z"}] },
    { "id": "gr-5001", "type": "GoodsReceipt",  "attributes": [{"name":"qty","value":10,"time":"2026-10-03T10:00:00Z"}] },
    { "id": "inv-9001","type": "Invoice",       "attributes": [{"name":"amount","value":1200.00,"time":"2026-10-04T09:00:00Z"}] },
    { "id": "agent-sophia-v3","type": "Agent",  "attributes": [{"name":"version","value":"3.2.1","time":"2026-10-01T00:00:00Z"}] }
  ],
  "events": [
    { "id":"e1","type":"CreatePO",     "time":"2026-10-01T08:00:00Z",
      "relationships":[{"objectId":"po-1001","qualifier":"created"},
                       {"objectId":"agent-sophia-v3","qualifier":"actor"}] },
    { "id":"e2","type":"ReceiveGoods", "time":"2026-10-03T10:00:00Z",
      "relationships":[{"objectId":"po-1001","qualifier":"against"},
                       {"objectId":"gr-5001","qualifier":"produced"}] },
    { "id":"e3","type":"PostInvoice",  "time":"2026-10-04T09:00:00Z",
      "relationships":[{"objectId":"inv-9001","qualifier":"posted"},
                       {"objectId":"po-1001","qualifier":"references"}] },
    { "id":"e4","type":"ClaimMatch",   "time":"2026-10-04T09:03:00Z",
      "relationships":[{"objectId":"inv-9001","qualifier":"subject"},
                       {"objectId":"po-1001","qualifier":"matched-po"},
                       {"objectId":"gr-5001","qualifier":"matched-gr"},
                       {"objectId":"agent-sophia-v3","qualifier":"claimant"}] },
    { "id":"e5","type":"VerifyMatch",  "time":"2026-10-04T09:03:30Z",
      "relationships":[{"objectId":"inv-9001","qualifier":"subject"},
                       {"objectId":"po-1001","qualifier":"verified-po"},
                       {"objectId":"gr-5001","qualifier":"verified-gr"}] },
    { "id":"e6","type":"Approve",      "time":"2026-10-04T09:04:00Z",
      "relationships":[{"objectId":"inv-9001","qualifier":"subject"}] }
  ]
}
```

Save as `ocel_invoice.json`.

### 5.3 Load with pm4py and discover a model

```python
import pm4py

# Load OCEL 2.0 JSON
ocel = pm4py.read_ocel2_json("ocel_invoice.json")

# Summary
print(pm4py.ocel_get_summary(ocel))

# Discover an object-centric Petri net
ocpn = pm4py.discover_oc_petri_net(ocel)
pm4py.view_ocpn(ocpn, format="png")

# Flatten to a classical case-centric log (per object type) for simple checks
inv_log = pm4py.ocel_flattening(ocel, object_type="Invoice")
```

### 5.4 Conformance check against a reference model

```python
# Reference: define the required happy path for Invoice as a Petri net
import pm4py
from pm4py.objects.petri_net.obj import PetriNet, Marking
from pm4py.objects.petri_net.utils import petri_utils

# Build a tiny reference: PostInvoice → ClaimMatch → VerifyMatch → Approve
net = PetriNet("invoice_ref")
p0 = PetriNet.Place("start"); p1 = PetriNet.Place("p1"); p2 = PetriNet.Place("p2")
p3 = PetriNet.Place("p3");    p4 = PetriNet.Place("end")
for p in (p0,p1,p2,p3,p4): net.places.add(p)

def _t(name): t = PetriNet.Transition(name, name); net.transitions.add(t); return t
t_post = _t("PostInvoice"); t_claim = _t("ClaimMatch")
t_verify = _t("VerifyMatch"); t_approve = _t("Approve")

petri_utils.add_arc_from_to(p0, t_post, net); petri_utils.add_arc_from_to(t_post, p1, net)
petri_utils.add_arc_from_to(p1, t_claim, net); petri_utils.add_arc_from_to(t_claim, p2, net)
petri_utils.add_arc_from_to(p2, t_verify, net); petri_utils.add_arc_from_to(t_verify, p3, net)
petri_utils.add_arc_from_to(p3, t_approve, net); petri_utils.add_arc_from_to(t_approve, p4, net)

im = Marking({p0: 1}); fm = Marking({p4: 1})

# Replay the real log against the reference
replayed = pm4py.conformance_diagnostics_alignments(inv_log, net, im, fm)
for r in replayed:
    print({k: r[k] for k in ("fitness","cost","alignment")})
```

The output tells you per-case **fitness** (how much of the real trace matches the reference), the **alignment** (step-by-step mapping), and any inserted / skipped events.

### 5.5 Deviation examples the check will catch

| Scenario | What conformance checking surfaces |
|---|---|
| Agent emits `ClaimMatch` with no prior `ReceiveGoods` | Missing event → fitness drop; alignment shows skip |
| Agent approves its own match (no `VerifyMatch`) | Missing synchronous move → the agent's `Approve` after `ClaimMatch` fails the reference |
| Duplicate `ClaimMatch` on the same invoice | Loop detected; precision metric drops |
| `Approve` on a `po-1001` with amount delta > tolerance | Caught by combining the alignment with an attribute predicate |

For verification platforms like Verified, this is the raw verification math — **re-replay the signed OCEL log against the agreed reference and sign the result**.

### 5.6 From log to signed evidence

The last step turns a mined trace into audit-ready evidence:

1. Agent emits `ClaimMatch` event with a payload hash of the source records.
2. Source system (ERP) emits `VerifyMatch` event with its own hash.
3. Verifier compares hashes and runs conformance.
4. Verifier produces a **signed evidence envelope** containing:
   - The OCEL event IDs replayed
   - Fitness + alignment
   - Reference model version
   - Verifier identity + timestamp + signature

That envelope is what the customer, the agent vendor, and the auditor all read from.

## 6. Where to use process mining (beyond agent oversight)

The classical table of fits, with the agent-oversight column added:

| Process | Why PM helps | Agent-oversight angle |
|---|---|---|
| **Order-to-Cash** | Variant explosion kills margin; mining finds the costly ones | Verify order-processing agent follows credit → block → release path |
| **Procure-to-Pay** | Maverick buying, maverick payments, 3-way-match breaks | 3-way-match conformance for AP automation agents |
| **Record-to-Report** | Close choreography is opaque; last-minute journals pile up | Verify month-end-close agents hit the right sequence |
| **Hire-to-Retire** | Onboarding delays, offboarding gaps | Verify HR-bot access-provisioning sequencing |
| **Issue-to-Resolve (ITSM)** | SLA breaches, ping-pong tickets | Verify triage/routing bots follow the policy |
| **Credit management** | Blocks concentrate on a few customers | Verify credit-release agents (see `../AI Use Cases/credit-block-release-agent/`) |
| **Returns / Warranty** | Fraud rings surface as rare variants | Verify RMA chatbots |
| **Lead-to-Opportunity** | Pipeline leakage per stage | Verify CRM-enrichment agents |
| **Manufacturing / Shop floor** | Machine state and WIP transitions | Verify MES copilots suggest the right next step |
| **Clinical / GxP** | Regulators demand replayable evidence | OCEL replay = inspection-ready audit trail |
| **Supply chain / shipments** | Multi-party, multi-object by nature | OCEL 2.0 natively models shipment-order-invoice |

## 7. Reference architecture — OCEL 2.0 pipeline alongside the agent

```
┌──────────────┐   source-system      ┌──────────────────────────────────┐
│  ERP / CRM / │   events + hashes    │  OCEL ingest service             │
│  WMS / email │─────────────────────►│  (REST / Kafka / Eventstream)    │
└──────────────┘                      └────────────┬─────────────────────┘
                                                   │
┌──────────────┐   agent-emitted      ┌────────────▼─────────────────────┐
│  AI agent    │   OCEL events        │  OCEL 2.0 log store              │
│ (Sophia-like)│─────────────────────►│  JSON append / SQLite / Delta    │
└──────────────┘                      └────────────┬─────────────────────┘
                                                   │
                     ┌─────────────────────────────┼─────────────────────┐
                     ▼                             ▼                     ▼
      ┌──────────────────────────┐  ┌──────────────────────────┐  ┌────────────────────┐
      │ Discovery (ocpn, variant)│  │ Conformance checker      │  │ Dashboard & alerts │
      │ pm4py / commercial tools │  │ (reference vs real)      │  │ Power BI / Streamlit│
      └──────────────────────────┘  └────────────┬─────────────┘  └────────────────────┘
                                                 │
                                                 ▼
                                    ┌──────────────────────────┐
                                    │ Signed verification env. │
                                    │ (Verified-style evidence)│
                                    └──────────────────────────┘
```

Everything in this stack is open-standard. OCEL 2.0 means the log store is portable across tooling, and the verifier is deterministic re-replay — exactly the property an independent verification platform needs.

## 8. Anti-patterns I avoid

- ❌ Mining with a flat case-ID when the process is multi-object — forces you to pick one anchor and lose the rest. Use OCEL 2.0.
- ❌ Treating process mining as a pre-sale consulting exercise — the value is **continuous conformance in production**, not a one-off report.
- ❌ Scoring an agent on its own telemetry — vendor logs don't say the job was done to the agreed standard. Mine from the source system.
- ❌ Discovering a model and freezing it — reference models evolve; version them like code.
- ❌ No signatures on events — "trust us, we logged it" fails the first audit.
- ❌ Dumping the full OCEL log into a BI tool and expecting insights — use purpose-built process-mining libraries first; BI consumes the derived metrics.
- ❌ Conformance thresholds set globally — tune per variant. A low-volume high-value variant can tolerate zero deviation; a high-volume low-value one tolerates more.
- ❌ No reference-model governance — if everyone can change what "done" means, the Work Contract concept collapses.

---

**Agent-oversight-with-process-mining readiness checklist:**

- [ ] Reference process defined and signed off (BPMN or Petri net), versioned in Git
- [ ] OCEL 2.0 event emission wired from agent framework (JSON/SQLite)
- [ ] Source-system verification events wired in with per-record hashes
- [ ] Discovery notebook rebuilds the mined model nightly
- [ ] Conformance checker runs per case, writes fitness + alignment to a `ocel_conformance` table
- [ ] Dashboard shows deviation rate by variant, by agent version
- [ ] Alert on fitness drop below threshold triggers rollback / human review
- [ ] Reference-model changes gated by PR with before/after conformance delta
- [ ] Signed evidence envelope produced per completed Work Contract
- [ ] Vendor comparison report on the same reference model + same KPIs

---

**External references used in this document:**

- Sophia (Krops GmbH) — [gosophia.ai](https://www.gosophia.ai/?lang=en) — product descriptions and claims summarised as stated on the page.
- Verified — [beverified.ai](https://beverified.ai/) — product descriptions, Work Contract concept, roadmap and status summarised as stated on the page.
- **OCEL 2.0 standard** — [ocel-standard.org](https://www.ocel-standard.org/) — the public specification and samples.
- **pm4py** — open-source Python library implementing OCEL 2.0 ingestion, discovery, and conformance checking.
