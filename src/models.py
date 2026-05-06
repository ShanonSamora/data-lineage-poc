"""Shared data models for lineage extraction."""
from __future__ import annotations

from enum import Enum
from pydantic import BaseModel


class NodeType(str, Enum):
    TABLE = "TABLE"
    VIEW = "VIEW"
    COLUMN = "COLUMN"
    PROCEDURE = "PROCEDURE"
    PYTHON_FUNCTION = "PYTHON_FUNCTION"
    FILE = "FILE"
    ADF_PIPELINE = "ADF_PIPELINE"
    ADF_DATASET = "ADF_DATASET"
    ADF_DATAFLOW = "ADF_DATAFLOW"


class EdgeType(str, Enum):
    HAS_COLUMN = "HAS_COLUMN"          # table -> column
    DERIVES_FROM = "DERIVES_FROM"      # column -> column (lineage)
    READS_FROM = "READS_FROM"          # view/proc -> table
    WRITES_TO = "WRITES_TO"            # proc -> table
    DEFINED_IN = "DEFINED_IN"          # table/view/proc -> file
    COPIES_TO = "COPIES_TO"            # ADF copy activity: dataset -> dataset
    TRIGGERS = "TRIGGERS"              # ADF pipeline -> pipeline


class LineageNode(BaseModel):
    id: str                 # e.g. "stg_customers.email"
    name: str               # e.g. "email"
    node_type: NodeType
    source_repo: str = ""   # repository name/alias (for multi-repo lineage)
    metadata: dict = {}     # file path, line number, etc.


class LineageEdge(BaseModel):
    source_id: str
    target_id: str
    edge_type: EdgeType
    transformation: str = ""   # e.g. "CONCAT(first_name, ' ', last_name)"
    confidence: float = 1.0    # 1.0 = deterministic, <1.0 = LLM-inferred
    metadata: dict = {}


class LineageGraph(BaseModel):
    nodes: list[LineageNode] = []
    edges: list[LineageEdge] = []

    def merge(self, other: LineageGraph) -> None:
        """Merge another graph into this one, deduplicating by id."""
        existing_node_ids = {n.id for n in self.nodes}
        for node in other.nodes:
            if node.id not in existing_node_ids:
                self.nodes.append(node)
                existing_node_ids.add(node.id)

        existing_edges = {(e.source_id, e.target_id, e.edge_type) for e in self.edges}
        for edge in other.edges:
            key = (edge.source_id, edge.target_id, edge.edge_type)
            if key not in existing_edges:
                self.edges.append(edge)
                existing_edges.add(key)
