#!/usr/bin/env python3
"""verify_grammar_disclosure.py — a missing grammar must disclose, never hide.

BL-036. Every tree-sitter analyzer used to gate grammar availability inside its
``matches()`` predicate::

    def matches(self, record, ctx):
        return record.language == "rust" and TS_AVAILABLE   # <-- the defect

When the grammar package was absent, the analyzer simply did not match, the
dispatch loop in ``pipeline._extract_all`` fell through, ``extract()`` never
ran, and the ``tree_sitter_unavailable`` diagnostic *inside* it was unreachable
dead code. The file got no AST **and** no error marker: it was indistinguishable
from a file that genuinely holds nothing.

That is a silent degradation, which this project forbids (PALS's Law:
degradation is disclosed, never silent). ``coverage._FAILURE_MARKERS`` already
lists ``tree_sitter_unavailable`` — it was waiting for a signal the registry
path could never emit.

The fix: availability is decided by ``extract()``, never by ``matches()``. The
analyzer owns its language; a missing grammar is an *outcome* of extraction (a
disclosed failure), not a reason to pretend the language is unhandled. This is
already the contract Dart / COBOL / Clojure follow.

Covered:
  1. Regression guard (source): no ``matches()`` in _builtins.py gates on any
     availability flag. This is what makes the defect unreintroducible.
  2. With the grammar unavailable, the analyzer still matches its language.
  3. With the grammar unavailable, extract() yields ``tree_sitter_unavailable``.
  4. End-to-end through the real dispatch + _safe_extract: the record carries
     the marker and no AST.
  5. coverage.classify_file_coverage() reports had_extraction_failure=True —
     i.e. the file is booked as a disclosed failure, NOT as a silent zero.
  6. Availability does not change which language an analyzer claims.
"""
from __future__ import annotations

import argparse
import inspect
import re
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codebase_mapper.inspection import _builtins
from codebase_mapper.inspection.coverage import classify_file_coverage
from codebase_mapper.inspection.models import FileRecord
from codebase_mapper.inspection.pipeline import _safe_extract
from codebase_mapper.shared_kernel.extensions import (
    PipelineCtx, iter_language_analyzers, reset_registries,
)

PASS = 0
FAIL = 0

#: Every language whose analyzer depends on a tree-sitter grammar, with the
#: module that owns the availability flag and a path whose extension routes to
#: the right grammar.
TS_LANGUAGES: tuple[tuple[str, str, str], ...] = (
    ("typescript", "tsjs", "a.ts"),
    ("javascript", "tsjs", "a.js"),
    ("rust", "rust", "a.rs"),
    ("ruby", "ruby", "a.rb"),
    ("go", "go", "a.go"),
    ("java", "java", "A.java"),
    ("objective-c", "objc", "a.m"),
    ("c", "c", "a.c"),
    ("cpp", "cpp", "a.cpp"),
    ("kotlin", "kotlin", "A.kt"),
    ("swift", "swift", "A.swift"),
    ("cfml", "cfml", "a.cfc"),
)

#: The diagnostic that must reach the record when a grammar is absent.
MARKER = "tree_sitter_unavailable"

#: Availability flags an analyzer must never consult in matches().
AVAILABILITY_FLAGS = ("TS_AVAILABLE", "CFML_TS_AVAILABLE")


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def _record(language: str, path: str) -> FileRecord:
    return FileRecord(path=path, git_blob_sha="0" * 40, content_sha256="0" * 64,
                      size_bytes=1, language=language, type_="source_code",
                      phases=["runtime"])


def _ctx(rec: FileRecord) -> PipelineCtx:
    return PipelineCtx(repo=None, commit="", records=[rec], blob_by_path={},
                       mode_by_path={}, paths_set={rec.path},
                       read_path=lambda p: b"")


