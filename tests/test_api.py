"""Tests for the FastAPI endpoints (api.py).

Uses FastAPI's TestClient. The /analyze endpoint runs the real engine against
sample_repo, which keeps these tests as integration smoke tests.
"""
import pytest
from fastapi.testclient import TestClient

from src import api
from src.api import app


@pytest.fixture
def client():
    # Reset in-memory graph between tests
    api._graph = None
    return TestClient(app)


def test_traversal_before_analyze_returns_400(client):
    """Calling /upstream/X without running /analyze first returns 400."""
    r = client.get("/upstream/anything")
    assert r.status_code == 400
    assert "analyze" in r.json()["detail"].lower()


def test_analyze_returns_ok_with_counts(client):
    r = client.post("/analyze")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["nodes_extracted"] > 100
    assert body["edges_extracted"] > 100


def test_graph_endpoint_returns_full_graph(client):
    client.post("/analyze")
    r = client.get("/graph")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)
    assert len(data["nodes"]) > 0


def test_upstream_traces_through_to_staging(client):
    """Upstream of a column in a reporting table should reach staging columns."""
    client.post("/analyze")
    r = client.get("/upstream/int_customers.full_name")
    assert r.status_code == 200
    node_ids = {n["id"] for n in r.json()["nodes"]}
    assert "stg_customers.first_name" in node_ids
    assert "stg_customers.last_name" in node_ids


def test_downstream_from_staging_reaches_reporting(client):
    """Downstream of a staging table should include reporting tables."""
    client.post("/analyze")
    r = client.get("/downstream/stg_customers")
    assert r.status_code == 200
    node_ids = {n["id"] for n in r.json()["nodes"]}
    assert any(nid.startswith("rpt_") for nid in node_ids)
    assert any(nid.startswith("int_") for nid in node_ids)


def test_impact_returns_affected_tables_and_columns(client):
    """Impact analysis returns structured counts of affected downstream assets."""
    client.post("/analyze")
    r = client.get("/impact/stg_transactions.amount")
    assert r.status_code == 200
    data = r.json()
    assert data["source"] == "stg_transactions.amount"
    assert data["total_affected"] > 0
    assert isinstance(data["affected_tables"], list)
    assert isinstance(data["affected_columns"], list)


def test_search_finds_matching_nodes(client):
    """/search returns nodes whose id/name contains the query (case-insensitive)."""
    client.post("/analyze")
    r = client.get("/search", params={"q": "customer"})
    assert r.status_code == 200
    results = r.json()
    assert len(results) > 0
    assert all("customer" in (n["id"] + n["name"]).lower() for n in results)


def test_upstream_nonexistent_node_returns_404(client):
    """Traversal of a non-existent node returns 404."""
    client.post("/analyze")
    r = client.get("/upstream/this_node_does_not_exist")
    assert r.status_code == 404


def test_web_ui_route_returns_html(client):
    """GET / returns the Jinja-rendered UI."""
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Data Lineage" in r.text
