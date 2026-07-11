#!/usr/bin/env python3
"""verify_readme_coverage.py — README ↔ code coverage drift guard.

The doc-hygiene audit found two silent README drifts: the supported-language
list hard-coded "nine languages" while the mapper supported far more, and the
`tools/cbm-cartogram` tool was undocumented at the front door. Prose
enumerations rot because nothing breaks when they lie — so this verifier turns
both into machine-checked invariants:

  1. The README's ``<!-- first-class-langs -->`` block must name every
     ``first_class`` language in ``docs/goals/tiobe-top50.yaml`` (the ledger
     ``verify_language_goal`` maintains against probed reality), plus the
     first-class config/markup formats that are outside TIOBE scope
     (HTML / CSS / JSON / YAML).
  2. Every ``tools/<name>/README.md`` must be linked from the root README.

A newly first-class language, or a new tool, therefore cannot land without the
README being updated in the same change — ``make check`` fails otherwise.

Run from the repo root:  uv run python tests/verify_readme_coverage.py
"""
from __future__ import annotations

import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")
LEDGER = os.path.join(ROOT, "docs", "goals", "tiobe-top50.yaml")

# First-class languages that are deliberately NOT in the TIOBE-50 ledger
# (config/markup formats). Kept here as the single source for the extra set.
NON_TIOBE_FIRST_CLASS = ("HTML", "CSS", "JSON", "YAML")

_BLOCK_RE = re.compile(
    r"<!--\s*first-class-langs:start\s*-->(?P<body>.*?)<!--\s*first-class-langs:end\s*-->",
    re.DOTALL,
)

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        if detail:
            print(f"        {detail}")


def main() -> int:
    readme = open(README, encoding="utf-8").read()
    reg = yaml.safe_load(open(LEDGER, encoding="utf-8"))
    ledger_first_class = [e["tiobe_name"] for e in reg["languages"]
                          if e.get("status") == "first_class"]

    m = _BLOCK_RE.search(readme)
    check("README has a <!-- first-class-langs --> marker block", bool(m),
          "add <!-- first-class-langs:start --> / :end --> markers around the list")
    if m:
        body = m.group("body")
        tokens = {t.strip() for t in re.split(r"[,\n]", body) if t.strip()}
        missing = [n for n in ledger_first_class if n not in tokens]
        check("README lists every ledger first_class language",
              not missing,
              f"missing from block (present in ledger, absent in README): {missing}")
        extras_missing = [e for e in NON_TIOBE_FIRST_CLASS if e not in body]
        check("README lists the config/markup first-class languages",
              not extras_missing, f"missing: {extras_missing}")

    tools_dir = os.path.join(ROOT, "tools")
    tool_names = []
    if os.path.isdir(tools_dir):
        tool_names = [d for d in sorted(os.listdir(tools_dir))
                      if os.path.isfile(os.path.join(tools_dir, d, "README.md"))]
    unlinked = [d for d in tool_names if f"tools/{d}" not in readme]
    check("every tools/<name>/README.md is linked from the README",
          not unlinked, f"unlinked tools: {unlinked}")

    print(f"\npassed: {PASS}   failed: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
