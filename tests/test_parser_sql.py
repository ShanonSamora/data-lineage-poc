"""Tests for the deterministic SQL parser (parser_sql.py).

Each test calls `parse_sql_file` with an in-memory SQL snippet (sql_text= argument)
and inspects the resulting LineageGraph for the expected nodes and edges.
"""
from pathlib import Path

from src.models import EdgeType, NodeType
from src.parser_sql import parse_sql_file


def _edges(graph, edge_type: EdgeType) -> list:
    return [e for e in graph.edges if e.edge_type == edge_type]


def _has_derives(graph, target_col: str, source_col: str) -> bool:
    return any(
        e.source_id == target_col and e.target_id == source_col
        for e in graph.edges
        if e.edge_type == EdgeType.DERIVES_FROM
    )


def test_plain_create_table_extracts_columns_only():
    """A plain CREATE TABLE produces COLUMN + HAS_COLUMN nodes but no DERIVES_FROM."""
    sql = """
    CREATE TABLE stg_customers (
        customer_id INT PRIMARY KEY,
        first_name VARCHAR(100),
        email VARCHAR(255)
    );
    """
    g = parse_sql_file(Path("test.sql"), sql_text=sql)

    table_node = next((n for n in g.nodes if n.id == "stg_customers"), None)
    assert table_node is not None
    assert table_node.node_type == NodeType.TABLE

    col_ids = {n.id for n in g.nodes if n.node_type == NodeType.COLUMN}
    assert "stg_customers.customer_id" in col_ids
    assert "stg_customers.first_name" in col_ids
    assert "stg_customers.email" in col_ids

    # No DERIVES_FROM because there's no SELECT source
    assert _edges(g, EdgeType.DERIVES_FROM) == []


def test_create_view_extracts_column_derivation():
    """CREATE VIEW ... AS SELECT generates DERIVES_FROM edges per output column."""
    sql = """
    CREATE VIEW v_customers AS
    SELECT customer_id, email FROM stg_customers;
    """
    g = parse_sql_file(Path("test.sql"), sql_text=sql)

    view = next((n for n in g.nodes if n.id == "v_customers"), None)
    assert view is not None and view.node_type == NodeType.VIEW

    assert _has_derives(g, "v_customers.customer_id", "stg_customers.customer_id")
    assert _has_derives(g, "v_customers.email", "stg_customers.email")


def test_concat_lists_all_source_columns():
    """CONCAT(a, b) -> output derives from BOTH input columns."""
    sql = """
    CREATE VIEW v_full AS
    SELECT CONCAT(first_name, ' ', last_name) AS full_name
    FROM stg_customers;
    """
    g = parse_sql_file(Path("test.sql"), sql_text=sql)

    assert _has_derives(g, "v_full.full_name", "stg_customers.first_name")
    assert _has_derives(g, "v_full.full_name", "stg_customers.last_name")


def test_join_resolves_aliases():
    """Aliases in JOINs are resolved to fully-qualified table names."""
    sql = """
    CREATE VIEW v_joined AS
    SELECT c.customer_id, a.account_type
    FROM stg_customers c
    JOIN stg_accounts a ON a.customer_id = c.customer_id;
    """
    g = parse_sql_file(Path("test.sql"), sql_text=sql)

    assert _has_derives(g, "v_joined.customer_id", "stg_customers.customer_id")
    assert _has_derives(g, "v_joined.account_type", "stg_accounts.account_type")


def test_create_view_emits_reads_from():
    """CREATE VIEW emits READS_FROM edges to every source table."""
    sql = """
    CREATE VIEW v_joined AS
    SELECT c.customer_id, a.account_type
    FROM stg_customers c
    JOIN stg_accounts a ON a.customer_id = c.customer_id;
    """
    g = parse_sql_file(Path("test.sql"), sql_text=sql)

    reads = {(e.source_id, e.target_id) for e in _edges(g, EdgeType.READS_FROM)}
    assert ("v_joined", "stg_customers") in reads
    assert ("v_joined", "stg_accounts") in reads


def test_case_expression_derives_from_all_branches():
    """CASE expression: output column derives from every column referenced in any branch."""
    sql = """
    CREATE VIEW v_risk AS
    SELECT
      CASE
        WHEN amount > 50000 THEN 'HIGH'
        WHEN counterparty_id IS NULL THEN 'MEDIUM'
        ELSE 'LOW'
      END AS risk_level
    FROM stg_transactions;
    """
    g = parse_sql_file(Path("test.sql"), sql_text=sql)

    assert _has_derives(g, "v_risk.risk_level", "stg_transactions.amount")
    assert _has_derives(g, "v_risk.risk_level", "stg_transactions.counterparty_id")


def test_window_function_lineage():
    """SUM(...) OVER (...) — output column derives from the windowed column."""
    sql = """
    CREATE TABLE rpt_cumulative AS
    SELECT
      customer_id,
      SUM(amount) OVER (PARTITION BY customer_id ORDER BY transaction_date) AS running_total
    FROM stg_transactions;
    """
    g = parse_sql_file(Path("test.sql"), sql_text=sql)

    assert _has_derives(g, "rpt_cumulative.running_total", "stg_transactions.amount")


def test_create_table_as_select_emits_defined_in():
    """CTAS produces a DEFINED_IN edge from the table to the file."""
    sql = "CREATE TABLE t AS SELECT id FROM src;"
    g = parse_sql_file(Path("schema.sql"), sql_text=sql)

    defined = [e for e in g.edges if e.edge_type == EdgeType.DEFINED_IN]
    assert any(e.source_id == "t" and "schema.sql" in e.target_id for e in defined)


def test_insert_into_select_emits_lineage():
    """INSERT INTO ... SELECT generates DERIVES_FROM and READS_FROM edges."""
    sql = """
    INSERT INTO rpt_summary (customer_id, total)
    SELECT customer_id, SUM(amount) AS total
    FROM stg_transactions
    GROUP BY customer_id;
    """
    g = parse_sql_file(Path("test.sql"), sql_text=sql)

    assert _has_derives(g, "rpt_summary.customer_id", "stg_transactions.customer_id")
    assert _has_derives(g, "rpt_summary.total", "stg_transactions.amount")
    reads = {(e.source_id, e.target_id) for e in _edges(g, EdgeType.READS_FROM)}
    assert ("rpt_summary", "stg_transactions") in reads


def test_multiple_statements_in_one_file():
    """Multiple ;-separated statements in one file all produce their edges."""
    sql = """
    CREATE TABLE a (id INT);
    CREATE TABLE b (id INT);
    CREATE VIEW v AS SELECT a.id FROM a JOIN b ON a.id = b.id;
    """
    g = parse_sql_file(Path("test.sql"), sql_text=sql)

    tables = {n.id for n in g.nodes if n.node_type == NodeType.TABLE}
    assert {"a", "b"}.issubset(tables)
    view = next((n for n in g.nodes if n.id == "v"), None)
    assert view is not None and view.node_type == NodeType.VIEW
    assert _has_derives(g, "v.id", "a.id")


def test_confidence_is_one_for_deterministic_edges():
    """All DERIVES_FROM edges from the SQL parser carry confidence 1.0."""
    sql = "CREATE VIEW v AS SELECT id FROM t;"
    g = parse_sql_file(Path("test.sql"), sql_text=sql)

    for e in _edges(g, EdgeType.DERIVES_FROM):
        assert e.confidence == 1.0
