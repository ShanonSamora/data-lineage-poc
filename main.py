"""
Entry point for the Data Lineage POC.

Usage:
    python main.py                  — Start the web server (API + UI)
    python main.py --analyze        — Analyze sample_repo and print results (no server)
    python main.py --analyze-local  — Same but skip Neo4j (print to stdout)
    python main.py --analyze-multi  — Analyze all configured repos (multi-repo mode)
    python main.py --pr-check       — Analyse lineage impact of a PR (Git diff)
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


def _run_server():
    import uvicorn
    from src.config import settings

    logger.info("Starting Data Lineage server on http://%s:%s", settings.host, settings.port)
    uvicorn.run("src.api:app", host=settings.host, port=settings.port, reload=True)


def _analyze(use_neo4j: bool):
    from src.config import settings
    from src.engine import analyze_directory

    repo = Path(settings.repo_path)
    logger.info("Analyzing directory: %s", repo.resolve())
    graph = analyze_directory(repo)
    logger.info("Extracted %d nodes, %d edges", len(graph.nodes), len(graph.edges))

    if use_neo4j:
        from src.graph_store import Neo4jStore

        store = Neo4jStore()
        store.setup_indexes()
        store.clear_all()
        result = store.store_graph(graph)
        store.close()
        logger.info("Stored in Neo4j: %s", result)
    else:
        # Print JSON summary to stdout
        output = {
            "nodes": [n.model_dump() for n in graph.nodes],
            "edges": [e.model_dump() for e in graph.edges],
        }
        print(json.dumps(output, indent=2, default=str))


def _analyze_multi(use_neo4j: bool):
    from src.config import settings
    from src.engine import analyze_multiple_repos

    repo_sources = settings.get_repo_sources()
    logger.info("Multi-repo analysis: %d repos configured", len(repo_sources))

    graph = analyze_multiple_repos(repo_sources)
    logger.info("Extracted %d nodes, %d edges across all repos", len(graph.nodes), len(graph.edges))

    if use_neo4j:
        from src.graph_store import Neo4jStore

        store = Neo4jStore()
        store.setup_indexes()
        store.clear_all()
        result = store.store_graph(graph)
        store.close()
        logger.info("Stored in Neo4j: %s", result)
    else:
        output = {
            "repos": [r.name for r in repo_sources],
            "nodes": [n.model_dump() for n in graph.nodes],
            "edges": [e.model_dump() for e in graph.edges],
        }
        print(json.dumps(output, indent=2, default=str))


def _pr_check(base_ref: str, head_ref: str, output_file: str | None):
    from src.pr_analyzer import analyze_pr, render_markdown

    repo_root = Path(".").resolve()
    logger.info("Running PR lineage check: %s..%s in %s", base_ref, head_ref, repo_root)

    result = analyze_pr(repo_root, base_ref, head_ref)
    report = render_markdown(result)

    if output_file:
        Path(output_file).write_text(report, encoding="utf-8")
        logger.info("Report written to %s", output_file)
    else:
        print(report)

    # Also dump structured JSON to stderr for programmatic consumption
    logger.info("Result: %s", json.dumps(result.as_dict(), indent=2, default=str))

    # Exit code: 0 = no impact, 1 = lineage impacted (useful for CI gates)
    if result.has_lineage_impact:
        logger.info("Lineage IMPACT DETECTED — review the report above.")
    else:
        logger.info("No lineage impact detected.")


def main():
    parser = argparse.ArgumentParser(description="Data Lineage POC")
    parser.add_argument("--analyze", action="store_true", help="Analyze repo and store in Neo4j")
    parser.add_argument("--analyze-local", action="store_true", help="Analyze repo, print JSON (no Neo4j)")
    parser.add_argument("--analyze-multi", action="store_true", help="Analyze all configured repos (multi-repo)")
    parser.add_argument("--analyze-multi-local", action="store_true", help="Multi-repo analysis, print JSON (no Neo4j)")
    parser.add_argument("--pr-check", action="store_true", help="Analyse lineage impact of a Git diff (PR mode)")
    parser.add_argument("--base", type=str, default="origin/main", help="Base Git ref for PR check (default: origin/main)")
    parser.add_argument("--head", type=str, default="HEAD", help="Head Git ref for PR check (default: HEAD)")
    parser.add_argument("--output-file", type=str, default=None, help="Write Markdown report to file instead of stdout")
    args = parser.parse_args()

    if args.analyze:
        _analyze(use_neo4j=True)
    elif args.analyze_local:
        _analyze(use_neo4j=False)
    elif args.analyze_multi:
        _analyze_multi(use_neo4j=True)
    elif args.analyze_multi_local:
        _analyze_multi(use_neo4j=False)
    elif args.pr_check:
        _pr_check(args.base, args.head, args.output_file)
    else:
        _run_server()


if __name__ == "__main__":
    main()
