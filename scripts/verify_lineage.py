"""Manual verification helper for lineage edges.

Reads a baseline JSON produced by `python main.py --analyze > baseline.json`
and prints each DERIVES_FROM edge with context, so a human reviewer can mark
each one CORRECT / WRONG / NOISE.

Usage:
    python scripts/verify_lineage.py validation/baseline-hybrid.json
    python scripts/verify_lineage.py validation/baseline-hybrid.json --type DERIVES_FROM
    python scripts/verify_lineage.py validation/baseline-hybrid.json --confidence-below 1.0
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_file", type=Path, help="JSON file from `--analyze`")
    parser.add_argument(
        "--type",
        default=None,
        help="Filter by edge type (DERIVES_FROM, READS_FROM, WRITES_TO, etc.). Default: all.",
    )
    parser.add_argument(
        "--confidence-below",
        type=float,
        default=None,
        help="Show only edges with confidence below this threshold (e.g. 1.0 → only LLM)",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print summary counts only, no per-edge listing",
    )
    args = parser.parse_args()

    if not args.baseline_file.is_file():
        print(f"File not found: {args.baseline_file}", file=sys.stderr)
        return 1

    data = json.loads(args.baseline_file.read_text(encoding="utf-8"))
    edges = data.get("edges", [])
    nodes = data.get("nodes", [])
    nodes_by_id = {n["id"]: n for n in nodes}

    if args.type:
        edges = [e for e in edges if e.get("edge_type") == args.type]
    if args.confidence_below is not None:
        edges = [e for e in edges if e.get("confidence", 1.0) < args.confidence_below]

    print(f"=== Lineage baseline: {args.baseline_file} ===")
    print(f"Total nodes: {len(nodes)}")
    print(f"Total edges (matching filters): {len(edges)}")
    print()
    print("Node types:", dict(Counter(n["node_type"] for n in nodes)))
    print("Edge types:", dict(Counter(e["edge_type"] for e in edges)))
    print(f"LLM edges (confidence < 1.0): {sum(1 for e in edges if e.get('confidence', 1.0) < 1.0)}")
    print(f"Deterministic edges (confidence == 1.0): {sum(1 for e in edges if e.get('confidence', 1.0) >= 1.0)}")
    print()

    if args.summary_only:
        return 0

    print("=== Edges ===")
    for i, e in enumerate(edges, 1):
        conf = e.get("confidence", 1.0)
        marker = "[LLM]" if conf < 1.0 else "[DET]"
        xform = e.get("transformation", "")
        src_type = nodes_by_id.get(e["source_id"], {}).get("node_type", "?")
        tgt_type = nodes_by_id.get(e["target_id"], {}).get("node_type", "?")
        print(
            f"{i:>4}. {marker} {e['edge_type']:<13} "
            f"{e['source_id']} ({src_type})  ->  {e['target_id']} ({tgt_type})"
        )
        if xform:
            print(f"      transformation: {xform}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
