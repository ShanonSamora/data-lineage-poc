"""
FastAPI REST API for the Data Lineage POC.

Endpoints:
  POST /analyze           — Scan sample_repo and build lineage graph
  GET  /graph             — Full graph (nodes + edges)
  GET  /upstream/{id}     — Trace upstream lineage for a node
  GET  /downstream/{id}   — Trace downstream impact for a node
  GET  /impact/{id}       — Impact analysis report
  GET  /search?q=         — Search nodes by name
  POST /pr-check          — Analyse lineage impact between two Git refs
  GET  /                  — Web UI
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel as PydanticBaseModel

from src.config import settings
from src.engine import analyze_directory, analyze_multiple_repos
from src.graph_store import Neo4jStore

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Data Lineage POC",
    description="Static Data Lineage with AI — Column-level lineage via source code analysis",
    version="0.1.0",
)

# Templates
_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

# Neo4j store (lazy init to tolerate missing Neo4j during import)
_store: Neo4jStore | None = None


def _get_store() -> Neo4jStore:
    global _store
    if _store is None:
        _store = Neo4jStore()
        _store.setup_indexes()
    return _store


# ------------------------------------------------------------------
# Web UI
# ------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def web_ui(request: Request):
    """Serve the single-page lineage explorer."""
    return templates.TemplateResponse(name="index.html", context={"request": request}, request=request)


# ------------------------------------------------------------------
# API endpoints
# ------------------------------------------------------------------
@app.post("/analyze")
async def analyze_repo():
    """
    Scan the configured repository, extract lineage, and store in Neo4j.
    """
    repo = Path(settings.repo_path)
    if not repo.is_dir():
        raise HTTPException(status_code=400, detail=f"Repo path not found: {repo}")

    graph = analyze_directory(repo)

    store = _get_store()
    store.clear_all()
    result = store.store_graph(graph)

    return {
        "status": "ok",
        "nodes_extracted": len(graph.nodes),
        "edges_extracted": len(graph.edges),
        **result,
    }


@app.post("/analyze-multi")
async def analyze_multi_repo():
    """
    Scan all configured repositories, merge lineage, and store in Neo4j.

    Configure repos via the REPOS env var:
      REPOS='[{"name":"warehouse","path":"./repos/warehouse"},{"name":"etl","path":"./repos/etl"}]'
    """
    repo_sources = settings.get_repo_sources()
    graph = analyze_multiple_repos(repo_sources)

    store = _get_store()
    store.clear_all()
    result = store.store_graph(graph)

    return {
        "status": "ok",
        "repos_scanned": [r.name for r in repo_sources],
        "nodes_extracted": len(graph.nodes),
        "edges_extracted": len(graph.edges),
        **result,
    }


@app.get("/graph")
async def get_full_graph():
    """Return the full lineage graph."""
    store = _get_store()
    return store.get_full_graph()


@app.get("/upstream/{node_id:path}")
async def get_upstream(node_id: str, depth: int = Query(default=10, ge=1, le=50)):
    """Trace upstream lineage for a node (where does this data come from?)."""
    store = _get_store()
    result = store.get_upstream(node_id, max_depth=depth)
    if not result["nodes"]:
        raise HTTPException(status_code=404, detail=f"No upstream found for '{node_id}'")
    return result


@app.get("/downstream/{node_id:path}")
async def get_downstream(node_id: str, depth: int = Query(default=10, ge=1, le=50)):
    """Trace downstream impact for a node (what depends on this data?)."""
    store = _get_store()
    result = store.get_downstream(node_id, max_depth=depth)
    if not result["nodes"]:
        raise HTTPException(status_code=404, detail=f"No downstream found for '{node_id}'")
    return result


@app.get("/impact/{node_id:path}")
async def impact_analysis(node_id: str):
    """Impact analysis: if this node changes, what is affected?"""
    store = _get_store()
    return store.get_impact_analysis(node_id)


@app.get("/search")
async def search_nodes(q: str = Query(..., min_length=1)):
    """Search nodes by name or ID."""
    store = _get_store()
    return store.search_nodes(q)


# ------------------------------------------------------------------
# In-memory mode (no Neo4j) — for quick demos
# ------------------------------------------------------------------
_in_memory_graph: dict | None = None


@app.post("/analyze-local")
async def analyze_local():
    """
    Analyze repo and return graph directly (no Neo4j required).
    Stores result in memory for subsequent /graph-local calls.
    """
    global _in_memory_graph
    repo = Path(settings.repo_path)
    if not repo.is_dir():
        raise HTTPException(status_code=400, detail=f"Repo path not found: {repo}")

    graph = analyze_directory(repo)
    _in_memory_graph = {
        "nodes": [n.model_dump() for n in graph.nodes],
        "edges": [e.model_dump() for e in graph.edges],
    }
    return {
        "status": "ok",
        "nodes_extracted": len(graph.nodes),
        "edges_extracted": len(graph.edges),
    }


@app.get("/graph-local")
async def get_local_graph():
    """Return in-memory graph (use after /analyze-local)."""
    if _in_memory_graph is None:
        raise HTTPException(status_code=400, detail="Run POST /analyze-local first")
    return _in_memory_graph


# ------------------------------------------------------------------
# PR lineage impact check
# ------------------------------------------------------------------
class PRCheckRequest(PydanticBaseModel):
    base_ref: str = "origin/main"
    head_ref: str = "HEAD"
    repo_path: str | None = None  # defaults to current working directory


@app.post("/pr-check")
async def pr_check(body: PRCheckRequest):
    """
    Analyse lineage impact between two Git refs.

    Returns structured diff + Markdown report.
    """
    from src.pr_analyzer import analyze_pr, render_markdown

    repo = Path(body.repo_path).resolve() if body.repo_path else Path(".").resolve()
    if not (repo / ".git").exists():
        raise HTTPException(status_code=400, detail=f"Not a Git repository: {repo}")

    result = analyze_pr(repo, body.base_ref, body.head_ref)
    return {
        **result.as_dict(),
        "markdown_report": render_markdown(result),
    }


@app.on_event("shutdown")
async def shutdown():
    if _store:
        _store.close()
