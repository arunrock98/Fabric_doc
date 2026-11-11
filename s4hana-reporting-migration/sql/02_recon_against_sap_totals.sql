-- Permanent control: warehouse fact vs SAP control totals (after go-live)
-- S/4HANA: compare the reporting fact with the Universal Journal aggregated per
-- company, period and account. Leading ledger only (RLDNR = '0L' in most setups).
-- Replace raw.acdoca with the name of your replicated ACDOCA table.

SELECT
    COALESCE(s.company_code, f.company_code) AS company_code,
    COALESCE(s.period,       f.period)       AS period,
    COALESCE(s.gl_account,   f.gl_account)   AS gl_account,
    s.amount_gc                              AS sap_amount_gc,
    f.amount_gc                              AS fact_amount_gc,
    COALESCE(f.amount_gc, 0) - COALESCE(s.amount_gc, 0) AS diff_amount_gc
FROM (
        SELECT rbukrs AS company_code,
               poper  AS period,
               racct  AS gl_account,
               SUM(ksl) AS amount_gc            -- KSL = group currency in many setups; verify yours
        FROM raw.acdoca
        WHERE rldnr = '0L'
          AND gjahr = '2025'
        GROUP BY rbukrs, poper, racct
     ) s
FULL OUTER JOIN (
        SELECT company_code,
               LPAD(CAST(period AS VARCHAR), 3, '0') AS period,
               gl_account,
               SUM(amount_gc) AS amount_gc
        FROM s4.fact_gl_actual
        WHERE fiscal_year = 2025
        GROUP BY company_code, period, gl_account
     ) f
    ON  s.company_code = f.company_code
    AND s.period       = f.period
    AND s.gl_account   = f.gl_account
WHERE ABS(COALESCE(f.amount_gc, 0) - COALESCE(s.amount_gc, 0)) > 0.01
ORDER BY ABS(COALESCE(f.amount_gc, 0) - COALESCE(s.amount_gc, 0)) DESC;
