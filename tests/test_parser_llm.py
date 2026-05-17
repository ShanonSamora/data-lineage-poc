"""Tests for the LLM lineage parser (parser_llm.py).

Mocks the OpenAI client so tests are deterministic and require no API key.
"""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.models import EdgeType, NodeType
from src.parser_llm import interpret_with_llm


def _mock_response(content: str, finish_reason: str = "stop"):
    """Build a minimal OpenAI-style response object."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


def _has_edge(graph, src: str, tgt: str, edge_type: EdgeType) -> bool:
    return any(
        e.source_id == src and e.target_id == tgt and e.edge_type == edge_type
        for e in graph.edges
    )


@pytest.fixture
def with_api_key(monkeypatch):
    """Provide a non-empty key so the LLM code path runs."""
    from src import parser_llm
    monkeypatch.setattr(parser_llm.settings, "openai_api_key", "sk-test-key")
    yield


def test_no_api_key_returns_empty_graph(monkeypatch):
    """Without an API key the parser returns an empty graph without calling the API."""
    from src import parser_llm
    monkeypatch.setattr(parser_llm.settings, "openai_api_key", "")
    g = interpret_with_llm(Path("test.py"), "import pandas")
    assert len(g.nodes) == 0
    assert len(g.edges) == 0


def test_placeholder_api_key_returns_empty_graph(monkeypatch):
    """A placeholder key (sk-your-...) is treated as unset."""
    from src import parser_llm
    monkeypatch.setattr(parser_llm.settings, "openai_api_key", "sk-your-key-here")
    g = interpret_with_llm(Path("test.py"), "import pandas")
    assert len(g.nodes) == 0


def test_valid_llm_response_builds_graph(with_api_key):
    """A well-formed JSON response produces the expected nodes and edges."""
    response_json = json.dumps({
        "tables": [
            {"name": "stg_customers", "type": "TABLE"},
            {"name": "df_customers", "type": "TABLE"},
        ],
        "columns": [
            {
                "target_table": "df_customers",
                "target_column": "customer_id",
                "source_table": "stg_customers",
                "source_column": "customer_id",
                "transformation": "pd.read_sql",
            }
        ],
        "data_flows": [
            {
                "source": "stg_customers",
                "target": "df_customers",
                "operation": "pd.read_sql",
            }
        ],
    })

    with patch("src.parser_llm.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _mock_response(response_json)
        g = interpret_with_llm(Path("transform.py"), "df = pd.read_sql('select * from stg_customers')")

    # Column-level lineage
    assert _has_edge(g, "df_customers.customer_id", "stg_customers.customer_id", EdgeType.DERIVES_FROM)
    # Table-level read flow: df_customers READS_FROM stg_customers
    assert _has_edge(g, "df_customers", "stg_customers", EdgeType.READS_FROM)


def test_malformed_json_returns_empty_graph(with_api_key):
    """If the LLM returns invalid JSON, the parser logs a warning and returns empty."""
    with patch("src.parser_llm.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _mock_response("not valid {json")
        g = interpret_with_llm(Path("test.py"), "code")
    assert len(g.nodes) == 0
    assert len(g.edges) == 0


def test_null_source_or_target_column_is_skipped(with_api_key):
    """Regression: when the LLM returns null source_table/target_table, the parser skips
    those entries instead of crashing with AttributeError."""
    response_json = json.dumps({
        "tables": [],
        "columns": [
            {"target_table": None, "target_column": "x", "source_table": "t", "source_column": "y"},
            {"target_table": "out", "target_column": "x", "source_table": None, "source_column": "y"},
            {"target_table": "out", "target_column": None, "source_table": "in", "source_column": "y"},
            {"target_table": "out", "target_column": "x", "source_table": "in", "source_column": "y"},
        ],
        "data_flows": [],
    })

    with patch("src.parser_llm.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _mock_response(response_json)
        g = interpret_with_llm(Path("test.py"), "code")

    # Only the last (valid) column entry should produce an edge.
    derives = [e for e in g.edges if e.edge_type == EdgeType.DERIVES_FROM]
    assert len(derives) == 1
    assert derives[0].source_id == "out.x"
    assert derives[0].target_id == "in.y"


def test_data_flow_write_operation_emits_writes_to(with_api_key):
    """Regression for A1: when operation describes a write (to_sql, INSERT, etc.),
    the edge is a WRITES_TO from source to target, not a reversed READS_FROM."""
    response_json = json.dumps({
        "tables": [],
        "columns": [],
        "data_flows": [
            {"source": "df_metrics", "target": "rpt_summary", "operation": "to_sql write"},
            {"source": "df_metrics", "target": "fact_table", "operation": "INSERT INTO"},
            {"source": "stg_customers", "target": "df_customers", "operation": "pd.read_sql"},
        ],
    })

    with patch("src.parser_llm.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _mock_response(response_json)
        g = interpret_with_llm(Path("test.py"), "code")

    # The two writes:
    assert _has_edge(g, "df_metrics", "rpt_summary", EdgeType.WRITES_TO)
    assert _has_edge(g, "df_metrics", "fact_table", EdgeType.WRITES_TO)
    # The read (consumer -> producer convention for READS_FROM)
    assert _has_edge(g, "df_customers", "stg_customers", EdgeType.READS_FROM)


def test_function_to_function_flow_is_dropped(with_api_key):
    """Function-to-function 'calls' flows are not data lineage and must be dropped."""
    response_json = json.dumps({
        "tables": [
            {"name": "load_data", "type": "PYTHON_FUNCTION"},
            {"name": "run_pipeline", "type": "PYTHON_FUNCTION"},
        ],
        "columns": [],
        "data_flows": [
            {"source": "load_data", "target": "run_pipeline", "operation": "calls"},
        ],
    })

    with patch("src.parser_llm.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _mock_response(response_json)
        g = interpret_with_llm(Path("test.py"), "code")

    # No READS_FROM or WRITES_TO edges between the two function nodes
    fn_edges = [
        e for e in g.edges
        if e.source_id in {"load_data", "run_pipeline"}
        and e.target_id in {"load_data", "run_pipeline"}
        and e.edge_type in (EdgeType.READS_FROM, EdgeType.WRITES_TO)
    ]
    assert fn_edges == []


def test_llm_confidence_is_below_one(with_api_key):
    """All LLM-inferred edges carry confidence < 1.0 to distinguish from deterministic."""
    response_json = json.dumps({
        "tables": [],
        "columns": [
            {"target_table": "a", "target_column": "x", "source_table": "b", "source_column": "y"},
        ],
        "data_flows": [
            {"source": "b", "target": "a", "operation": "select"},
        ],
    })

    with patch("src.parser_llm.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _mock_response(response_json)
        g = interpret_with_llm(Path("test.py"), "code")

    for e in g.edges:
        if e.edge_type in (EdgeType.DERIVES_FROM, EdgeType.READS_FROM, EdgeType.WRITES_TO):
            assert e.confidence < 1.0, f"LLM edge has confidence {e.confidence}: {e}"


def test_truncated_response_logs_warning(with_api_key, caplog):
    """When finish_reason='length' the parser logs a warning about truncation."""
    response_json = json.dumps({"tables": [], "columns": [], "data_flows": []})

    with patch("src.parser_llm.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = _mock_response(
            response_json, finish_reason="length"
        )
        with caplog.at_level("WARNING"):
            interpret_with_llm(Path("test.py"), "code")

    assert any("truncated" in r.message.lower() for r in caplog.records)
