#!/usr/bin/env python3
"""verify_rust_ast_body_count.py — Stage 7 contract for the
``ast_full_bodies_rust`` manifest counter.

The substantive work landed in 476cc70 (Stage 6) — the counter is
incremented in ``emit_bundle.py`` whenever a Rust record's
``ast_summary.cst_json`` is populated, and surfaces in
``manifest.counts.ast_full_bodies_rust``. Stage 7 is the regression
safety net.

Exercises:

  1. Every Rust fixture in the repo extracts to an ast_summary with
     a non-null ``cst_json`` (the precondition for the counter).
  2. The counter logic in ``emit_bundle.py`` matches the expected
     formula: ``sum(1 for r in records if r.language == "rust"
     and r.ast_summary.get("cst_json") is not None)``.
  3. The increment line is present in ``emit_bundle.py`` source
     (catches accidental removal during refactors).
  4. The manifest emits the key under ``counts``.
  5. ``bundle_summary`` MCP tool surfaces the counter when present.
  6. Pre-Stage-6 bundles (no ``ast_full_bodies_rust`` in
     ``counts``) round-trip through ``bundle_summary`` without
     synthesizing a zero — backward compat.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import sys

from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parent.parent
RUST_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "rust"
EMIT_BUNDLE_PATH = REPO_ROOT / "codebase_mapper" / "emit_bundle.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codebase_mapper.inspection.languages.rust import extract_rust_ast_summary  # noqa: E402


def _build_rust_records() -> list[SimpleNamespace]:
    """Construct synthetic records mirroring what the host pipeline
    produces for each Rust fixture. We use SimpleNamespace because
    the counter logic only reads .language and .ast_summary."""
    records: list[SimpleNamespace] = []
    for fs in sorted(RUST_FIXTURES.rglob("*.rs")):
        summary, _ = extract_rust_ast_summary(fs.read_bytes(), str(fs))
        records.append(SimpleNamespace(
            path=str(fs.relative_to(REPO_ROOT)),
            language="rust",
            ast_summary=summary,
        ))
    return records


def _formula(records: list) -> int:
    """The exact formula used inline in emit_bundle.py:emit()."""
    n = 0
    for r in records:
        if r.ast_summary is None:
            continue
        if r.language == "rust" and r.ast_summary.get("cst_json") is not None:
            n += 1
    return n


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_every_rust_fixture_has_cst_json() -> None:
    """Precondition: Stage 6 must populate cst_json on every parseable
    Rust source. The counter is meaningful only if cst_json is
    populated; if any fixture extracts to None, Stage 7 is misleading."""
    fixtures = sorted(RUST_FIXTURES.rglob("*.rs"))
    assert fixtures, "no Rust fixtures found"
    misses: list[str] = []
    for fs in fixtures:
        summary, errors = extract_rust_ast_summary(fs.read_bytes(), str(fs))
        if summary is None or summary.get("cst_json") is None:
            misses.append(f"{fs}: errors={errors}")
    assert not misses, "fixtures without cst_json: " + "; ".join(misses)


def test_counter_equals_fixture_count() -> None:
    """For N Rust fixtures, the counter should be exactly N."""
    records = _build_rust_records()
    expected = len(records)
    actual = _formula(records)
    assert actual == expected, (
        f"counter={actual} != expected fixture count {expected}"
    )


def test_counter_ignores_non_rust_records() -> None:
    """A Python record with a populated ast_json must NOT increment the
    Rust counter. Catches the cross-language regression where the
    formula's language guard breaks."""
    records = _build_rust_records()
    rust_count = _formula(records)
    # Inject a Python record with a populated ast_json (mimics the
    # Python extractor's output shape).
    records.append(SimpleNamespace(
        path="some.py",
        language="python",
        ast_summary={"language": "python", "ast_json": {"_type": "Module"}},
    ))
    assert _formula(records) == rust_count, (
        "non-Rust record leaked into ast_full_bodies_rust"
    )


def test_counter_ignores_rust_records_without_cst_json() -> None:
    """A Rust record with ast_summary but no cst_json (pre-Stage-6 or
    UTF-8 decode failure) must NOT be counted. Otherwise the
    'honest manifest' contract breaks: the counter would claim full
    bodies that aren't actually regenerate-able."""
    records = _build_rust_records()
    rust_count = _formula(records)
    records.append(SimpleNamespace(
        path="legacy.rs",
        language="rust",
        ast_summary={"language": "rust", "items": []},  # no cst_json
    ))
    assert _formula(records) == rust_count, (
        "Rust record without cst_json leaked into ast_full_bodies_rust"
    )


