"""
Azure Data Factory (ADF) pipeline parser.

Extracts lineage from ADF pipeline JSON definitions. Handles:
- Copy activities:  dataset → dataset (COPIES_TO)
- Data flows:       source → sink mappings
- Pipeline triggers: pipeline → pipeline (TRIGGERS)
- Execute Pipeline activities: pipeline → pipeline dependencies

ADF pipeline definitions are stored as JSON files in a Git repository
(typically under the ``pipeline/``, ``dataset/``, and ``dataflow/`` folders
following the ARM template or ADF Git integration structure).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from src.models import (
    EdgeType,
    LineageEdge,
    LineageGraph,
    LineageNode,
    NodeType,
)

logger = logging.getLogger(__name__)

ADF_EXTENSIONS = {".json"}

# Subfolders recognised as ADF artefact containers
_ADF_FOLDERS = {"pipeline", "dataset", "dataflow", "linkedService", "trigger"}


def is_adf_file(file_path: Path) -> bool:
    """Return True if the file looks like an ADF JSON artefact."""
    if file_path.suffix.lower() != ".json":
        return False
    # Check if any parent folder matches ADF convention
    parts = {p.lower() for p in file_path.parts}
    return bool(parts & _ADF_FOLDERS)


def parse_adf_file(file_path: Path, content: str | None = None, source_repo: str = "") -> LineageGraph:
    """
    Parse a single ADF JSON file and extract lineage.

    Supported artefact types:
    - pipeline/*.json  → activities, copy lineage, execute-pipeline deps
    - dataset/*.json   → dataset nodes
    - dataflow/*.json  → data-flow source/sink lineage
    """
    file_path = Path(file_path)
    if content is None:
        content = file_path.read_text(encoding="utf-8", errors="replace")

    try:
        doc = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in %s — skipping", file_path)
        return LineageGraph()

    # Determine artefact type from folder name
    folder = _artefact_folder(file_path)

    if folder == "pipeline":
        return _parse_pipeline(doc, file_path, source_repo)
    elif folder == "dataset":
        return _parse_dataset(doc, file_path, source_repo)
    elif folder == "dataflow":
        return _parse_dataflow(doc, file_path, source_repo)
    else:
        return LineageGraph()


# ── Internal helpers ─────────────────────────────────────────────────

def _artefact_folder(path: Path) -> str:
    """Return the ADF artefact folder name (pipeline, dataset, etc.)."""
    for part in reversed(path.parts):
        if part.lower() in _ADF_FOLDERS:
            return part.lower()
    return ""


def _safe_name(doc: dict, fallback: str) -> str:
    """Extract the name from an ADF JSON doc, with a path-based fallback."""
    return doc.get("name", Path(fallback).stem)


def _ref_name(ref: dict) -> str:
    """Extract the referenceName from an ADF reference object."""
    if isinstance(ref, dict):
        return ref.get("referenceName", ref.get("name", ""))
    return str(ref)


# ── Pipeline parsing ─────────────────────────────────────────────────

def _parse_pipeline(doc: dict, file_path: Path, source_repo: str) -> LineageGraph:
    graph = LineageGraph()
    pipeline_name = _safe_name(doc, file_path.name)

    # Pipeline node
    pipeline_node = LineageNode(
        id=f"adf.pipeline.{pipeline_name}",
        name=pipeline_name,
        node_type=NodeType.ADF_PIPELINE,
        source_repo=source_repo,
        metadata={"file": str(file_path)},
    )
    graph.nodes.append(pipeline_node)

    # File node
    file_node = LineageNode(
        id=f"file:{file_path}",
        name=file_path.name,
        node_type=NodeType.FILE,
        source_repo=source_repo,
    )
    graph.nodes.append(file_node)
    graph.edges.append(LineageEdge(
        source_id=pipeline_node.id,
        target_id=file_node.id,
        edge_type=EdgeType.DEFINED_IN,
    ))

    # Parse activities
    properties = doc.get("properties", doc)
    activities = properties.get("activities", [])

    for activity in activities:
        _parse_activity(activity, pipeline_name, graph, file_path, source_repo)

    return graph


def _parse_activity(activity: dict, pipeline_name: str, graph: LineageGraph, file_path: Path, source_repo: str) -> None:
    act_type = activity.get("type", "")

    if act_type == "Copy":
        _parse_copy_activity(activity, pipeline_name, graph, source_repo)
    elif act_type == "ExecutePipeline":
        _parse_execute_pipeline(activity, pipeline_name, graph, source_repo)
    elif act_type in ("MappingDataFlow", "ExecuteDataFlow"):
        _parse_dataflow_activity(activity, pipeline_name, graph, source_repo)
    # Other activity types could be added here (Lookup, ForEach, etc.)


def _parse_copy_activity(activity: dict, pipeline_name: str, graph: LineageGraph, source_repo: str) -> None:
    """Extract source → sink lineage from a Copy activity."""
    inputs = activity.get("inputs", [])
    outputs = activity.get("outputs", [])

    # Resolve source dataset
    for inp in inputs:
        ds_name = _ref_name(inp)
        if ds_name:
            node = LineageNode(
                id=f"adf.dataset.{ds_name}",
                name=ds_name,
                node_type=NodeType.ADF_DATASET,
                source_repo=source_repo,
            )
            graph.nodes.append(node)
            # Pipeline reads from this dataset
            graph.edges.append(LineageEdge(
                source_id=f"adf.pipeline.{pipeline_name}",
                target_id=node.id,
                edge_type=EdgeType.READS_FROM,
            ))

    # Resolve sink dataset
    for out in outputs:
        ds_name = _ref_name(out)
        if ds_name:
            node = LineageNode(
                id=f"adf.dataset.{ds_name}",
                name=ds_name,
                node_type=NodeType.ADF_DATASET,
                source_repo=source_repo,
            )
            graph.nodes.append(node)
            # Pipeline writes to this dataset
            graph.edges.append(LineageEdge(
                source_id=f"adf.pipeline.{pipeline_name}",
                target_id=node.id,
                edge_type=EdgeType.WRITES_TO,
            ))

    # Source → Sink copy edge
    for inp in inputs:
        for out in outputs:
            src_ds = _ref_name(inp)
            sink_ds = _ref_name(out)
            if src_ds and sink_ds:
                graph.edges.append(LineageEdge(
                    source_id=f"adf.dataset.{src_ds}",
                    target_id=f"adf.dataset.{sink_ds}",
                    edge_type=EdgeType.COPIES_TO,
                    transformation=f"Copy({activity.get('name', '')})",
                ))


def _parse_execute_pipeline(activity: dict, pipeline_name: str, graph: LineageGraph, source_repo: str) -> None:
    """Extract pipeline → pipeline dependency from ExecutePipeline activity."""
    type_props = activity.get("typeProperties", {})
    pipeline_ref = type_props.get("pipeline", {})
    child_name = _ref_name(pipeline_ref)

    if child_name:
        child_node = LineageNode(
            id=f"adf.pipeline.{child_name}",
            name=child_name,
            node_type=NodeType.ADF_PIPELINE,
            source_repo=source_repo,
        )
        graph.nodes.append(child_node)
        graph.edges.append(LineageEdge(
            source_id=f"adf.pipeline.{pipeline_name}",
            target_id=child_node.id,
            edge_type=EdgeType.TRIGGERS,
        ))


def _parse_dataflow_activity(activity: dict, pipeline_name: str, graph: LineageGraph, source_repo: str) -> None:
    """Extract data-flow reference from a DataFlow activity."""
    type_props = activity.get("typeProperties", {})
    df_ref = type_props.get("dataflow", type_props.get("dataFlow", {}))
    df_name = _ref_name(df_ref)

    if df_name:
        df_node = LineageNode(
            id=f"adf.dataflow.{df_name}",
            name=df_name,
            node_type=NodeType.ADF_DATAFLOW,
            source_repo=source_repo,
        )
        graph.nodes.append(df_node)
        graph.edges.append(LineageEdge(
            source_id=f"adf.pipeline.{pipeline_name}",
            target_id=df_node.id,
            edge_type=EdgeType.TRIGGERS,
        ))


# ── Dataset parsing ──────────────────────────────────────────────────

def _parse_dataset(doc: dict, file_path: Path, source_repo: str) -> LineageGraph:
    graph = LineageGraph()
    ds_name = _safe_name(doc, file_path.name)

    node = LineageNode(
        id=f"adf.dataset.{ds_name}",
        name=ds_name,
        node_type=NodeType.ADF_DATASET,
        source_repo=source_repo,
        metadata={"file": str(file_path)},
    )
    graph.nodes.append(node)

    # Try to resolve the underlying table name
    properties = doc.get("properties", doc)
    type_props = properties.get("typeProperties", {})
    table_name = type_props.get("tableName") or type_props.get("table") or type_props.get("fileName")
    schema_name = type_props.get("schema", "")

    if table_name:
        fq_name = f"{schema_name}.{table_name}" if schema_name else table_name
        table_node = LineageNode(
            id=fq_name,
            name=table_name,
            node_type=NodeType.TABLE,
            source_repo=source_repo,
            metadata={"resolved_from": f"adf.dataset.{ds_name}"},
        )
        graph.nodes.append(table_node)
        # Dataset maps to a physical table
        graph.edges.append(LineageEdge(
            source_id=node.id,
            target_id=table_node.id,
            edge_type=EdgeType.READS_FROM,
            transformation="ADF dataset → physical table",
        ))

    return graph


# ── Dataflow parsing ─────────────────────────────────────────────────

def _parse_dataflow(doc: dict, file_path: Path, source_repo: str) -> LineageGraph:
    graph = LineageGraph()
    df_name = _safe_name(doc, file_path.name)

    df_node = LineageNode(
        id=f"adf.dataflow.{df_name}",
        name=df_name,
        node_type=NodeType.ADF_DATAFLOW,
        source_repo=source_repo,
        metadata={"file": str(file_path)},
    )
    graph.nodes.append(df_node)

    properties = doc.get("properties", doc)
    type_props = properties.get("typeProperties", {})

    # Sources
    for source in type_props.get("sources", []):
        ds_ref = source.get("dataset", {})
        ds_name_ref = _ref_name(ds_ref)
        if ds_name_ref:
            graph.edges.append(LineageEdge(
                source_id=f"adf.dataflow.{df_name}",
                target_id=f"adf.dataset.{ds_name_ref}",
                edge_type=EdgeType.READS_FROM,
            ))

    # Sinks
    for sink in type_props.get("sinks", []):
        ds_ref = sink.get("dataset", {})
        ds_name_ref = _ref_name(ds_ref)
        if ds_name_ref:
            graph.edges.append(LineageEdge(
                source_id=f"adf.dataflow.{df_name}",
                target_id=f"adf.dataset.{ds_name_ref}",
                edge_type=EdgeType.WRITES_TO,
            ))

    return graph
