"""
Lineage graph diff engine.

Compares two LineageGraph snapshots (before/after) and computes:
- Added / removed / unchanged nodes
- Added / removed / modified edges
- Downstream impact summary for changed or removed nodes
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from src.models import EdgeType, LineageEdge, LineageGraph, LineageNode


class ChangeType(str, Enum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"


class NodeChange(BaseModel):
    change_type: ChangeType
    node: LineageNode
    previous: LineageNode | None = None  # only for MODIFIED


class EdgeChange(BaseModel):
    change_type: ChangeType
    edge: LineageEdge
    previous: LineageEdge | None = None  # only for MODIFIED


class LineageDiff(BaseModel):
    """Result of comparing two lineage graphs."""

    node_changes: list[NodeChange] = []
    edge_changes: list[EdgeChange] = []

    @property
    def has_changes(self) -> bool:
        return bool(self.node_changes or self.edge_changes)

    @property
    def added_nodes(self) -> list[LineageNode]:
        return [c.node for c in self.node_changes if c.change_type == ChangeType.ADDED]

    @property
    def removed_nodes(self) -> list[LineageNode]:
        return [c.node for c in self.node_changes if c.change_type == ChangeType.REMOVED]

    @property
    def added_edges(self) -> list[LineageEdge]:
        return [c.edge for c in self.edge_changes if c.change_type == ChangeType.ADDED]

    @property
    def removed_edges(self) -> list[LineageEdge]:
        return [c.edge for c in self.edge_changes if c.change_type == ChangeType.REMOVED]

    @property
    def modified_edges(self) -> list[EdgeChange]:
        return [c for c in self.edge_changes if c.change_type == ChangeType.MODIFIED]

    def summary_counts(self) -> dict:
        return {
            "nodes_added": len(self.added_nodes),
            "nodes_removed": len(self.removed_nodes),
            "edges_added": len(self.added_edges),
            "edges_removed": len(self.removed_edges),
            "edges_modified": len(self.modified_edges),
        }


def _edge_key(e: LineageEdge) -> tuple[str, str, str]:
    return (e.source_id, e.target_id, e.edge_type.value)


def compute_diff(before: LineageGraph, after: LineageGraph) -> LineageDiff:
    """
    Compare *before* and *after* lineage graphs and return a structured diff.

    Only considers nodes and edges that belong to the files being compared.
    """
    diff = LineageDiff()

    # --- Node diff ---
    before_nodes = {n.id: n for n in before.nodes}
    after_nodes = {n.id: n for n in after.nodes}

    for nid, node in after_nodes.items():
        if nid not in before_nodes:
            diff.node_changes.append(NodeChange(change_type=ChangeType.ADDED, node=node))

    for nid, node in before_nodes.items():
        if nid not in after_nodes:
            diff.node_changes.append(NodeChange(change_type=ChangeType.REMOVED, node=node))

    # --- Edge diff ---
    before_edges = {_edge_key(e): e for e in before.edges}
    after_edges = {_edge_key(e): e for e in after.edges}

    for key, edge in after_edges.items():
        if key not in before_edges:
            diff.edge_changes.append(EdgeChange(change_type=ChangeType.ADDED, edge=edge))
        else:
            old = before_edges[key]
            # Check if transformation or confidence changed
            if old.transformation != edge.transformation or old.confidence != edge.confidence:
                diff.edge_changes.append(
                    EdgeChange(change_type=ChangeType.MODIFIED, edge=edge, previous=old)
                )

    for key, edge in before_edges.items():
        if key not in after_edges:
            diff.edge_changes.append(EdgeChange(change_type=ChangeType.REMOVED, edge=edge))

    return diff


# Lineage edges whose *producer* endpoint is the target (consumer derives/reads
# from producer). Downstream — the consumers of a node — is reached by walking
# target → source. This mirrors the authoritative ``/impact`` traversal in api.py.
_CONSUMER_FROM_TARGET = {EdgeType.DERIVES_FROM, EdgeType.READS_FROM}
# Lineage edges whose *producer* endpoint is the source (producer writes/copies
# to consumer). Downstream is reached by walking source → target.
_CONSUMER_FROM_SOURCE = {EdgeType.WRITES_TO, EdgeType.COPIES_TO}
# Structural edges (HAS_COLUMN: table→column, DEFINED_IN: table→file) are not data
# flow — excluded so impact doesn't fan out across every column/file.


def _downstream_adjacency(graph: LineageGraph) -> dict[str, set[str]]:
    """Adjacency where ``adj[x]`` is the set of nodes *directly downstream* of ``x``."""
    adj: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.edge_type in _CONSUMER_FROM_TARGET:
            adj.setdefault(edge.target_id, set()).add(edge.source_id)
            # ADF dataset↔physical-table aliases are the same logical entity: bidirectional.
            if edge.edge_type == EdgeType.READS_FROM and "ADF dataset" in (edge.transformation or ""):
                adj.setdefault(edge.source_id, set()).add(edge.target_id)
        elif edge.edge_type in _CONSUMER_FROM_SOURCE:
            adj.setdefault(edge.source_id, set()).add(edge.target_id)
    return adj


def _changed_endpoint(edge: LineageEdge) -> str:
    """The *consumer* endpoint of a lineage edge — the node whose computation changed.

    For DERIVES_FROM/READS_FROM the consumer is the source; for WRITES_TO/COPIES_TO
    it is the target. Walking downstream from this node yields the affected dependents.
    """
    if edge.edge_type in _CONSUMER_FROM_SOURCE:
        return edge.target_id
    return edge.source_id


def _bfs(seeds: set[str], adj: dict[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    queue = list(seeds)
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for neighbor in adj.get(current, ()):  # type: ignore[arg-type]
            if neighbor not in visited:
                queue.append(neighbor)
    return visited


def collect_impacted_nodes(
    diff: LineageDiff, before_graph: LineageGraph, after_graph: LineageGraph
) -> list[str]:
    """Return IDs of nodes *downstream* of the change (i.e. potentially impacted).

    Direction matters: a change to column X impacts the columns/tables that derive
    **from** X, not the inputs X derives from. We seed from the consumer endpoint of
    each change and walk the downstream adjacency.

    Added/modified things are walked in the *after* graph (the new dependents); removed
    things are walked in the *before* graph (what *used to* depend on the deleted node),
    since they no longer exist in the after graph.
    """
    after_seeds: set[str] = set()
    before_seeds: set[str] = set()

    for change in diff.node_changes:
        if change.change_type == ChangeType.REMOVED:
            before_seeds.add(change.node.id)
        else:  # ADDED or MODIFIED — present in the after graph
            after_seeds.add(change.node.id)

    for change in diff.edge_changes:
        # Structural edges aren't data flow; their endpoints are covered by node changes.
        if change.edge.edge_type in (EdgeType.HAS_COLUMN, EdgeType.DEFINED_IN):
            continue
        endpoint = _changed_endpoint(change.edge)
        if change.change_type == ChangeType.REMOVED:
            before_seeds.add(endpoint)
        else:
            after_seeds.add(endpoint)

    if not after_seeds and not before_seeds:
        return []

    impacted: set[str] = set()
    if after_seeds:
        impacted |= _bfs(after_seeds, _downstream_adjacency(after_graph))
    if before_seeds:
        impacted |= _bfs(before_seeds, _downstream_adjacency(before_graph))

    # Exclude the seeds themselves — we want only the *downstream* dependents.
    impacted -= (after_seeds | before_seeds)
    return sorted(impacted)
