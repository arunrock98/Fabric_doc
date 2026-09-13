# Fabric Semantic Model — Finance

A reference design for a **Finance semantic model on Microsoft Fabric** using a Lakehouse (Delta) source, **Direct Lake** storage mode, and Power BI as the consumption layer. Covers the domains that actually appear in enterprise finance: **General Ledger, Accounts Payable, Accounts Receivable, GR/IR, Controlling (Cost & Profit Center Accounting)**.

Everything below is generic and employer-neutral — patterns I use repeatedly, not any employer's schema or data.

---

## 1. Storage mode: Direct Lake first

| Mode | When to use | Trade-off |
|---|---|---|
| **Direct Lake** | Default for finance facts on Fabric Lakehouse/Warehouse | No refresh, no data copy, sub-second on well-modeled data |
| **Import** | Small dimensions with heavy DAX time-intel, or when Direct Lake falls back to DirectQuery too often | Extra refresh step, but predictable DAX perf |
| **DirectQuery** | Avoid for finance; too slow for typical measure patterns | Only for compliance-driven "no data leaves warehouse" |

**Direct Lake fallback traps** (measures silently DirectQuery when hit):
- V-Ordered tables missing → run `OPTIMIZE ... VORDER` after big loads
- Column dictionary size > 3 GB → split hot columns to their own tables or Import
- More than 1 relationship path between two tables → collapse to one active

Check fallback frequency in the **Direct Lake Performance analyzer** semantic-link view.

## 2. Star schema

```
                        ┌────────────────────┐
                        │      Dim_Date      │
                        └─────────┬──────────┘
                                  │
   ┌────────────────┐   ┌─────────┴────────┐   ┌────────────────────┐
   │  Dim_Company   ├───┤     Fact_GL      ├───┤    Dim_Account     │
   └────────────────┘   │  (JournalLines)  │   │  (COA hierarchy)   │
                        └─────────┬────────┘   └────────────────────┘
                                  │
                        ┌─────────┴────────┐
                        │ Dim_ProfitCenter │
                        └──────────────────┘

   ┌────────────────┐   ┌──────────────────┐   ┌────────────────────┐
   │   Dim_Vendor   ├───┤     Fact_AP      ├───┤    Dim_Currency    │
   └────────────────┘   │  (Open + Cleared)│   └────────────────────┘
                        └──────────────────┘

   ┌────────────────┐   ┌──────────────────┐   ┌────────────────────┐
   │  Dim_Customer  ├───┤     Fact_AR      ├───┤   Dim_PaymentTerm  │
   └────────────────┘   │  (Open + Cleared)│   └────────────────────┘
                        └──────────────────┘

   ┌────────────────┐   ┌──────────────────┐
   │   Dim_Vendor   ├───┤   Fact_GR_IR     │
   └────────────────┘   │ (Goods vs Invoice)│
                        └──────────────────┘

   ┌────────────────┐   ┌──────────────────┐   ┌────────────────────┐
   │ Dim_CostCenter ├───┤  Fact_Controlling├───┤ Dim_CostElement    │
   └────────────────┘   │  (CO postings)   │   └────────────────────┘
                        └──────────────────┘
```

### Fact tables

| Table | Grain | Key columns | Typical measures |
|---|---|---|---|
| `Fact_GL` | 1 row per GL line item | `CompanyCode`, `FiscalYear`, `DocNumber`, `LineItem` | Total Debit/Credit, Balance |
| `Fact_AP` | 1 row per open + cleared AP item | `CompanyCode`, `VendorKey`, `DocNumber`, `LineItem` | AP Open, DPO, Aged buckets |
| `Fact_AR` | 1 row per open + cleared AR item | `CompanyCode`, `CustomerKey`, `DocNumber`, `LineItem` | AR Open, DSO, Overdue |
| `Fact_GR_IR` | 1 row per GR or IR posting | `CompanyCode`, `PO`, `POLine`, `MovementType`, `DocNumber` | GR/IR Balance, Aging |
| `Fact_Controlling` | 1 row per CO posting | `CompanyCode`, `CostCenter`, `CostElement`, `PostingDate` | Plan vs Actual, Variance |

