"""
Deterministic SQL lineage parser using sqlglot.

Extracts column-level lineage from standard SQL statements:
- CREATE TABLE ... AS SELECT
- CREATE VIEW ... AS SELECT
- INSERT INTO ... SELECT
- Plain SELECT (with CTEs, JOINs, sub-queries, CASE, window functions)

Returns a LineageGraph with nodes (tables, columns) and edges (DERIVES_FROM, HAS_COLUMN, etc.).
"""
from __future__ import annotations

import logging
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from src.models import (
    EdgeType,
    LineageEdge,
    LineageGraph,
    LineageNode,
    NodeType,
)

logger = logging.getLogger(__name__)


def _safe_qualify(expression: exp.Expression) -> exp.Expression:
    """Attempt to qualify columns; return original on failure."""
    try:
        return qualify(expression, dialect="postgres")
    except Exception:
        return expression


def _extract_source_columns(select_expr: exp.Select) -> list[tuple[str, str, str]]:
    """
    Walk a SELECT and return (target_alias, source_table, source_column) tuples.
    """
    results: list[tuple[str, str, str]] = []

    for projection in select_expr.expressions:
        alias = ""
        if isinstance(projection, exp.Alias):
            alias = projection.alias
            inner = projection.this
        else:
            inner = projection
            if isinstance(inner, exp.Column):
                alias = inner.name

        # Collect all Column references inside this projection
        for col in inner.find_all(exp.Column):
            table = col.table or ""
            column_name = col.name
            results.append((alias or column_name, table, column_name))

    return results


def parse_sql_file(file_path: str | Path, sql_text: str | None = None) -> LineageGraph:
    """
    Parse a SQL file and extract column-level lineage.

    Args:
        file_path: Path to the SQL file (used for metadata).
        sql_text: Raw SQL content. If None, reads from file_path.

    Returns:
        LineageGraph with extracted nodes and edges.
    """
    file_path = Path(file_path)
    if sql_text is None:
        sql_text = file_path.read_text(encoding="utf-8")

    graph = LineageGraph()
    file_node = LineageNode(
        id=str(file_path),
        name=file_path.name,
        node_type=NodeType.FILE,
        metadata={"path": str(file_path)},
    )
    graph.nodes.append(file_node)

    try:
        statements = sqlglot.parse(sql_text, dialect="postgres")
    except Exception as e:
        logger.warning("sqlglot parse error in %s: %s", file_path, e)
        return graph

    for statement in statements:
        if statement is None:
            continue
        try:
            _process_statement(statement, graph, file_path)
        except Exception as e:
            logger.debug("Could not fully process statement in %s: %s", file_path, e)

    return graph


def _process_statement(stmt: exp.Expression, graph: LineageGraph, file_path: Path) -> None:
    """Route a top-level statement to the appropriate handler."""

    # CREATE TABLE ... AS SELECT / CREATE VIEW ... AS SELECT
    if isinstance(stmt, exp.Create):
        _handle_create(stmt, graph, file_path)
        return

    # INSERT INTO ... SELECT
    if isinstance(stmt, exp.Insert):
        _handle_insert(stmt, graph, file_path)
        return

    # Standalone SELECT (e.g. inside procedures we may extract)
    if isinstance(stmt, exp.Select):
        _handle_select(stmt, graph, file_path, target_table=None)
        return

    # CREATE PROCEDURE / FUNCTION — extract inner SQL
    if isinstance(stmt, (exp.Command,)):
        # sqlglot may parse PL/pgSQL blocks as Command; skip gracefully
        logger.debug("Skipping command-type node in %s", file_path)
        return

    # Walk children looking for CREATE/SELECT inside complex blocks
    for child in stmt.walk():
        if isinstance(child, exp.Create):
            _handle_create(child, graph, file_path)
        elif isinstance(child, exp.Insert):
            _handle_insert(child, graph, file_path)


