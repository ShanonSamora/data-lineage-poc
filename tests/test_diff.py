"""Tests for the lineage diff engine."""
from src.diff import (
    ChangeType,
    EdgeChange,
    LineageDiff,
    NodeChange,
    collect_impacted_nodes,
    compute_diff,
)
from src.models import EdgeType, LineageEdge, LineageGraph, LineageNode, NodeType


def _node(id: str, node_type: NodeType = NodeType.TABLE) -> LineageNode:
    return LineageNode(id=id, name=id.split(".")[-1], node_type=node_type)


def _edge(src: str, tgt: str, edge_type: EdgeType = EdgeType.DERIVES_FROM, transformation: str = "") -> LineageEdge:
    return LineageEdge(source_id=src, target_id=tgt, edge_type=edge_type, transformation=transformation)


class TestComputeDiff:
    def test_identical_graphs_produce_no_changes(self):
        g = LineageGraph(
            nodes=[_node("t1"), _node("t2")],
            edges=[_edge("t1", "t2")],
        )
        diff = compute_diff(g, g)
        assert not diff.has_changes

    def test_added_node_detected(self):
        before = LineageGraph(nodes=[_node("t1")])
        after = LineageGraph(nodes=[_node("t1"), _node("t2")])
        diff = compute_diff(before, after)
        assert len(diff.added_nodes) == 1
        assert diff.added_nodes[0].id == "t2"
        assert len(diff.removed_nodes) == 0

    def test_removed_node_detected(self):
        before = LineageGraph(nodes=[_node("t1"), _node("t2")])
        after = LineageGraph(nodes=[_node("t1")])
        diff = compute_diff(before, after)
        assert len(diff.removed_nodes) == 1
        assert diff.removed_nodes[0].id == "t2"

    def test_added_edge_detected(self):
        before = LineageGraph(
            nodes=[_node("t1"), _node("t2")],
            edges=[],
        )
        after = LineageGraph(
            nodes=[_node("t1"), _node("t2")],
            edges=[_edge("t1", "t2")],
        )
        diff = compute_diff(before, after)
        assert len(diff.added_edges) == 1
        assert diff.added_edges[0].source_id == "t1"

    def test_removed_edge_detected(self):
        before = LineageGraph(
            nodes=[_node("t1"), _node("t2")],
            edges=[_edge("t1", "t2")],
        )
        after = LineageGraph(
            nodes=[_node("t1"), _node("t2")],
            edges=[],
        )
        diff = compute_diff(before, after)
        assert len(diff.removed_edges) == 1

    def test_modified_edge_detected(self):
        before = LineageGraph(
            nodes=[_node("t1"), _node("t2")],
            edges=[_edge("t1", "t2", transformation="SUM(amount)")],
        )
        after = LineageGraph(
            nodes=[_node("t1"), _node("t2")],
            edges=[_edge("t1", "t2", transformation="AVG(amount)")],
        )
        diff = compute_diff(before, after)
        assert len(diff.modified_edges) == 1
        assert diff.modified_edges[0].edge.transformation == "AVG(amount)"
        assert diff.modified_edges[0].previous.transformation == "SUM(amount)"

    def test_summary_counts(self):
        before = LineageGraph(nodes=[_node("t1")])
        after = LineageGraph(nodes=[_node("t1"), _node("t2"), _node("t3")])
        diff = compute_diff(before, after)
        counts = diff.summary_counts()
        assert counts["nodes_added"] == 2
        assert counts["nodes_removed"] == 0


