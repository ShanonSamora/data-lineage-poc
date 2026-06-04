"""
LLM-based lineage interpreter for complex cases.

Handles code that deterministic parsers cannot resolve:
- Dynamic SQL (string-built queries)
- Python/Pandas/PySpark transformations
- Stored procedures with procedural logic
- Complex macros and templates

Uses OpenAI-compatible API to analyze code and extract structured lineage.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from openai import OpenAI

from src.config import settings
from src.models import (
    EdgeType,
    LineageEdge,
    LineageGraph,
    LineageNode,
    NodeType,
)
from src.paths import file_node_id, repo_relative_path

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a data lineage extraction engine. Given source code (SQL, Python, PySpark, etc.),
you MUST identify column-level data lineage and return ONLY a JSON object.

Analyze:
1. Which tables/dataframes are READ (sources).
2. Which tables/dataframes are WRITTEN or produced (targets).
3. For each output column, which input column(s) does it derive from and what transformation is applied.
4. How data flows between tables, dataframes, and functions (reads and writes).

Return this EXACT JSON structure (no markdown, no explanation):
{
  "tables": [
    {"name": "table_name", "type": "TABLE|VIEW|PROCEDURE|PYTHON_FUNCTION"}
  ],
  "columns": [
    {
      "target_table": "output_table",
      "target_column": "output_col",
      "source_table": "input_table",
      "source_column": "input_col",
      "transformation": "description of the transformation"
    }
  ],
  "data_flows": [
    {
      "source": "source_table_or_dataframe",
      "target": "target_table_or_dataframe",
      "operation": "description (e.g., pd.read_sql, merge, groupby, INSERT INTO)"
    }
  ]
}

Rules:
- Use lowercase for all names.
- If a column is computed (e.g. SUM, CASE, concat), list ALL source columns.

For Python/Pandas/PySpark code, emit TABLE entries ONLY for entities that exist at a persistence boundary:
  (a) Database tables/views referenced by literal name in pd.read_sql/spark.read/SELECT (READ boundary).
  (b) DataFrames produced directly by pd.read_sql/spark.read (these are the named DataFrame variables of the READ boundary).
  (c) The literal target name of df.to_sql("X"), spark.write.saveAsTable("X"), or INSERT INTO X (the WRITE boundary).

DO NOT emit TABLE entries for intermediate DataFrames produced by:
  - merge / join / concat results
  - groupby / agg / pivot results
  - apply / transform / map results
  - function returns (e.g. result = build_something(...))
  - variable renames (e.g. df_metrics = result)

For each to_sql("X", ...) call, emit columns entries that map EACH column of X to its ULTIMATE source column in the upstream database tables/views. "See through" the pandas transformations — do not stop at the intermediate DataFrame, trace back to the named table the data originally came from.

DO NOT emit data_flows between intermediate variables.
DO NOT emit data_flows between two PYTHON_FUNCTION entries (function calls are not data lineage and will be discarded).

The "operation" string MUST clearly indicate direction: use "pd.read_sql", "SELECT", "spark.read" for reads; use "to_sql", "INSERT", "saveAsTable" for writes.
For dynamic SQL, infer the likely tables/columns from the string template.
Return empty arrays if no lineage can be determined.
"""


