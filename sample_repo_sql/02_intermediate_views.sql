-- ============================================================
-- Intermediate / Business Logic Layer
-- Joins, aggregations, and business rules applied
-- ============================================================

-- Enriched customer view with full name and region
CREATE VIEW int_customers AS
SELECT
    c.customer_id,
    CONCAT(c.first_name, ' ', c.last_name)  AS full_name,
    c.email,
    c.country_code,
    b.region                                  AS customer_region,
    a.account_id,
    a.account_type,
    a.currency                                AS account_currency,
    a.status                                  AS account_status
FROM stg_customers c
JOIN stg_accounts a ON a.customer_id = c.customer_id
JOIN stg_branches b ON b.branch_id = a.branch_id;


-- Daily account balances with USD normalization
CREATE VIEW int_daily_balances AS
SELECT
    t.account_id,
    CAST(t.transaction_date AS DATE)            AS balance_date,
    t.currency                                  AS original_currency,
    SUM(
        CASE
            WHEN t.transaction_type = 'CREDIT' THEN t.amount
            WHEN t.transaction_type = 'DEBIT'  THEN -t.amount
            ELSE 0
        END
    )                                           AS daily_net_amount,
    ROUND(SUM(
        CASE
            WHEN t.transaction_type = 'CREDIT' THEN t.amount * er.rate
            WHEN t.transaction_type = 'DEBIT'  THEN -t.amount * er.rate
            ELSE 0
        END
    ), 2)                                       AS daily_net_amount_usd
FROM stg_transactions t
LEFT JOIN stg_exchange_rates er
    ON er.rate_date = CAST(t.transaction_date AS DATE)
    AND er.from_currency = t.currency
    AND er.to_currency = 'USD'
GROUP BY
    t.account_id,
    CAST(t.transaction_date AS DATE),
    t.currency;


-- Transaction risk scoring
CREATE VIEW int_transaction_risk AS
SELECT
    t.transaction_id,
    t.account_id,
    t.amount,
    t.currency,
    t.transaction_date,
    t.transaction_type,
    t.counterparty_id,
    CASE
        WHEN t.amount > 50000                   THEN 'HIGH'
        WHEN t.amount > 10000                   THEN 'MEDIUM'
        ELSE 'LOW'
    END                                         AS risk_level,
    CASE
        WHEN t.counterparty_id IS NULL          THEN TRUE
        ELSE FALSE
    END                                         AS is_anonymous
FROM stg_transactions t;