class _GrammarsGone:
    """Simulate an install with no tree-sitter grammars.

    Patches the availability flag off in the registry module *and* in every
    language module, which is exactly the state of the root container image
    today (its dependency list omits the grammar wheels — BL-030).
    """

    def __init__(self) -> None:
        self._saved: list[tuple[object, str, object]] = []

    def __enter__(self) -> "_GrammarsGone":
        mods = [_builtins]
        for _lang, mod_name, _p in TS_LANGUAGES:
            mods.append(sys.modules[f"codebase_mapper.inspection.languages.{mod_name}"])
        for mod in mods:
            for flag in AVAILABILITY_FLAGS:
                if hasattr(mod, flag):
                    self._saved.append((mod, flag, getattr(mod, flag)))
                    setattr(mod, flag, False)
        return self

    def __exit__(self, *exc: object) -> None:
        for mod, flag, value in self._saved:
            setattr(mod, flag, value)


def test_matches_never_gates_on_availability() -> None:
    """The regression guard: read the source of every matches() in _builtins."""
    print("\n-- matches() must not gate on grammar availability (regression guard) --")
    offenders: list[str] = []
    analyzers = [
        obj for name, obj in vars(_builtins).items()
        if inspect.isclass(obj) and name.endswith("Analyzer")
    ]
    check("analyzer classes discovered", len(analyzers) >= 12, f"n={len(analyzers)}")
    for cls in analyzers:
        try:
            src = inspect.getsource(cls.matches)
        except (OSError, TypeError):
            continue
        for flag in AVAILABILITY_FLAGS:
            if re.search(rf"\b{flag}\b", src):
                offenders.append(f"{cls.__name__}.matches reads {flag}")
    check("no matches() consults an availability flag",
          not offenders, "; ".join(offenders))


def test_unavailable_grammar_is_disclosed() -> None:
    print("\n-- a missing grammar discloses through the real dispatch path --")
    reset_registries()  # clears, then re-registers the builtin analyzers
    analyzers = list(iter_language_analyzers())

    with _GrammarsGone():
        for language, _mod, path in TS_LANGUAGES:
            rec = _record(language, path)
            ctx = _ctx(rec)

            matched = [a for a in analyzers if a.matches(rec, ctx)]
            check(f"{language}: analyzer still matches with no grammar",
                  len(matched) == 1, f"matched={[a.name for a in matched]}")
            if not matched:
                continue

            # The real dispatch: pipeline._extract_all runs exactly this.
            summary, errors = _safe_extract(matched[0], rec, b"x", ctx)
            rec.ast_summary, rec.extraction_errors = summary, errors

            check(f"{language}: extraction discloses {MARKER}",
                  MARKER in errors, f"errors={errors}")
            check(f"{language}: no AST is fabricated",
                  summary is None, f"summary={type(summary).__name__}")

            cov = classify_file_coverage(rec)
            check(f"{language}: booked as a disclosed failure, not a silent zero",
                  cov.had_extraction_failure is True and cov.ast_present is False,
                  f"had_extraction_failure={cov.had_extraction_failure} "
                  f"ast_present={cov.ast_present}")


def test_availability_does_not_change_language_claim() -> None:
    """An analyzer claims the same language whether or not its grammar is there.

    This is the invariant the fix rests on: availability is an outcome of
    extraction, not a property of language ownership.
    """
    print("\n-- language ownership is independent of grammar availability --")
    reset_registries()  # clears, then re-registers the builtin analyzers
    analyzers = list(iter_language_analyzers())

    for language, _mod, path in TS_LANGUAGES:
        rec = _record(language, path)
        ctx = _ctx(rec)
        present = {a.name for a in analyzers if a.matches(rec, ctx)}
        with _GrammarsGone():
            absent = {a.name for a in analyzers if a.matches(rec, ctx)}
        check(f"{language}: same analyzer claims it either way",
              present == absent and len(present) == 1,
              f"present={present} absent={absent}")


def main() -> int:
    argparse.ArgumentParser().parse_args()
    print("== grammar-unavailable disclosure (BL-036) ==")
    test_matches_never_gates_on_availability()
    test_unavailable_grammar_is_disclosed()
    test_availability_does_not_change_language_claim()
    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
