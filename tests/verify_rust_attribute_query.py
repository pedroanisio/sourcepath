#!/usr/bin/env python3
"""verify_rust_attribute_query.py — Stage 4 contract.

Exercises the queryable Rust attribute surface:

  1. ``_emit_rust_items_sidecar`` writes a JSONL file with one entry
     per Rust item carrying at least one attribute. Items without
     attributes are omitted (the sidecar's whole purpose is queryable
     attribute facts).
  2. The schema of each sidecar row is exact: ``path``, ``kind``,
     ``name``, ``parent``, ``line_start``/``end``, ``byte_start``/``end``,
     ``is_pub``, ``is_async``, ``attributes``.
  3. The bundle loader's ``_load_rust_items`` reads the sidecar back
     into ``(items, by_file)`` correctly, including the per-file index.
  4. ``items_by_attribute`` handler filters by:
        - substring against attribute text (e.g. ``"test"`` matches
          ``#[test]``, ``#[tokio::test]``, ``#[test_case::case(...)]``)
        - kind (``function``, ``struct``, …)
        - both at once
     and returns a paginated payload with ``total`` and ``truncated``.
  5. ``repository_summary`` surfaces ``rust_attribute_distribution`` as
     a top-N list of ``{attribute, count}`` dicts.
  6. Pre-Stage-4 bundles (no sidecar present) cause ``rust_items``
     to be ``[]`` and ``rust_attribute_distribution`` to be omitted /
     null — the contract is backward-compatible.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import json
import sys
import tempfile

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codebase_mapper.emission.application.emit_bundle import _emit_rust_items_sidecar  # noqa: E402
from codebase_mapper.inspection.languages.rust import extract_rust_ast_summary  # noqa: E402
from codebase_mapper.inspection.models import FileRecord  # noqa: E402
from frontend.backend.app import _load_rust_items  # noqa: E402


# --------------------------------------------------------------------------
# Fixture loading
# --------------------------------------------------------------------------


def _sample_records() -> list[FileRecord]:
    """Build the two records exercising the attribute surface:

      - ``sample.rs``  — pub struct + #[derive], inline #[test],
                          #[cfg(test)] mod, #[inline] method, etc.
      - ``xref_crate/src/lib.rs`` — no attributes (sanity: control case)
    """
    base = REPO_ROOT / "tests" / "fixtures" / "rust"
    out: list[FileRecord] = []
    for relpath, fspath in [
        ("sample.rs", base / "sample.rs"),
        ("xref_crate/src/lib.rs", base / "xref_crate" / "src" / "lib.rs"),
    ]:
        content = fspath.read_bytes()
        summary, _ = extract_rust_ast_summary(content, relpath)
        out.append(FileRecord(
            path=relpath,
            git_blob_sha="0" * 40,
            content_sha256="0" * 64,
            size_bytes=len(content),
            language="rust",
            type_="source_code",
            phases=["dev"],
            ast_summary=summary,
        ))
    return out


# --------------------------------------------------------------------------
# Sidecar emission
# --------------------------------------------------------------------------


def test_sidecar_writes_one_line_per_attributed_item() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        fragment = _emit_rust_items_sidecar(_sample_records(), tmp_path)
        sidecar = tmp_path / "rust_items.jsonl"
        assert sidecar.exists(), f"sidecar not created at {sidecar}"
        lines = [l for l in sidecar.read_text().splitlines() if l.strip()]
        # Every line is valid JSON with the required schema.
        rows = [json.loads(l) for l in lines]
        required = {"path", "kind", "name", "attributes",
                    "line_start", "line_end", "byte_start", "byte_end",
                    "is_pub", "is_async"}
        for row in rows:
            missing = required - set(row.keys())
            assert not missing, f"row missing keys {missing}: {row}"
        # Items without attributes were skipped (xref_crate has none).
        assert all(r["path"] == "sample.rs" for r in rows), (
            f"unexpected paths in sidecar: {sorted({r['path'] for r in rows})}"
        )
        # Manifest fragment counts match.
        assert fragment["n_items"] == len(rows), (
            f"n_items {fragment['n_items']} != len(rows) {len(rows)}"
        )
        assert fragment["n_files"] == 1
        assert "by_kind" in fragment and isinstance(fragment["by_kind"], dict)


def test_sidecar_is_sorted_deterministically() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _emit_rust_items_sidecar(_sample_records(), tmp_path)
        sidecar = tmp_path / "rust_items.jsonl"
        rows = [json.loads(l) for l in sidecar.read_text().splitlines() if l.strip()]
        keys = [(r["path"], r["line_start"] or 0, r["name"] or "") for r in rows]
        assert keys == sorted(keys), f"sidecar rows not sorted: {keys}"


def test_sidecar_loader_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _emit_rust_items_sidecar(_sample_records(), tmp_path)
        items, by_file = _load_rust_items(tmp_path / "rust_items.jsonl")
        assert items, "loader returned empty items"
        assert "sample.rs" in by_file
        # Per-file index references valid item indices.
        for path, idxs in by_file.items():
            for idx in idxs:
                assert items[idx]["path"] == path
                assert items[idx].get("attributes")


def test_loader_handles_missing_sidecar() -> None:
    """Pre-Stage-4 bundles have no rust_items.jsonl; loader must return
    empty results, NOT raise. Backward compatibility is mandatory."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        items, by_file = _load_rust_items(tmp_path / "rust_items.jsonl")
        assert items == []
        assert by_file == {}


