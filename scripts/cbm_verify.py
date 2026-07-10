#!/usr/bin/env python3
"""cbm_verify — the failing bundle quality gate (error-free-mapping E9).

Usage:
    python scripts/cbm_verify.py --bundle <dir> [--accept-degradation COMP]...
                                 [--skip-hashes] [--allow-skipped-shacl]
                                 [--max-parse-error-share F]
                                 [--max-unlanguaged-share F]
                                 [--max-silent-zero N]
                                 [--min-import-resolution F]

Exit 0 when every check passes; exit 1 with one line per violation
otherwise. A bundle that ships errors fails — it does not describe them.
"""
from __future__ import annotations

import argparse
import sys

from pathlib import Path

from codebase_mapper.verification.bundle_gate import Budgets, check_bundle


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--accept-degradation", action="append", default=[],
                    metavar="COMPONENT",
                    help="acknowledge a recorded degradation by component name")
    ap.add_argument("--skip-hashes", action="store_true",
                    help="skip artifact sha256 recompute (fast mode)")
    ap.add_argument("--allow-skipped-shacl", action="store_true")
    ap.add_argument("--max-parse-error-share", type=float, default=0.05)
    ap.add_argument("--max-unlanguaged-share", type=float, default=0.03)
    ap.add_argument("--max-silent-zero", type=int, default=0)
    ap.add_argument("--min-import-resolution", type=float, default=0.5)
    a = ap.parse_args(argv)

    budgets = Budgets(
        max_parse_error_share=a.max_parse_error_share,
        max_unlanguaged_share=a.max_unlanguaged_share,
        max_silent_zero_files=a.max_silent_zero,
        min_import_resolution=a.min_import_resolution,
        allow_skipped_shacl=a.allow_skipped_shacl,
    )
    violations = check_bundle(
        Path(a.bundle), budgets,
        accept_degradations=set(a.accept_degradation),
        skip_hashes=a.skip_hashes,
    )
    if violations:
        for v in violations:
            print(f"[verify-bundle] FAIL {v['id']}: {v['text']}", file=sys.stderr)
        print(f"[verify-bundle] {len(violations)} violation(s) — bundle rejected",
              file=sys.stderr)
        return 1
    print("[verify-bundle] PASS — every check green", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
