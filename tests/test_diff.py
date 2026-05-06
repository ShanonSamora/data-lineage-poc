"""Tests for the lineage diff engine."""
from src.diff import ChangeType, LineageDiff, collect_impacted_nodes, compute_diff
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
        assert collect_impacted_nodes(diff, graph) == []

    def test_downstream_traversal(self):
        """If t1 is modified and t2 derives from t1, t2 is impacted."""
        diff = LineageDiff()
        diff.edge_changes.append(
            __import__("src.diff", fromlist=["EdgeChange"]).EdgeChange(
                change_type=ChangeType.REMOVED,
                edge=_edge("t1.col_a", "t2.col_b"),
            )
        )

        graph = LineageGraph(
            nodes=[_node("t1"), _node("t2"), _node("t3")],
            edges=[
                _edge("t1.col_a", "t2.col_b"),
                _edge("t2.col_b", "t3.col_c"),
            ],
        )

        impacted = collect_impacted_nodes(diff, graph)
        assert "t2.col_b" in impacted or "t3.col_c" in impacted
