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

import hashlib
import logging
import re
from pathlib import Path

from src.models import LineageGraph
from src.parser_sql import parse_sql_file
from src.parser_llm import interpret_with_llm
from src.parser_adf import is_adf_file, parse_adf_file
from src.paths import repo_relative_path

logger = logging.getLogger(__name__)

# A shared per-run cache maps (source_repo, relative_path, content_hash) → LineageGraph.
# Used by the PR analyser so an unchanged file is analysed once and reused for both the
# base and head graphs. This both halves LLM cost and — critically — eliminates phantom
# diffs from LLM non-determinism: the same file content always yields the identical
# sub-graph instead of two slightly different LLM paraphrases.
FileCache = dict

# SQL constructs that signal the deterministic parser may be insufficient.
# These patterns are matched with case-insensitive word boundaries to avoid
# false positives like the `END` in `CASE WHEN ... END` or the `BEGIN` in
# a column comment.
_LLM_TRIGGER_PATTERNS = [
    re.compile(r"\bCREATE\s+(OR\s+REPLACE\s+)?PROCEDURE\b", re.IGNORECASE),
    re.compile(r"\bCREATE\s+(OR\s+REPLACE\s+)?FUNCTION\b", re.IGNORECASE),
    re.compile(r"\bLANGUAGE\s+plpgsql\b", re.IGNORECASE),
    re.compile(r"\bEXECUTE\s+IMMEDIATE\b", re.IGNORECASE),
    re.compile(r"\bEXEC\s*\(", re.IGNORECASE),
    re.compile(r"\bsp_executesql\b", re.IGNORECASE),
    # Procedural blocks: BEGIN followed by a DECLARE or another procedural statement.
    re.compile(r"\bBEGIN\b[\s\r\n]+(DECLARE|SET\s+@|EXEC|EXECUTE|IF\s|WHILE\s)", re.IGNORECASE),
    # PL/pgSQL DECLARE block — `DECLARE ... BEGIN`, not the column-level DECLARE.
    re.compile(r"\bDECLARE\b[\s\S]{0,500}?\bBEGIN\b", re.IGNORECASE),
]

SQL_EXTENSIONS = {".sql", ".ddl", ".dml", ".hql"}
PYTHON_EXTENSIONS = {".py", ".pyspark"}
ADF_EXTENSIONS = {".json"}
ALL_EXTENSIONS = SQL_EXTENSIONS | PYTHON_EXTENSIONS | ADF_EXTENSIONS


def _needs_llm_fallback(sql_text: str, deterministic_graph: LineageGraph) -> bool:
    """Decide whether an LLM pass is needed for a SQL file.

    Triggers if:
      1. The file contains procedural constructs the SQL parser can't fully resolve
         (stored procedures, EXECUTE IMMEDIATE, dynamic SQL, PL/pgSQL blocks).
      2. The deterministic parser produced zero column-level edges despite the file
         being non-trivial — suggests the content is opaque (e.g. all dynamic SQL).
    """
    for pattern in _LLM_TRIGGER_PATTERNS:
        if pattern.search(sql_text):
            return True

    from src.models import EdgeType
    lineage_edges = [e for e in deterministic_graph.edges if e.edge_type == EdgeType.DERIVES_FROM]
    if not lineage_edges and len(sql_text.strip()) > 100:
        return True

    return False


