#!/usr/bin/env python3
"""Language-support goal verifier — TIOBE top 50, cumulative since 2026.

The goal (README, "Goals"): fully support every language that appears
in the TIOBE index top 50 from 2026 onward. The registry
``docs/goals/tiobe-top50.yaml`` is cumulative — languages are only ever
added — and carries a per-language *ledger status* that this verifier
holds against mechanically probed reality:

  first_class  detection + analyzer + import resolver + symbol chunker
               + L4 summary gate + a signatures/verify test suite
  partial      detection + analyzer, but missing some of the rest
  detected     extension→language mapping only
  none         the mapper does not know the language

Failure modes (both directions of drift):
  - ledger claims MORE than the probes find → fabricated support
  - ledger claims LESS than the probes find → stale ledger
  - a first_class language probing lower → support regression
    (the goal is cumulative: support, once shipped, must not regress)

Bootstrap/update: ``python tests/verify_language_goal.py --fill`` prints
the probed status for every registry entry, to be pasted deliberately.

Run from the repo root:  python tests/verify_language_goal.py
"""
from __future__ import annotations

import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

REGISTRY = os.path.join(ROOT, "docs", "goals", "tiobe-top50.yaml")
LEVELS = ("none", "detected", "partial", "first_class")

# mapper language key → basename used by analyzer modules / test suites
_MODULE_ALIAS = {"typescript": "tsjs", "javascript": "tsjs",
                 "objective-c": "objc", "objective-cpp": "objc"}

PASS = FAIL = 0


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


# ---------------------------------------------------------------------------
# mechanical probes
# ---------------------------------------------------------------------------


def _probe_record(key: str):
    from codebase_mapper.inspection.models import FileRecord
    rec = FileRecord(
        path=f"__goal_probe__.{key}", git_blob_sha="0" * 40,
        content_sha256="0" * 64, size_bytes=1, language=key,
        type_="source_code", phases=["runtime"],
        atime=None, mtime=None, ctime=None, git_commit_time=None,
    )
    # ImportResolvers only match records that carry an AST summary — the
    # pipeline never resolves unparsed files. A stub summary lets the
    # probe exercise the language gate, which is what we measure here.
    rec.ast_summary = {"__goal_probe__": True}
    return rec


def _chunker_dispatch_keys() -> set[str]:
    """Language keys the L2 chunker dispatches on — read from the
    dispatch conditions themselves (``record.language == "x"`` /
    ``record.language in ("x", "y")``)."""
    src = open(os.path.join(
        ROOT, "plugins", "chunks_embeddings", "chunker.py")).read()
    keys: set[str] = set()
    for m in re.finditer(r'record\.language\s*==\s*"([a-z-]+)"', src):
        keys.add(m.group(1))
    for m in re.finditer(r'record\.language\s+in\s+\(([^)]*)\)', src):
        keys.update(re.findall(r'"([a-z-]+)"', m.group(1)))
    return keys


def probe_all() -> dict[str, dict]:
    from codebase_mapper.shared_kernel.constants import LANG_BY_EXT
    from codebase_mapper.shared_kernel.extensions import (
        PipelineCtx, iter_import_resolvers, iter_language_analyzers,
        reset_registries,
    )
    from plugins.llm_enrich.enricher import SUPPORTED_LANGUAGES

    reset_registries()
    analyzers = iter_language_analyzers()
    resolvers = iter_import_resolvers()
    detected_keys = set(LANG_BY_EXT.values())
    chunker_keys = _chunker_dispatch_keys()

    def probe(key: str) -> dict:
        rec = _probe_record(key)
        ctx = PipelineCtx(repo=None, commit="", records=[rec],
                          blob_by_path={}, mode_by_path={},
                          paths_set={rec.path}, read_path=lambda p: b"")

        def any_match(components) -> bool:
            for comp in components:
                try:
                    if comp.matches(rec, ctx):
                        return True
                except Exception:
                    continue
            return False

        alias = _MODULE_ALIAS.get(key, key)
        facets = {
            "detected": key in detected_keys,
            "analyzer": any_match(analyzers),
            "resolver": any_match(resolvers),
            "chunker": key in chunker_keys,
            "l4": key in SUPPORTED_LANGUAGES,
            "tests": (
                os.path.exists(os.path.join(
                    ROOT, "tests", f"test_signatures_{alias}.py"))
                or os.path.exists(os.path.join(
                    ROOT, "tests", f"verify_{alias}.py"))),
        }
        if all(facets.values()):
            level = "first_class"
        elif facets["detected"] and facets["analyzer"]:
            level = "partial"
        elif facets["detected"]:
            level = "detected"
        else:
            level = "none"
        return {"level": level, "facets": facets}

    reg = yaml.safe_load(open(REGISTRY))
    return {e["tiobe_name"]: probe(e["mapper_key"]) if e.get("mapper_key")
            else {"level": "none", "facets": {}}
            for e in reg["languages"]}


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    reg = yaml.safe_load(open(REGISTRY))
    entries = reg.get("languages", [])
    probed = probe_all()

    if "--fill" in argv:
        for e in entries:
            print(f"{e['tiobe_name']}: {probed[e['tiobe_name']]['level']}"
                  f"  {probed[e['tiobe_name']]['facets'] or ''}")
        return 0

    print("Language-support goal — TIOBE top 50, cumulative since 2026")
    meta = reg.get("meta", {})
    check("registry declares a dated, verifiable source",
          bool(meta.get("source")) and bool(meta.get("snapshot")),
          f"meta={meta}")
    check("registry holds the full cumulative set (>= 50 languages)",
          len(entries) >= 50, f"{len(entries)} entries")
    names = [e["tiobe_name"] for e in entries]
    check("no duplicate languages", len(names) == len(set(names)))
    check("every entry carries first_seen + status",
          all(e.get("first_seen") and e.get("status") in LEVELS
              for e in entries))

    mismatches = []
    regressions = []
    for e in entries:
        name, claimed = e["tiobe_name"], e["status"]
        actual = probed[name]["level"]
        if claimed != actual:
            direction = ("fabricated (ledger > code)"
                         if LEVELS.index(claimed) > LEVELS.index(actual)
                         else "stale (code > ledger)")
            mismatches.append(f"{name}: ledger={claimed} probed={actual}"
                              f" [{direction}] {probed[name]['facets']}")
            if claimed == "first_class":
                regressions.append(name)
    check("ledger matches probed reality for every language",
          not mismatches, " | ".join(mismatches[:6]))
    check("no support regression on first_class languages",
          not regressions, ", ".join(regressions))

    tally = {lvl: sum(1 for e in entries if e["status"] == lvl)
             for lvl in LEVELS}
    print(f"\n  progress: {tally['first_class']}/{len(entries)} first-class"
          f" · {tally['partial']} partial · {tally['detected']} detected"
          f" · {tally['none']} unsupported")
    print(f"\npassed: {PASS}   failed: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
