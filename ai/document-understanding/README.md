# Bringing Documents into Process Intelligence — on Microsoft Fabric

How **multimodal LLMs** turn confirmations, invoices and delivery notes into **process data** using Microsoft Fabric. Same pattern most people recognise from Celonis-centric write-ups, re-architected on Fabric so the whole chain — ingestion, extraction, matching, writeback, process-mining event emission, and the UX analysts actually use — runs on OneLake + Power BI + Function Apps, with no Celonis dependency.

Reference: internal deck *AI Document Ingestion* (Oct 2026) — business framing, match-status taxonomy, value model, and P2P landscape are kept intact; the platform is replaced end-to-end.

Everything below is generic and employer-neutral.

---

## 1. Your process data stops where the documents start

Process mining only sees what is in the system. Most of what shapes a case lives outside it:

- IDC puts **~90 % of enterprise data as unstructured** — emails, PDFs, scans, images.
- In Purchase-to-Pay (P2P), the system shows a tidy chain: `SendPO → Confirm → ReceiveGoods → ReceiveInvoice → Pay`. Reality is: between those steps people open PDFs, read confirmations, compare delivery notes, cross-check invoices — and nothing of what they read makes it to the event log.

**The blind spot:** deviations in the document that never reach the event log stay invisible until they cause a problem downstream.

## 2. Where manual document handling breaks down

The same pattern repeats for confirmations, invoices, delivery notes, certificates:

| # | Where it breaks | What goes wrong |
|---|---|---|
| 1 | **Scattered inboxes** | PDFs, scans and photos arrive in personal and shared mailboxes, many layouts, many languages |
| 2 | **No link to system record** | Without a reliable key (PO number, invoice number) documents sit unassigned or get lost |
| 3 | **Line-by-line comparison** | Quantities, prices, dates, materials — checked manually against SAP |
| 4 | **Deviations found too late** | Differences surface at goods receipt, invoice posting or payment |
| 5 | **Slow clarification** | Individual emails to vendors, no status tracking |
| 6 | **System not updated** | Information in the document is never written back; planning works with stale data |

## 3. Why a multimodal LLM, not classic OCR

Templates break. Multimodal language models adapt.

| | Classic OCR + templates | Multimodal LLM extraction |
|---|---|---|
| New layout | New template must be built and maintained | Reads the document from the image, no template per layout |
| Languages | One setup per language | Several in one setup; EN / DE / HR observed with no accuracy delta |
| Free text fields | "KW 14" or "asap" not understood | Interprets from context, guided by the prompt |
| Tables & line items | Breaks when columns move | Returns line items in a fixed schema that rules can match |

Research reference: SAP + Stanford on business documents — strong multimodal models reach **F1 76.8 image-only vs 74.1 OCR-only** (Gemini 1.5 Pro); the gap is wider when layouts drift. The practical implication: on business documents, image-in is now at least as good as OCR-in, and far more robust to new layouts.

## 4. The pattern — the LLM reads, rules decide, people handle exceptions

```
┌──────────────┐    ┌──────────┐    ┌──────┐    ┌──────────┐    ┌──────────┐
│ PDF / scan / │───►│  Prompt  │───►│ LLM  │───►│  Rules   │───►│  People  │
│ photo + the  │    │ + fields │    │      │    │ (match)  │    │ (review  │
│ matching PO/ │    │ + schema │    │      │    │          │    │ excepts) │
│ SAP record   │    └──────────┘    └──────┘    └──────────┘    └──────────┘
└──────────────┘
```

Example LLM output from a confirmation:

```json
{
  "po_number": "4511726",
  "item": 10,
  "material": "950-789",
  "quantity": 33,
  "unit_price": 18.98,
  "delivery_date": "2026-03-10"
}
```

Why this split matters:

- The LLM never decides alone. **Matching and writebacks follow transparent business rules.**
- Every value stays traceable to the original document (page, bounding box, hash).
- The same pattern works for any document type; only the prompt and the rule set change.

## 5. The architecture — same pattern, built on Fabric