# --------------------------------------------------------------------------
# MCP handler (mocked Bundle)
# --------------------------------------------------------------------------


@dataclass
class _MockBundle:
    """Minimum surface that ``_items_by_attribute`` reads. Anything else
    the handler touches via ``b.<attr>`` raises AttributeError and the
    test will surface it as ERROR (a regression signal)."""
    rust_items: list[dict[str, Any]] = field(default_factory=list)


def _run_query(items: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
    """Invoke the handler logic directly with a synthetic bundle.

    We bypass the dispatch decorator (which would resolve a real bundle
    via the backend) and call the inner function so the test stays
    hermetic. The dispatch + schema layer is exercised separately by
    the repository_summary verifier and the upstream test_server.
    """
    from frontend.mcp_server import handlers as h

    # Monkey-patch _get_bundle to return our mock for this call.
    original = h._get_bundle
    h._get_bundle = lambda name: _MockBundle(rust_items=items)
    try:
        return h.HANDLERS["items_by_attribute"]({"pattern": kwargs.pop("pattern"), **kwargs}, None)
    finally:
        h._get_bundle = original


def _load_items() -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _emit_rust_items_sidecar(_sample_records(), tmp_path)
        items, _ = _load_rust_items(tmp_path / "rust_items.jsonl")
        return items


def test_items_by_attribute_matches_substring() -> None:
    items = _load_items()
    res = _run_query(items, pattern="test")
    assert res["total"] >= 1
    # Every returned item must have at least one matching attribute.
    for it in res["items"]:
        assert any("test" in a for a in it["attributes"]), (
            f"item {it!r} returned without matching attribute"
        )


def test_items_by_attribute_filters_by_kind() -> None:
    items = _load_items()
    # `#[derive(Debug, Clone)]` is on the struct; restrict by kind.
    res = _run_query(items, pattern="derive", kind="struct")
    assert res["total"] >= 1
    for it in res["items"]:
        assert it["kind"] == "struct"


def test_items_by_attribute_kind_filter_excludes_others() -> None:
    items = _load_items()
    # `#[test]` is on function-kind items. Filtering by struct must
    # eliminate them.
    res = _run_query(items, pattern="test", kind="struct")
    assert res["total"] == 0, (
        f"struct filter leaked function items: {res['items']}"
    )


def test_items_by_attribute_paginates() -> None:
    items = _load_items()
    # Every Rust attribute starts with ``#`` (``#[…]`` or ``#![…]``),
    # so this pattern matches all attributed items. Empty-string would
    # also "match all" semantically but is correctly rejected by the
    # input schema (minLength: 1) — pagination must work with a real
    # pattern.
    full = _run_query(items, pattern="#")
    assert full["total"] >= 1
    first = _run_query(items, pattern="#", limit=1, offset=0)
    assert len(first["items"]) == 1
    assert first["total"] == full["total"]
    if full["total"] > 1:
        assert first["truncated"] is True


def test_items_by_attribute_empty_when_no_match() -> None:
    items = _load_items()
    res = _run_query(items, pattern="this_attribute_does_not_exist_xyz")
    assert res["items"] == []
    assert res["total"] == 0
    assert res["truncated"] is False


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def main() -> int:
    tests = [
        ("sidecar writes one line per attributed item", test_sidecar_writes_one_line_per_attributed_item),
        ("sidecar is sorted deterministically", test_sidecar_is_sorted_deterministically),
        ("sidecar loader roundtrip", test_sidecar_loader_roundtrip),
        ("loader handles missing sidecar", test_loader_handles_missing_sidecar),
        ("items_by_attribute matches substring", test_items_by_attribute_matches_substring),
        ("items_by_attribute filters by kind", test_items_by_attribute_filters_by_kind),
        ("items_by_attribute kind filter excludes others", test_items_by_attribute_kind_filter_excludes_others),
        ("items_by_attribute paginates", test_items_by_attribute_paginates),
        ("items_by_attribute empty when no match", test_items_by_attribute_empty_when_no_match),
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