class TestCollectImpactedNodes:
    def test_no_changes_means_no_impact(self):
        diff = LineageDiff()
        graph = LineageGraph(nodes=[_node("t1")])
        assert collect_impacted_nodes(diff, graph, graph) == []

    def test_modified_edge_impacts_consumers_not_inputs(self):
        """Chain: rpt.col DERIVES_FROM int.col DERIVES_FROM stg.col.

        Modifying the int.col→stg.col edge must surface rpt.col (a *consumer* of
        int.col), NOT stg.col (an input). This is the direction the old code got wrong.
        """
        graph = LineageGraph(
            nodes=[_node("rpt.col", NodeType.COLUMN), _node("int.col", NodeType.COLUMN),
                   _node("stg.col", NodeType.COLUMN)],
            edges=[_edge("rpt.col", "int.col"), _edge("int.col", "stg.col")],
        )
        diff = LineageDiff(edge_changes=[
            EdgeChange(change_type=ChangeType.MODIFIED,
                       edge=_edge("int.col", "stg.col", transformation="ROUND(x)"),
                       previous=_edge("int.col", "stg.col", transformation="x")),
        ])
        impacted = collect_impacted_nodes(diff, graph, graph)
        assert "rpt.col" in impacted, "consumer of the changed column must be impacted"
        assert "stg.col" not in impacted, "an input is upstream, not downstream"
        assert "int.col" not in impacted, "the changed node itself is excluded"

    def test_removed_node_impacts_former_dependents_via_before_graph(self):
        """A removed node no longer exists in 'after', so its dependents are found
        by walking the *before* graph: a.x ← b.y ← c.z, remove c.z → b.y and a.x impacted."""
        before = LineageGraph(
            nodes=[_node("a.x", NodeType.COLUMN), _node("b.y", NodeType.COLUMN),
                   _node("c.z", NodeType.COLUMN)],
            edges=[_edge("a.x", "b.y"), _edge("b.y", "c.z")],
        )
        after = LineageGraph(nodes=[_node("a.x", NodeType.COLUMN), _node("b.y", NodeType.COLUMN)])
        diff = LineageDiff(node_changes=[
            NodeChange(change_type=ChangeType.REMOVED, node=_node("c.z", NodeType.COLUMN)),
        ])
        impacted = collect_impacted_nodes(diff, before, after)
        assert "b.y" in impacted and "a.x" in impacted

    def test_added_edge_impacts_new_dependents_in_after_graph(self):
        """An added edge seeds from the consumer (its source); downstream dependents
        of that consumer in the *after* graph are impacted."""
        after = LineageGraph(
            nodes=[_node("new.col", NodeType.COLUMN), _node("existing.col", NodeType.COLUMN),
                   _node("downstream.col", NodeType.COLUMN)],
            edges=[_edge("new.col", "existing.col"), _edge("downstream.col", "new.col")],
        )
        diff = LineageDiff(edge_changes=[
            EdgeChange(change_type=ChangeType.ADDED, edge=_edge("new.col", "existing.col")),
        ])
        impacted = collect_impacted_nodes(diff, LineageGraph(), after)
        assert "downstream.col" in impacted
        assert "existing.col" not in impacted

    def test_writes_to_consumer_endpoint_and_traversal(self):
        """WRITES_TO's consumer is the target table; a view that READS_FROM that
        table is downstream of a change to the writing procedure."""
        graph = LineageGraph(
            nodes=[_node("proc", NodeType.PROCEDURE), _node("tbl"), _node("rpt", NodeType.VIEW)],
            edges=[
                _edge("proc", "tbl", EdgeType.WRITES_TO),
                _edge("rpt", "tbl", EdgeType.READS_FROM),
            ],
        )
        diff = LineageDiff(edge_changes=[
            EdgeChange(change_type=ChangeType.MODIFIED,
                       edge=_edge("proc", "tbl", EdgeType.WRITES_TO, transformation="new"),
                       previous=_edge("proc", "tbl", EdgeType.WRITES_TO, transformation="old")),
        ])
        impacted = collect_impacted_nodes(diff, graph, graph)
        assert "rpt" in impacted

    def test_structural_edges_do_not_drive_impact(self):
        """HAS_COLUMN / DEFINED_IN changes alone shouldn't fan out impact."""
        graph = LineageGraph(
            nodes=[_node("t"), _node("t.col", NodeType.COLUMN), _node("f", NodeType.FILE)],
            edges=[_edge("t", "t.col", EdgeType.HAS_COLUMN), _edge("t", "f", EdgeType.DEFINED_IN)],
        )
        diff = LineageDiff(edge_changes=[
            EdgeChange(change_type=ChangeType.ADDED, edge=_edge("t", "t.col", EdgeType.HAS_COLUMN)),
            EdgeChange(change_type=ChangeType.ADDED, edge=_edge("t", "f", EdgeType.DEFINED_IN)),
        ])
        assert collect_impacted_nodes(diff, graph, graph) == []
