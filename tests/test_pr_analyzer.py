"""Tests for the PR analyser markdown rendering."""
from src.diff import ChangeType, EdgeChange, LineageDiff, NodeChange
from src.models import EdgeType, LineageEdge, LineageNode, NodeType
from src.pr_analyzer import PRAnalysisResult, _filter_relevant, render_markdown


def test_filter_relevant_scopes_to_source_repos():
    """Only data files *under an analysed repo* are relevant — not the analysis code."""
    changed = [
        "sample_repo_sql/02_intermediate_views.sql",  # relevant
        "sample_repo_python/transform_pipeline.py",    # relevant
        "sample_repo_adf/pipeline/IngestRawData.json", # relevant (ADF)
        "src/diff.py",                                  # NOT relevant (analysis code)
        "README.md",                                    # NOT relevant
        "config/settings.json",                         # NOT relevant (non-ADF json)
    ]
    repos = ["sample_repo_sql", "sample_repo_python", "sample_repo_adf"]
    result = _filter_relevant(changed, repos)
    assert result == [
        "sample_repo_sql/02_intermediate_views.sql",
        "sample_repo_python/transform_pipeline.py",
        "sample_repo_adf/pipeline/IngestRawData.json",
    ]


def test_no_relevant_files():
    result = PRAnalysisResult(
        changed_files=["README.md"],
        relevant_files=[],
        diff=LineageDiff(),
        impacted_nodes=[],
        base_ref="main",
        head_ref="feature",
    )
    md = render_markdown(result)
    assert "No SQL or Python files" in md
    assert "not affected" in md


def test_relevant_files_but_no_impact():
    result = PRAnalysisResult(
        changed_files=["sql/test.sql"],
        relevant_files=["sql/test.sql"],
        diff=LineageDiff(),
        impacted_nodes=[],
        base_ref="main",
        head_ref="feature",
    )
    md = render_markdown(result)
    assert "no lineage impact" in md
    assert "`sql/test.sql`" in md


def test_full_report_with_changes():
    diff = LineageDiff(
        node_changes=[
            NodeChange(
                change_type=ChangeType.ADDED,
                node=LineageNode(id="new_table", name="new_table", node_type=NodeType.TABLE),
            ),
        ],
        edge_changes=[
            EdgeChange(
                change_type=ChangeType.ADDED,
                edge=LineageEdge(
                    source_id="src.col",
                    target_id="new_table.col",
                    edge_type=EdgeType.DERIVES_FROM,
                    transformation="UPPER(name)",
                ),
            ),
        ],
    )
    result = PRAnalysisResult(
        changed_files=["sql/new.sql"],
        relevant_files=["sql/new.sql"],
        diff=diff,
        impacted_nodes=["downstream.col_a"],
        base_ref="main",
        head_ref="feature",
    )
    md = render_markdown(result)
    assert "Node Changes" in md
    assert "Edge Changes" in md
    assert "Downstream Impact" in md
    assert "`new_table`" in md
    assert "UPPER(name)" in md
    assert "`downstream.col_a`" in md


def test_has_lineage_impact_property():
    diff = LineageDiff(
        node_changes=[
            NodeChange(
                change_type=ChangeType.ADDED,
                node=LineageNode(id="t", name="t", node_type=NodeType.TABLE),
            ),
        ],
    )
    result = PRAnalysisResult(
        changed_files=[], relevant_files=[], diff=diff, impacted_nodes=[],
        base_ref="a", head_ref="b",
    )
    assert result.has_lineage_impact is True


def test_as_dict_structure():
    result = PRAnalysisResult(
        changed_files=["a.sql"], relevant_files=["a.sql"],
        diff=LineageDiff(), impacted_nodes=[],
        base_ref="main", head_ref="feat",
    )
    d = result.as_dict()
    assert "base_ref" in d
    assert "summary" in d
    assert "has_lineage_impact" in d


def test_report_always_ends_with_newline():
    """Regression: the report must end with a newline.

    The CI step appends it to $GITHUB_OUTPUT inside a heredoc; without a trailing
    newline the last line merges into the closing delimiter and GitHub Actions fails
    with "Matching delimiter not found". This was a real failure on PR #1.
    """
    # No relevant files
    r1 = PRAnalysisResult([], [], LineageDiff(), [], "a", "b")
    # Relevant files but no impact
    r2 = PRAnalysisResult(["x.sql"], ["x.sql"], LineageDiff(), [], "a", "b")
    # Full report with changes
    diff = LineageDiff(node_changes=[
        NodeChange(change_type=ChangeType.ADDED,
                   node=LineageNode(id="t", name="t", node_type=NodeType.TABLE)),
    ])
    r3 = PRAnalysisResult(["x.sql"], ["x.sql"], diff, ["d.col"], "a", "b")
    for r in (r1, r2, r3):
        assert render_markdown(r).endswith("\n")
