#!/usr/bin/env python3
"""verify_ast_coverage.py — AST extraction coverage asset (R2).

The kernel-scale review (R2) asked for a published coverage table as a
first-class bundle asset: symbols extracted, parse errors, zero-AST
files, and — the load-bearing column — **files that parsed cleanly yet
yielded zero symbols**. Tree-sitter does not run the C preprocessor, so
macro-generated definitions (``SYSCALL_DEFINE*``, ``DEFINE_PER_CPU``,
tracepoint macros) produce *no parse error* and *no symbol*. A coverage
table that counts only parse errors would look green and hide exactly
that loss. This verifier pins the behavior that catches it.

Covers the pure logic in
[codebase_mapper/inspection/coverage.py](../codebase_mapper/inspection/coverage.py)
— no repository, no Ollama, no emission I/O required.

Run:  .venv/bin/python tests/verify_ast_coverage.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from codebase_mapper.inspection.coverage import (
    FileCoverage,
    aggregate_coverage,
    classify_file_coverage,
    count_symbols,
)
from codebase_mapper.inspection.models import FileRecord

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def _rec(path: str, language: str | None, type_: str,
         ast_summary: dict | None, errors: list[str] | None = None) -> FileRecord:
    return FileRecord(
        path=path, git_blob_sha="0" * 40, content_sha256="0" * 64,
        size_bytes=100, language=language, type_=type_, phases=[],
        ast_summary=ast_summary, extraction_errors=list(errors or []),
    )


# ------------------------------------------------------------- count_symbols

def test_count_symbols() -> None:
    check("items list counted",
          count_symbols({"language": "c", "items": [{"a": 1}, {"b": 2}]}) == 2)
    check("empty items → 0",
          count_symbols({"language": "c", "items": []}) == 0)
    check("None ast_summary → None (zero-AST)", count_symbols(None) is None)
    # Full-body languages express structure as a nested AST, not a flat
    # item list — not counted by this metric (returns None, not 0, so they
    # are never mistaken for silent-zero).
    check("python ast_json (no items) → None",
          count_symbols({"language": "python", "ast_json": {"body": []}}) is None)
    check("tsjs cst_json (no items) → None",
          count_symbols({"language": "typescript", "cst_json": {}}) is None)


# ------------------------------------------------------- classify_file_coverage

def test_healthy_file() -> None:
    fc = classify_file_coverage(_rec(
        "kernel/sched/fair.c", "c", "source_code",
        {"language": "c", "items": [{"name": "x"}, {"name": "y"}],
         "imports": [{"source": "sched.h"}]}))
    check("healthy: ast present", fc.ast_present is True)
    check("healthy: no parse error", fc.had_parse_error is False)
    check("healthy: symbol_count = 2", fc.symbol_count == 2, str(fc.symbol_count))
    check("healthy: import_count = 1", fc.import_count == 1, str(fc.import_count))
    check("healthy: NOT silent-zero", fc.is_silent_zero is False)


def test_silent_zero_macro_file() -> None:
    """The whole point of R2: clean parse, zero symbols → flagged."""
    fc = classify_file_coverage(_rec(
        "drivers/net/wireless/realtek/rtw89/rtw8852c_table.c", "c",
        "source_code",
        {"language": "c", "items": [], "imports": [{"source": "phy.h"}]}))
    check("silent-zero: ast present", fc.ast_present is True)
    check("silent-zero: no parse error", fc.had_parse_error is False)
    check("silent-zero: symbol_count = 0", fc.symbol_count == 0)
    check("silent-zero: IS flagged", fc.is_silent_zero is True)


def test_parse_error_is_not_silent() -> None:
    fc = classify_file_coverage(_rec(
        "broken.c", "c", "source_code",
        {"language": "c", "items": []}, ["parse_errors_present"]))
    check("parse-error: had_parse_error", fc.had_parse_error is True)
    check("parse-error: NOT silent-zero (it's a loud error)",
          fc.is_silent_zero is False)


def test_zero_ast_file() -> None:
    fc = classify_file_coverage(_rec(
        "weird.c", "c", "source_code", None,
        ["extract_failed: RuntimeError: boom"]))
    check("zero-AST: ast_present False", fc.ast_present is False)
    check("zero-AST: extraction failure flagged",
          fc.had_extraction_failure is True)
    check("zero-AST: symbol_count None", fc.symbol_count is None)
    check("zero-AST: NOT silent-zero (no clean AST)", fc.is_silent_zero is False)


def test_full_body_python_not_silent() -> None:
    fc = classify_file_coverage(_rec(
        "mod.py", "python", "source_code",
        {"language": "python", "ast_json": {"body": []}}))
    check("python: symbol_count None (full-body)", fc.symbol_count is None)
    check("python: NOT silent-zero (not item-counted)",
          fc.is_silent_zero is False)


def test_non_source_excluded() -> None:
    recs = [
        _rec("README.md", None, "documentation", None),
        _rec("Cargo.lock", None, "lockfile", None),
        _rec("a.c", "c", "source_code", {"items": [{"n": 1}]}),
    ]
    rep = aggregate_coverage(recs)
    check("only source_code in denominator", rep["n_source_files"] == 1,
          str(rep["n_source_files"]))


# ------------------------------------------------------------- aggregate

def _mixed_records() -> list[FileRecord]:
    return [
        _rec("a.c", "c", "source_code", {"items": [{"n": 1}, {"n": 2}]}),   # healthy
        _rec("table.c", "c", "source_code", {"items": []}),                # silent-zero
        _rec("broken.c", "c", "source_code", {"items": []},
             ["parse_errors_present"]),                                    # parse error
        _rec("gone.c", "c", "source_code", None,
             ["extract_failed: X"]),                                       # zero-AST
        _rec("g.go", "go", "source_code", {"items": [{"n": 1}]}),          # healthy go
        _rec("m.py", "python", "source_code",
             {"ast_json": {"body": []}}),                                  # full-body
        _rec("README.md", None, "documentation", None),                   # excluded
    ]


def test_aggregate_totals() -> None:
    rep = aggregate_coverage(_mixed_records())
    t = rep["totals"]
    check("n_source_files = 6", rep["n_source_files"] == 6, str(rep["n_source_files"]))
    check("files_with_ast = 5", t["files_with_ast"] == 5, str(t["files_with_ast"]))
    check("files_with_parse_errors = 1", t["files_with_parse_errors"] == 1,
          str(t["files_with_parse_errors"]))
    check("files_zero_ast = 1", t["files_zero_ast"] == 1, str(t["files_zero_ast"]))
    check("silent_zero count = 1", t["silent_zero_symbol_files"] == 1,
          str(t["silent_zero_symbol_files"]))
    check("total symbols = 3 (2+0+0+1)", t["symbols_extracted"] == 3,
          str(t["symbols_extracted"]))


def test_aggregate_by_language() -> None:
    rep = aggregate_coverage(_mixed_records())
    langs = rep["by_language"]
    check("c present", "c" in langs)
    check("c files = 4", langs["c"]["files"] == 4, str(langs.get("c")))
    check("c silent-zero = 1", langs["c"]["silent_zero_symbol_files"] == 1)
    check("python full_body counted", langs["python"]["full_body_files"] == 1,
          str(langs.get("python")))


def test_aggregate_silent_list_and_determinism() -> None:
    rep = aggregate_coverage(_mixed_records())
    sl = rep["silent_zero_symbol_file_list"]
    check("silent list has exactly the one file", len(sl) == 1, str(sl))
    check("silent list entry is the table file",
          sl[0]["path"] == "table.c", str(sl))
    # Deterministic: same input → identical output.
    import json
    a = json.dumps(aggregate_coverage(_mixed_records()), sort_keys=True)
    b = json.dumps(aggregate_coverage(_mixed_records()), sort_keys=True)
    check("aggregate is deterministic", a == b)


def test_silent_list_cap_discloses_truncation() -> None:
    """No silent caps: when the preview list is capped, the count and a
    truncation flag must still be reported (PALS's Law / R2 credibility)."""
    recs = [_rec(f"t{i}.c", "c", "source_code", {"items": []})
            for i in range(5)]
    rep = aggregate_coverage(recs, max_listed=2)
    check("full count preserved despite cap",
          rep["totals"]["silent_zero_symbol_files"] == 5,
          str(rep["totals"]))
    check("preview list capped at max_listed",
          len(rep["silent_zero_symbol_file_list"]) == 2,
          str(len(rep["silent_zero_symbol_file_list"])))
    check("truncation disclosed",
          rep["silent_zero_symbol_file_list_truncated"] is True)


def main() -> int:
    tests = [
        test_count_symbols,
        test_healthy_file,
        test_silent_zero_macro_file,
        test_parse_error_is_not_silent,
        test_zero_ast_file,
        test_full_body_python_not_silent,
        test_non_source_excluded,
        test_aggregate_totals,
        test_aggregate_by_language,
        test_aggregate_silent_list_and_determinism,
        test_silent_list_cap_discloses_truncation,
    ]
    for t in tests:
        print(f"\n{t.__name__}:")
        try:
            t()
        except Exception:
            global FAIL
            FAIL += 1
            print(f"  FAIL  {t.__name__} raised:")
            traceback.print_exc()
    print(f"\n{'=' * 50}\n  {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