def interpret_with_llm(
    file_path: str | Path,
    code: str,
    source_repo: str = "",
    repo_root: str | Path | None = None,
) -> LineageGraph:
    """
    Send code to an LLM and parse the structured lineage response.

    Args:
        file_path: Path to the source file (for metadata).
        code: Source code content to analyze.
        source_repo: Repo name used to build a stable, relative FILE-node ID.
        repo_root: Root the FILE-node ID is made relative to (see ``src.paths``).

    Returns:
        LineageGraph with LLM-inferred nodes and edges.
    """
    graph = LineageGraph()
    file_id = file_node_id(file_path, repo_root, source_repo)
    file_rel = repo_relative_path(file_path, repo_root)

    if not settings.openai_api_key or settings.openai_api_key.startswith("sk-your"):
        logger.warning("OpenAI API key not configured — skipping LLM interpretation for %s", file_path)
        return graph

    client = OpenAI(api_key=settings.openai_api_key)

    try:
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"File: {file_path}\n\n```\n{code}\n```"},
            ],
            temperature=0.0,
            max_tokens=16384,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.error("LLM API call failed for %s: %s", file_path, e)
        return graph

    if response.choices[0].finish_reason == "length":
        logger.warning(
            "LLM response truncated for %s (hit max_tokens=16384) — lineage may be incomplete",
            file_path,
        )

    raw = response.choices[0].message.content or ""

    # Strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned invalid JSON for %s: %.200s", file_path, raw)
        return graph

    # Build graph from LLM response
    file_node = LineageNode(
        id=file_id,
        name=Path(file_path).name,
        node_type=NodeType.FILE,
        metadata={"path": file_rel},
    )
    graph.nodes.append(file_node)

    for tbl in data.get("tables", []):
        name = tbl["name"].lower()
        ttype = tbl.get("type", "TABLE").upper()
        ntype = {
            "VIEW": NodeType.VIEW,
            "PROCEDURE": NodeType.PROCEDURE,
            "PYTHON_FUNCTION": NodeType.PYTHON_FUNCTION,
        }.get(ttype, NodeType.TABLE)

        graph.nodes.append(LineageNode(
            id=name,
            name=name,
            node_type=ntype,
            metadata={"file": file_rel, "source": "llm"},
        ))
        graph.edges.append(LineageEdge(
            source_id=name,
            target_id=file_id,
            edge_type=EdgeType.DEFINED_IN,
        ))

    for col in data.get("columns", []):
        if not col.get("target_table") or not col.get("source_table"):
            continue
        target_table = col["target_table"].lower()
        target_col = (col.get("target_column") or "").lower()
        source_table = col["source_table"].lower()
        source_col = (col.get("source_column") or "").lower()
        if not target_col or not source_col:
            continue
        transformation = col.get("transformation", "")

        target_col_id = f"{target_table}.{target_col}"
        source_col_id = f"{source_table}.{source_col}"

        graph.nodes.append(LineageNode(
            id=target_col_id, name=target_col,
            node_type=NodeType.COLUMN,
            metadata={"table": target_table, "source": "llm"},
        ))
        graph.nodes.append(LineageNode(
            id=source_col_id, name=source_col,
            node_type=NodeType.COLUMN,
            metadata={"table": source_table, "source": "llm"},
        ))

        # HAS_COLUMN edges so the prune pass recognizes these tables as schematized.
        # Duplicates are deduplicated by LineageGraph.merge().
        graph.edges.append(LineageEdge(
            source_id=target_table,
            target_id=target_col_id,
            edge_type=EdgeType.HAS_COLUMN,
        ))
        graph.edges.append(LineageEdge(
            source_id=source_table,
            target_id=source_col_id,
            edge_type=EdgeType.HAS_COLUMN,
        ))

        graph.edges.append(LineageEdge(
            source_id=target_col_id,
            target_id=source_col_id,
            edge_type=EdgeType.DERIVES_FROM,
            transformation=transformation,
            confidence=0.85,
            metadata={"source": "llm"},
        ))

    existing_node_ids = {n.id for n in graph.nodes}
    # Existing PYTHON_FUNCTION ids — used to drop function-call edges that aren't data lineage.
    function_ids = {n.id for n in graph.nodes if n.node_type == NodeType.PYTHON_FUNCTION}

    for flow in data.get("data_flows", []):
        source = flow.get("source", "").lower()
        target = flow.get("target", "").lower()
        operation = flow.get("operation", "")
        if not source or not target:
            continue

        # Drop function-to-function "calls" edges: data lineage only, not invocation graph.
        if source in function_ids and target in function_ids:
            continue

        for name in (source, target):
            if name not in existing_node_ids:
                graph.nodes.append(LineageNode(
                    id=name,
                    name=name,
                    node_type=NodeType.TABLE,
                    metadata={"file": file_rel, "source": "llm"},
                ))
                existing_node_ids.add(name)

        # Classify the flow: write-like operations (to_sql, INSERT, saveAsTable) produce
        # WRITES_TO edges in the source → target direction. Reads (read_sql, SELECT, merge,
        # groupby, etc.) produce READS_FROM in target → source (consumer → producer) form.
        op_lower = operation.lower()
        is_write = any(kw in op_lower for kw in (
            "to_sql", "insert", "saveastable", "write", "writeto", "writes_to",
            "df.to_", "spark.write", "savemode",
        ))
        if is_write:
            graph.edges.append(LineageEdge(
                source_id=source,
                target_id=target,
                edge_type=EdgeType.WRITES_TO,
                transformation=operation,
                confidence=0.85,
                metadata={"source": "llm"},
            ))
        else:
            graph.edges.append(LineageEdge(
                source_id=target,
                target_id=source,
                edge_type=EdgeType.READS_FROM,
                transformation=operation,
                confidence=0.85,
                metadata={"source": "llm"},
            ))

    return graph
