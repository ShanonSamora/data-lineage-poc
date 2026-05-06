-- ============================================================
-- Reporting / Mart Layer
-- Final tables consumed by dashboards and regulatory reports
-- ============================================================

-- Regulatory KPI: Customer exposure report (BCBS 239 compliant)
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
    SUM(db.daily_net_amount_usd) OVER (
        PARTITION BY ic.customer_id
        ORDER BY db.balance_date
    )                               AS cumulative_exposure_usd
FROM int_customers ic
JOIN int_daily_balances db   ON db.account_id = ic.account_id
JOIN int_transaction_risk tr ON tr.account_id = ic.account_id
                             AND CAST(tr.transaction_date AS DATE) = db.balance_date;


-- Dashboard: Regional risk summary
CREATE VIEW rpt_regional_risk_summary AS
SELECT
    ic.customer_region,
    tr.risk_level,
    COUNT(DISTINCT tr.transaction_id)   AS transaction_count,
    SUM(tr.amount)                       AS total_amount,
    COUNT(DISTINCT ic.customer_id)       AS customer_count
FROM int_customers ic
JOIN int_transaction_risk tr ON tr.account_id = ic.account_id
GROUP BY
    ic.customer_region,
    tr.risk_level;
