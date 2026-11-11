-- Results table the agent writes to when RESULTS_TABLE is set (Snowflake syntax).
-- Keeps a full audit trail of every run for Finance sign-off and trend reporting.

CREATE SCHEMA IF NOT EXISTS qa;

CREATE TABLE IF NOT EXISTS qa.validation_results (
    run_id     VARCHAR(16)   NOT NULL,
    run_ts     TIMESTAMP_NTZ NOT NULL,
    rule_id    VARCHAR(100)  NOT NULL,
    status     VARCHAR(30)   NOT NULL,   -- MISSING_IN_NEW, MISSING_IN_LEGACY, AMOUNT_MISMATCH, COUNT_MISMATCH, ORPHAN_KEY
    row_json   VARCHAR       NOT NULL,   -- the mismatch row (grain keys, legacy/new values, diff)
    triage     VARCHAR(20),              -- DATA | MAPPING | EXPECTED_CHANGE, filled by the team
    owner      VARCHAR(100),
    comment    VARCHAR
);

-- Open issues per rule and run, for a Power BI "migration readiness" page
-- SELECT rule_id, run_ts, COUNT(*) AS open_mismatches
-- FROM qa.validation_results
-- WHERE triage IS NULL
-- GROUP BY rule_id, run_ts
-- ORDER BY run_ts DESC;
