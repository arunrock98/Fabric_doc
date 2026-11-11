-- Reconciliation: legacy (ECC-based) vs new (S/4HANA-based) GL fact
-- Grain: company_code x fiscal_year x period x gl_account
-- Pattern: aggregate each side in an inline subquery, FULL OUTER JOIN, classify.
-- This is the SQL the validation agent generates from config/validation_rules.yml.

SELECT
    COALESCE(o.company_code, n.company_code) AS company_code,
    COALESCE(o.fiscal_year,  n.fiscal_year)  AS fiscal_year,
    COALESCE(o.period,       n.period)       AS period,
    COALESCE(o.gl_account,   n.gl_account)   AS gl_account,
    o.amount_gc                              AS legacy_amount_gc,
    n.amount_gc                              AS new_amount_gc,
    COALESCE(n.amount_gc, 0) - COALESCE(o.amount_gc, 0) AS diff_amount_gc,
    CASE
        WHEN o.company_code IS NULL THEN 'MISSING_IN_LEGACY'
        WHEN n.company_code IS NULL THEN 'MISSING_IN_NEW'
        WHEN ABS(COALESCE(n.amount_gc, 0) - COALESCE(o.amount_gc, 0)) > 0.01 THEN 'AMOUNT_MISMATCH'
        ELSE 'MATCH'
    END                                      AS status
FROM (
        SELECT company_code, fiscal_year, period, gl_account,
               SUM(amount_gc) AS amount_gc
        FROM legacy.fact_gl_actual
        WHERE fiscal_year = 2025
        GROUP BY company_code, fiscal_year, period, gl_account
     ) o
FULL OUTER JOIN (
        SELECT company_code, fiscal_year, period, gl_account,
               SUM(amount_gc) AS amount_gc
        FROM s4.fact_gl_actual
        WHERE fiscal_year = 2025
        GROUP BY company_code, fiscal_year, period, gl_account
     ) n
    ON  o.company_code = n.company_code
    AND o.fiscal_year  = n.fiscal_year
    AND o.period       = n.period
    AND o.gl_account   = n.gl_account
ORDER BY ABS(COALESCE(n.amount_gc, 0) - COALESCE(o.amount_gc, 0)) DESC;
