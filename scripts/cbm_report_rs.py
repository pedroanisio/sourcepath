#!/usr/bin/env python3
"""cbm_report_rs.py — dispatch shim for the Rust ``cbm-report`` PDF renderer.

The binary lives in ``tools/cbm-report`` (Rust crate). It streams
``inventory.jsonld`` from disk in fixed-size blocks instead of loading the
graph into a store, which is why it exists next to ``cbm_report.py``:

* ``cbm.py report``     (Python)  — pyoxigraph-backed graph analytics; the
  complete Structural X-Ray in HTML / MD / JSON.
* ``cbm.py report-rs``  (Rust)    — 8-page PDF with an independent recount of
  the inventory; built for multi-GB bundles where a graph load is the
  bottleneck. Figures are mechanical; the final page carries the
  "Evidence basis & confidence" disclosure.

This shim only locates and executes the compiled binary so the unified CLI
can route to it; the crate's own argument parsing stays the source of truth.
Binary resolution order: ``$CBM_REPORT_BIN``, then the crate's release and
debug build paths.

Usage:
    python scripts/cbm.py report-rs <bundle-or-parent-dir> [-o out.pdf]
"""
from __future__ import annotations

__file_meta__ = {
    "role": "tool",
    "status": "active",
    "summary": "Locates and executes the Rust cbm-report binary.",
    "rules": [
        {
            "id": "shim-only",
            "severity": "warning",
            "text": "No report logic here: this file may only resolve the "
            "binary and forward argv; rendering behavior belongs to the "
            "tools/cbm-report crate.",
        },
    ],
}

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CRATE_DIR = REPO_ROOT / "tools" / "cbm-report"
_BUILD_HINT = (
    "build it first:\n"
    f"       cargo build --release --manifest-path {CRATE_DIR / 'Cargo.toml'}\n"
    "       (or set CBM_REPORT_BIN to an existing cbm-report binary)"
)


def find_binary() -> Path | None:
    """Resolve the cbm-report binary: env override, then build outputs."""
    override = os.environ.get("CBM_REPORT_BIN")
    if override:
        p = Path(override)
        return p if p.is_file() else None
    for profile in ("release", "debug"):
        p = CRATE_DIR / "target" / profile / "cbm-report"
        if p.is_file():
            return p
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    binary = find_binary()
    if binary is None:
        override = os.environ.get("CBM_REPORT_BIN")
        where = (f"CBM_REPORT_BIN={override!r} does not exist"
                 if override else "no compiled cbm-report binary found")
        print(f"error: {where}; {_BUILD_HINT}", file=sys.stderr)
        return 1
    return subprocess.run([str(binary), *argv]).returncode


if __name__ == "__main__":
    sys.exit(main())
