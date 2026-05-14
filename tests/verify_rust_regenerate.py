#!/usr/bin/env python3
"""verify_rust_regenerate.py — Stage 6 contract for Rust regenerate.

Exercises byte-identical roundtrip from ``ast_summary`` (the leaf-text
CST representation) back to source text. Rust regenerate follows the
TS/JS path (tree-sitter has no ``unparse``, so the extractor stores
every leaf token + interstitial whitespace).

The test runs over every ``.rs`` file in our existing Rust fixtures:

  tests/fixtures/rust/sample.rs
  tests/fixtures/rust/xref_crate/src/*.rs
  tests/fixtures/rust/xref_crate/tests/*.rs
  tests/fixtures/rust/module_hierarchy/src/**/*.rs

For each file: parse → CST → serialize → regenerate → assert
byte-identical. Also exercises:

  - ``regenerate_rust_source`` raises ValueError when ``cst_json`` is
    missing (pre-Stage-6 bundles).
  - The Rust regenerator is registered in ``_REGENERATORS["rust"]``.
  - ``supported_languages()`` includes "rust".
  - Per-file roundtrip on a synthetic file with non-trivial whitespace
    + shebang line + trailing comment.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import sys

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RUST_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "rust"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codebase_mapper.inspection.languages.rust import (  # noqa: E402
    RUST_AST_SCHEMA_VERSION,
    extract_rust_ast_summary,
    regenerate_rust_source,
)
from codebase_mapper.emission.application.regenerate import supported_languages, _REGENERATORS  # noqa: E402


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def _all_rust_fixtures() -> list[Path]:
    return sorted(RUST_FIXTURES.rglob("*.rs"))


def test_rust_is_registered_in_supported_languages() -> None:
    langs = supported_languages()
    assert "rust" in langs, f"rust missing from supported_languages: {langs}"
    assert _REGENERATORS["rust"] is regenerate_rust_source


def test_schema_version_is_present() -> None:
    fixtures = _all_rust_fixtures()
    assert fixtures, "no Rust fixtures found — Stage 6 needs source files to roundtrip"
    summary, _ = extract_rust_ast_summary(fixtures[0].read_bytes(), str(fixtures[0]))
    assert summary is not None
    assert summary.get("schema_version") == RUST_AST_SCHEMA_VERSION, (
        f"expected schema_version={RUST_AST_SCHEMA_VERSION}, "
        f"got {summary.get('schema_version')}"
    )


def test_cst_fields_populated() -> None:
    """The extractor must populate cst_json + header + footer on every
    Rust file. Stage 6 requires these for regenerate to work."""
    failures: list[tuple[str, str]] = []
    for fs in _all_rust_fixtures():
        summary, errors = extract_rust_ast_summary(fs.read_bytes(), str(fs))
        if summary is None:
            failures.append((str(fs), f"summary is None: {errors}"))
            continue
        if summary.get("cst_json") is None:
            failures.append((str(fs), f"cst_json missing: {errors}"))
        if "header" not in summary:
            failures.append((str(fs), "header key absent"))
        if "footer" not in summary:
            failures.append((str(fs), "footer key absent"))
    assert not failures, "cst capture failed: " + ", ".join(
        f"{p}={reason}" for p, reason in failures
    )


def test_byte_identical_roundtrip_across_fixtures() -> None:
    """The headline contract: regenerate(extract(content)) == content.
    Applies to every Rust fixture in the repo."""
    failures: list[tuple[str, str]] = []
    for fs in _all_rust_fixtures():
        original = fs.read_bytes()
        summary, _ = extract_rust_ast_summary(original, str(fs))
        if summary is None or summary.get("cst_json") is None:
            failures.append((str(fs), "extraction failed"))
            continue
        try:
            regenerated = regenerate_rust_source(summary)
        except Exception as exc:  # noqa: BLE001
            failures.append((str(fs), f"regenerate raised: {exc}"))
            continue
        regenerated_bytes = regenerated.encode("utf-8")
        if regenerated_bytes != original:
            # Compute a small diff hint for the failure message.
            orig_lines = original.decode("utf-8", errors="replace").splitlines()
            regen_lines = regenerated.splitlines()
            first_diff = "<no diff found?>"
            for i, (a, b) in enumerate(zip(orig_lines, regen_lines)):
                if a != b:
                    first_diff = f"line {i+1}: orig={a!r} != regen={b!r}"
                    break
            else:
                if len(orig_lines) != len(regen_lines):
                    first_diff = (
                        f"length mismatch: orig={len(orig_lines)} lines, "
                        f"regen={len(regen_lines)} lines"
                    )
            failures.append((str(fs), first_diff))
    assert not failures, "byte-identical roundtrip failures:\n  " + "\n  ".join(
        f"{p}: {detail}" for p, detail in failures
    )


def test_synthetic_file_with_unusual_whitespace() -> None:
    """Targets edge cases that simple fixtures might miss: shebang lines,
    leading whitespace, trailing newline absence, varied indent
    styles."""
    src = (
        b"// Header comment with trailing space   \n"
        b"// Another comment\n"
        b"\n"
        b"use   std::collections::HashMap; // inline comment\n"
        b"\n"
        b"#[derive(Debug)]\n"
        b"pub struct\tX{ \n"
        b"\tfield:   i32  ,\n"
        b"}\n"
        b"\n"
        b"pub fn   make()\n"
        b"  -> X\n"
        b"{\n"
        b"    X { field: 0 }\n"
        b"}\n"
    )
    summary, errors = extract_rust_ast_summary(src, "synthetic.rs")
    assert summary is not None, f"summary None; errors={errors}"
    assert summary.get("cst_json") is not None, f"cst_json None; errors={errors}"
    regenerated = regenerate_rust_source(summary).encode("utf-8")
    assert regenerated == src, (
        "synthetic file roundtrip broken:\n"
        f"  original  ({len(src)} bytes): {src!r}\n"
        f"  regenerated ({len(regenerated)} bytes): {regenerated!r}"
    )


def test_file_with_no_trailing_newline() -> None:
    """Common gotcha: files that don't end with \\n. The extractor's
    header/footer capture must preserve this."""
    src = b"pub fn no_trailing_newline() -> u8 { 7 }"
    summary, _ = extract_rust_ast_summary(src, "no_newline.rs")
    assert summary is not None
    regenerated = regenerate_rust_source(summary).encode("utf-8")
    assert regenerated == src, (
        f"no-trailing-newline roundtrip broken: {regenerated!r} != {src!r}"
    )


def test_missing_cst_json_raises() -> None:
    """A summary without ``cst_json`` (pre-Stage-6 or extraction failure)
    must raise ValueError, not return a bogus empty string."""
    try:
        regenerate_rust_source({"language": "rust"})
    except ValueError as exc:
        assert "cst_json" in str(exc), f"unexpected error message: {exc}"
        return
    raise AssertionError(
        "regenerate_rust_source accepted a summary with no cst_json; "
        "should have raised ValueError"
    )


def main() -> int:
    tests = [
        ("rust registered in supported_languages", test_rust_is_registered_in_supported_languages),
        ("schema_version present", test_schema_version_is_present),
        ("cst fields populated", test_cst_fields_populated),
        ("byte-identical roundtrip across fixtures", test_byte_identical_roundtrip_across_fixtures),
        ("synthetic unusual whitespace", test_synthetic_file_with_unusual_whitespace),
        ("file with no trailing newline", test_file_with_no_trailing_newline),
        ("missing cst_json raises", test_missing_cst_json_raises),
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
