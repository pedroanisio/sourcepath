"""Command-line interface for the Repository Recomposer.

    python -m recomposer <decomposition.yaml> [--plan OUT.md] [--yaml OUT.yaml] [--stdout]

Consumes a Decomposer YAML document — never the raw bundle — and emits the
Natural Description Build Plan as Markdown and/or YAML. With no output flags it
prints a short summary.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from .model import PHASE_TITLE
from .plan import recompose
from .render import to_markdown
from .serialize import to_yaml


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m recomposer",
        description="Generate an ordered natural-language reconstruction plan "
                    "from a Decomposer YAML document.",
    )
    parser.add_argument("decomposition", type=Path,
                        help="Path to a decomposition YAML produced by "
                             "`python -m decomposer ... --yaml`.")
    parser.add_argument("--plan", type=Path, default=None,
                        help="Write the Markdown build plan to this path.")
    parser.add_argument("--yaml", type=Path, default=None,
                        help="Write the YAML build plan to this path.")
    parser.add_argument("--stdout", action="store_true",
                        help="Print the Markdown plan to stdout.")
    args = parser.parse_args(argv)

    if not args.decomposition.exists():
        parser.error(f"decomposition file not found: {args.decomposition}")
    doc = yaml.safe_load(args.decomposition.read_text())
    if not isinstance(doc, dict) or "parts" not in doc:
        parser.error(f"not a decomposition document (no `parts`): {args.decomposition}")

    plan = recompose(doc)

    wrote = False
    if args.plan:
        args.plan.write_text(to_markdown(plan))
        print(f"wrote Markdown build plan -> {args.plan}", file=sys.stderr)
        wrote = True
    if args.yaml:
        args.yaml.write_text(to_yaml(plan))
        print(f"wrote YAML build plan     -> {args.yaml}", file=sys.stderr)
        wrote = True
    if args.stdout:
        print(to_markdown(plan))
        wrote = True

    if not wrote:
        phases = sorted({s.phase for s in plan.steps})
        print(f"repository: {plan.repository.get('name')}")
        print(f"steps:      {len(plan.steps)} across {len(phases)} phases")
        for n in phases:
            k = sum(1 for s in plan.steps if s.phase == n)
            print(f"  phase {n:>2} ({PHASE_TITLE[n]}): {k} step(s)")
        print(f"skipped phases: {len(plan.skipped_phases)}")
        print(f"open assumptions: {len(plan.open_assumptions)}")
        print("(pass --plan/--yaml/--stdout to emit full output)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