def _handle_create(stmt: exp.Create, graph: LineageGraph, file_path: Path) -> None:
    """Handle CREATE TABLE / CREATE VIEW statements."""
    table_expr = stmt.this
    if not isinstance(table_expr, (exp.Table, exp.Schema)):
        return

    if isinstance(table_expr, exp.Schema):
        table_name = table_expr.this.name if isinstance(table_expr.this, exp.Table) else str(table_expr.this)
    else:
        table_name = table_expr.name

    table_name = table_name.lower()
    kind = stmt.args.get("kind", "").upper()
    node_type = NodeType.VIEW if "VIEW" in kind else NodeType.TABLE

    # Add table/view node
    table_node = LineageNode(
        id=table_name,
        name=table_name,
        node_type=node_type,
        metadata={"file": str(file_path)},
    )
    graph.nodes.append(table_node)
    graph.edges.append(LineageEdge(
        source_id=table_name,
        target_id=str(file_path),
        edge_type=EdgeType.DEFINED_IN,
    ))

    # If it's CREATE ... AS SELECT, extract lineage from the SELECT
    select = stmt.find(exp.Select)
    if select:
        _handle_select(select, graph, file_path, target_table=table_name)
    else:
        # Plain CREATE TABLE with column definitions
        _extract_column_defs(stmt, table_name, graph)


def _handle_insert(stmt: exp.Insert, graph: LineageGraph, file_path: Path) -> None:
    """Handle INSERT INTO ... SELECT statements."""
    table_expr = stmt.this
    if isinstance(table_expr, exp.Table):
        target = table_expr.name.lower()
    else:
        return

    select = stmt.find(exp.Select)
    if select:
        _handle_select(select, graph, file_path, target_table=target)


def _handle_select(
    select: exp.Select,
    graph: LineageGraph,
    file_path: Path,
    target_table: str | None,
) -> None:
    """Extract column-level lineage from a SELECT statement."""
    # Try to qualify columns to resolve ambiguous references
    qualified = _safe_qualify(select)

    # Collect FROM/JOIN source tables
    source_tables: set[str] = set()
    for table in qualified.find_all(exp.Table):
        tname = table.name.lower()
        if tname:
            source_tables.add(tname)
            if target_table:
                graph.edges.append(LineageEdge(
                    source_id=target_table,
                    target_id=tname,
                    edge_type=EdgeType.READS_FROM,
                ))

    # Extract column mappings
    mappings = _extract_source_columns(qualified)
    for target_col_name, src_table, src_col in mappings:
        src_table = src_table.lower()
        src_col = src_col.lower()
        target_col_name = target_col_name.lower()

        # Resolve table alias → real table name if possible
        resolved_table = src_table if src_table in source_tables else ""

        if target_table:
            target_col_id = f"{target_table}.{target_col_name}"
            graph.nodes.append(LineageNode(
                id=target_col_id,
                name=target_col_name,
                node_type=NodeType.COLUMN,
                metadata={"table": target_table},
            ))
            graph.edges.append(LineageEdge(
                source_id=target_table,
                target_id=target_col_id,
                edge_type=EdgeType.HAS_COLUMN,
            ))

            if resolved_table and src_col:
                src_col_id = f"{resolved_table}.{src_col}"
                graph.nodes.append(LineageNode(
                    id=src_col_id,
                    name=src_col,
                    node_type=NodeType.COLUMN,
                    metadata={"table": resolved_table},
                ))
                graph.edges.append(LineageEdge(
                    source_id=target_col_id,
                    target_id=src_col_id,
                    edge_type=EdgeType.DERIVES_FROM,
                    confidence=1.0,
                ))


def _extract_column_defs(stmt: exp.Create, table_name: str, graph: LineageGraph) -> None:
    """Extract column definitions from a plain CREATE TABLE."""
    for col_def in stmt.find_all(exp.ColumnDef):
        col_name = col_def.name.lower()
        col_id = f"{table_name}.{col_name}"
        graph.nodes.append(LineageNode(
            id=col_id,
            name=col_name,
            node_type=NodeType.COLUMN,
            metadata={"table": table_name},
        ))
        graph.edges.append(LineageEdge(
            source_id=table_name,
            target_id=col_id,
            edge_type=EdgeType.HAS_COLUMN,
        ))
