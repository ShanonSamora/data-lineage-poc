"""
Hybrid lineage engine — the core orchestrator.

Strategy:
1. Classify each file by type (SQL, Python, ADF JSON, etc.).
2. For SQL files: run deterministic parser first.
   - If the parser produces meaningful lineage → use it (confidence=1.0).
   - If the file contains constructs the parser can't handle (stored procs,
     dynamic SQL, PL/pgSQL blocks) → fall back to LLM.
3. For Python files: always use LLM (sqlglot can't parse Python).
4. For ADF JSON files: deterministic parse of pipeline/dataset/dataflow definitions.
5. Merge all partial graphs into a single unified lineage graph.
6. Multi-repo: scan multiple directories and tag each node with its source_repo.
"""
from __future__ import annotations

import logging
from pathlib import Path

from src.models import LineageGraph
from src.parser_sql import parse_sql_file
from src.parser_llm import interpret_with_llm
from src.parser_adf import is_adf_file, parse_adf_file

logger = logging.getLogger(__name__)

# SQL constructs that signal the deterministic parser may be insufficient
_LLM_TRIGGER_KEYWORDS = [
    "CREATE OR REPLACE PROCEDURE",
    "CREATE OR REPLACE FUNCTION",
    "CREATE PROCEDURE",
    "CREATE FUNCTION",
    "LANGUAGE plpgsql",
    "EXECUTE IMMEDIATE",
    "EXEC(",
    "sp_executesql",
    "DECLARE",
    "BEGIN",
]

SQL_EXTENSIONS = {".sql", ".ddl", ".dml", ".hql"}
PYTHON_EXTENSIONS = {".py", ".pyspark"}
ADF_EXTENSIONS = {".json"}
ALL_EXTENSIONS = SQL_EXTENSIONS | PYTHON_EXTENSIONS | ADF_EXTENSIONS


def _needs_llm_fallback(sql_text: str, deterministic_graph: LineageGraph) -> bool:
    """Decide whether an LLM pass is needed for a SQL file."""
    upper = sql_text.upper()
    for keyword in _LLM_TRIGGER_KEYWORDS:
        if keyword.upper() in upper:
            return True

    # If deterministic parser found zero lineage edges, hand off to LLM
    from src.models import EdgeType
    lineage_edges = [e for e in deterministic_graph.edges if e.edge_type == EdgeType.DERIVES_FROM]
    if not lineage_edges and len(sql_text.strip()) > 100:
        return True

    return False


def analyze_file(file_path: str | Path, source_repo: str = "") -> LineageGraph:
    """
    Analyze a single file and return its lineage graph.

    Uses the hybrid strategy: deterministic first, LLM fallback when needed.
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()
    content = file_path.read_text(encoding="utf-8", errors="replace")

    if suffix in SQL_EXTENSIONS:
        return _analyze_sql(file_path, content)
    elif suffix in PYTHON_EXTENSIONS:
        return _analyze_python(file_path, content)
    elif suffix in ADF_EXTENSIONS and is_adf_file(file_path):
        return parse_adf_file(file_path, content, source_repo=source_repo)
    else:
        logger.debug("Skipping unsupported file type: %s", file_path)
        return LineageGraph()


def _analyze_sql(file_path: Path, content: str) -> LineageGraph:
    """Hybrid analysis for SQL files."""
    # Step 1: deterministic parse
    det_graph = parse_sql_file(file_path, content)
    logger.info(
        "Deterministic parse of %s: %d nodes, %d edges",
        file_path.name, len(det_graph.nodes), len(det_graph.edges),
    )

    # Step 2: check if LLM fallback is needed
    if _needs_llm_fallback(content, det_graph):
        logger.info("LLM fallback triggered for %s", file_path.name)
        llm_graph = interpret_with_llm(file_path, content)
        det_graph.merge(llm_graph)
        logger.info(
            "After LLM merge: %d nodes, %d edges",
            len(det_graph.nodes), len(det_graph.edges),
        )

    return det_graph


def _analyze_python(file_path: Path, content: str) -> LineageGraph:
    """Python files always require LLM interpretation."""
    logger.info("Sending Python file to LLM: %s", file_path.name)
    return interpret_with_llm(file_path, content)


def analyze_directory(directory: str | Path, recursive: bool = True, source_repo: str = "") -> LineageGraph:
    """
    Scan a directory for SQL, Python, and ADF files and build a unified lineage graph.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    graph = LineageGraph()
    pattern = "**/*" if recursive else "*"

    scannable = SQL_EXTENSIONS | PYTHON_EXTENSIONS | ADF_EXTENSIONS
    for path in sorted(directory.glob(pattern)):
        if path.is_file() and path.suffix.lower() in scannable:
            try:
                partial = analyze_file(path, source_repo=source_repo)
                graph.merge(partial)
            except Exception as e:
                logger.error("Failed to analyze %s: %s", path, e)

    logger.info(
        "Directory scan complete: %d files → %d nodes, %d edges",
        sum(1 for n in graph.nodes if n.node_type.value == "FILE"),
        len(graph.nodes),
        len(graph.edges),
    )
    return graph


def analyze_multiple_repos(repo_sources: list | None = None) -> LineageGraph:
    """
    Scan multiple repositories and merge their lineage into a unified graph.

    Parameters
    ----------
    repo_sources : list of RepoSource objects (from config). If None, reads from settings.

    Returns
    -------
    A single LineageGraph spanning all configured repositories, with each node
    tagged with its ``source_repo``.
    """
    if repo_sources is None:
        from src.config import settings
        repo_sources = settings.get_repo_sources()

    graph = LineageGraph()
    for repo_src in repo_sources:
        repo_dir = Path(repo_src.path)
        if not repo_dir.is_dir():
            logger.warning("Repo path not found, skipping: %s (%s)", repo_src.name, repo_dir)
            continue

        logger.info("Scanning repo '%s' at %s", repo_src.name, repo_dir.resolve())
        partial = analyze_directory(repo_dir, source_repo=repo_src.name)

        # Tag all nodes with the source repo name
        for node in partial.nodes:
            if not node.source_repo:
                node.source_repo = repo_src.name

        graph.merge(partial)

    logger.info(
        "Multi-repo scan complete: %d repos → %d nodes, %d edges",
        len(repo_sources), len(graph.nodes), len(graph.edges),
    )
    return graph
