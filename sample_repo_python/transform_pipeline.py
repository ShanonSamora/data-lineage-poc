"""
Python/Pandas transformation — risk scoring & customer segmentation.

This is the "Python in the middle" step of the hybrid pipeline:
  stg_*  -->  int_*  -->  THIS PYTHON STEP  -->  int_python_customer_scores  -->  rpt_*

Reads from SQL intermediate views, applies enrichment that's awkward in pure SQL
(numeric scoring formula + bucket assignment), and writes the result back as a
SQL table that downstream reporting views consume.

Lineage the LLM should extract:
  int_python_customer_scores             <-- READS_FROM --   int_customers
  int_python_customer_scores             <-- READS_FROM --   int_daily_balances
  int_python_customer_scores             <-- READS_FROM --   int_transaction_risk
  int_python_customer_scores.<column>    <-- DERIVES_FROM -- int_<view>.<column>
"""
import pandas as pd
from sqlalchemy import create_engine


def load_intermediate_views(conn) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Pull the three SQL intermediate views into memory.

    Reads:
      int_customers          (customer_id, full_name, customer_region, account_id, account_type, ...)
      int_daily_balances     (account_id, balance_date, daily_net_amount, daily_net_amount_usd)
      int_transaction_risk   (account_id, transaction_id, risk_level, is_anonymous, amount)
    """
    df_customers = pd.read_sql(
        "SELECT customer_id, full_name, customer_region, account_id, account_type, "
        "account_currency, account_status FROM int_customers",
        conn,
    )
    df_balances = pd.read_sql(
        "SELECT account_id, balance_date, daily_net_amount, daily_net_amount_usd "
        "FROM int_daily_balances",
        conn,
    )
    df_risk = pd.read_sql(
        "SELECT account_id, transaction_id, risk_level, is_anonymous, amount "
        "FROM int_transaction_risk",
        conn,
    )
    return df_customers, df_balances, df_risk


def aggregate_account_metrics(df_balances: pd.DataFrame, df_risk: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate balances and risk indicators per account.

    Output columns derive from:
      total_net_usd            <-- int_daily_balances.daily_net_amount_usd
      avg_daily_balance        <-- int_daily_balances.daily_net_amount
      balance_volatility_usd   <-- int_daily_balances.daily_net_amount_usd (std)
      high_risk_txn_count      <-- int_transaction_risk.risk_level
      anonymous_txn_count      <-- int_transaction_risk.is_anonymous
      txn_count                <-- int_transaction_risk.transaction_id (count)
    """
    balance_agg = (
        df_balances.groupby("account_id")
        .agg(
            total_net_usd=("daily_net_amount_usd", "sum"),
            avg_daily_balance=("daily_net_amount", "mean"),
            balance_volatility_usd=("daily_net_amount_usd", "std"),
        )
        .reset_index()
        .fillna({"balance_volatility_usd": 0.0})
    )

    risk_agg = (
        df_risk.groupby("account_id")
        .agg(
            high_risk_txn_count=("risk_level", lambda s: (s == "HIGH").sum()),
            anonymous_txn_count=("is_anonymous", "sum"),
            txn_count=("transaction_id", "count"),
        )
        .reset_index()
    )

    return balance_agg.merge(risk_agg, on="account_id", how="outer").fillna(0)


def compute_risk_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Composite risk score and segment label.

    The score combines volatility, high-risk txn ratio, and anonymous-counterparty
    ratio — the kind of weighted blend that's painful to maintain in pure SQL.

    Lineage:
      risk_score   <-- balance_volatility_usd, high_risk_txn_count, anonymous_txn_count, txn_count
      segment      <-- risk_score (bucket thresholds)
    """
    df = df.copy()
    safe_txn_count = df["txn_count"].clip(lower=1)
    high_risk_ratio = df["high_risk_txn_count"] / safe_txn_count
    anonymous_ratio = df["anonymous_txn_count"] / safe_txn_count

    df["risk_score"] = (
        (df["balance_volatility_usd"].clip(lower=0) / 10000.0) * 0.4
        + high_risk_ratio * 50.0
        + anonymous_ratio * 30.0
    ).round(2)

    df["segment"] = df["risk_score"].apply(
        lambda s: "HIGH_RISK" if s >= 40 else ("MEDIUM_RISK" if s >= 15 else "LOW_RISK")
    )
    return df


def build_customer_scores(
    df_customers: pd.DataFrame,
    account_metrics: pd.DataFrame,
) -> pd.DataFrame:
    """
    Join account-level metrics back to the customer dimension.

    Final output: one row per (customer_id, account_id) with risk metrics.
    Lineage:
      int_python_customer_scores.customer_id        <-- int_customers.customer_id
      int_python_customer_scores.full_name          <-- int_customers.full_name
      int_python_customer_scores.customer_region    <-- int_customers.customer_region
      int_python_customer_scores.account_id         <-- int_customers.account_id
      int_python_customer_scores.total_net_usd      <-- int_daily_balances.daily_net_amount_usd
      int_python_customer_scores.balance_volatility_usd  <-- int_daily_balances.daily_net_amount_usd
      int_python_customer_scores.high_risk_txn_count <-- int_transaction_risk.risk_level
      int_python_customer_scores.anonymous_txn_count <-- int_transaction_risk.is_anonymous
      int_python_customer_scores.risk_score          <-- computed from balances + risk
      int_python_customer_scores.segment             <-- derived from risk_score
    """
    scored = compute_risk_score(account_metrics)
    return df_customers.merge(scored, on="account_id", how="left")[
        [
            "customer_id",
            "full_name",
            "customer_region",
            "account_id",
            "account_type",
            "total_net_usd",
            "avg_daily_balance",
            "balance_volatility_usd",
            "high_risk_txn_count",
            "anonymous_txn_count",
            "txn_count",
            "risk_score",
            "segment",
        ]
    ]


def write_customer_scores(df_scores: pd.DataFrame, conn) -> None:
    """
    Persist results so downstream SQL reporting views can consume them.

    Lineage:
      int_python_customer_scores  <-- WRITES_TO -- (this function via df.to_sql)
    """
    df_scores.to_sql("int_python_customer_scores", conn, if_exists="replace", index=False)


def run_pipeline(database_url: str) -> None:
    """End-to-end pipeline: int_* views  ->  int_python_customer_scores table."""
    engine = create_engine(database_url)
    with engine.connect() as conn:
        df_customers, df_balances, df_risk = load_intermediate_views(conn)
        account_metrics = aggregate_account_metrics(df_balances, df_risk)
        df_scores = build_customer_scores(df_customers, account_metrics)
        write_customer_scores(df_scores, conn)
