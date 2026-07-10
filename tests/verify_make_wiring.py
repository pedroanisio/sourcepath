#!/usr/bin/env python3
"""verify_make_wiring.py — every test on disk is executed by a make target.

drift-risk H9: the Makefile's verifier groups are hand-maintained allowlists,
and nothing checked them against the tests/ directory — 67 test files (all
top-level pytest suites, the decomposer/recomposer suites, doc hygiene) were
run by no ``make`` target, so a new test could land and ``make test`` stayed
green forever without executing it.

Contract enforced here:

  1. every ``tests/verify_*.py`` on disk is referenced by the Makefile
     (a verifier group or a dedicated target) or carries an explicit
     exclusion reason below — intentional and forgotten are distinguishable;
  2. every ``$(TESTS_DIR)/*.py`` the Makefile references exists on disk
     (no stale group entries);
  3. the pytest-discovery targets cover the pytest tree: ``test-units``
     runs all of tests/ (including decomposer/ and recomposer/) and
     ``test-backend`` runs both frontend service suites — and every
     ``test_*.py`` in the repo lives under one of those covered trees;
  4. the ``test`` umbrella actually depends on the discovery targets, so
     ``make test`` is the superset it claims to be.

Run from the repo root:  uv run python tests/verify_make_wiring.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"

# verify_*.py deliberately NOT wired to any make target: name -> reason.
EXCLUDED_WITH_REASON: dict[str, str] = {
    # (none today — add entries here with a reason, never silently)
}

# Trees whose test_*.py files are covered by pytest-discovery targets.
COVERED_TREES = ("tests", "frontend/backend/tests", "frontend/mcp_server/tests")

# Directories never scanned for stray tests (scratch/vendored/generated).
SCAN_EXCLUDE = {"_tmp", "_site", "_explore", ".repo", "node_modules", ".venv",
                "__pycache__", ".git", "target", ".claude", "docs", "static"}

_FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"== {label} == {'ok' if ok else 'FAIL'}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        _FAILURES.append(label)


def main() -> int:
    mk = MAKEFILE.read_text(encoding="utf-8")

    # 1. every verify_*.py is wired or excluded-with-reason
    on_disk = sorted(p.name for p in (REPO_ROOT / "tests").glob("verify_*.py"))
    unwired = [n for n in on_disk
               if n not in mk and n not in EXCLUDED_WITH_REASON]
    check(f"every tests/verify_*.py is wired into the Makefile "
          f"({len(on_disk)} on disk)", not unwired,
          f"unwired (add to a group or EXCLUDED_WITH_REASON): {unwired}")
    stale_excl = [n for n in EXCLUDED_WITH_REASON if n not in on_disk]
    check("exclusion list has no stale entries", not stale_excl,
          f"{stale_excl}")

    # 2. every Makefile tests/ reference exists on disk
    referenced = re.findall(r"\$\(TESTS_DIR\)/([A-Za-z0-9_./-]+\.py)", mk)
    missing = sorted({r for r in referenced
                      if not (REPO_ROOT / "tests" / r).is_file()})
    check(f"every Makefile test reference exists on disk "
          f"({len(set(referenced))} references)", not missing, f"{missing}")

    # 3. pytest-discovery targets cover the whole pytest tree
    check("test-units runs pytest discovery over tests/",
          re.search(r"test-units:.*\n\t\$\(PYTEST\) \$\(TESTS_DIR\)", mk)
          is not None)
    check("test-backend runs both frontend service suites",
          "backend/tests" in mk and "mcp_server/tests" in mk)
    strays = []
    for p in REPO_ROOT.rglob("test_*.py"):
        rel = p.relative_to(REPO_ROOT)
        if SCAN_EXCLUDE & set(rel.parts):
            continue
        if not any(str(rel).startswith(tree + "/") for tree in COVERED_TREES):
            strays.append(str(rel))
    check("every test_*.py lives under a covered pytest tree",
          not strays, f"uncovered: {sorted(strays)}")

    # 4. the umbrella depends on the discovery targets
    umbrella = re.search(r"^test:\s*(.+?)\s*##", mk, re.MULTILINE)
    deps = umbrella.group(1).split() if umbrella else []
    needed = {"test-core", "test-drift", "test-units", "test-backend",
              "test-docs"}
    check("`make test` includes the discovery + drift + docs targets",
          needed <= set(deps), f"missing from umbrella: {sorted(needed - set(deps))}")

    if _FAILURES:
        print(f"\n{len(_FAILURES)} check(s) failed", file=sys.stderr)
        return 1
    print("\nall make-wiring checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