def analyze_file(
    file_path: str | Path,
    source_repo: str = "",
    repo_root: str | Path | None = None,
    cache: FileCache | None = None,
) -> LineageGraph:
    """
    Analyze a single file and return its lineage graph.

    Uses the hybrid strategy: deterministic first, LLM fallback when needed.

    ``repo_root`` makes FILE-node IDs relative (stable across machines / worktrees).
    ``cache`` (optional) memoises results by file content so an unchanged file is
    analysed once — see ``FileCache``.
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()
    if suffix not in ALL_EXTENSIONS:
        logger.debug("Skipping unsupported file type: %s", file_path)
        return LineageGraph()

    content = file_path.read_text(encoding="utf-8", errors="replace")

    cache_key = None
    if cache is not None:
        rel = repo_relative_path(file_path, repo_root)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        cache_key = (source_repo, rel, digest)
        hit = cache.get(cache_key)
        if hit is not None:
            logger.debug("Cache hit for %s", rel)
            return hit.model_copy(deep=True)

    if suffix in SQL_EXTENSIONS:
        result = _analyze_sql(file_path, content, source_repo, repo_root)
    elif suffix in PYTHON_EXTENSIONS:
        result = _analyze_python(file_path, content, source_repo, repo_root)
    elif suffix in ADF_EXTENSIONS and is_adf_file(file_path):
        result = parse_adf_file(file_path, content, source_repo=source_repo, repo_root=repo_root)
    else:
        logger.debug("Skipping unsupported file type: %s", file_path)
        result = LineageGraph()

    if cache_key is not None:
        cache[cache_key] = result.model_copy(deep=True)
    return result


def _analyze_sql(
    file_path: Path, content: str, source_repo: str = "", repo_root: str | Path | None = None
) -> LineageGraph:
    """Hybrid analysis for SQL files."""
    # Step 1: deterministic parse
    det_graph = parse_sql_file(file_path, content, source_repo, repo_root)
    logger.info(
        "Deterministic parse of %s: %d nodes, %d edges",
        file_path.name, len(det_graph.nodes), len(det_graph.edges),
    )

    # Step 2: check if LLM fallback is needed
    if _needs_llm_fallback(content, det_graph):
        logger.info("LLM fallback triggered for %s", file_path.name)
        llm_graph = interpret_with_llm(file_path, content, source_repo, repo_root)
        det_graph.merge(llm_graph)
        logger.info(
            "After LLM merge: %d nodes, %d edges",
            len(det_graph.nodes), len(det_graph.edges),
        )

    # Step 3: wire stored-procedure nodes to the tables they build.
    _link_procedures_to_outputs(content, det_graph)

    return det_graph


_PROC_DEF_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION)\s+([A-Za-z0-9_.\"]+)",
    re.IGNORECASE,
)


def _link_procedures_to_outputs(content: str, graph: LineageGraph) -> None:
    """Connect a stored-procedure node to the tables it builds.

    The LLM extracts a procedure's column lineage but attributes the WRITES_TO /
    DERIVES_FROM edges to the *source* tables, leaving the PROCEDURE node (created by the
    ADF parser from a pipeline's stored-procedure activity) with no link to the table it
    actually populates. We find each CREATE PROCEDURE/FUNCTION in the file and add a
    WRITES_TO edge from it to every table DEFINED_IN the same file, so the orchestration
    chain (pipeline → procedure → table) connects instead of dead-ending. The procedure id
    matches the ADF parser's convention (schema dropped, lowercased) so the nodes merge.
    """
    from src.models import EdgeType, LineageEdge, LineageNode, NodeType

    proc_ids = [
        m.group(1).replace('"', "").split(".")[-1].lower()
        for m in _PROC_DEF_RE.finditer(content)
    ]
    proc_ids = [p for p in proc_ids if p]
    if not proc_ids:
        return

    node_type = {n.id: n.node_type for n in graph.nodes}
    # Tables the procedure *writes* are the targets of the WRITES_TO edges the LLM produced
    # for its INSERT/UPDATE statements (e.g. "<source> WRITES_TO rpt_monthly_summary").
    # Using WRITES_TO targets — not every table DEFINED_IN the file — avoids wrongly linking
    # the procedure to the source tables it only reads.
    output_tables = {
        e.target_id for e in graph.edges
        if e.edge_type == EdgeType.WRITES_TO
        and node_type.get(e.target_id) in (NodeType.TABLE, NodeType.VIEW)
    }
    if not output_tables:
        return

    existing = {(e.source_id, e.target_id, e.edge_type) for e in graph.edges}
    have_node = {n.id for n in graph.nodes}
    for proc_id in proc_ids:
        if proc_id not in have_node:
            graph.nodes.append(LineageNode(
                id=proc_id, name=proc_id, node_type=NodeType.PROCEDURE,
                metadata={"source": "sql-procedure"},
            ))
            have_node.add(proc_id)
        for tbl in output_tables:
            key = (proc_id, tbl, EdgeType.WRITES_TO)
            if key not in existing:
                graph.edges.append(LineageEdge(
                    source_id=proc_id, target_id=tbl, edge_type=EdgeType.WRITES_TO,
                    transformation="stored procedure output",
                ))
                existing.add(key)


_PROC_DEF_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION)\s+([A-Za-z0-9_.\"]+)",
    re.IGNORECASE,
)


def _link_procedures_to_outputs(content: str, graph: LineageGraph) -> None:
    """Connect a stored-procedure node to the tables it builds.

    The LLM extracts a procedure's column lineage but attributes the WRITES_TO /
    DERIVES_FROM edges to the *source* tables, leaving the PROCEDURE node (created by the
    ADF parser from a pipeline's stored-procedure activity) with no link to the table it
    actually populates. We find each CREATE PROCEDURE/FUNCTION in the file and add a
    WRITES_TO edge from it to every table DEFINED_IN the same file, so the orchestration
    chain (pipeline → procedure → table) connects instead of dead-ending. The procedure id
    matches the ADF parser's convention (schema dropped, lowercased) so the nodes merge.
    """
    from src.models import EdgeType, LineageEdge, LineageNode, NodeType

    proc_ids = [
        m.group(1).replace('"', "").split(".")[-1].lower()
        for m in _PROC_DEF_RE.finditer(content)
    ]
    proc_ids = [p for p in proc_ids if p]
    if not proc_ids:
        return

    node_type = {n.id: n.node_type for n in graph.nodes}
    # Tables the procedure *writes* are the targets of the WRITES_TO edges the LLM produced
    # for its INSERT/UPDATE statements (e.g. "<source> WRITES_TO rpt_monthly_summary").
    # Using WRITES_TO targets — not every table DEFINED_IN the file — avoids wrongly linking
    # the procedure to the source tables it only reads.
    output_tables = {
        e.target_id for e in graph.edges
        if e.edge_type == EdgeType.WRITES_TO
        and node_type.get(e.target_id) in (NodeType.TABLE, NodeType.VIEW)
    }
    if not output_tables:
        return

    existing = {(e.source_id, e.target_id, e.edge_type) for e in graph.edges}
    have_node = {n.id for n in graph.nodes}
    for proc_id in proc_ids:
        if proc_id not in have_node:
            graph.nodes.append(LineageNode(
                id=proc_id, name=proc_id, node_type=NodeType.PROCEDURE,
                metadata={"source": "sql-procedure"},
            ))
            have_node.add(proc_id)
        for tbl in output_tables:
            key = (proc_id, tbl, EdgeType.WRITES_TO)
            if key not in existing:
                graph.edges.append(LineageEdge(
                    source_id=proc_id, target_id=tbl, edge_type=EdgeType.WRITES_TO,
                    transformation="stored procedure output",
                ))
                existing.add(key)


def _analyze_python(
    file_path: Path, content: str, source_repo: str = "", repo_root: str | Path | None = None
) -> LineageGraph:
    """Python files always require LLM interpretation."""
    logger.info("Sending Python file to LLM: %s", file_path.name)
    return interpret_with_llm(file_path, content, source_repo, repo_root)


def _analyze_directory_raw(
    directory: Path, recursive: bool, source_repo: str, cache: FileCache | None = None
) -> LineageGraph:
    """Scan a directory without running the post-pass prune.

    Used internally by ``analyze_multiple_repos`` so pruning runs once on the merged
    graph — otherwise cross-repo nodes (e.g. an LLM-detected table in the Python repo
    that's properly schematized by the SQL repo) get dropped before they can connect.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    graph = LineageGraph()
    pattern = "**/*" if recursive else "*"
    scannable = SQL_EXTENSIONS | PYTHON_EXTENSIONS | ADF_EXTENSIONS
    for path in sorted(directory.glob(pattern)):
        if path.is_file() and path.suffix.lower() in scannable:
            try:
                # FILE-node IDs are made relative to the repo directory so they're
                # stable across the base/head worktrees compared during a PR check.
                partial = analyze_file(path, source_repo=source_repo, repo_root=directory, cache=cache)
                graph.merge(partial)
            except Exception as e:
                logger.error("Failed to analyze %s: %s", path, e)
    return graph


def analyze_directory(
    directory: str | Path, recursive: bool = True, source_repo: str = "", cache: FileCache | None = None
) -> LineageGraph:
    """
    Scan a directory for SQL, Python, and ADF files and build a unified lineage graph.
    """
    directory = Path(directory)
    graph = _analyze_directory_raw(directory, recursive, source_repo, cache)
    _prune_dead_nodes(graph)

    logger.info(
        "Directory scan complete: %d files → %d nodes, %d edges",
        sum(1 for n in graph.nodes if n.node_type.value == "FILE"),
        len(graph.nodes),
        len(graph.edges),
    )
    return graph


def _prune_dead_nodes(graph: LineageGraph) -> None:
    """Drop nodes that don't carry meaningful lineage.

    Two pruning rules, applied iteratively until no more nodes are removed:

    1. *Orphan* nodes — only have ``DEFINED_IN``/``HAS_COLUMN`` edges as source, no
       real lineage edges. These are typically local variables the LLM noticed but
       couldn't connect.

    2. *Schemaless LLM tables* — TABLE nodes with ``metadata.source == "llm"`` that
       have no ``HAS_COLUMN`` edges. These are intermediate pandas variables
       (`balance_agg`, `df_scores`, etc.) that the LLM emitted as tables but
       couldn't enumerate columns for. They add noise without value.

    Protected node types (always kept regardless): FILE, PROCEDURE, PYTHON_FUNCTION,
    ADF_PIPELINE, ADF_DATASET, ADF_DATAFLOW."""
    from src.models import EdgeType, NodeType

    PROTECTED = {NodeType.FILE, NodeType.PROCEDURE, NodeType.PYTHON_FUNCTION,
                 NodeType.ADF_PIPELINE, NodeType.ADF_DATASET, NodeType.ADF_DATAFLOW}

    total_pruned = 0
    # Iterate until stable — pruning one node may create new orphans.
    for _ in range(10):
        edges_by_node: dict[str, list] = {}
        has_column_count: dict[str, int] = {}
        for e in graph.edges:
            edges_by_node.setdefault(e.source_id, []).append(e)
            edges_by_node.setdefault(e.target_id, []).append(e)
            if e.edge_type == EdgeType.HAS_COLUMN:
                has_column_count[e.source_id] = has_column_count.get(e.source_id, 0) + 1

        dead_ids: set[str] = set()
        for n in graph.nodes:
            if n.node_type in PROTECTED:
                continue
            edges = edges_by_node.get(n.id, [])

            # Rule 1: orphan (no edges except an outgoing DEFINED_IN to the file).
            # HAS_COLUMN edges count as meaningful — a node with columns is a real schematized
            # table even if no upstream/downstream lineage points at it yet (e.g. staging tables
            # before view files are parsed).
            meaningful = [
                e for e in edges
                if not (e.source_id == n.id and e.edge_type == EdgeType.DEFINED_IN)
            ]
            if not meaningful:
                dead_ids.add(n.id)
                continue

            # Rule 2: schemaless LLM table — TABLE with source=llm metadata but no HAS_COLUMN.
            if (
                n.node_type == NodeType.TABLE
                and (n.metadata or {}).get("source") == "llm"
                and has_column_count.get(n.id, 0) == 0
            ):
                dead_ids.add(n.id)

        if not dead_ids:
            break

        total_pruned += len(dead_ids)
        graph.nodes = [n for n in graph.nodes if n.id not in dead_ids]
        graph.edges = [
            e for e in graph.edges
            if e.source_id not in dead_ids and e.target_id not in dead_ids
        ]

    if total_pruned:
        logger.info("Pruned %d dead/schemaless nodes (and their incident edges)", total_pruned)


def analyze_multiple_repos(
    repo_sources: list | None = None, cache: FileCache | None = None
) -> LineageGraph:
    """
    Scan multiple repositories and merge their lineage into a unified graph.

    Parameters
    ----------
    repo_sources : list of RepoSource objects (from config). If None, reads from settings.
    cache : optional per-run file cache (see ``FileCache``). Pass the *same* cache
            object when building both the base and head graphs of a PR check so
            unchanged files are analysed once and produce identical sub-graphs.

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
        # Skip pruning per-repo so cross-repo references survive the merge.
        partial = _analyze_directory_raw(repo_dir, True, repo_src.name, cache)

        # Tag all nodes with the source repo name
        for node in partial.nodes:
            if not node.source_repo:
                node.source_repo = repo_src.name

        graph.merge(partial)

    # Single prune pass on the fully-merged graph.
    _prune_dead_nodes(graph)

    # Propagate columns onto raw-source ADF datasets (Blob CSV/parquet) from the
    # staging table they copy into. Their JSON declares only file location, but
    # in practice they carry the same schema as the sink they land in.
    _propagate_copy_source_columns(graph)

    # Infer export-sink schemas: an external Copy target (e.g. the BI mart) has no DDL,
    # but a no-mapping Copy moves columns 1:1 from the SQL source it reads.
    _propagate_copy_sink_columns(graph)

    logger.info(
        "Multi-repo scan complete: %d repos → %d nodes, %d edges",
        len(repo_sources), len(graph.nodes), len(graph.edges),
    )
    return graph


def _propagate_copy_sink_columns(graph: LineageGraph) -> None:
    """Infer an export sink's schema from the SQL source it is copied from.

    An ADF Copy to an external system (e.g. SqlRptCustomerExposure → ExternalReportingMart,
    landing in customer_exposure_snapshot) leaves the sink schemaless — that table lives in
    another system with no DDL in the repo. A Copy with no explicit column mapping moves
    columns 1:1, so we give the sink table the source table's columns and add inferred
    DERIVES_FROM edges (confidence 0.9, ``metadata.source = "inferred-copy"``) so the export
    surfaces as column-level lineage instead of a dead, schemaless node.
    """
    from src.models import EdgeType, LineageEdge, LineageNode, NodeType

    # ADF dataset → physical table aliases, and HAS_COLUMN ownership.
    alias_for: dict[str, str] = {}
    for e in graph.edges:
        if e.edge_type == EdgeType.READS_FROM and "ADF dataset" in (e.transformation or ""):
            alias_for[e.source_id] = e.target_id

    has_col_by_owner: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.edge_type == EdgeType.HAS_COLUMN:
            has_col_by_owner.setdefault(e.source_id, []).append(e.target_id)

    new_nodes: list[LineageNode] = []
    new_edges: list[LineageEdge] = []
    for e in graph.edges:
        if e.edge_type != EdgeType.COPIES_TO:
            continue
        src_tbl = alias_for.get(e.source_id)   # SQL source behind the source dataset
        sink_tbl = alias_for.get(e.target_id)  # external table behind the sink dataset
        if not src_tbl or not sink_tbl:
            continue
        if has_col_by_owner.get(sink_tbl):
            continue  # sink already has a real schema — don't fabricate
        src_cols = has_col_by_owner.get(src_tbl, [])
        if not src_cols:
            continue
        for src_col_id in src_cols:
            col_name = src_col_id.split(".")[-1]
            sink_col_id = f"{sink_tbl}.{col_name}"
            new_nodes.append(LineageNode(
                id=sink_col_id, name=col_name, node_type=NodeType.COLUMN,
                metadata={"table": sink_tbl, "source": "inferred-copy"},
            ))
            new_edges.append(LineageEdge(
                source_id=sink_tbl, target_id=sink_col_id, edge_type=EdgeType.HAS_COLUMN,
                confidence=0.9, metadata={"source": "inferred-copy"},
            ))
            new_edges.append(LineageEdge(
                source_id=sink_col_id, target_id=src_col_id, edge_type=EdgeType.DERIVES_FROM,
                transformation="1:1 ADF copy (inferred)", confidence=0.9,
                metadata={"source": "inferred-copy"},
            ))

    if new_nodes:
        graph.nodes.extend(new_nodes)
        graph.edges.extend(new_edges)
        logger.info("Inferred %d export-sink columns (1:1 copy) from SQL sources", len(new_nodes))


def _propagate_copy_source_columns(graph: LineageGraph) -> None:
    """For each ADF_DATASET with no HAS_COLUMN edges, inherit columns from its COPIES_TO sink.

    Raw blob datasets (CSV, parquet) declare a file location but no schema. The sink
    they get copied into is usually a SQL-backed dataset aliased to a staging table
    that does have a full schema. We add HAS_COLUMN edges from the source dataset
    to those staging columns so the source dataset surfaces a schema in the UI and
    JSON export. The propagated edges are tagged ``metadata.source = "propagated"``
    and given confidence 0.9 so they're distinguishable from deterministic schemas.
    """
    from src.models import EdgeType, LineageEdge, NodeType

    # Index HAS_COLUMN edges by source (the owning table/dataset).
    has_col_by_owner: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.edge_type == EdgeType.HAS_COLUMN:
            has_col_by_owner.setdefault(e.source_id, []).append(e.target_id)

    # Index COPIES_TO edges (source dataset -> sink dataset).
    copies_to_by_source: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.edge_type == EdgeType.COPIES_TO:
            copies_to_by_source.setdefault(e.source_id, []).append(e.target_id)

    # Index ADF dataset → physical table aliases (READS_FROM with the alias marker).
    alias_for: dict[str, str] = {}
    for e in graph.edges:
        if e.edge_type == EdgeType.READS_FROM and "ADF dataset" in (e.transformation or ""):
            alias_for[e.source_id] = e.target_id

    new_edges: list[LineageEdge] = []
    for node in graph.nodes:
        if node.node_type != NodeType.ADF_DATASET:
            continue
        if has_col_by_owner.get(node.id):
            continue  # already has its own columns
        if node.id in alias_for:
            continue  # SQL-backed datasets surface columns via alias resolution
        # Follow COPIES_TO -> sink dataset -> (alias) -> physical table
        for sink_id in copies_to_by_source.get(node.id, []):
            cols = has_col_by_owner.get(sink_id) or has_col_by_owner.get(alias_for.get(sink_id, ""), [])
            for col_id in cols:
                new_edges.append(LineageEdge(
                    source_id=node.id,
                    target_id=col_id,
                    edge_type=EdgeType.HAS_COLUMN,
                    transformation=f"inherited from {sink_id}",
                    confidence=0.9,
                    metadata={"source": "propagated"},
                ))

    if new_edges:
        # Deduplicate (source, target, type) — a Blob dataset may copy to multiple sinks.
        seen: set[tuple[str, str, str]] = set()
        for existing in graph.edges:
            if existing.edge_type == EdgeType.HAS_COLUMN:
                seen.add((existing.source_id, existing.target_id, existing.edge_type.value))
        added = 0
        for e in new_edges:
            key = (e.source_id, e.target_id, e.edge_type.value)
            if key not in seen:
                seen.add(key)
                graph.edges.append(e)
                added += 1
        if added:
            logger.info("Propagated %d HAS_COLUMN edges to raw-source ADF datasets", added)
