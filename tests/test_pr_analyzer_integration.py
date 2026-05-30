"""End-to-end integration test for ``analyze_pr`` against a real temporary git repo.

Creates a throwaway repo with a base commit and a head commit that adds a derived
column, runs the full PR analysis (git worktree for the base graph, working tree for
head, diff, downstream impact, markdown), and asserts the result is correct.

Uses only plain SQL (no procedural constructs) so the deterministic parser handles it
with **no LLM calls** — the test runs offline and in well under a second. Skipped if
``git`` is unavailable.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.pr_analyzer import analyze_pr, render_markdown

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    # Hermetic identity + no signing so the test doesn't depend on the host's git config.
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Lineage Test")
    _git(repo, "config", "commit.gpgsign", "false")


STAGING = """\
CREATE TABLE stg_orders (
    order_id    INT,
    amount      DECIMAL(18,2),
    customer_id INT
);
"""

REPORT_BASE = """\
CREATE VIEW rpt_orders AS
SELECT o.order_id,
       o.amount AS total
FROM stg_orders o;
"""

# Head adds a new derived column.
REPORT_HEAD = """\
CREATE VIEW rpt_orders AS
SELECT o.order_id,
       o.amount AS total,
       o.amount * 2 AS double_total
FROM stg_orders o;
"""


def test_analyze_pr_detects_added_column(tmp_path: Path):
    repo = tmp_path / "repo"
    sql_dir = repo / "sample_repo_sql"
    sql_dir.mkdir(parents=True)
    _init_repo(repo)

    (sql_dir / "01_staging.sql").write_text(STAGING, encoding="utf-8")
    (sql_dir / "02_reporting.sql").write_text(REPORT_BASE, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    (sql_dir / "02_reporting.sql").write_text(REPORT_HEAD, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add double_total")
    head = _git(repo, "rev-parse", "HEAD")

    result = analyze_pr(["sample_repo_sql"], base, head, repo_root=repo)

    # The changed file is recognised as lineage-relevant.
    assert any("02_reporting.sql" in f for f in result.relevant_files)
    assert result.has_lineage_impact

    # The new derived column is detected as an added node.
    added = {n.id for n in result.diff.added_nodes}
    assert "rpt_orders.double_total" in added

    # Relative FILE ids mean no phantom file add/remove churn between base and head.
    file_churn = [c for c in result.diff.node_changes if c.node.node_type.value == "FILE"]
    assert file_churn == [], f"unexpected FILE-node churn (path normalisation broken?): {file_churn}"

    # The markdown report renders and mentions the new column.
    md = render_markdown(result)
    assert "Data Lineage Impact Report" in md
    assert "double_total" in md


def test_analyze_pr_no_relevant_changes(tmp_path: Path):
    """A PR that only touches a non-data file reports no lineage impact."""
    repo = tmp_path / "repo"
    sql_dir = repo / "sample_repo_sql"
    sql_dir.mkdir(parents=True)
    _init_repo(repo)

    (sql_dir / "01_staging.sql").write_text(STAGING, encoding="utf-8")
    (repo / "README.md").write_text("# hello\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "README.md").write_text("# hello world\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "docs only")
    head = _git(repo, "rev-parse", "HEAD")

    result = analyze_pr(["sample_repo_sql"], base, head, repo_root=repo)
    assert result.relevant_files == []
    md = render_markdown(result)
    assert "not affected" in md
