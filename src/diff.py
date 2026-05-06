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


def collect_impacted_nodes(diff: LineageDiff, full_graph: LineageGraph) -> list[str]:
    """
    Given a diff and the full current lineage graph, return IDs of nodes that
    are *downstream* of any changed node (i.e. potentially impacted).

    Uses a BFS traversal over DERIVES_FROM, READS_FROM, WRITES_TO, HAS_COLUMN
    edges in the downstream direction.
    """
    # Seed nodes: anything that was removed or part of a modified/removed edge
    seed_ids: set[str] = set()

    for change in diff.node_changes:
        if change.change_type in (ChangeType.REMOVED, ChangeType.MODIFIED):
            seed_ids.add(change.node.id)

    for change in diff.edge_changes:
        if change.change_type in (ChangeType.REMOVED, ChangeType.MODIFIED):
            seed_ids.add(change.edge.source_id)
        if change.change_type == ChangeType.ADDED:
            seed_ids.add(change.edge.target_id)

    if not seed_ids:
        return []

    # Build adjacency (source → targets that depend on source)
    downstream_adj: dict[str, set[str]] = {}
    _DOWNSTREAM_EDGE_TYPES = {EdgeType.DERIVES_FROM, EdgeType.READS_FROM, EdgeType.WRITES_TO, EdgeType.HAS_COLUMN}
    for edge in full_graph.edges:
        if edge.edge_type in _DOWNSTREAM_EDGE_TYPES:
            downstream_adj.setdefault(edge.source_id, set()).add(edge.target_id)

    # BFS
    visited: set[str] = set()
    queue = list(seed_ids)
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for neighbor in downstream_adj.get(current, []):
            if neighbor not in visited:
                queue.append(neighbor)

    # Exclude the seeds themselves — we want only *downstream* impact
    impacted = visited - seed_ids
    return sorted(impacted)
