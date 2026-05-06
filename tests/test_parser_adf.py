"""Tests for the ADF pipeline parser."""
import json

from pathlib import Path

from src.models import EdgeType, NodeType
from src.parser_adf import is_adf_file, parse_adf_file


def test_is_adf_file_pipeline():
    assert is_adf_file(Path("adf/pipeline/MyPipeline.json")) is True


def test_is_adf_file_dataset():
    assert is_adf_file(Path("adf/dataset/MyDataset.json")) is True


def test_is_adf_file_non_adf():
    assert is_adf_file(Path("config/settings.json")) is False


def test_is_adf_file_non_json():
    assert is_adf_file(Path("adf/pipeline/readme.md")) is False


def test_parse_copy_activity():
    pipeline_json = {
        "name": "TestPipeline",
        "properties": {
            "activities": [
                {
                    "name": "CopyData",
                    "type": "Copy",
                    "inputs": [{"referenceName": "SourceDS", "type": "DatasetReference"}],
                    "outputs": [{"referenceName": "SinkDS", "type": "DatasetReference"}],
                    "typeProperties": {
                        "source": {"type": "BlobSource"},
                        "sink": {"type": "SqlSink"},
                    },
                }
            ]
        },
    }
    content = json.dumps(pipeline_json)
    graph = parse_adf_file(Path("pipeline/TestPipeline.json"), content, source_repo="test-repo")

    # Should have pipeline node + source dataset + sink dataset
    node_ids = {n.id for n in graph.nodes}
    assert "adf.pipeline.TestPipeline" in node_ids
    assert "adf.dataset.SourceDS" in node_ids
    assert "adf.dataset.SinkDS" in node_ids

    # Should have READS_FROM, WRITES_TO, and COPIES_TO edges
    edge_types = {(e.source_id, e.target_id, e.edge_type) for e in graph.edges}
    assert ("adf.pipeline.TestPipeline", "adf.dataset.SourceDS", EdgeType.READS_FROM) in edge_types
    assert ("adf.pipeline.TestPipeline", "adf.dataset.SinkDS", EdgeType.WRITES_TO) in edge_types
    assert ("adf.dataset.SourceDS", "adf.dataset.SinkDS", EdgeType.COPIES_TO) in edge_types

    # Nodes should be tagged with source_repo
    for node in graph.nodes:
        if node.node_type != NodeType.FILE:
            assert node.source_repo == "test-repo"


def test_parse_execute_pipeline():
    pipeline_json = {
        "name": "ParentPipeline",
        "properties": {
            "activities": [
                {
                    "name": "RunChild",
                    "type": "ExecutePipeline",
                    "typeProperties": {
                        "pipeline": {
                            "referenceName": "ChildPipeline",
                            "type": "PipelineReference",
                        },
                    },
                }
            ]
        },
    }
    content = json.dumps(pipeline_json)
    graph = parse_adf_file(Path("pipeline/ParentPipeline.json"), content)

    edge_types = {(e.source_id, e.target_id, e.edge_type) for e in graph.edges}
    assert ("adf.pipeline.ParentPipeline", "adf.pipeline.ChildPipeline", EdgeType.TRIGGERS) in edge_types


def test_parse_dataset_with_table():
    dataset_json = {
        "name": "SqlRawCustomers",
        "properties": {
            "type": "AzureSqlTable",
            "typeProperties": {
                "schema": "raw",
                "table": "customers",
            },
        },
    }
    content = json.dumps(dataset_json)
    graph = parse_adf_file(Path("dataset/SqlRawCustomers.json"), content)

    node_ids = {n.id for n in graph.nodes}
    assert "adf.dataset.SqlRawCustomers" in node_ids
    assert "raw.customers" in node_ids  # Physical table resolved


def test_parse_dataflow():
    dataflow_json = {
        "name": "TestFlow",
        "properties": {
            "type": "MappingDataFlow",
            "typeProperties": {
                "sources": [
                    {"name": "Src", "dataset": {"referenceName": "SrcDataset"}}
                ],
                "sinks": [
                    {"name": "Snk", "dataset": {"referenceName": "SnkDataset"}}
                ],
            },
        },
    }
    content = json.dumps(dataflow_json)
    graph = parse_adf_file(Path("dataflow/TestFlow.json"), content)

    node_ids = {n.id for n in graph.nodes}
    assert "adf.dataflow.TestFlow" in node_ids

    edge_types = {(e.source_id, e.target_id, e.edge_type) for e in graph.edges}
    assert ("adf.dataflow.TestFlow", "adf.dataset.SrcDataset", EdgeType.READS_FROM) in edge_types
    assert ("adf.dataflow.TestFlow", "adf.dataset.SnkDataset", EdgeType.WRITES_TO) in edge_types


def test_invalid_json_returns_empty_graph():
    graph = parse_adf_file(Path("pipeline/bad.json"), "not valid json")
    assert len(graph.nodes) == 0
    assert len(graph.edges) == 0
