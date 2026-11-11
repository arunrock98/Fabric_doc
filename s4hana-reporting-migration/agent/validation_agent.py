"""
Finance migration validation agent.

Compares a legacy finance build (e.g. ECC-based) with a new build (e.g. S/4HANA-based)
rule by rule, classifies every difference, explains likely root causes, and writes a
report for Finance sign-off.

Usage
    python agent/validation_agent.py --config config/validation_rules.yml --demo
    python agent/validation_agent.py --config config/validation_rules.yml          # Snowflake

Environment (Snowflake mode)
    SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD (or SNOWFLAKE_AUTHENTICATOR),
    SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE, SNOWFLAKE_ROLE
Optional
    LLM_PROVIDER (anthropic | openai), LLM_API_KEY, LLM_MODEL, LLM_API_URL
    TEAMS_WEBHOOK_URL   post a run summary to a Microsoft Teams channel
    RESULTS_TABLE       write row-level results back to the warehouse (e.g. qa.validation_results)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import statistics
import sys
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


# --------------------------------------------------------------------------- helpers
def ident(name: str) -> str:
    """Allow only plain identifiers (optionally schema-qualified) in generated SQL."""
    if not IDENT.match(name):
        raise ValueError(f"Invalid identifier in config: {name!r}")
    return name


@dataclass
class RuleResult:
    rule: dict
    rows: list[dict] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    ai_explanation: str | None = None

    @property
    def mismatches(self) -> list[dict]:
        return [r for r in self.rows if r["status"] != "MATCH"]

    @property
    def passed(self) -> bool:
        return not self.mismatches


# --------------------------------------------------------------------------- warehouse
class Warehouse:
    """Thin wrapper so the agent runs the same way on DuckDB (demo) and Snowflake."""

    def __init__(self, demo: bool):
        self.demo = demo
        if demo:
            import duckdb

            self.con = duckdb.connect()
            self.paramstyle = "?"
        else:
            import snowflake.connector  # pip install snowflake-connector-python

            self.con = snowflake.connector.connect(
                account=os.environ["SNOWFLAKE_ACCOUNT"],
                user=os.environ["SNOWFLAKE_USER"],
                password=os.environ.get("SNOWFLAKE_PASSWORD"),
                authenticator=os.environ.get("SNOWFLAKE_AUTHENTICATOR", "snowflake"),
                warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
                database=os.environ.get("SNOWFLAKE_DATABASE"),
                role=os.environ.get("SNOWFLAKE_ROLE"),
            )
            self.paramstyle = "%s"

    def query(self, sql: str) -> list[dict]:
        cur = self.con.cursor()
        cur.execute(sql)
        cols = [c[0].lower() for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def execute(self, sql: str, params=None):
        cur = self.con.cursor()
        cur.execute(sql, params or [])

    def insert_many(self, table: str, columns: list[str], rows: list[tuple]):
        if not rows:
            return
        ph = ", ".join([self.paramstyle] * len(columns))
        sql = f"INSERT INTO {ident(table)} ({', '.join(columns)}) VALUES ({ph})"
        self.con.cursor().executemany(sql, rows)


# --------------------------------------------------------------------------- SQL builders
def build_reconcile_sql(rule: dict, run: dict) -> str:
    """Aggregate both builds at the rule's grain and compare them (inline subqueries)."""
    grain = [ident(g) for g in rule["grain"]]
    measures = [ident(m) for m in rule.get("measures", [])]
    count = rule.get("count_lines", False)
    where = f"WHERE {rule['filter'].format(**run)}" if rule.get("filter") else ""

    def side(table: str) -> str:
        aggs = [f"SUM({m}) AS {m}" for m in measures]
        if count:
            aggs.append("COUNT(*) AS line_count")
        return (
            f"SELECT {', '.join(grain)}, {', '.join(aggs)} "
            f"FROM {ident(table)} {where} GROUP BY {', '.join(grain)}"
        )

    select_keys = [f"COALESCE(o.{g}, n.{g}) AS {g}" for g in grain]
    select_vals = []
    for m in measures:
        select_vals += [f"o.{m} AS legacy_{m}", f"n.{m} AS new_{m}"]
    if count:
        select_vals += ["o.line_count AS legacy_line_count", "n.line_count AS new_line_count"]
    select_vals += [
        f"CASE WHEN o.{grain[0]} IS NULL THEN 1 ELSE 0 END AS missing_in_legacy",
        f"CASE WHEN n.{grain[0]} IS NULL THEN 1 ELSE 0 END AS missing_in_new",
    ]
    join = " AND ".join(f"o.{g} = n.{g}" for g in grain)
    return (
        f"SELECT {', '.join(select_keys + select_vals)} "
        f"FROM ({side(rule['legacy_table'])}) o "
        f"FULL OUTER JOIN ({side(rule['new_table'])}) n ON {join} "
        f"ORDER BY {', '.join(grain)}"
    )