**Grain rules I enforce:**
- Store both **local** and **group currency** amounts pre-converted at posting time. Never convert in DAX.
- Store `PostingDate`, `DocumentDate`, `DueDate`, `ClearingDate` — they answer different questions.
- Keep `IsCleared` as an explicit column, not derived from `ClearingDate IS NULL` at query time.

### Dimensions

| Table | Notes |
|---|---|
| `Dim_Date` | Fiscal calendar with **fiscal year variant** column — SAP-style non-Jan starts. Marked as Date table. |
| `Dim_Company` | Includes `CurrencyCode` (local) and `ControllingArea`. |
| `Dim_Account` | Full COA hierarchy — level columns L1..L6 flattened for slicer perf. |
| `Dim_Vendor` | SCD Type 2 on `PaymentTerms`, `Blocked` status. |
| `Dim_Customer` | SCD Type 2 on `CreditLimit`, `Blocked`. |
| `Dim_CostCenter` | Hierarchy L1..L6, plus `ResponsiblePerson` for RLS by manager. |
| `Dim_ProfitCenter` | Hierarchy, plus `Segment` for management reporting. |
| `Dim_CostElement` | Primary vs secondary cost element flag. |
| `Dim_Currency` | Only needed if you support ad-hoc currency conversion in DAX. |

## 3. Relationships

- **All single-direction** (many-to-one from fact → dim). Bi-directional cross-filter is a foot-gun in finance — use `TREATAS` or `CROSSFILTER()` inline when you truly need it.
- **Single active** between any two tables. Inactive relationships (e.g., `Fact_AR` to `Dim_Date` via `DueDate`) are activated per-measure with `USERELATIONSHIP()`.
- Never relate two facts directly — always through a conformed dimension.

## 4. Core DAX measure library

Grouped by domain. All measures use **explicit measures** — no implicit summarization on fact columns.

### 4.1 GL

```dax
Total Debit :=
    CALCULATE(
        SUM ( Fact_GL[AmountLocal] ),
        Fact_GL[DebitCredit] = "D"
    )

Total Credit :=
    CALCULATE(
        SUM ( Fact_GL[AmountLocal] ),
        Fact_GL[DebitCredit] = "C"
    )

GL Balance :=
    [Total Debit] - [Total Credit]

GL Balance YTD :=
    CALCULATE(
        [GL Balance],
        DATESYTD ( Dim_Date[Date], "31/12" )   -- adjust fiscal year end
    )
```

### 4.2 AR — DSO and aging

```dax
AR Open :=
    CALCULATE(
        SUM ( Fact_AR[AmountLocal] ),
        Fact_AR[IsCleared] = FALSE
    )

Revenue 12M :=
    CALCULATE(
        SUM ( Fact_GL[AmountLocal] ),
        Dim_Account[AccountType] = "Revenue",
        DATESINPERIOD ( Dim_Date[Date], MAX ( Dim_Date[Date] ), -12, MONTH )
    )

DSO :=
    DIVIDE ( [AR Open], [Revenue 12M] ) * 365

AR Overdue Days :=
    SUMX (
        FILTER ( Fact_AR, Fact_AR[IsCleared] = FALSE ),
        MAX ( 0, DATEDIFF ( Fact_AR[DueDate], TODAY (), DAY ) )
    )

AR Aging Bucket :=      -- calculated column on Fact_AR, not a measure
    VAR d = DATEDIFF ( Fact_AR[DueDate], TODAY (), DAY )
    RETURN
        SWITCH (
            TRUE (),
            d <= 0,  "Not due",
            d <= 30, "1-30",
            d <= 60, "31-60",
            d <= 90, "61-90",
            "90+"
        )
```

### 4.3 AP — DPO

```dax
AP Open :=
    CALCULATE(
        SUM ( Fact_AP[AmountLocal] ),
        Fact_AP[IsCleared] = FALSE
    )

Purchases 12M :=
    CALCULATE(
        SUM ( Fact_GL[AmountLocal] ),
        Dim_Account[AccountType] = "Expense",
        DATESINPERIOD ( Dim_Date[Date], MAX ( Dim_Date[Date] ), -12, MONTH )
    )

DPO :=
    DIVIDE ( [AP Open], [Purchases 12M] ) * 365
```

