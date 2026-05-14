#!/usr/bin/env python3
"""verify_rust_tests_edges.py — Stage 3 contract for Rust test edges.

Exercises:

  1. ``count_rust_inline_test_files`` correctly counts source files
     containing ``#[test]`` / ``#[tokio::test]`` / ``#[async_std::test]``
     functions (the ``#[cfg(test)] mod tests { #[test] fn … }`` pattern).
  2. ``infer_tests_edges`` falls back to Rust ``use``-analysis when
     filename heuristics produce no match. The fixture's integration
     test ``tests/integration_aggregate.rs`` has a name that doesn't
     mirror any source-file basename, so only use-analysis can
     attribute it to ``src/helpers.rs``.
  3. ``#[cfg(test)]`` on a module by itself is NOT counted (it gates
     compilation but doesn't mark functions as tests).
  4. Backwards compat: ``infer_tests_edges(records)`` still works
     without the new kwargs (default args preserve existing behavior).

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import sys

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codebase_mapper.languages.rust import extract_rust_ast_summary
from codebase_mapper.models import FileRecord
from codebase_mapper.tests_edges import (
    count_rust_inline_test_files,
    infer_tests_edges,
)


# --------------------------------------------------------------------------
# Record-builder utilities
# --------------------------------------------------------------------------


def _build_record(repo_relative_path: str, fs_path: Path, type_: str) -> FileRecord:
    """Construct a FileRecord with a real ast_summary computed from the
    fixture file. The ``path`` field is a bundle-relative path, which
    may differ from the on-disk location (we mount the fixture as if
    its Cargo.toml were at the repo root)."""
    content = fs_path.read_bytes()
    summary, _errors = extract_rust_ast_summary(content, repo_relative_path)
    return FileRecord(
        path=repo_relative_path,
        git_blob_sha="0" * 40,
        content_sha256="0" * 64,
        size_bytes=len(content),
        language="rust",
        type_=type_,
        phases=["dev", "runtime"] if type_ == "source_code" else ["test"],
        ast_summary=summary,
    )


def _xref_crate_records() -> tuple[list[FileRecord], set[str]]:
    """Build the records for the xref_crate fixture: lib.rs, helpers.rs,
    and the integration test under tests/."""
    base = REPO_ROOT / "tests" / "fixtures" / "rust" / "xref_crate"
    records = [
        _build_record("src/lib.rs", base / "src" / "lib.rs", "source_code"),
        _build_record("src/helpers.rs", base / "src" / "helpers.rs", "source_code"),
        _build_record(
            "tests/integration_aggregate.rs",
            base / "tests" / "integration_aggregate.rs",
            "test_code",
        ),
    ]
    paths = {r.path for r in records}
    return records, paths


def _crates() -> list[dict]:
    """Mirror the workspace pipeline.detect_rust_workspaces output for
    our fixture: single crate at the repo root."""
    return [{"name": "xref_fixture", "crate_dir": ""}]


# --------------------------------------------------------------------------
# Tests — inline #[test] detection
# --------------------------------------------------------------------------


def test_inline_test_count_on_sample_rs() -> None:
    """``tests/fixtures/rust/sample.rs`` has both a free ``#[test]`` and
    a ``#[cfg(test)] mod tests { #[test] fn … }`` — should count as one
    file (per-file, not per-function)."""
    sample = REPO_ROOT / "tests" / "fixtures" / "rust" / "sample.rs"
    rec = _build_record("sample.rs", sample, "source_code")
    assert count_rust_inline_test_files([rec]) == 1, (
        "expected sample.rs to count as 1 inline-test file"
    )


def test_inline_test_count_zero_on_no_test_attrs() -> None:
    """The xref_crate fixture's source files have no ``#[test]``
    attributes — only the integration test does."""
    records, _ = _xref_crate_records()
    src_records = [r for r in records if r.type_ == "source_code"]
    assert count_rust_inline_test_files(src_records) == 0, (
        f"expected 0 inline-test source files in xref_crate; "
        f"items by file: " + str({
            r.path: [
                (it["name"], it.get("attributes"))
                for it in (r.ast_summary or {}).get("items", [])
                if "test" in str(it.get("attributes", "")).lower()
            ]
            for r in src_records
        })
    )


def test_cfg_test_alone_is_not_a_test_marker() -> None:
    """``#[cfg(test)]`` on a module without any inner ``#[test]`` fn
    should NOT count. This is the regression test for over-counting
    (the false positive that "test in the attribute string" would
    introduce)."""
    src = (
        b"#[cfg(test)]\n"
        b"mod gated {\n"
        b"    pub fn ordinary() -> u8 { 0 }\n"
        b"}\n"
    )
    summary, _ = extract_rust_ast_summary(src, "gated.rs")
    rec = FileRecord(
        path="gated.rs",
        git_blob_sha="0" * 40,
        content_sha256="0" * 64,
        size_bytes=len(src),
        language="rust",
        type_="source_code",
        phases=["dev"],
        ast_summary=summary,
    )
    assert count_rust_inline_test_files([rec]) == 0, (
        "#[cfg(test)] alone should not mark a file as inline-test"
    )


# --------------------------------------------------------------------------
# Tests — tests_edges with Rust use-analysis fallback
# --------------------------------------------------------------------------


def test_use_analysis_finds_subject_when_filename_misses() -> None:
    """``tests/integration_aggregate.rs`` shares no basename with any
    src/ file; only use-analysis can attribute it to src/helpers.rs."""
    records, paths = _xref_crate_records()
    edges = infer_tests_edges(records, rust_crates=_crates(), paths_set=paths)
    subject_paths = {e.subject_path for e in edges if e.test_path == "tests/integration_aggregate.rs"}
    assert "src/helpers.rs" in subject_paths, (
        f"use-analysis fallback did not find src/helpers.rs; "
        f"edges={[(e.test_path, e.subject_path) for e in edges]}"
    )


def test_path_heuristic_still_runs_first() -> None:
    """When a filename DOES match a source basename, the path heuristic
    fires; use-analysis only runs as a fallback."""
    records, paths = _xref_crate_records()
    # Synthesize tests/helpers_test.rs — same crate-rooted import as the
    # integration_aggregate fixture, but a filename that matches helpers.
    test_path = "tests/helpers_test.rs"
    base = REPO_ROOT / "tests" / "fixtures" / "rust" / "xref_crate"
    helpers_test_rec = _build_record(
        test_path,
        base / "tests" / "integration_aggregate.rs",  # reuse content
        "test_code",
    )
    # Rewrite the path on the record (the ast_summary doesn't depend on it).
    helpers_test_rec.path = test_path
    records.append(helpers_test_rec)
    paths.add(test_path)

    edges = infer_tests_edges(records, rust_crates=_crates(), paths_set=paths)
    # Path heuristic must have matched helpers_test → helpers.rs.
    helpers_test_subjects = {e.subject_path for e in edges if e.test_path == test_path}
    assert "src/helpers.rs" in helpers_test_subjects, (
        f"path heuristic missed helpers_test.rs → src/helpers.rs; "
        f"edges from {test_path}: {helpers_test_subjects}"
    )


def test_backwards_compatible_without_kwargs() -> None:
    """``infer_tests_edges(records)`` — the original signature — must
    still work. Use-analysis is opt-in."""
    records, _ = _xref_crate_records()
    edges = infer_tests_edges(records)
    # Path heuristic alone produces NO edges for integration_aggregate
    # (no matching basename). The call must not raise.
    int_edges = [e for e in edges if e.test_path == "tests/integration_aggregate.rs"]
    assert int_edges == [], (
        f"expected no path-heuristic edges for integration_aggregate; got {int_edges}"
    )


def test_no_external_crate_subjects() -> None:
    """The integration test imports from ``xref_fixture::helpers`` (in
    repo) — no external-crate subjects should leak into the edges."""
    records, paths = _xref_crate_records()
    edges = infer_tests_edges(records, rust_crates=_crates(), paths_set=paths)
    for e in edges:
        assert e.subject_path in paths, (
            f"edge subject {e.subject_path!r} is not in paths_set"
        )


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def main() -> int:
    tests = [
        ("inline test count on sample.rs", test_inline_test_count_on_sample_rs),
        ("inline test count zero on no test attrs", test_inline_test_count_zero_on_no_test_attrs),
        ("#[cfg(test)] alone is not a test marker", test_cfg_test_alone_is_not_a_test_marker),
        ("use-analysis finds subject when filename misses", test_use_analysis_finds_subject_when_filename_misses),
        ("path heuristic still runs first", test_path_heuristic_still_runs_first),
        ("backwards compatible without kwargs", test_backwards_compatible_without_kwargs),
        ("no external crate subjects", test_no_external_crate_subjects),
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            print(f"FAIL  {name}: {exc}", file=sys.stderr)
            failures += 1
        except Exception as exc:  # noqa: BLE001
            import traceback
            print(f"ERROR {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            failures += 1
        else:
            print(f"PASS  {name}")
    if failures:
        print(f"\n{failures} test(s) failed.", file=sys.stderr)
        return 1
    print(f"\nAll {len(tests)} tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