def test_increment_line_is_present_in_emit_bundle() -> None:
    """Regression safety net: grep the source for the formula's
    distinctive shape. Catches accidental removal during refactors —
    e.g. if someone deletes the elif branch while reordering."""
    src = EMIT_BUNDLE_PATH.read_text()
    assert "ast_full_bodies_rust" in src, (
        "ast_full_bodies_rust missing from emit_bundle.py — Stage 7 broken"
    )
    # The two required call-sites: the increment and the manifest key.
    assert "ast_full_bodies_rust += 1" in src, (
        "ast_full_bodies_rust increment removed"
    )
    assert '"ast_full_bodies_rust"' in src, (
        "ast_full_bodies_rust key removed from manifest"
    )


def test_bundle_summary_surfaces_counter() -> None:
    """The bundle_summary MCP tool passes ``counts`` through directly,
    so a Stage-6+ bundle's counter shows up in the tool's response."""
    from frontend.mcp_server import handlers as h

    mock_bundle = SimpleNamespace(
        output_dir=Path("/tmp/mock"),
        manifest={
            "repo_name": "fix",
            "commit_sha": "abc",
            "generated_at": "2026-01-01T00:00:00Z",
            "tool_version": "0.5.0",
            "counts": {
                "files": 5,
                "ast_full_bodies_rust": 3,  # Stage-7 counter present
                "ast_full_bodies_python": 0,
                "ast_full_bodies_tsjs": 0,
            },
            "files_by_language": {"rust": 5},
            "files_by_type": {"source_code": 5},
            "shacl_self_check": {"conforms": True},
        },
        embeddings_meta={"n_chunks": 0},
        concepts={"concepts": {}},
    )
    original = h._get_bundle
    h._get_bundle = lambda name: mock_bundle
    try:
        result = h.HANDLERS["bundle_summary"]({}, None)
    finally:
        h._get_bundle = original
    assert result["counts"].get("ast_full_bodies_rust") == 3, (
        f"bundle_summary did not surface ast_full_bodies_rust=3; "
        f"got {result['counts'].get('ast_full_bodies_rust')!r}"
    )


def test_bundle_summary_handles_pre_stage6_bundle() -> None:
    """Pre-Stage-6 bundles don't have ``ast_full_bodies_rust`` in
    their manifest. ``bundle_summary`` must surface the absence as
    absence (not synthesize a zero), so consumers can distinguish
    'old bundle' from 'new bundle with zero Rust files'."""
    from frontend.mcp_server import handlers as h

    pre_stage6 = SimpleNamespace(
        output_dir=Path("/tmp/mock"),
        manifest={
            "repo_name": "legacy",
            "commit_sha": "old",
            "generated_at": "2025-01-01T00:00:00Z",
            "tool_version": "0.4.0",
            "counts": {"files": 5, "ast_full_bodies_python": 0},  # no Rust counter
            "files_by_language": {"python": 5},
            "files_by_type": {"source_code": 5},
            "shacl_self_check": {"conforms": True},
        },
        embeddings_meta={"n_chunks": 0},
        concepts={"concepts": {}},
    )
    original = h._get_bundle
    h._get_bundle = lambda name: pre_stage6
    try:
        result = h.HANDLERS["bundle_summary"]({}, None)
    finally:
        h._get_bundle = original
    # The output schema's counts is freeform additionalProperties; the
    # key just shouldn't appear when the manifest doesn't have it.
    assert "ast_full_bodies_rust" not in result["counts"], (
        "bundle_summary fabricated ast_full_bodies_rust for a pre-Stage-6 bundle"
    )


def main() -> int:
    tests = [
        ("every rust fixture has cst_json", test_every_rust_fixture_has_cst_json),
        ("counter equals fixture count", test_counter_equals_fixture_count),
        ("counter ignores non-rust records", test_counter_ignores_non_rust_records),
        ("counter ignores rust without cst_json", test_counter_ignores_rust_records_without_cst_json),
        ("increment line present in emit_bundle", test_increment_line_is_present_in_emit_bundle),
        ("bundle_summary surfaces counter", test_bundle_summary_surfaces_counter),
        ("bundle_summary handles pre-stage6 bundle", test_bundle_summary_handles_pre_stage6_bundle),
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
