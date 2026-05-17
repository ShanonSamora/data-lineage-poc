"""Tests for the hybrid analysis engine (engine.py)."""
from pathlib import Path
from unittest.mock import patch

import pytest

from src.engine import (
    _needs_llm_fallback,
    _prune_dead_nodes,
    analyze_directory,
    analyze_file,
)
from src.models import EdgeType, LineageEdge, LineageGraph, LineageNode, NodeType


# ── _needs_llm_fallback ────────────────────────────────────────────────

def test_fallback_triggers_for_create_procedure():
    sql = "CREATE OR REPLACE PROCEDURE p() LANGUAGE plpgsql AS $$ BEGIN END $$;"
    assert _needs_llm_fallback(sql, LineageGraph()) is True


def test_fallback_triggers_for_execute_immediate():
    sql = "EXECUTE IMMEDIATE 'SELECT * FROM ' || tbl;"
    assert _needs_llm_fallback(sql, LineageGraph()) is True


def test_fallback_does_not_trigger_for_case_when_end():
    """Regression for A4: 'END' in CASE expressions should not trigger LLM fallback
    when the deterministic parser already produced lineage edges."""
    sql = (
        "CREATE VIEW v AS SELECT id, "
        "CASE WHEN amount > 100 THEN 'HIGH' ELSE 'LOW' END AS risk_level "
        "FROM t;"
    )
    # Simulate a graph that already has DERIVES_FROM edges
    g = LineageGraph(
        nodes=[],
        edges=[
            LineageEdge(
                source_id="v.risk_level",
                target_id="t.amount",
                edge_type=EdgeType.DERIVES_FROM,
            )
        ],
    )
    assert _needs_llm_fallback(sql, g) is False


def test_fallback_does_not_trigger_for_plain_begin_keyword():
    """A bare 'BEGIN' in a SQL comment or string should not trigger LLM fallback."""
    sql = "-- BEGIN: customer reports section\nCREATE VIEW v AS SELECT id FROM t;"
    g = LineageGraph(
        nodes=[],
        edges=[
            LineageEdge(
                source_id="v.id",
                target_id="t.id",
                edge_type=EdgeType.DERIVES_FROM,
            )
        ],
    )
    assert _needs_llm_fallback(sql, g) is False


def test_fallback_triggers_when_no_lineage_extracted():
    """If the parser produced zero DERIVES_FROM edges for a substantial file, fall back to LLM."""
    sql = "SELECT * FROM dynamic_table_" + "x" * 200  # >100 chars
    assert _needs_llm_fallback(sql, LineageGraph()) is True


def test_fallback_does_not_trigger_for_short_dll_file():
    """A short pure-DDL file (CREATE TABLE) shouldn't trigger LLM just because it has no lineage."""
    sql = "CREATE TABLE t (id INT);"  # under 100 chars
    assert _needs_llm_fallback(sql, LineageGraph()) is False


# ── _prune_dead_nodes ──────────────────────────────────────────────────

def test_prune_removes_isolated_table_node():
    """A TABLE node with only a DEFINED_IN edge as source is treated as dead and pruned."""
    g = LineageGraph(
        nodes=[
            LineageNode(id="lonely_var", name="lonely_var", node_type=NodeType.TABLE),
            LineageNode(id="real_tbl", name="real_tbl", node_type=NodeType.TABLE),
            LineageNode(id="file:x.py", name="x.py", node_type=NodeType.FILE),
        ],
        edges=[
            LineageEdge(
                source_id="lonely_var",
                target_id="file:x.py",
                edge_type=EdgeType.DEFINED_IN,
            ),
            LineageEdge(
                source_id="real_tbl",
                target_id="file:x.py",
                edge_type=EdgeType.DEFINED_IN,
            ),
            # real_tbl has a real lineage edge — should survive pruning
            LineageEdge(
                source_id="real_tbl",
                target_id="other_tbl",
                edge_type=EdgeType.READS_FROM,
            ),
        ],
    )
    _prune_dead_nodes(g)

    ids = {n.id for n in g.nodes}
    assert "lonely_var" not in ids
    assert "real_tbl" in ids


