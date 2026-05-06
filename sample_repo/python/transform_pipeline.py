"""
Sample Python/Pandas transformation pipeline.
This demonstrates the kind of code an LLM can interpret
but a pure SQL parser cannot.
"""
import pandas as pd


def load_transactions(path: str) -> pd.DataFrame:
    """Load raw transactions from CSV."""
    return pd.read_csv(path)


def enrich_transactions(df_txn: pd.DataFrame, df_rates: pd.DataFrame) -> pd.DataFrame:
    """Enrich transactions with USD-converted amounts."""
    df_txn["transaction_date"] = pd.to_datetime(df_txn["transaction_date"])
    df_txn["txn_date"] = df_txn["transaction_date"].dt.date

    df_rates["rate_date"] = pd.to_datetime(df_rates["rate_date"]).dt.date

    merged = df_txn.merge(
        df_rates,
        left_on=["txn_date", "currency"],
        right_on=["rate_date", "from_currency"],
        how="left",
    )

    merged["amount_usd"] = merged["amount"] * merged["rate"].fillna(1.0)
    return merged


def compute_customer_summary(df_enriched: pd.DataFrame, df_customers: pd.DataFrame) -> pd.DataFrame:
    """Aggregate by customer — produces the same output as rpt_customer_exposure."""
    summary = (
        df_enriched.groupby("account_id")
        .agg(
            total_amount=("amount", "sum"),
            total_amount_usd=("amount_usd", "sum"),
            txn_count=("transaction_id", "count"),
            max_amount=("amount", "max"),
        )
        .reset_index()
    )

    result = summary.merge(df_customers, on="account_id", how="left")
    result["risk_flag"] = result["max_amount"].apply(lambda x: "HIGH" if x > 50000 else "NORMAL")
    return result[
        ["customer_id", "full_name", "account_id", "total_amount", "total_amount_usd", "txn_count", "risk_flag"]
    ]


def build_dynamic_query(table_name: str, date_col: str, start: str, end: str) -> str:
    """
    Builds a SQL query dynamically — impossible for static SQL parser,
    but an LLM can interpret the resulting lineage.
    """
    return f"""
        SELECT
            account_id,
            SUM(amount) AS total_amount,
            COUNT(*)    AS txn_count
        FROM {table_name}
        WHERE {date_col} BETWEEN '{start}' AND '{end}'
        GROUP BY account_id
    """