```
   Email / SharePoint / SFTP / Scanner
                │
                ▼
   ┌──────────────────────────────┐
   │ Logic App  OR  Power Automate│   (SharePoint / Outlook / Teams)
   │ Lands PDFs/images + envelope │
   │ metadata (sender, subject,   │
   │ received_at, attachment hash)│
   └──────────────┬───────────────┘
                  │
                  ▼
   ┌──────────────────────────────┐
   │ Fabric OneLake (bronze_lh)   │
   │   Files/documents/incoming/  │
   │   date=YYYY-MM-DD/*.pdf      │
   │   Tables/document_envelope   │
   └──────────────┬───────────────┘
                  │
                  ▼
   ┌──────────────────────────────┐
   │ Fabric Spark notebook        │
   │   classify_doc_type.ipynb    │   (multimodal LLM: AP invoice |
   │                              │    order confirmation | delivery
   │                              │    note | contract | …)
   └──────────────┬───────────────┘
                  │
                  ▼
   ┌──────────────────────────────┐
   │ extract_fields.ipynb         │   (same LLM, type-specific tool-
   │   Writes silver_lh.doc_      │    use schema; vision+text; cites
   │   extractions                │    page + bbox per field)
   └──────────────┬───────────────┘
                  │
                  ▼
   ┌──────────────────────────────┐
   │ Fabric Warehouse (gold_wh)   │   (SAP-side facts land here via
   │   vw_po, vw_po_lines,        │    SAP CDC — see ../../sap-to-
   │   vw_invoice, vw_gr          │    fabric/)
   └──────────────┬───────────────┘
                  │
                  ▼
   ┌──────────────────────────────┐
   │ T-SQL / Spark MATCH engine   │   (match-status taxonomy below)
   │   Writes doc_match_log       │
   └──────────────┬───────────────┘
                  │
                 fan-out ─────────────────────────────────────────┐
                  │                                               │
                  ▼                                               ▼
   ┌──────────────────────────┐              ┌──────────────────────────┐
   │ Power BI (Direct Lake)   │              │ OCEL 2.0 event emitter   │
   │ "Vendor Confirmations"   │              │  Appends events to       │
   │ + Power App for review   │              │  OCEL store (JSON/Delta) │
   │ + Data Activator alerts  │              │  → process-mining dashbd │
   └──────────┬───────────────┘              └──────────────────────────┘
              │                                               ▲
              ▼                                               │
   ┌──────────────────────────┐                               │
   │ Function App (SAP BAPI / │───────────────────────────────┘
   │  OData writeback)        │   emits WriteBack events too
   └──────────────────────────┘
```

**One-to-one Celonis → Fabric mapping:**

| Celonis concept | Fabric equivalent |
|---|---|
| Event log + knowledge model | Delta tables (silver + gold) + semantic model (Direct Lake) |
| Studio views / apps | Power BI pages + Power Apps for approvals |
| Action Flows | Logic Apps / Power Automate / Data Factory pipelines + Function Apps |
| Attribute functions | DAX measures + T-SQL views |
| Signals / alerts | Data Activator |
| Vendor-side writebacks | Function App → SAP BAPI/OData |
| Process mining layer | OCEL 2.0 event store + `pm4py` (see [`../../process-mining/`](../../process-mining/README.md)) |

## 6. Ingestion patterns

| Document source | Fabric wiring |
|---|---|
| Shared Outlook mailbox | **Logic App** `When a new email arrives` → save attachment to OneLake path → write `document_envelope` row |
| SharePoint document library | **Power Automate** `When a file is created` → copy to OneLake, metadata to envelope table |
| SFTP / scanner | **Data Factory Copy activity** with SFTP connector → OneLake Files; **Metadata activity** writes envelope |
| EDI / API vendor portals | **Data Factory Web activity** or **custom Function App** pulling JSON/XML, pre-parses structured parts |

`document_envelope` (bronze) at minimum:

```
file_path (OneLake abfs)
received_at
source_system (mailbox|sharepoint|sftp|…)
sender
subject
attachment_hash          -- SHA-256 of the file bytes, dedup key
page_count
language_detected        -- optional, set after classify step
doc_type_detected        -- set after classify step
batch_id
```

Deduplication at ingestion by `attachment_hash` — the same confirmation forwarded four times does not get processed four times.

## 7. Classification — "what kind of document is this?"

Short, cheap vision call that labels the document before the expensive extraction step. Lets you dispatch to the right prompt and schema.

