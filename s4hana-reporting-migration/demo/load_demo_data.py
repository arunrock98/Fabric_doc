"""
Builds a small legacy (ECC-based) and new (S/4HANA-based) GL fact in DuckDB with
deliberately planted migration issues, so the validation agent has something to find.

Planted issues
  1. Special period 13 postings are missing in the new build (extraction filter 1-12).
  2. Company 1000, period 3, revenue account 400000: sign applied twice in the new build.
  3. Company 3000: new build uses local currency instead of group currency (ratio 1.08).
  4. Company 2000, period 5, account 600000: amounts moved from profit center P100 to P200.
  5. New build contains GL account 999999, which is missing in the account dimension.
"""

import random

COMPANIES = ["1000", "2000", "3000"]
ACCOUNTS = {"400000": -1, "500000": 1, "600000": 1, "610000": 1}  # sign: revenue negative
PROFIT_CENTERS = ["P100", "P200"]
YEAR = 2025


def _legacy_rows():
    rnd = random.Random(42)
    rows, doc = [], 100000
    for co in COMPANIES:
        for period in range(1, 14):  # 13 = special period (year-end adjustments)
            accounts = ACCOUNTS if period <= 12 else {"600000": 1}
            for acc, sign in accounts.items():
                for pc in PROFIT_CENTERS:
                    for _ in range(3 if period <= 12 else 1):
                        doc += 1
                        amount = round(sign * rnd.uniform(1_000, 50_000), 2)
                        rows.append((co, YEAR, period, acc, pc, f"{doc}", amount))
    return rows


def _new_rows(legacy):
    rows = []
    for co, yr, period, acc, pc, doc, amt in legacy:
        if period >= 13:                                   # issue 1
            continue
        if co == "1000" and period == 3 and acc == "400000":
            amt = -amt                                     # issue 2
        if co == "3000":
            amt = round(amt * 1.08, 2)                     # issue 3
        if co == "2000" and period == 5 and acc == "600000" and pc == "P100":
            pc = "P200"                                    # issue 4
        rows.append((co, yr, period, acc, pc, doc, amt))
    rows.append(("1000", YEAR, 12, "999999", "P100", "999001", 1250.00))   # issue 5
    rows.append(("1000", YEAR, 12, "999999", "P100", "999002", -1250.00))  # nets to zero, still an orphan
    return rows


def load(con):
    for schema in ("legacy", "s4", "dims"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    ddl = ("(company_code VARCHAR, fiscal_year INTEGER, period INTEGER, gl_account VARCHAR, "
           "profit_center VARCHAR, document_number VARCHAR, amount_gc DECIMAL(18,2))")
    con.execute(f"CREATE OR REPLACE TABLE legacy.fact_gl_actual {ddl}")
    con.execute(f"CREATE OR REPLACE TABLE s4.fact_gl_actual {ddl}")
    legacy = _legacy_rows()
    con.executemany("INSERT INTO legacy.fact_gl_actual VALUES (?, ?, ?, ?, ?, ?, ?)", legacy)
    con.executemany("INSERT INTO s4.fact_gl_actual VALUES (?, ?, ?, ?, ?, ?, ?)", _new_rows(legacy))
    con.execute("CREATE OR REPLACE TABLE dims.dim_account (gl_account VARCHAR, account_name VARCHAR)")
    con.executemany("INSERT INTO dims.dim_account VALUES (?, ?)", [
        ("400000", "Revenue"), ("500000", "Cost of goods sold"),
        ("600000", "Personnel expenses"), ("610000", "Other operating expenses")])