### 4.4 GR/IR reconciliation

```dax
GR Value :=
    CALCULATE(
        SUM ( Fact_GR_IR[AmountLocal] ),
        Fact_GR_IR[MovementType] = "GR"
    )

IR Value :=
    CALCULATE(
        SUM ( Fact_GR_IR[AmountLocal] ),
        Fact_GR_IR[MovementType] = "IR"
    )

GR/IR Balance :=
    [GR Value] - [IR Value]

GR/IR Aging Days :=
    AVERAGEX (
        FILTER ( Fact_GR_IR, [GR/IR Balance] <> 0 ),
        DATEDIFF ( Fact_GR_IR[PostingDate], TODAY (), DAY )
    )
```

### 4.5 Controlling — plan vs actual

```dax
Actual Cost :=
    CALCULATE(
        SUM ( Fact_Controlling[AmountLocal] ),
        Fact_Controlling[Version] = "Actual"
    )

Plan Cost :=
    CALCULATE(
        SUM ( Fact_Controlling[AmountLocal] ),
        Fact_Controlling[Version] = "Plan"
    )

Variance :=
    [Actual Cost] - [Plan Cost]

Variance % :=
    DIVIDE ( [Variance], [Plan Cost] )
```

## 5. Row-Level Security (RLS)

Common finance RLS patterns — pick per organization:

- **By CompanyCode** — controller sees their entity only.
- **By ProfitCenter** — segment leaders see their segment.
- **By CostCenter manager** — via `Dim_CostCenter[ResponsiblePerson]` + `USERPRINCIPALNAME()`.

Example role — "CostCenter Manager":

```dax
[ResponsiblePerson] = USERPRINCIPALNAME()
```

Applied on `Dim_CostCenter`; propagates to `Fact_Controlling` via the star.

**Do not** apply RLS filters directly on fact tables — kills Direct Lake column store perf. Always filter through the dimension.

## 6. OLS (Object-Level Security)

Hide sensitive columns from non-privileged roles:
- `Dim_Vendor[BankAccount]`, `Dim_Employee[Salary]` → OLS "None" for most roles.
- OLS is defined in **TMDL / TOM** — the Power BI Desktop UI still exposes it only via Tabular Editor.

## 7. Refresh strategy

Direct Lake reads Delta directly — the "refresh" you care about is:

1. **Lakehouse load** finishes (bronze → silver → gold notebooks / pipeline).
2. **`OPTIMIZE ... VORDER`** runs on all gold tables that back the semantic model.
3. **Semantic-model reframe** — either automatic on query, or forced via `Refresh` API `type: clearValues` / `full` to warm cache.

For month-end close reports where sub-second matters: warm the cache with the top 20 measure/dimension combos right after gold load. Semantic-link (`sempy`) makes this a 30-line notebook.

## 8. Deployment

- Model authored in **Tabular Editor 2/3** as **TMDL** (`.tmdl`) files, checked into Git alongside Lakehouse notebooks.
- Deployed via **Fabric Deployment Pipelines** (Dev → Test → Prod) with parameter rules for capacity + workspace bindings.
- CI: `fabric-cli` in Azure DevOps pipeline runs `best-practice-analyzer` on TMDL before merge.

## 9. Anti-patterns I avoid

- ❌ Wide fact table with 200 columns "just in case" — kills Direct Lake dictionary.
- ❌ DAX currency conversion at query time — pre-convert during silver load.
- ❌ Calculated columns on fact tables for anything a measure can express — bloats storage.
- ❌ Bi-directional relationships to "make slicers cross-filter" — use `TREATAS` instead.
- ❌ Storing amounts as strings from SAP extract — cast at bronze.
- ❌ One giant measure table with 400 measures — group by folder + display folder in TMDL.

---

**Files planned to land next to this README:**

- `tmdl/` — `.tmdl` snippets for each table + measure group
- `dax/` — one `.dax` file per measure family for easy copy-in
- `sample-data/` — a tiny synthetic Parquet set so the model demos end-to-end on a local Lakehouse
