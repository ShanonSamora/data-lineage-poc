"""
FastAPI REST API for the Data Lineage POC.

In-memory only — no external graph database required.

Endpoints:
  POST /analyze           — Scan all configured source repos and build lineage graph
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
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel as PydanticBaseModel

from src.config import settings
from src.engine import analyze_multiple_repos
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


# Lineage-themed favicon: two source nodes deriving into one. Served directly so any
# client probing /favicon.ico gets a 200 instead of cluttering the logs with a 404.
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#0f172a"/>'
    '<path d="M8.5 8.5 L21 16 M8.5 23.5 L21 16" stroke="#475569" stroke-width="2" '
    'fill="none" stroke-linecap="round"/>'
    '<circle cx="8.5" cy="8.5" r="3.4" fill="#60a5fa"/>'
    '<circle cx="8.5" cy="23.5" r="3.4" fill="#34d399"/>'
    '<circle cx="22" cy="16" r="4.2" fill="#a78bfa"/></svg>'
)


@app.get("/", response_class=HTMLResponse)
async def web_ui(request: Request):
    return templates.TemplateResponse(name="index.html", context={"request": request}, request=request)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")


@app.post("/analyze")
async def analyze_repo():
    """Scan all configured source repositories, merge lineage, store in memory.

    With no ``REPOS`` env var, this auto-detects the POC's three sibling sample dirs
    (``sample_repo_sql``, ``sample_repo_python``, ``sample_repo_adf``). Set ``REPOS``
    to a JSON array to point at real repos. Falls back to ``REPO_PATH`` if neither
    is configured.
    """
    global _graph
    repo_sources = settings.get_repo_sources()
    found = [s for s in repo_sources if Path(s.path).is_dir()]
    if not found:
        raise HTTPException(
            status_code=400,
            detail=f"No configured repo paths exist on disk: {[s.path for s in repo_sources]}",
        )
    _graph = analyze_multiple_repos(repo_sources)
    return {
        "status": "ok",
        "repos_scanned": [s.name for s in found],
        "nodes_extracted": len(_graph.nodes),
        "edges_extracted": len(_graph.edges),
    }


@app.post("/analyze-multi")
async def analyze_multi_repo():
    """Deprecated alias for ``POST /analyze`` — kept for older clients."""
    return await analyze_repo()


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
    # A node that exists but has no upstream (e.g. a raw source) is a valid 200 with just
    # itself — not a 404, which would be indistinguishable from "node not found".
    return _bfs(node_id, forward, nodes_by_id, depth)


@app.get("/downstream/{node_id:path}")
async def get_downstream(node_id: str, depth: int = Query(default=10, ge=1, le=50)):
    """Trace downstream impact (what depends on this data?)."""
    _forward, reverse, nodes_by_id = _build_adjacency()
    if node_id not in nodes_by_id:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    # A node that exists but has no downstream (e.g. a terminal report) is a valid 200 with
    # just itself — not a 404, which would be indistinguishable from "node not found".
    return _bfs(node_id, reverse, nodes_by_id, depth)


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
    repo_root: str | None = None
    repo_paths: list[str] | None = None  # source-repo subdirs to scan; falls back to configured sources


@app.post("/pr-check")
async def pr_check(body: PRCheckRequest):
    """Analyse lineage impact between two Git refs across all configured source repos."""
    from src.pr_analyzer import analyze_pr, render_markdown

    repo_root = Path(body.repo_root).resolve() if body.repo_root else Path(".").resolve()
    if not (repo_root / ".git").exists():
        raise HTTPException(status_code=400, detail=f"Not a Git repository: {repo_root}")

    if body.repo_paths:
        repo_paths = body.repo_paths
    else:
        repo_paths = [s.path for s in settings.get_repo_sources()]
    if not repo_paths:
        raise HTTPException(status_code=400, detail="No source repo paths resolved")

    result = analyze_pr(repo_paths, body.base_ref, body.head_ref, repo_root=repo_root)
    return {
        **result.as_dict(),
        "markdown_report": render_markdown(result),
    }
