"""
FastAPI REST API for the Data Lineage POC.

In-memory only — no external graph database required.

Endpoints:
  POST /analyze           — Scan sample_repo and build lineage graph
  GET  /graph             — Full graph (nodes + edges)
  GET  /upstream/{id}     — Trace upstream lineage for a node
  GET  /downstream/{id}   — Trace downstream impact for a node
  GET  /impact/{id}       — Impact analysis report
  GET  /search?q=         — Search nodes by name
  POST /pr-check          — Analyse lineage impact between two Git refs
  GET  /                  — Web UI

Concurrency note:
  ``_graph`` is a module-level singleton mutated by ``POST /analyze`` and read by every
  traversal endpoint. There is NO locking — appropriate for the single-user POC scope
  but not safe under concurrent ``/analyze`` requests across workers. A productive
  deployment should either (a) move to a persistent backend, or (b) wrap mutations in
  an ``asyncio.Lock`` and run a single Uvicorn worker.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel as PydanticBaseModel

from src.config import settings
from src.engine import analyze_directory, analyze_multiple_repos
from src.models import LineageGraph

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Data Lineage POC",
    description="Static Data Lineage with AI — Column-level lineage via source code analysis",
    version="0.2.0",
)

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))


# In-memory graph — populated by POST /analyze
_graph: LineageGraph | None = None
# Edges where source = consumer, target = producer (consumer "reads from" producer).
# BFS reaches the producer by following these edges forward.
_BACKWARD_EDGES = {"DERIVES_FROM", "READS_FROM"}
# Edges where source = producer, target = consumer (producer "writes to" consumer).
# To keep BFS logic uniform, we flip these when building adjacency so they behave
# like backward edges.
_FORWARD_EDGES = {"WRITES_TO", "COPIES_TO"}
_TRAVERSAL_EDGE_TYPES = _BACKWARD_EDGES | _FORWARD_EDGES


def _graph_dict() -> dict:
    if _graph is None:
        return {"nodes": [], "edges": []}
    return {
        "nodes": [n.model_dump() for n in _graph.nodes],
        "edges": [e.model_dump() for e in _graph.edges],
    }


def _require_graph() -> LineageGraph:
    if _graph is None:
        raise HTTPException(status_code=400, detail="Run POST /analyze first")
    return _graph


def _build_adjacency() -> tuple[dict, dict, dict]:
    """Returns (forward, reverse, nodes_by_id) for traversal.

    Forward = direction in which BFS reaches upstream (producers) from a starting node.
    Reverse = direction in which BFS reaches downstream (consumers).

    Backward-pointing edges (DERIVES_FROM, READS_FROM) are stored as-is.
    Forward-pointing edges (WRITES_TO, COPIES_TO) are flipped so the BFS
    direction semantics remain consistent.

    Special case: an ADF "dataset → physical table" READS_FROM edge represents
    an alias/wrapping relationship (the dataset and the table are the same
    logical entity), so it's added in both directions. Otherwise sink datasets
    would lose their connection to the underlying table.
    """
    g = _require_graph()
    forward: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    reverse: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for e in g.edges:
        et = e.edge_type.value
        if et not in _TRAVERSAL_EDGE_TYPES:
            continue
        edict = e.model_dump()
        if et in _FORWARD_EDGES:
            src, tgt = e.target_id, e.source_id
        else:
            src, tgt = e.source_id, e.target_id
        forward[src].append((tgt, edict))
        reverse[tgt].append((src, edict))
        # Dataset↔table aliases: add the reverse direction too
        if et == "READS_FROM" and "ADF dataset" in (e.transformation or ""):
            forward[tgt].append((src, edict))
            reverse[src].append((tgt, edict))
    nodes_by_id = {n.id: n.model_dump() for n in g.nodes}
    return forward, reverse, nodes_by_id


def _bfs(start_id: str, adj: dict, nodes_by_id: dict, max_depth: int) -> dict:
    visited_nodes: dict[str, dict] = {}
    visited_edges: list[dict] = []
    seen_edge_keys: set[tuple] = set()
    queue = deque([(start_id, 0)])
    seen = {start_id}
    if start_id in nodes_by_id:
        visited_nodes[start_id] = nodes_by_id[start_id]
    while queue:
        cur, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for next_id, edict in adj.get(cur, []):
            key = (edict["source_id"], edict["target_id"], edict["edge_type"])
            if key not in seen_edge_keys:
                seen_edge_keys.add(key)
                visited_edges.append(edict)
            if next_id not in seen:
                seen.add(next_id)
                if next_id in nodes_by_id:
                    visited_nodes[next_id] = nodes_by_id[next_id]
                queue.append((next_id, depth + 1))
    return {"nodes": list(visited_nodes.values()), "edges": visited_edges}


@app.on_event("startup")
async def _check_llm_config():
    """Warn at startup if the LLM fallback is unavailable. Doesn't block startup."""
    key = settings.openai_api_key
    if not key or key.startswith("sk-your"):
        logger.warning(
            "OPENAI_API_KEY not configured — LLM fallback disabled. "
            "Deterministic SQL and ADF lineage will still work, but Python files and "
            "stored procedures will be skipped. Set OPENAI_API_KEY in .env to enable."
        )
    else:
        logger.info("OpenAI model %r configured for LLM fallback.", settings.openai_model)


