"""
Entry point for the Data Lineage POC.

Usage:
    python main.py                  — Start the web server (API + UI)
    python main.py --analyze        — Analyze all configured source repos, print JSON
    python main.py --pr-check       — Analyse lineage impact of a PR (Git diff)

Multi-repo:
    By default the engine auto-detects ``sample_repo_sql``, ``sample_repo_python``,
    and ``sample_repo_adf`` in the current directory. Override with one or more
    ``--repo <path>`` flags, or the ``REPOS`` env var (JSON).
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("lineage")


def _resolve_sources(repo_args: list[str] | None):
    """Build the list of ``RepoSource`` objects for this run.

    Explicit ``--repo`` flags take precedence over auto-detection / env config.
    """
    from src.config import RepoSource, settings

    if repo_args:
        return [RepoSource(name=Path(p).name or p, path=p) for p in repo_args]
    return settings.get_repo_sources()


def _run_server():
    import uvicorn

    from src.config import settings

    logger.info("Starting Data Lineage server on http://%s:%s", settings.host, settings.port)
    uvicorn.run("src.api:app", host=settings.host, port=settings.port, reload=True)


def _analyze(repo_args: list[str] | None):
    from src.engine import analyze_multiple_repos

    sources = _resolve_sources(repo_args)
    logger.info("Analyzing %d source repo(s): %s", len(sources), [s.name for s in sources])
    graph = analyze_multiple_repos(sources)
    logger.info("Extracted %d nodes, %d edges across all repos", len(graph.nodes), len(graph.edges))

    output = {
        "repos": [s.name for s in sources],
        "nodes": [n.model_dump() for n in graph.nodes],
        "edges": [e.model_dump() for e in graph.edges],
    }
    print(json.dumps(output, indent=2, default=str))


def _pr_check(base_ref: str, head_ref: str, output_file: str | None, repo_args: list[str] | None):
    from src.pr_analyzer import analyze_pr, render_markdown

    sources = _resolve_sources(repo_args)
    repo_paths = [s.path for s in sources]

    repo_root = Path(".").resolve()
    logger.info(
        "Running PR lineage check %s..%s over %d repo(s) in %s",
        base_ref, head_ref, len(repo_paths), repo_root,
    )

    result = analyze_pr(repo_paths, base_ref, head_ref, repo_root=repo_root)
    report = render_markdown(result)

    if output_file:
        Path(output_file).write_text(report, encoding="utf-8")
        logger.info("Report written to %s", output_file)
    else:
        print(report)

    logger.info("Result: %s", json.dumps(result.as_dict(), indent=2, default=str))

    if result.has_lineage_impact:
        logger.info("Lineage IMPACT DETECTED — review the report above.")
    else:
        logger.info("No lineage impact detected.")


def main():
    parser = argparse.ArgumentParser(description="Data Lineage POC")
    parser.add_argument("--analyze", action="store_true", help="Analyze configured repos and print JSON")
    parser.add_argument(
        "--analyze-multi",
        action="store_true",
        help="Deprecated alias for --analyze (kept for older scripts)",
    )
    parser.add_argument("--pr-check", action="store_true", help="Analyse lineage impact of a Git diff (PR mode)")
    parser.add_argument(
        "--repo",
        action="append",
        default=None,
        help="Source repo path (repeatable). Overrides auto-detection / REPOS env var.",
    )
    parser.add_argument("--base", type=str, default="origin/main", help="Base Git ref for PR check (default: origin/main)")
    parser.add_argument("--head", type=str, default="HEAD", help="Head Git ref for PR check (default: HEAD)")
    parser.add_argument("--output-file", type=str, default=None, help="Write Markdown report to file instead of stdout")
    args = parser.parse_args()

    if args.analyze or args.analyze_multi:
        _analyze(args.repo)
    elif args.pr_check:
        _pr_check(args.base, args.head, args.output_file, args.repo)
    else:
        _run_server()


if __name__ == "__main__":
    main()
