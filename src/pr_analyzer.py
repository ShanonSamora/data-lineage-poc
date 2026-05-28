"""
Pull-request lineage analyser.

Diffs the **full multi-repo** lineage graph between two Git refs:

1. Lists changed files between base and head (used in the report).
2. Builds the full lineage graph at HEAD (from the current working tree).
3. Builds the full lineage graph at BASE (from a temporary ``git worktree`` at the
   base SHA). This is what enables cross-source-repo impact analysis — a change in
   the Python repo can flag impact in the SQL repo even though only Python files
   were touched.
4. Diffs the two full graphs.
5. Collects downstream impact in the HEAD graph for each changed node.
6. Renders a Markdown report.

Can be driven from:
  - CLI:            python main.py --pr-check --base origin/main --head HEAD --repo <path> ...
  - GitHub Actions: .github/workflows/lineage-check.yml
  - API:            POST /pr-check  (see src/api.py)
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

import git  # GitPython

from src.config import RepoSource
from src.diff import (
    ChangeType,
    LineageDiff,
    collect_impacted_nodes,
    compute_diff,
)
from src.engine import (
    ADF_EXTENSIONS,
    PYTHON_EXTENSIONS,
    SQL_EXTENSIONS,
    analyze_multiple_repos,
)
from src.models import EdgeType, LineageGraph

logger = logging.getLogger(__name__)

RELEVANT_EXTENSIONS = SQL_EXTENSIONS | PYTHON_EXTENSIONS | ADF_EXTENSIONS


# ── Git helpers ──────────────────────────────────────────────────────

def _get_changed_files(repo: git.Repo, base_ref: str, head_ref: str) -> list[str]:
    """Workspace-relative paths of files changed between *base_ref* and *head_ref*."""
    diffs = repo.commit(base_ref).diff(repo.commit(head_ref))
    paths: set[str] = set()
    for d in diffs:
        if d.a_path:
            paths.add(d.a_path)
        if d.b_path:
            paths.add(d.b_path)
    return sorted(paths)


def _filter_relevant(paths: list[str]) -> list[str]:
    """Keep only SQL, Python, and ADF JSON files."""
    from src.parser_adf import is_adf_file as _is_adf

    result = []
    for p in paths:
        suffix = Path(p).suffix.lower()
        if suffix in SQL_EXTENSIONS | PYTHON_EXTENSIONS:
            result.append(p)
        elif suffix == ".json" and _is_adf(Path(p)):
            result.append(p)
    return result


def _build_base_graph(repo_root: Path, repo_paths: list[str], base_ref: str) -> LineageGraph:
    """Materialize the base SHA in a temporary ``git worktree`` and run multi-repo analysis."""
    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "base"
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", "--force", str(worktree), base_ref],
                cwd=str(repo_root),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error("git worktree add failed: %s", (e.stderr or e.stdout or "").strip())
            return LineageGraph()

        try:
            base_sources = []
            for rel in repo_paths:
                p = worktree / rel
                if p.is_dir():
                    base_sources.append(RepoSource(name=Path(rel).name, path=str(p)))
            if not base_sources:
                logger.warning("None of the configured repo paths exist in base worktree")
                return LineageGraph()
            return analyze_multiple_repos(base_sources)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
            )


# ── Core analysis ────────────────────────────────────────────────────

class PRAnalysisResult:
    """Structured result from analysing a pull request."""

    def __init__(
        self,
        changed_files: list[str],
        relevant_files: list[str],
        diff: LineageDiff,
        impacted_nodes: list[str],
        base_ref: str,
        head_ref: str,
    ):
        self.changed_files = changed_files
        self.relevant_files = relevant_files
        self.diff = diff
        self.impacted_nodes = impacted_nodes
        self.base_ref = base_ref
        self.head_ref = head_ref

    @property
    def has_lineage_impact(self) -> bool:
        return self.diff.has_changes

    def as_dict(self) -> dict:
        return {
            "base_ref": self.base_ref,
            "head_ref": self.head_ref,
            "changed_files": self.changed_files,
            "relevant_files": self.relevant_files,
            "has_lineage_impact": self.has_lineage_impact,
            "summary": self.diff.summary_counts(),
            "impacted_downstream_nodes": self.impacted_nodes,
            "node_changes": [c.model_dump() for c in self.diff.node_changes],
            "edge_changes": [c.model_dump() for c in self.diff.edge_changes],
        }


def analyze_pr(
    repo_paths: list[str | Path],
    base_ref: str,
    head_ref: str,
    repo_root: str | Path | None = None,
) -> PRAnalysisResult:
    """
    Full multi-repo PR lineage impact analysis.

    Builds the full lineage graph at HEAD (from the working tree) and at BASE
    (from a temporary ``git worktree``), then diffs them.

    Parameters
    ----------
    repo_paths : list of source-repo subdirectory paths relative to the Git root
                 (e.g. ``["sample_repo_sql", "sample_repo_python", "sample_repo_adf"]``).
    base_ref   : Git ref for the PR base (e.g. ``origin/main``).
    head_ref   : Git ref for the PR head (e.g. ``HEAD``).
    repo_root  : optional override for the Git repo root. Defaults to CWD.
    """
    repo_root_path = Path(repo_root or ".").resolve()
    repo = git.Repo(repo_root_path, search_parent_directories=True)
    repo_root_path = Path(repo.working_tree_dir)

    rel_paths = [str(p) for p in repo_paths]

    # 1. Changed files (for the report)
    changed = _get_changed_files(repo, base_ref, head_ref)
    relevant = _filter_relevant(changed)
    logger.info(
        "PR %s..%s — %d changed files, %d relevant",
        base_ref, head_ref, len(changed), len(relevant),
    )

    # 2. HEAD graph from current working tree
    head_sources = []
    for rel in rel_paths:
        p = (repo_root_path / rel).resolve()
        if p.is_dir():
            head_sources.append(RepoSource(name=Path(rel).name, path=str(p)))
    if not head_sources:
        logger.warning("No configured repo paths exist in working tree; nothing to analyze")
        return PRAnalysisResult(
            changed_files=changed,
            relevant_files=relevant,
            diff=LineageDiff(),
            impacted_nodes=[],
            base_ref=base_ref,
            head_ref=head_ref,
        )
    logger.info("Building HEAD graph from %d repos", len(head_sources))
    after_graph = analyze_multiple_repos(head_sources)

    # 3. BASE graph from temporary worktree
    logger.info("Building BASE graph from git worktree at %s", base_ref)
    before_graph = _build_base_graph(repo_root_path, rel_paths, base_ref)

    # 4. Diff
    diff = compute_diff(before_graph, after_graph)

    # 5. Impact: BFS in the HEAD graph from each changed node
    impacted: list[str] = []
    if diff.has_changes:
        impacted = collect_impacted_nodes(diff, after_graph)

    logger.info(
        "PR analysis complete: %d node changes, %d edge changes, %d downstream impacted",
        len(diff.node_changes), len(diff.edge_changes), len(impacted),
    )

    return PRAnalysisResult(
        changed_files=changed,
        relevant_files=relevant,
        diff=diff,
        impacted_nodes=impacted,
        base_ref=base_ref,
        head_ref=head_ref,
    )


# ── Markdown report ──────────────────────────────────────────────────

_ICON = {
    ChangeType.ADDED: "🟢",
    ChangeType.REMOVED: "🔴",
    ChangeType.MODIFIED: "🟡",
}

_EDGE_LABEL = {
    EdgeType.HAS_COLUMN: "has column",
    EdgeType.DERIVES_FROM: "derives from",
    EdgeType.READS_FROM: "reads from",
    EdgeType.WRITES_TO: "writes to",
    EdgeType.DEFINED_IN: "defined in",
}


def render_markdown(result: PRAnalysisResult) -> str:
    """Render the PR analysis result as a Markdown comment."""
    lines: list[str] = []
    lines.append("## Data Lineage Impact Report")
    lines.append("")

    if not result.relevant_files:
        lines.append("> **No SQL or Python files were changed in this PR.**")
        lines.append("> Data lineage is **not affected**.")
        return "\n".join(lines)

    if not result.has_lineage_impact:
        lines.append("> Changes were found in data files, but **no lineage impact** was detected.")
        lines.append("")
        lines.append("**Scanned files:**")
        for f in result.relevant_files:
            lines.append(f"- `{f}`")
        return "\n".join(lines)

    # Summary
    counts = result.diff.summary_counts()
    lines.append("| Metric | Count |")
    lines.append("|--------|------:|")
    lines.append(f"| Nodes added | {counts['nodes_added']} |")
    lines.append(f"| Nodes removed | {counts['nodes_removed']} |")
    lines.append(f"| Edges added | {counts['edges_added']} |")
    lines.append(f"| Edges removed | {counts['edges_removed']} |")
    lines.append(f"| Edges modified | {counts['edges_modified']} |")
    lines.append(f"| Downstream nodes impacted | {len(result.impacted_nodes)} |")
    lines.append("")

    # Files scanned
    lines.append("<details><summary><strong>Files analysed</strong></summary>")
    lines.append("")
    for f in result.relevant_files:
        lines.append(f"- `{f}`")
    lines.append("")
    lines.append("</details>")
    lines.append("")

    # Node changes
    if result.diff.node_changes:
        lines.append("### Node Changes")
        lines.append("")
        lines.append("| Status | Node | Type |")
        lines.append("|--------|------|------|")
        for c in result.diff.node_changes:
            icon = _ICON[c.change_type]
            lines.append(f"| {icon} {c.change_type.value} | `{c.node.id}` | {c.node.node_type.value} |")
        lines.append("")

    # Edge changes
    if result.diff.edge_changes:
        lines.append("### Edge Changes")
        lines.append("")
        lines.append("| Status | Source | → | Target | Relationship | Transformation |")
        lines.append("|--------|--------|---|--------|-------------|----------------|")
        for c in result.diff.edge_changes:
            icon = _ICON[c.change_type]
            label = _EDGE_LABEL.get(c.edge.edge_type, c.edge.edge_type.value)
            xform = f"`{c.edge.transformation}`" if c.edge.transformation else "—"
            lines.append(
                f"| {icon} {c.change_type.value} | `{c.edge.source_id}` | → | `{c.edge.target_id}` | {label} | {xform} |"
            )
            if c.change_type == ChangeType.MODIFIED and c.previous:
                old_xform = f"`{c.previous.transformation}`" if c.previous.transformation else "—"
                lines.append(
                    f"| | ↳ *was* | | | | {old_xform} |"
                )
        lines.append("")

    # Downstream impact
    if result.impacted_nodes:
        lines.append("### Downstream Impact")
        lines.append("")
        lines.append(
            "The following nodes are downstream of the changes and may be affected:"
        )
        lines.append("")
        for nid in result.impacted_nodes[:30]:  # cap at 30 to avoid huge comments
            lines.append(f"- `{nid}`")
        if len(result.impacted_nodes) > 30:
            lines.append(f"- ... and {len(result.impacted_nodes) - 30} more")
        lines.append("")

    # Confidence note
    lines.append("---")
    lines.append(
        "*Generated by [Data Lineage POC](https://github.com) — "
        "hybrid static analysis (deterministic SQL parser + LLM fallback).*"
    )

    return "\n".join(lines)