```python
import base64, json
from anthropic import Anthropic
from notebookutils import mssparkutils

client = Anthropic(
    base_url = mssparkutils.credentials.getSecret("kv-fabric-ai","claude-endpoint") + "/anthropic",
    api_key  = mssparkutils.credentials.getSecret("kv-fabric-ai","claude-api-key"),
)

def classify(pdf_or_image_bytes: bytes, mime="application/pdf"):
    data_b64 = base64.standard_b64encode(pdf_or_image_bytes).decode()
    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=256,
        tools=[{
            "name":"label_document",
            "input_schema":{"type":"object","properties":{
                "doc_type":{"type":"string","enum":[
                    "order_confirmation","supplier_invoice","delivery_note",
                    "contract","payment_advice","quote","other"]},
                "language":{"type":"string"},
                "confidence":{"type":"number","minimum":0,"maximum":1}},
                "required":["doc_type","language","confidence"]}
        }],
        tool_choice={"type":"tool","name":"label_document"},
        messages=[{"role":"user","content":[
            {"type":"document","source":{"type":"base64","media_type":mime,"data":data_b64}},
            {"type":"text","text":"Classify this business document and detect the language."}
        ]}]
    )
    return next(b.input for b in msg.content if b.type=="tool_use")
```

Confidence < 0.7 → route to a human classifier queue; do not run extraction blindly.

## 8. Extraction with tool-use — strict schema, page citations

Per-doc-type prompt + strict output schema. Shown for **order confirmation**:

```python
oc_tool = [{
    "name":"record_order_confirmation",
    "input_schema":{"type":"object","properties":{
        "po_number":{"type":"string"},
        "vendor_name":{"type":"string"},
        "currency":{"type":"string"},
        "line_items":{"type":"array","items":{"type":"object","properties":{
            "line_no":{"type":"integer"},
            "material":{"type":"string"},
            "description":{"type":"string"},
            "quantity":{"type":"number"},
            "unit":{"type":"string"},
            "unit_price":{"type":"number"},
            "delivery_date":{"type":"string","format":"date"},
            "page":{"type":"integer"},
            "bbox":{"type":"array","items":{"type":"number"}}   # [x0,y0,x1,y1]
        },"required":["line_no","quantity","unit_price","delivery_date"]}},
        "overall_confidence":{"type":"number"}
    },"required":["po_number","line_items"]}
}]

msg = client.messages.create(
    model="claude-sonnet-5", max_tokens=4096,
    tools=oc_tool, tool_choice={"type":"tool","name":"record_order_confirmation"},
    system=[{"type":"text","text": OC_SYSTEM_PROMPT,
             "cache_control":{"type":"ephemeral"}}],   # cache the system prompt
    messages=[{"role":"user","content":[
        {"type":"document","source":{"type":"base64","media_type":"application/pdf","data":data_b64}},
        {"type":"text","text": f"Expected PO: {expected_po}. Expected currency: {company_currency}. Reject the document (set po_number='') if the number on the page does not resemble the expected one."}
    ]}]
)
extracted = next(b.input for b in msg.content if b.type=="tool_use")
```

Key moves:
- **Document attachment** (`type:"document"`) — gives Claude the whole PDF, pages intact, no pre-conversion to images.
- **Tool-use** returns a dict that already matches your silver schema.
- **Page + bbox per line** — the UI can draw a highlight on the PDF; auditors see provenance.
- **Expected hint** in the user turn — the extractor rejects a PO that doesn't look like what you asked about; defeats mix-ups in multi-attachment emails.
- **Prompt caching** on the system prompt — 90 % cost cut on repeated invocations.

Lands as `silver_lh.doc_extractions` with one row per field (`doc_id`, `field_name`, `value`, `page`, `bbox`, `confidence`) and one row per line item in `silver_lh.doc_line_items`.

## 9. The match engine — deterministic, rule-based

Match on **business keys first**, then **business values**. T-SQL on Fabric Warehouse (works equally well in Spark):

