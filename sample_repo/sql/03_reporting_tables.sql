-- ============================================================
-- Reporting / Mart Layer
-- Final tables consumed by dashboards and regulatory reports
-- Reads from SQL intermediate views AND from int_python_customer_scores,
-- the table written by the Python step (sample_repo/python/transform_pipeline.py).
-- ============================================================

-- Regulatory KPI: Customer exposure report (BCBS 239 compliant)
-- Now enriched with Python-computed risk score and segment.
CREATE TABLE rpt_customer_exposure AS
SELECT
    ic.customer_id,
    ic.full_name,
    ic.customer_region,
    ic.account_id,
    ic.account_type,
    ic.account_currency,
    db.balance_date,
    db.daily_net_amount,
    db.daily_net_amount_usd,
    tr.risk_level,
    tr.is_anonymous,
    ps.risk_score                       AS python_risk_score,
    ps.segment                          AS python_risk_segment,
    SUM(db.daily_net_amount_usd) OVER (
        PARTITION BY ic.customer_id
        ORDER BY db.balance_date
    )                                   AS cumulative_exposure_usd
FROM int_customers ic
JOIN int_daily_balances db   ON db.account_id = ic.account_id
JOIN int_transaction_risk tr ON tr.account_id = ic.account_id
                             AND CAST(tr.transaction_date AS DATE) = db.balance_date
LEFT JOIN int_python_customer_scores ps ON ps.customer_id = ic.customer_id
                                       AND ps.account_id = ic.account_id;


-- Dashboard: Regional risk summary (rolls up Python segments too)
CREATE VIEW rpt_regional_risk_summary AS
SELECT
    ic.customer_region,
    tr.risk_level,
    ps.segment                           AS python_risk_segment,
    COUNT(DISTINCT tr.transaction_id)    AS transaction_count,
    SUM(tr.amount)                       AS total_amount,
    COUNT(DISTINCT ic.customer_id)       AS customer_count,
    AVG(ps.risk_score)                   AS avg_python_risk_score
FROM int_customers ic
JOIN int_transaction_risk tr            ON tr.account_id = ic.account_id
LEFT JOIN int_python_customer_scores ps  ON ps.customer_id = ic.customer_id
                                         AND ps.account_id = ic.account_id
GROUP BY
    ic.customer_region,
    tr.risk_level,
    ps.segment;


-- Customer scorecard view — purely a presentation layer over the Python output
CREATE VIEW rpt_customer_scorecard AS
SELECT
    ps.customer_id,
    ps.full_name,
    ps.customer_region,
    ps.account_id,
    ps.account_type,
    ps.total_net_usd,
    ps.balance_volatility_usd,
    ps.high_risk_txn_count,
    ps.anonymous_txn_count,
    ps.risk_score,
    ps.segment,
    ic.account_currency,
    ic.account_status
FROM int_python_customer_scores ps
JOIN int_customers ic ON ic.customer_id = ps.customer_id
                      AND ic.account_id = ps.account_id;
