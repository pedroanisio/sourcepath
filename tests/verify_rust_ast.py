#!/usr/bin/env python3
"""verify_rust_ast.py — Stage 1 deep-AST contract for Rust.

Exercises:
  1. ``extract_rust_ast_summary`` populates the new ``items`` field with
     per-symbol metadata (kind, name, parent, byte/line ranges,
     attributes, is_pub, is_async) for every function/method/struct/
     enum/trait/impl/mod in tests/fixtures/rust/sample.rs.
  2. Attributes are correctly attached to the items they decorate
     (``#[derive(...)]``, ``#[inline]``, ``#[test]``, ``#[cfg(test)]``).
  3. ``_chunk_rust`` emits symbol-level L2 chunks for every chunkable
     top-level item (functions, structs, enums, traits, impl blocks,
     trait/impl methods). Chunks carry plausible byte/line ranges and
     a SHA-256 of their text.
  4. Inline ``#[test]`` functions are surfaced in ``items`` with their
     attribute, so downstream consumers can detect tests outside the
     ``tests/`` directory convention.
  5. Nested ``mod tests { ... }`` items recurse and surface inner
     functions with ``parent == "tests"``.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import sys

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "rust" / "sample.rs"

# `frontend` and `plugins` aren't on sys.path by default; the host package
# is, via pip install -e .
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_fixture() -> bytes:
    assert FIXTURE.is_file(), f"missing fixture: {FIXTURE}"
    return FIXTURE.read_bytes()


def _find(items: list[dict], name: str, kind: str | None = None,
          parent: str | None | object = ...) -> dict:
    """Find a single item by name + optional (kind, parent) filter.

    Names are not unique: ``Account`` matches the struct and two impls;
    ``save``/``load`` match both trait method signatures and their impls.
    Pass ``kind`` and/or ``parent`` to disambiguate. ``parent=...`` (the
    sentinel) means "don't filter on parent".
    """
    matches = [it for it in items if it["name"] == name]
    if kind is not None:
        matches = [it for it in matches if it["kind"] == kind]
    if parent is not ...:
        matches = [it for it in matches if it.get("parent") == parent]
    assert matches, (
        f"no item matched name={name!r} kind={kind!r} parent={parent!r}"
    )
    assert len(matches) == 1, (
        f"ambiguous match for name={name!r} kind={kind!r} parent={parent!r}: "
        f"{matches}"
    )
    return matches[0]


def _items_with_parent(items: list[dict], parent: str | None) -> list[dict]:
    return [it for it in items if it.get("parent") == parent]


# --------------------------------------------------------------------------
# AST-summary checks
# --------------------------------------------------------------------------


def test_summary_has_items_field() -> None:
    from codebase_mapper.languages.rust import extract_rust_ast_summary

    content = _load_fixture()
    summary, errors = extract_rust_ast_summary(content, "sample.rs")
    assert summary is not None, f"summary is None; errors={errors}"
    assert "items" in summary, "summary missing 'items' key"
    assert isinstance(summary["items"], list)
    assert summary["items"], "items list is empty for a non-empty fixture"


def test_top_level_kinds_present() -> None:
    from codebase_mapper.languages.rust import extract_rust_ast_summary

    summary, _ = extract_rust_ast_summary(_load_fixture(), "sample.rs")
    items = summary["items"]
    # Async pub function at top level
    fetch = _find(items, "fetch_account", kind="function", parent=None)
    assert fetch["is_async"] is True
    assert fetch["is_pub"] is True
    # Inline-test free function (non-pub)
    inline_test = _find(items, "account_starts_at_zero", kind="function", parent=None)
    assert inline_test["is_pub"] is False
    # Types
    _find(items, "Account", kind="struct", parent=None)
    _find(items, "AccountKind", kind="enum", parent=None)
    _find(items, "Persistable", kind="trait", parent=None)
    # The `mod tests` module
    _find(items, "tests", kind="mod", parent=None)


def test_attributes_attach() -> None:
    from codebase_mapper.languages.rust import extract_rust_ast_summary

    summary, _ = extract_rust_ast_summary(_load_fixture(), "sample.rs")
    items = summary["items"]
    # Struct carries the derive (not the impls, which share the name)
    account_struct = _find(items, "Account", kind="struct", parent=None)
    assert any("derive" in a for a in account_struct["attributes"]), (
        f"#[derive] not attached to Account struct; attrs={account_struct['attributes']}"
    )
    # Inline-test free function carries #[test]
    test_fn = _find(items, "account_starts_at_zero", kind="function", parent=None)
    assert any("test" in a for a in test_fn["attributes"]), (
        f"#[test] not attached to account_starts_at_zero; attrs={test_fn['attributes']}"
    )
    # cfg(test) on the mod
    tests_mod = _find(items, "tests", kind="mod", parent=None)
    assert any("cfg" in a for a in tests_mod["attributes"]), (
        f"#[cfg(test)] not attached to mod tests; attrs={tests_mod['attributes']}"
    )


def test_impl_methods_recurse() -> None:
    from codebase_mapper.languages.rust import extract_rust_ast_summary

    summary, _ = extract_rust_ast_summary(_load_fixture(), "sample.rs")
    items = summary["items"]
    # Methods under the inherent impl carry parent == "Account"
    inherent_methods = [it for it in items if it.get("parent") == "Account" and it["kind"] == "method"]
    names = {it["name"] for it in inherent_methods}
    assert {"new", "deposit", "internal_audit"}.issubset(names), (
        f"inherent impl methods missing; got {names}"
    )
    deposit = next(it for it in inherent_methods if it["name"] == "deposit")
    assert deposit["is_async"] is True
    assert deposit["is_pub"] is True
    audit = next(it for it in inherent_methods if it["name"] == "internal_audit")
    assert any("inline" in a for a in audit["attributes"]), (
        f"#[inline] not attached to internal_audit; attrs={audit['attributes']}"
    )
    assert audit["is_pub"] is False


def test_trait_methods_recurse() -> None:
    from codebase_mapper.languages.rust import extract_rust_ast_summary

    summary, _ = extract_rust_ast_summary(_load_fixture(), "sample.rs")
    items = summary["items"]
    trait_methods = [it for it in items if it.get("parent") == "Persistable"]
    names = {it["name"] for it in trait_methods}
    assert {"save", "load"}.issubset(names), (
        f"trait Persistable methods missing; got {names}"
    )


def test_nested_mod_recurses() -> None:
    from codebase_mapper.languages.rust import extract_rust_ast_summary

    summary, _ = extract_rust_ast_summary(_load_fixture(), "sample.rs")
    items = summary["items"]
    nested = [it for it in items if it.get("parent") == "tests"]
    names = {it["name"] for it in nested}
    assert "deposit_increases_balance" in names, (
        f"inner fn deposit_increases_balance not surfaced; got {names}"
    )
    fn = next(it for it in nested if it["name"] == "deposit_increases_balance")
    assert any("test" in a for a in fn["attributes"]), (
        f"#[test] not attached to nested fn; attrs={fn['attributes']}"
    )


# --------------------------------------------------------------------------
# Chunker checks
# --------------------------------------------------------------------------


def test_chunker_emits_symbol_level_chunks() -> None:
    from plugins.chunks_embeddings.chunker import _chunk_rust

    content = _load_fixture()
    chunks = _chunk_rust(content, "sample.rs")
    assert chunks, "chunker returned no chunks for a non-empty fixture"
    by_name = {c["symbol"]: c for c in chunks}
    # Top-level functions
    assert "fetch_account" in by_name
    assert by_name["fetch_account"]["kind"] == "function"
    assert by_name["account_starts_at_zero"]["kind"] == "function"
    # Types
    assert by_name["Account"]["kind"] == "class"
    assert by_name["AccountKind"]["kind"] == "class"
    assert by_name["Persistable"]["kind"] == "class"
    # Trait methods are emitted as method chunks
    trait_methods = [c for c in chunks if c["parent_symbol"] == "Persistable"]
    assert {m["symbol"] for m in trait_methods} == {"save", "load"}
    # Inherent impl methods
    inherent = [c for c in chunks if c["parent_symbol"] == "Account" and c["kind"] == "method"]
    inherent_names = {m["symbol"] for m in inherent}
    assert {"new", "deposit", "internal_audit"}.issubset(inherent_names)


def test_chunk_ranges_are_plausible() -> None:
    from plugins.chunks_embeddings.chunker import _chunk_rust

    content = _load_fixture()
    chunks = _chunk_rust(content, "sample.rs")
    for c in chunks:
        assert c["byte_start"] >= 0
        assert c["byte_end"] > c["byte_start"], f"empty chunk: {c}"
        assert c["line_start"] >= 1
        assert c["line_end"] >= c["line_start"]
        assert c["text"], f"empty text: {c}"
        assert len(c["content_sha256"]) == 64, f"bad sha: {c['content_sha256']}"


def main() -> int:
    tests = [
        ("summary has items field", test_summary_has_items_field),
        ("top-level kinds present", test_top_level_kinds_present),
        ("attributes attach", test_attributes_attach),
        ("impl methods recurse", test_impl_methods_recurse),
        ("trait methods recurse", test_trait_methods_recurse),
        ("nested mod recurses", test_nested_mod_recurses),
        ("chunker emits symbol chunks", test_chunker_emits_symbol_level_chunks),
        ("chunk ranges plausible", test_chunk_ranges_are_plausible),
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            print(f"FAIL  {name}: {exc}", file=sys.stderr)
            failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
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