```sql
WITH oc AS (
    SELECT doc_id, po_number, line_no, material, quantity, unit_price, delivery_date
    FROM silver.doc_line_items
    WHERE doc_type = 'order_confirmation'
),
po AS (
    SELECT po_number, line_no, material, order_qty, order_price, requested_delivery_date
    FROM gold.vw_po_lines
)
SELECT
    oc.doc_id, oc.po_number, oc.line_no,
    CASE
        WHEN po.po_number IS NULL                                        THEN 'No PO Found'
        WHEN po.material <> oc.material                                  THEN 'Partial Match'
        WHEN oc.quantity = po.order_qty
         AND oc.unit_price = po.order_price
         AND oc.delivery_date = po.requested_delivery_date               THEN 'Full Match'
        WHEN ABS(oc.quantity - po.order_qty) / po.order_qty <= 0.02
         AND ABS(oc.unit_price - po.order_price) / po.order_price <= 0.01
         AND DATEDIFF(day, oc.delivery_date, po.requested_delivery_date)
             BETWEEN -3 AND 3                                            THEN 'Match w/ Tolerance'
        ELSE                                                                  'Partial Match'
    END AS match_status
FROM oc LEFT JOIN po
    ON  po.po_number = oc.po_number
    AND po.line_no   = oc.line_no;
```

Tolerance thresholds live in **config, not code** — load from a `meta.match_tolerances` table so Finance / Procurement can change them without a release.

### 9.1 Match-status taxonomy

| Status | Meaning | Default action |
|---|---|---|
| **Full Match** | Every tracked field matches exactly | Auto-confirm; writeback to SAP |
| **Match w/ Tolerance** | Values within configured tolerance (qty/price/date) | Auto-confirm; log tolerance used |
| **Partial Match** | Business key matched; one or more fields differ beyond tolerance | Review queue — show the delta |
| **No PO Found** | Business key could not be matched | Clarification queue — email vendor via Logic App |

## 10. Review UX — Power BI + Power App

**Power BI "Vendor Confirmations" workspace** (certified, Direct Lake on `gold_wh`):

- KPI cards: total confirmations, open, validated, PO-correctly-detected %, manual-correction %.
- Table with status filter: `PO No. | Status | OC Matching Status | OC Date | Days since OC | Company Code | Supplier`.
- Drill-through to a case page showing the extracted JSON next to the original PDF (embed via blob SAS URL).

**Power App on the same semantic model** gives analysts the Approve / Reject / Edit / Email-vendor buttons — writes to `doc_review_log` which the Function App reads for SAP writeback.

Data Activator rules on `doc_match_log`:
- Any `Partial Match` on a strategic vendor → ping buyer in Teams.
- Any `No PO Found` older than 48 h → escalate to lead.

## 11. SAP writeback

Function App (Python / .NET) wraps:

- **Order confirmation**: `BAPI_PO_CHANGE` to update delivery date / qty, or `ME_CONFIRMATION_UPDATE` on EKES.
- **Invoice**: posted via **MIRO** / `BAPI_INCOMINGINVOICE_CREATE`.
- **Delivery note**: `BAPI_GOODSMVT_CREATE` on receipt.

Called from Fabric via a **Data Factory Web activity** at `AutoConfirm` fusion or when the human presses Approve. Must be:

- **Idempotent** — key the request by `doc_id` + `po_line`; the service refuses duplicates.
- **Retried** with exponential backoff; compensating action on hard failure.
- **Logged** — SAP response landed in `doc_writeback_log`.

## 12. Emitting OCEL 2.0 events — the bullet-proof part

This is where the Fabric build quietly beats the Celonis pattern: the same pipeline that extracts and matches also **emits OCEL 2.0 events** into a process-mining-ready store. No separate re-connecting of document data to an event log — the extraction *is* the event source.

For every document pass, append events to `ocel_lh.events` (Delta table shaped per OCEL 2.0):

| Event type | Objects touched | When |
|---|---|---|
| `DocumentReceived` | `Document`, `Vendor` | Logic App drop |
| `DocumentClassified` | `Document`, `AI_Agent` | Classification notebook |
| `FieldsExtracted` | `Document`, `AI_Agent`, inferred PO | Extraction notebook |
| `MatchComputed` | `Document`, `PurchaseOrder`, `POLine` | Match engine |
| `ReviewRequested` | `Document`, `Analyst` | For non-full matches |
| `ReviewDecided` | `Document`, `Analyst` | On Approve/Reject |
| `WrittenBackToSAP` | `Document`, `PurchaseOrder`, `SAPSystem` | Function App success |

