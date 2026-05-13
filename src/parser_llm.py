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
- For Python/Pandas, treat DataFrames as tables. Use variable names as table names.
  Identify which database tables each DataFrame reads from or writes to.
  If a function reads a table into a DataFrame, add a data_flow from the database table to the DataFrame.
  If a DataFrame writes to a table (e.g. to_sql), add a data_flow from the DataFrame to the table.
  If a function takes DataFrames as input and produces a new one, add data_flows from input DataFrames to the output.
- For PYTHON_FUNCTION entries, add data_flows connecting them to the DataFrames/tables they read and produce.
- For dynamic SQL, infer the likely tables/columns from the string template.
- Include data_flows for EVERY read/write/transform relationship between entities.
- Return empty arrays if no lineage can be determined.
"""


def interpret_with_llm(file_path: str | Path, code: str) -> LineageGraph:
    """
    Send code to an LLM and parse the structured lineage response.

    Args:
        file_path: Path to the source file (for metadata).
        code: Source code content to analyze.

    Returns:
        LineageGraph with LLM-inferred nodes and edges.
    """
    graph = LineageGraph()

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
            max_tokens=4096,
        )
    except Exception as e:
        logger.error("LLM API call failed for %s: %s", file_path, e)
        return graph

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
        id=str(file_path),
        name=Path(file_path).name,
        node_type=NodeType.FILE,
        metadata={"path": str(file_path)},
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
            metadata={"file": str(file_path), "source": "llm"},
        ))
        graph.edges.append(LineageEdge(
            source_id=name,
            target_id=str(file_path),
            edge_type=EdgeType.DEFINED_IN,
        ))

    for col in data.get("columns", []):
        target_table = col["target_table"].lower()
        target_col = col["target_column"].lower()
        source_table = col["source_table"].lower()
        source_col = col["source_column"].lower()
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

        graph.edges.append(LineageEdge(
            source_id=target_col_id,
            target_id=source_col_id,
            edge_type=EdgeType.DERIVES_FROM,
            transformation=transformation,
            confidence=0.85,
            metadata={"source": "llm"},
        ))

    existing_node_ids = {n.id for n in graph.nodes}
    for flow in data.get("data_flows", []):
        source = flow.get("source", "").lower()
        target = flow.get("target", "").lower()
        operation = flow.get("operation", "")
        if not source or not target:
            continue

        for name in (source, target):
            if name not in existing_node_ids:
                graph.nodes.append(LineageNode(
                    id=name,
                    name=name,
                    node_type=NodeType.TABLE,
                    metadata={"file": str(file_path), "source": "llm"},
                ))
                existing_node_ids.add(name)

        graph.edges.append(LineageEdge(
            source_id=target,
            target_id=source,
            edge_type=EdgeType.READS_FROM,
            transformation=operation,
            confidence=0.85,
            metadata={"source": "llm"},
        ))

    return graph