def test_prune_preserves_file_and_function_nodes():
    """FILE / PROCEDURE / PYTHON_FUNCTION / ADF_* nodes are always preserved."""
    g = LineageGraph(
        nodes=[
            LineageNode(id="file:a.py", name="a.py", node_type=NodeType.FILE),
            LineageNode(id="proc1", name="proc1", node_type=NodeType.PROCEDURE),
            LineageNode(id="fn1", name="fn1", node_type=NodeType.PYTHON_FUNCTION),
        ],
        edges=[],
    )
    _prune_dead_nodes(g)
    assert {n.id for n in g.nodes} == {"file:a.py", "proc1", "fn1"}


def test_prune_drops_llm_table_with_no_columns():
    """LLM-sourced TABLE nodes without HAS_COLUMN edges are intermediate pandas vars; prune them.
    But LLM tables that DO have HAS_COLUMN edges survive (they're real persisted targets)."""
    g = LineageGraph(
        nodes=[
            LineageNode(
                id="real_table", name="real_table", node_type=NodeType.TABLE,
                metadata={"source": "llm"},
            ),
            LineageNode(
                id="real_table.col", name="col", node_type=NodeType.COLUMN,
                metadata={"table": "real_table", "source": "llm"},
            ),
            LineageNode(
                id="intermediate_var", name="intermediate_var", node_type=NodeType.TABLE,
                metadata={"source": "llm"},
            ),
        ],
        edges=[
            LineageEdge(source_id="real_table", target_id="real_table.col",
                        edge_type=EdgeType.HAS_COLUMN),
            LineageEdge(source_id="intermediate_var", target_id="real_table",
                        edge_type=EdgeType.WRITES_TO, confidence=0.85),
        ],
    )
    _prune_dead_nodes(g)
    ids = {n.id for n in g.nodes}
    assert "real_table" in ids, "Real LLM table with columns must survive"
    assert "real_table.col" in ids
    assert "intermediate_var" not in ids, "Schemaless LLM table must be pruned"


def test_prune_keeps_deterministic_tables_without_columns():
    """A deterministic-sourced TABLE without HAS_COLUMN edges is kept (rule only applies to LLM)."""
    g = LineageGraph(
        nodes=[
            LineageNode(id="deterministic_tbl", name="deterministic_tbl", node_type=NodeType.TABLE),
            LineageNode(id="other_tbl", name="other_tbl", node_type=NodeType.TABLE,
                        metadata={"source": "llm"}),
            LineageNode(id="other_tbl.x", name="x", node_type=NodeType.COLUMN,
                        metadata={"table": "other_tbl", "source": "llm"}),
        ],
        edges=[
            LineageEdge(source_id="other_tbl", target_id="other_tbl.x",
                        edge_type=EdgeType.HAS_COLUMN),
            # Both tables involved in a real edge so neither is "orphan"
            LineageEdge(source_id="other_tbl", target_id="deterministic_tbl",
                        edge_type=EdgeType.READS_FROM, confidence=1.0),
        ],
    )
    _prune_dead_nodes(g)
    ids = {n.id for n in g.nodes}
    assert "deterministic_tbl" in ids, "Deterministic table must survive even without HAS_COLUMN"
    assert "other_tbl" in ids


# ── analyze_directory ─────────────────────────────────────────────────

def test_analyze_directory_produces_non_empty_graph():
    """The real sample_repo produces a graph with the expected order of magnitude."""
    g = analyze_directory("sample_repo")
    assert len(g.nodes) > 100, f"expected >100 nodes, got {len(g.nodes)}"
    assert len(g.edges) > 100, f"expected >100 edges, got {len(g.edges)}"

    node_types = {n.node_type for n in g.nodes}
    # We expect multi-language coverage
    assert NodeType.TABLE in node_types
    assert NodeType.VIEW in node_types
    assert NodeType.COLUMN in node_types
    assert NodeType.ADF_PIPELINE in node_types
    assert NodeType.ADF_DATASET in node_types


def test_analyze_directory_merges_without_duplicate_nodes():
    """Running analyze on the same dir twice doesn't double-count via merge."""
    g = analyze_directory("sample_repo")
    ids = [n.id for n in g.nodes]
    assert len(ids) == len(set(ids)), "duplicate node IDs after analyze"


def test_analyze_unsupported_file_type_returns_empty(tmp_path):
    """A file with no recognized extension returns an empty graph (no error)."""
    f = tmp_path / "random.txt"
    f.write_text("hello world")
    g = analyze_file(f)
    assert len(g.nodes) == 0
    assert len(g.edges) == 0
