#!/usr/bin/env python3
"""cbm_cartogram.py — dispatch shim for the interactive Cartogram map.

The Cartogram (``tools/cbm-cartogram``) renders a bundle as a self-contained
interactive HTML map: top-level directories become regions, and the measured
import and test relations are explorable with zoom, search, and
level-of-detail aggregation. It is the interactive companion to the dossier's
printed metro and district views — same measured facts, explorable instead of
frozen.

This shim only locates Node, runs the tool's own normalizer and bundler, and
places the output; rendering behavior stays in the Cartogram's own sources.
It requires an L3 bundle (``scripts/run_l3.py`` or ``run_l4.py``): the
normalizer refuses a bare L1 bundle rather than draw an empty map, and that
refusal is surfaced verbatim.

Note: the normalizer writes the intermediate ``data/atlas-data.js`` inside
the tool directory (the bundler reads it from that fixed path), so two
concurrent builds would race each other. Fine for a CLI; do not parallelize.

Usage:
    python scripts/cbm.py cartogram <bundle-dir> [-o out.html]
"""
from __future__ import annotations

__file_meta__ = {
    "role": "tool",
    "status": "active",
    "summary": "Builds the standalone Cartogram HTML from a bundle.",
    "rules": [
        {
            "id": "shim-only",
            "severity": "warning",
            "text": "No rendering logic here: this file may only resolve "
            "node, invoke the cartogram's own normalizer/bundler, and "
            "place the output.",
        },
    ],
}

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from codebase_mapper.shared_kernel.settings import default_report_path, load_env

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = REPO_ROOT / "tools" / "cbm-cartogram"
NORMALIZER = TOOL_DIR / "tools" / "normalize-inventory.mjs"
BUNDLER = TOOL_DIR / "tools" / "build-standalone.mjs"
ATLAS_DATA = TOOL_DIR / "data" / "atlas-data.js"


def main(argv: list[str] | None = None) -> int:
    load_env()  # .env (repo-scoped) fills gaps; real environment always wins
    ap = argparse.ArgumentParser(
        prog="python scripts/cbm.py cartogram",
        description="Build the standalone interactive Cartogram HTML from a "
                    "codebase-mapper bundle (requires an L3 bundle and Node >= 20).")
    ap.add_argument("bundle", type=Path,
                    help="Bundle directory (contains inventory.jsonld).")
    ap.add_argument("-o", "--out", type=Path,
                    help="Output HTML (default: standardized timestamped "
                         "name under CBM_REPORTS_DIR).")
    args = ap.parse_args(argv)

    if shutil.which("node") is None:
        print("error: node not found — the Cartogram build needs Node >= 20; "
              "install it (or use the printed views: cbm.py dossier)",
              file=sys.stderr)
        return 1
    inventory = args.bundle / "inventory.jsonld"
    if not inventory.is_file():
        print(f"error: {inventory} not found — pass a bundle directory "
              "produced by scripts/run_l3.py (the Cartogram needs the L3 "
              "layer; a bare L1 bundle is refused)", file=sys.stderr)
        return 2

    source = os.path.basename(str(args.bundle).rstrip("/")) or str(args.bundle)
    out = args.out or default_report_path(source, "cartogram", ext="html")
    out.parent.mkdir(parents=True, exist_ok=True)

    norm = subprocess.run(
        ["node", str(NORMALIZER), str(inventory), str(ATLAS_DATA)],
        capture_output=True, text=True)
    if norm.returncode != 0:
        # e.g. the L1 refusal: "no chunks/concepts ... run scripts/run_l3.py"
        print(norm.stderr.strip() or norm.stdout.strip(), file=sys.stderr)
        return norm.returncode
    build = subprocess.run(["node", str(BUNDLER), str(out)],
                           capture_output=True, text=True)
    if build.returncode != 0:
        print(build.stderr.strip() or build.stdout.strip(), file=sys.stderr)
        return build.returncode
    print(f"[cartogram] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