Each event carries hashes of the source payload and the SAP response. The log is **replayable** against a reference P2P model — see [`../../process-mining/README.md`](../../process-mining/README.md) for the OCEL schema and `pm4py` conformance-check code.

This gives you:
- **Variant discovery** — which documents go the smooth path vs the clarification detour
- **Conformance** — did the AI agent follow the standard P2P conformation pattern
- **Lead-time analysis** — median / p95 time from `DocumentReceived` to `WrittenBackToSAP`
- **Audit** — a tamper-evident trail for every automated SAP update

## 13. Value tracking — the model

Same calculation shape as the source deck, implemented as DAX on the match log:

**Settings:**
- `MinSavedPerFullMatch` = 1 min
- `MinSavedPerPartialMatch` = 0.25 min
- `FTEValueEUR` = 64,000 / year
- → `EURperMin` = 64,000 / (220 d × 8 h × 60 min) ≈ 0.6061

**DAX measures:**

```dax
Full Match Lines :=
    CALCULATE ( COUNTROWS ( doc_match_log ),
        doc_match_log[match_status] IN { "Full Match", "Match w/ Tolerance" } )

Partial Match Lines :=
    CALCULATE ( COUNTROWS ( doc_match_log ),
        doc_match_log[match_status] = "Partial Match" )

Minutes Saved :=
    [Full Match Lines] * 1
  + [Partial Match Lines] * 0.25

EUR Saved :=
    [Minutes Saved] * DIVIDE ( 64000, 220 * 8 * 60 )
```