def build_orphan_sql(rule: dict, run: dict) -> str:
    where = f"AND {rule['filter'].format(**run)}" if rule.get("filter") else ""
    fk, dk = ident(rule["fact_key"]), ident(rule["dim_key"])
    return (
        f"SELECT f.{fk} AS {fk}, f.line_count "
        f"FROM (SELECT {fk}, COUNT(*) AS line_count FROM {ident(rule['fact_table'])} "
        f"WHERE 1 = 1 {where} GROUP BY {fk}) f "
        f"LEFT JOIN (SELECT DISTINCT {dk} FROM {ident(rule['dim_table'])}) d ON f.{fk} = d.{dk} "
        f"WHERE d.{dk} IS NULL ORDER BY f.{fk}"
    )


# --------------------------------------------------------------------------- classification
def classify(row: dict, rule: dict, defaults: dict) -> dict:
    tol_abs = float(rule.get("tolerance_abs", defaults.get("tolerance_abs", 0.01)))
    tol_pct = float(rule.get("tolerance_pct", defaults.get("tolerance_pct", 0.0)))
    if row["missing_in_new"]:
        row["status"] = "MISSING_IN_NEW"
    elif row["missing_in_legacy"]:
        row["status"] = "MISSING_IN_LEGACY"
    else:
        row["status"] = "MATCH"
        for m in rule.get("measures", []):
            old, new = float(row[f"legacy_{m}"] or 0), float(row[f"new_{m}"] or 0)
            diff = new - old
            row[f"diff_{m}"] = round(diff, 2)
            if abs(diff) > max(tol_abs, abs(old) * tol_pct):
                row["status"] = "AMOUNT_MISMATCH"
        if row["status"] == "MATCH" and rule.get("count_lines"):
            if row["legacy_line_count"] != row["new_line_count"]:
                row["status"] = "COUNT_MISMATCH"
    for m in rule.get("measures", []):
        row.setdefault(f"diff_{m}", round(float(row[f"new_{m}"] or 0) - float(row[f"legacy_{m}"] or 0), 2))
    return row


# --------------------------------------------------------------------------- explanations
def explain_heuristically(res: RuleResult) -> list[str]:
    """Rule-based root-cause hints. Fast, offline, and a good prompt for the LLM step."""
    rule, mism = res.rule, res.mismatches
    findings: list[str] = []
    if not mism or rule.get("type") != "reconcile" or not rule.get("measures"):
        return findings
    m = rule["measures"][0]
    grain = rule["grain"]

    # 1. Special periods missing
    missing_new = [r for r in mism if r["status"] == "MISSING_IN_NEW"]
    if missing_new and "period" in grain:
        special = [r for r in missing_new if int(r["period"]) >= 13]
        if special and len(special) == len(missing_new):
            periods = sorted({int(r["period"]) for r in special})
            findings.append(
                f"All {len(special)} rows missing in the new build are in special period(s) "
                f"{periods}. Likely cause: the extraction only selects periods 1-12. "
                f"Check the period filter of the new source layer."
            )

    amt = [r for r in mism if r["status"] == "AMOUNT_MISMATCH"]

    # 2. Sign flipped: new is approximately the negative of legacy
    flips = [r for r in amt if r[f"legacy_{m}"] and abs(float(r[f"new_{m}"]) + float(r[f"legacy_{m}"])) <= 0.01 * abs(float(r[f"legacy_{m}"]))]
    if flips:
        keys = sorted({str(r.get("gl_account", r.get(grain[-1]))) for r in flips})
        findings.append(
            f"{len(flips)} row(s) where the new amount is the exact negative of legacy "
            f"(keys: {', '.join(keys[:5])}). Likely cause: debit/credit sign applied twice, "
            f"since S/4HANA Universal Journal amounts are already signed."
        )

    # 3. Constant ratio per company: currency type / FX
    if "company_code" in grain:
        by_co: dict[str, list[float]] = {}
        for r in amt:
            if r in flips or not r[f"legacy_{m}"]:
                continue
            by_co.setdefault(str(r["company_code"]), []).append(float(r[f"new_{m}"]) / float(r[f"legacy_{m}"]))
        for co, ratios in by_co.items():
            if len(ratios) >= 3 and statistics.pstdev(ratios) < 0.01:
                avg = statistics.mean(ratios)
                if 1.9 <= avg <= 2.1:
                    findings.append(
                        f"Company {co}: new amounts are about 2x legacy on {len(ratios)} rows. "
                        f"Likely cause: duplicated lines (non-leading ledger included or a fan-out join)."
                    )
                else:
                    findings.append(
                        f"Company {co}: new/legacy ratio is a constant {avg:.4f} on {len(ratios)} rows. "
                        f"Likely cause: wrong currency field (e.g. local instead of group currency)."
                    )

    # 4. Reassignment: differences cancel out at a coarser grain
    if len(grain) > 1 and amt:
        parent = grain[:-1]
        groups: dict[tuple, float] = {}
        counts: dict[tuple, int] = {}
        for r in mism:
            k = tuple(r[g] for g in parent)
            groups[k] = groups.get(k, 0.0) + float(r[f"diff_{m}"])
            counts[k] = counts.get(k, 0) + 1
        netted = [k for k, v in groups.items() if abs(v) <= 0.01 and counts[k] >= 2]
        if netted:
            findings.append(
                f"{len(netted)} group(s) where differences net to zero across '{grain[-1]}' "
                f"(e.g. {dict(zip(parent, netted[0]))}). Likely cause: values moved between "
                f"{grain[-1]} members, i.e. a derivation or mapping change. If this reflects new "
                f"S/4HANA derivation logic, document it as an expected change for Finance approval."
            )

    # 5. Counts differ but amounts match
    cnt = [r for r in mism if r["status"] == "COUNT_MISMATCH"]
    if cnt:
        findings.append(
            f"{len(cnt)} row(s) with equal amounts but different line counts. Likely cause: "
            f"line granularity changed (e.g. lines split or summarised); check joins for duplicates."
        )
    return findings