@app.get("/", response_class=HTMLResponse)
async def web_ui(request: Request):
    return templates.TemplateResponse(name="index.html", context={"request": request}, request=request)


@app.post("/analyze")
async def analyze_repo():
    """Scan the configured repository, extract lineage, store in memory."""
    global _graph
    repo = Path(settings.repo_path)
    if not repo.is_dir():
        raise HTTPException(status_code=400, detail=f"Repo path not found: {repo}")

    _graph = analyze_directory(repo)
    return {
        "status": "ok",
        "nodes_extracted": len(_graph.nodes),
        "edges_extracted": len(_graph.edges),
    }


@app.post("/analyze-multi")
async def analyze_multi_repo():
    """Scan all configured repositories, merge lineage, store in memory."""
    global _graph
    repo_sources = settings.get_repo_sources()
    _graph = analyze_multiple_repos(repo_sources)
    return {
        "status": "ok",
        "repos_scanned": [r.name for r in repo_sources],
        "nodes_extracted": len(_graph.nodes),
        "edges_extracted": len(_graph.edges),
    }


@app.get("/graph")
async def get_full_graph():
    """Return the full in-memory lineage graph."""
    return _graph_dict()


@app.get("/upstream/{node_id:path}")
async def get_upstream(node_id: str, depth: int = Query(default=10, ge=1, le=50)):
    """Trace upstream lineage (where does this data come from?)."""
    forward, _reverse, nodes_by_id = _build_adjacency()
    if node_id not in nodes_by_id:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    result = _bfs(node_id, forward, nodes_by_id, depth)
    if len(result["nodes"]) <= 1:
        raise HTTPException(status_code=404, detail=f"No upstream found for '{node_id}'")
    return result


@app.get("/downstream/{node_id:path}")
async def get_downstream(node_id: str, depth: int = Query(default=10, ge=1, le=50)):
    """Trace downstream impact (what depends on this data?)."""
    _forward, reverse, nodes_by_id = _build_adjacency()
    if node_id not in nodes_by_id:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    result = _bfs(node_id, reverse, nodes_by_id, depth)
    if len(result["nodes"]) <= 1:
        raise HTTPException(status_code=404, detail=f"No downstream found for '{node_id}'")
    return result


@app.get("/impact/{node_id:path}")
async def impact_analysis(node_id: str):
    """Impact analysis: if this node changes, what is affected?"""
    _forward, reverse, nodes_by_id = _build_adjacency()
    if node_id not in nodes_by_id:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    downstream = _bfs(node_id, reverse, nodes_by_id, max_depth=50)
    affected_tables: set[str] = set()
    affected_columns: set[str] = set()
    for n in downstream["nodes"]:
        if n["id"] == node_id:
            continue
        nt = n.get("node_type")
        if nt in ("TABLE", "VIEW"):
            affected_tables.add(n["id"])
        elif nt == "COLUMN":
            affected_columns.add(n["id"])
    return {
        "source": node_id,
        "affected_tables": sorted(affected_tables),
        "affected_columns": sorted(affected_columns),
        "total_affected": len(affected_tables) + len(affected_columns),
        "full_graph": downstream,
    }


@app.get("/search")
async def search_nodes(q: str = Query(..., min_length=1)):
    """Search nodes by name or ID."""
    g = _require_graph()
    q_lower = q.lower()
    matches = [
        {"id": n.id, "name": n.name, "node_type": n.node_type.value}
        for n in g.nodes
        if q_lower in n.id.lower() or q_lower in (n.name or "").lower()
    ]
    return matches[:50]


class PRCheckRequest(PydanticBaseModel):
    base_ref: str = "origin/main"
    head_ref: str = "HEAD"
    repo_path: str | None = None


@app.post("/pr-check")
async def pr_check(body: PRCheckRequest):
    """Analyse lineage impact between two Git refs."""
    from src.pr_analyzer import analyze_pr, render_markdown

    repo = Path(body.repo_path).resolve() if body.repo_path else Path(".").resolve()
    if not (repo / ".git").exists():
        raise HTTPException(status_code=400, detail=f"Not a Git repository: {repo}")

    result = analyze_pr(repo, body.base_ref, body.head_ref)
    return {
        **result.as_dict(),
        "markdown_report": render_markdown(result),
    }