Illustrative display (ties back to the source deck's demo scale: 12 confirmations, 38 line items):

| | Count | Minutes saved |
|---|---:|---:|
| Full matches (incl. tolerance) | 30 | 75 |
| Partial matches | 8 | 10 |
| **Total** | **38** | **85** |

**85 min × €0.61/min ≈ €51.5 saved on this one batch.** Scale the batch; the arithmetic holds.

## 14. One connected landscape — AI agents along P2P

Start with one use case, grow on the same Fabric foundation:

| P2P step | Agent | Fabric wiring |
|---|---|---|
| Create PR | **Intelligent Buying agent** | Semantic-link + Claude; grounded on history |
| Approve PR | **PR Approval agent** | Policy YAML + Claude explanation; writes to decision log |
| Vendor Confirmation | **Vendor Confirmation agent** (this doc's use case) | Multimodal LLM + rules match |
| Receive Goods | **Delivery Note agent** | Same pattern, delivery-note schema |
| Receive Invoice | **Invoice Ingestion agent** | Multimodal LLM; links invoice → PO → GR |
| 3-Way Match | **3-Way Match agent** | Deterministic SQL; LLM only writes the clarification email |
| Clear Invoice | existing BAPI path |

All seven share: OneLake landing, same `doc_extractions` + `doc_match_log` schema, same OCEL store, same Power BI workspace, same Function-App writeback service. Adding the next agent is a new prompt + a new tolerance config, not a new platform.

## 15. More use cases — same pattern, different document

| Area | Use case | What the LLM extracts | What rules do |
|---|---|---|---|
| **Accounts Payable** | Contract payment terms | Payment terms, discount schedule, incoterms | Match against PO / GR / invoice |
| **Accounts Payable** | Invoice currency check | Currency printed on the invoice | Strengthen currency validator |
| **Accounts Payable** | Vendor inquiry emails | Payment-status requests + referenced docs | Open status ticket; attach PDFs |
| **Order Management** | Sales order ingestion | Full SO structure from a customer email | Auto-create quote or route for review |
| **Accounts Receivable** | Deduction validation | Deduction amount + reason + supporting PDF | Build the dispute case |
| **Procurement** | Technical drawings | Specs, material grades, tolerances | Prep negotiation sheet for commodity buyer |

## 16. Is your process a candidate — Fabric fit check

**Strong fit if:**

- Documents arrive by email / SharePoint / SFTP as PDF, scan or image, in high volume.
- Many senders, layouts, or languages make templates impractical.
- Content must be checked against system data (SAP, Dynamics, CRM).
- Deviations today are found late and cause rework.
- You want to automate the process quickly, not after a 9-month platform rollout.

**What you need to start on Fabric:**

- A Fabric workspace on **F64+** capacity.
- SAP (or system-of-record) data landing in OneLake via **SAP CDC** — see [`../../sap-to-fabric/`](../../sap-to-fabric/README.md).
- Access to the mailbox / SharePoint / SFTP where documents arrive.
- Azure Key Vault with multimodal-LLM credentials (Azure AI Foundry / Bedrock / Anthropic direct — see [`../claude/`](../claude/README.md)).
- A sample of **real documents** (50–200 per type) for prompt tuning and validation.
- A **test SAP** connection for writebacks before production.
- Early IT involvement for the Function App service account + BAPI permissions.

## 17. Governance

Full framework in [`../../governance/`](../../governance/README.md). Specific to document ingestion:

- **Sensitivity label** `Confidential — Finance` on `silver_lh.doc_extractions` and downstream.
- **Retention** — original PDFs retained in bronze per compliance policy (7y for AP invoices typical); extractions retained alongside.
- **PII** — redact or hash on landing where the doc includes personal data (vendor-side contact names, bank details).
- **Audit log** — the OCEL store doubles as the audit trail; every call to the LLM also lands in `ai_calls` ([schema](../README.md#12-cost-model)).
- **Kill switch** — a workspace parameter halts auto-writeback instantly if quality drifts.

## 18. Cost model

Per document, back-of-envelope with Claude Sonnet via Azure AI Foundry:

| Step | Tokens (in/out, cached) | Approx cost |
|---|---|---|
| Classify (document attachment, small schema) | 2k / 50 / cached 1k | ~€0.002 |
| Extract (full PDF, line items) | 8k / 500 / cached 4k | ~€0.012 |
| Match (deterministic SQL) | — | Fabric CU only |
| Writeback (deterministic) | — | Function App compute |
| **Per document** | | **~€0.015** |

At 10,000 docs/month ≈ **€150**. Match that against the labour minutes saved from §13 and the payoff is immediate even on modest volumes.

## 19. Anti-patterns

- ❌ Running OCR as a pre-step and feeding Claude the OCR text. You lose the layout and the vision model's spatial reasoning. Pass the PDF.
- ❌ Letting the LLM decide the match. Deterministic rules + tolerance config. Always.
- ❌ No `expected PO` hint in the extraction call. Multi-attachment emails will cross-contaminate.
- ❌ Templates smuggled back in via "regex-cleanup" in the notebook. Delete them.
- ❌ Writing back to SAP without idempotency keys. Duplicate POs follow.
- ❌ No bbox / page citation. The analyst UX cannot show provenance; auditors do not sign off.
- ❌ Skipping the OCEL emission. You rebuild the connection to process mining later and pay twice.
- ❌ Pricing the project on headcount reduction instead of lead-time + error-rate reduction. The business case is cycle time, not FTE cuts.
- ❌ Picking Fabric only for compute and running the LLM on an unapproved endpoint. Residency and audit break.

---

**Enablement checklist:**

- [ ] Fabric workspace on F64+ with Finance domain + sensitivity label
- [ ] OneLake Files path for incoming documents; `document_envelope` table live
- [ ] Logic App / Power Automate flow landing attachments + envelope rows
- [ ] Multimodal LLM endpoint provisioned (Azure AI Foundry preferred); Key Vault wired
- [ ] Classification notebook validated on 50+ real docs across types
- [ ] Per-type extraction prompt + tool-use schema; prompt caching enabled
- [ ] `doc_extractions` + `doc_line_items` silver tables emitting with bbox + page
- [ ] Match engine running on gold warehouse; `meta.match_tolerances` populated
- [ ] Power BI "Vendor Confirmations" report + Power App review flow published
- [ ] Function App writeback service deployed with idempotency + retry; test SAP wired
- [ ] OCEL 2.0 event emitter writing to `ocel_lh.events` after every stage
- [ ] Value-tracking DAX measures published; business sign-off on parameters
- [ ] Data Activator alerts configured; kill-switch parameter tested
- [ ] Rollout plan: one agent live, next three on the backlog, platform shared