def explain_with_llm(res: RuleResult) -> str | None:
    """Optional: ask an LLM for a plain-language explanation. Sends aggregates only."""
    key = os.environ.get("LLM_API_KEY")
    if not key or res.passed:
        return None
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    sample = [{k: (str(v) if isinstance(v, (dt.date, dt.datetime)) else v) for k, v in r.items()} for r in res.mismatches[:20]]
    prompt = (
        "You are a senior SAP finance data engineer validating an ECC to S/4HANA reporting migration. "
        "Below is one reconciliation rule, aggregated mismatch rows (no line-item data) and rule-based findings. "
        "Explain the most likely root causes in plain business language for a controller, "
        "classify each as DATA, MAPPING or EXPECTED CHANGE, and propose the next check. Max 150 words.\n\n"
        f"Rule: {json.dumps({k: res.rule[k] for k in ('id', 'description', 'grain', 'measures') if k in res.rule})}\n"
        f"Mismatches (first 20 of {len(res.mismatches)}): {json.dumps(sample, default=str)}\n"
        f"Rule-based findings: {json.dumps(res.findings)}"
    )
    try:
        if provider == "anthropic":
            url = os.environ.get("LLM_API_URL", "https://api.anthropic.com/v1/messages")
            body = {"model": os.environ.get("LLM_MODEL", "claude-sonnet-5-5"), "max_tokens": 400,
                    "messages": [{"role": "user", "content": prompt}]}
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        else:
            url = os.environ.get("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
            body = {"model": os.environ.get("LLM_MODEL", "gpt-4o-mini"), "max_tokens": 400,
                    "messages": [{"role": "user", "content": prompt}]}
            headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        if provider == "anthropic":
            return "".join(b.get("text", "") for b in data.get("content", []))
        return data["choices"][0]["message"]["content"]
    except Exception as exc:  # the run must never fail because the LLM is unavailable
        return f"(LLM explanation unavailable: {exc})"


# --------------------------------------------------------------------------- reporting
def fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def write_report(results: list[RuleResult], cfg: dict, out_dir: Path, run_id: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    top_n = int(cfg.get("defaults", {}).get("top_n", 10))
    lines = [
        f"# Validation report: {cfg['run'].get('name', 'migration')}",
        "",
        f"Run id `{run_id}` · {dt.datetime.now():%Y-%m-%d %H:%M} · fiscal year {cfg['run'].get('fiscal_year')}",
        "",
        "## Summary",
        "",
        "| Rule | Severity | Rows compared | Mismatches | Result |",
        "|---|---|---|---|---|",
    ]
    defaults = cfg.get("defaults", {})
    for r in results:
        sev = r.rule.get("severity", defaults.get("severity", "critical"))
        lines.append(f"| `{r.rule['id']}` | {sev} | {len(r.rows)} | {len(r.mismatches)} | {'✅ PASS' if r.passed else '❌ FAIL'} |")
    for r in results:
        lines += ["", f"## `{r.rule['id']}`", "", r.rule.get("description", ""), ""]
        if r.passed:
            lines.append("No differences within tolerance.")
            continue
        if r.findings:
            lines.append("**Likely root causes (rule-based):**")
            lines += [f"- {f}" for f in r.findings]
            lines.append("")
        if r.ai_explanation:
            lines += ["**AI explanation:**", "", r.ai_explanation.strip(), ""]
        cols = [c for c in r.mismatches[0].keys() if c not in ("missing_in_new", "missing_in_legacy")]
        lines += [f"**Top {min(top_n, len(r.mismatches))} of {len(r.mismatches)} mismatches:**", "",
                  "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        ranked = sorted(r.mismatches, key=lambda x: -abs(float(next((x[k] for k in x if k.startswith("diff_")), 0) or 0)))
        for row in ranked[:top_n]:
            lines.append("| " + " | ".join(fmt(row[c]) for c in cols) + " |")
    lines += ["", "---", "Classify every mismatch as **DATA**, **MAPPING** or **EXPECTED CHANGE** before sign-off."]
    path = out_dir / "validation_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")

    with open(out_dir / "validation_results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["run_id", "rule_id", "status", "row_json"])
        for r in results:
            for row in r.mismatches:
                w.writerow([run_id, r.rule["id"], row["status"], json.dumps(row, default=str)])
    return path


def post_to_teams(results: list[RuleResult], cfg: dict, report: Path):
    url = os.environ.get("TEAMS_WEBHOOK_URL")
    if not url:
        return
    failed = [r.rule["id"] for r in results if not r.passed]
    text = (f"**{cfg['run'].get('name')}**: {len(results) - len(failed)}/{len(results)} rules passed."
            + (f" Failed: {', '.join(failed)}." if failed else "") + f" Report: {report}")
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), headers={"content-type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as exc:
        print(f"Teams notification failed: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------- main
def run(config_path: str, demo: bool, out_dir: str) -> int:
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    defaults, run_params = cfg.get("defaults", {}), cfg.get("run", {})
    wh = Warehouse(demo)
    if demo:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))
        from load_demo_data import load  # noqa: E402

        load(wh.con)

    run_id = uuid.uuid4().hex[:8]
    results: list[RuleResult] = []
    for rule in cfg["rules"]:
        res = RuleResult(rule)
        if rule["type"] == "reconcile":
            res.rows = [classify(r, rule, defaults) for r in wh.query(build_reconcile_sql(rule, run_params))]
        elif rule["type"] == "orphan_keys":
            res.rows = [dict(r, status="ORPHAN_KEY") for r in wh.query(build_orphan_sql(rule, run_params))]
            if res.rows:
                res.findings.append(
                    f"{len(res.rows)} {rule['fact_key']} value(s) in {rule['fact_table']} have no member in "
                    f"{rule['dim_table']}. Reports will show them as blank/unknown; extend the dimension or fix the mapping.")
        else:
            raise ValueError(f"Unknown rule type: {rule['type']}")
        res.findings += explain_heuristically(res)
        res.ai_explanation = explain_with_llm(res)
        results.append(res)
        print(f"{'PASS' if res.passed else 'FAIL'}  {rule['id']:<28} rows={len(res.rows):<5} mismatches={len(res.mismatches)}")

    report = write_report(results, cfg, Path(out_dir), run_id)

    table = os.environ.get("RESULTS_TABLE")
    if table and not demo:
        wh.insert_many(table, ["run_id", "run_ts", "rule_id", "status", "row_json"],
                       [(run_id, dt.datetime.now(), r.rule["id"], row["status"], json.dumps(row, default=str))
                        for r in results for row in r.mismatches])
    post_to_teams(results, cfg, report)
    print(f"\nReport written to {report}")

    critical_failed = [r for r in results if not r.passed
                       and r.rule.get("severity", defaults.get("severity", "critical")) == "critical"]
    return 1 if critical_failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Finance migration validation agent")
    ap.add_argument("--config", default="config/validation_rules.yml")
    ap.add_argument("--demo", action="store_true", help="run on built-in sample data (DuckDB)")
    ap.add_argument("--out", default="output")
    a = ap.parse_args()
    sys.exit(run(a.config, a.demo, a.out))
