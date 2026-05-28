-- ============================================================
-- Sample Data Warehouse: Source tables (staging layer)
-- Simulates raw data arriving from operational systems
-- ============================================================

CREATE TABLE stg_customers (
    customer_id     INT PRIMARY KEY,
    first_name      VARCHAR(100),
    last_name       VARCHAR(100),
    email           VARCHAR(255),
    phone           VARCHAR(50),
    country_code    CHAR(2),
    created_at      TIMESTAMP,
    updated_at      TIMESTAMP
);

CREATE TABLE stg_accounts (
    account_id      INT PRIMARY KEY,
    customer_id     INT,
    account_type    VARCHAR(50),
    currency        CHAR(3),
    opened_date     DATE,
    status          VARCHAR(20),
    branch_id       INT
);

CREATE TABLE stg_transactions (
    transaction_id  BIGINT PRIMARY KEY,
    account_id      INT,
    transaction_date TIMESTAMP,
    amount          DECIMAL(18,2),
    currency        CHAR(3),
    transaction_type VARCHAR(20),
    description     VARCHAR(500),
    counterparty_id INT
);

CREATE TABLE stg_exchange_rates (
    rate_date       DATE,
    from_currency   CHAR(3),
    to_currency     CHAR(3),
    rate            DECIMAL(18,8),
    PRIMARY KEY (rate_date, from_currency, to_currency)
);

CREATE TABLE stg_branches (
    branch_id       INT PRIMARY KEY,
    branch_name     VARCHAR(200),
    region          VARCHAR(100),
    country_code    CHAR(2)
);
