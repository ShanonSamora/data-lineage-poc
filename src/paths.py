"""Helpers for building stable, repo-relative file identifiers.

FILE-node IDs must be stable across machines and, crucially, across the base/head
worktrees compared during a PR check. Embedding an absolute path (e.g. the
temporary ``git worktree`` directory the base graph is built from) makes every
file node differ between base and head purely because of the path prefix,
producing a flood of spurious add/remove diffs that drown out the real change.

We therefore derive FILE-node IDs from the path *relative to the scanned repo
root*, prefixed with the source-repo name so that identically-named files in
different repos don't collide.
"""
from __future__ import annotations

from pathlib import Path


def repo_relative_path(file_path: str | Path, repo_root: str | Path | None) -> str:
    """Return *file_path* relative to *repo_root* as a POSIX string.

    Falls back to the bare filename when the path is outside *repo_root* or no
    root is provided — this keeps IDs machine-independent for ad-hoc and
    unit-test calls that pass a bare path.
    """
    p = Path(file_path)
    if repo_root is not None:
        try:
            return p.resolve().relative_to(Path(repo_root).resolve()).as_posix()
        except ValueError:
            pass
    return p.name


def file_node_id(
    file_path: str | Path,
    repo_root: str | Path | None = None,
    source_repo: str = "",
    prefix: str = "",
) -> str:
    """Build a stable FILE-node identifier.

    Format: ``{prefix}{source_repo}/{relative-path}`` (the ``source_repo/`` part
    is omitted when no source repo is given). ``prefix`` lets the ADF parser keep
    its ``file:`` marker.
    """
    rel = repo_relative_path(file_path, repo_root)
    base = f"{source_repo}/{rel}" if source_repo else rel
    return f"{prefix}{base}"
