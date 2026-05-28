-- ============================================================
-- Stored Procedures — harder for deterministic parsers
-- These demonstrate cases where LLM fallback adds value
-- ============================================================

CREATE OR REPLACE PROCEDURE sp_refresh_monthly_summary(
    p_year  INT,
    p_month INT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_start_date DATE;
    v_end_date   DATE;
BEGIN
    v_start_date := MAKE_DATE(p_year, p_month, 1);
    v_end_date   := v_start_date + INTERVAL '1 month' - INTERVAL '1 day';

    DELETE FROM rpt_monthly_summary
    WHERE report_month = v_start_date;

    INSERT INTO rpt_monthly_summary (
        report_month,
        customer_id,
        full_name,
        region,
        total_credits,
        total_debits,
        net_position_usd,
        max_risk_level,
        transaction_count
    )
    SELECT
        v_start_date                            AS report_month,
        ic.customer_id,
        ic.full_name,
        ic.customer_region                      AS region,
        SUM(CASE WHEN t.transaction_type = 'CREDIT'
                 THEN t.amount ELSE 0 END)     AS total_credits,
        SUM(CASE WHEN t.transaction_type = 'DEBIT'
                 THEN t.amount ELSE 0 END)     AS total_debits,
        SUM(db.daily_net_amount_usd)            AS net_position_usd,
        MAX(tr.risk_level)                      AS max_risk_level,
        COUNT(t.transaction_id)                 AS transaction_count
    FROM int_customers ic
    JOIN stg_transactions t         ON t.account_id = ic.account_id
    JOIN int_daily_balances db      ON db.account_id = ic.account_id
                                    AND db.balance_date = CAST(t.transaction_date AS DATE)
    JOIN int_transaction_risk tr    ON tr.transaction_id = t.transaction_id
    WHERE t.transaction_date BETWEEN v_start_date AND v_end_date
    GROUP BY ic.customer_id, ic.full_name, ic.customer_region;

    RAISE NOTICE 'Monthly summary refreshed for %', v_start_date;
END;
$$;
