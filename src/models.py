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
        """Merge another graph into this one, deduplicating by id.

        Node-merge policy: if a node already exists, keep the deterministic version
        (metadata.source != "llm") over the LLM version. This prevents staging tables
        that the LLM also "discovers" from being relabeled as llm-sourced.

        Edge-merge policy: keep the highest-confidence variant when the same edge
        (source_id, target_id, edge_type) appears twice."""
        nodes_by_id = {n.id: (i, n) for i, n in enumerate(self.nodes)}
        for incoming in other.nodes:
            if incoming.id not in nodes_by_id:
                self.nodes.append(incoming)
                nodes_by_id[incoming.id] = (len(self.nodes) - 1, incoming)
                continue
            idx, existing = nodes_by_id[incoming.id]
            existing_is_llm = (existing.metadata or {}).get("source") == "llm"
            incoming_is_llm = (incoming.metadata or {}).get("source") == "llm"
            # Prefer deterministic: replace only if existing was LLM and incoming is not.
            if existing_is_llm and not incoming_is_llm:
                self.nodes[idx] = incoming
                nodes_by_id[incoming.id] = (idx, incoming)

        edges_by_key: dict = {}
        for i, e in enumerate(self.edges):
            edges_by_key[(e.source_id, e.target_id, e.edge_type)] = (i, e)
        for incoming in other.edges:
            key = (incoming.source_id, incoming.target_id, incoming.edge_type)
            if key not in edges_by_key:
                self.edges.append(incoming)
                edges_by_key[key] = (len(self.edges) - 1, incoming)
                continue
            idx, existing = edges_by_key[key]
            # Keep the higher-confidence edge (preserves deterministic + transformation strings).
            if incoming.confidence > existing.confidence:
                self.edges[idx] = incoming
                edges_by_key[key] = (idx, incoming)
