"""Command-line interface for the Repository Decomposer.

    python -m decomposer <bundle_dir> [--yaml OUT.yaml] [--report OUT.md] [--stdout]

Reads a codebase-mapper bundle directory and emits the Part II YAML
decomposition and/or a human-readable Markdown report. With no output flags it
prints a short summary to stdout.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .decompose import decompose
from .report import to_markdown
from .serialize import to_yaml


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m decomposer",
        description="Decompose a codebase-mapper bundle into structural, "
                    "behavioral, semantic, dependency, data, and operational parts.",
    )
    parser.add_argument(
        "bundle_dir", type=Path,
        help="Path to a bundle directory (contains run_manifest.json, "
             "inventory.jsonld, ...), e.g. _tmp/cbm-itself",
    )
    parser.add_argument("--yaml", type=Path, default=None,
                        help="Write the YAML decomposition to this path.")
    parser.add_argument("--report", type=Path, default=None,
                        help="Write the Markdown report to this path.")
    parser.add_argument("--stdout", action="store_true",
                        help="Print the YAML decomposition to stdout.")
    args = parser.parse_args(argv)

    if not (args.bundle_dir / "run_manifest.json").exists():
        parser.error(f"not a bundle directory (no run_manifest.json): {args.bundle_dir}")

    decomp = decompose(args.bundle_dir)

    wrote_something = False
    if args.yaml:
        args.yaml.write_text(to_yaml(decomp))
        print(f"wrote YAML decomposition -> {args.yaml}", file=sys.stderr)
        wrote_something = True
    if args.report:
        args.report.write_text(to_markdown(decomp))
        print(f"wrote Markdown report    -> {args.report}", file=sys.stderr)
        wrote_something = True
    if args.stdout:
        print(to_yaml(decomp))
        wrote_something = True

    if not wrote_something:
        r = decomp.repository
        print(f"repository: {r.get('name')} ({r.get('files')} files)")
        print(f"parts:      {r.get('n_parts')}")
        print(f"architecture: {decomp.detected_architecture.style} "
              f"({decomp.detected_architecture.confidence.value})")
        print(f"module cycles: {r.get('n_module_cycles')}")
        print(f"quality findings: {len(decomp.quality_gates)}")
        print("(pass --yaml/--report/--stdout to emit full output)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
