#!/usr/bin/env python3
"""verify_rust_xrefs.py — Stage 2 contract for the Rust xref resolver.

Exercises intra- and inter-file ``calls`` edge emission against a
two-file fixture (``tests/fixtures/rust/xref_crate/``). Asserts:

  1. The resolver is registered under ``_RESOLVERS["rust"]``.
  2. Intra-file calls (``helper_sum`` from ``main_entry``) produce an
     edge with ``resolver = "rust_intra_file"``.
  3. Inter-file calls via ``use crate::helpers::{add, multiply as mul}``
     produce edges with ``resolver = "rust_inter_file"`` pointing at the
     correct ``helpers.rs`` chunks.
  4. Aliased imports (``multiply as mul``) bind by the alias at the call
     site but resolve to the original symbol in the target file.
  5. Multiple call sites in different src chunks each get their own
     edge (no dedup at the resolver level — that's the aggregator's job).
  6. Bare names that aren't imports (locals, builtins) don't produce
     unresolved entries.

The test mocks ``PipelineCtx`` directly rather than running the full
pipeline. The contract under test is the resolver + its dependence on
``l2_10_chunks`` / ``host:rust_crates`` / ``paths_set`` / ``read_path``
— none of which require the rest of the host to be live.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import sys

from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "rust" / "xref_crate"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _bundle_relative(path: Path) -> str:
    """Bundle-relative POSIX path. Tests mount the fixture as if its
    Cargo.toml were at the repo root, so we strip the leading ``tests/
    fixtures/rust/xref_crate/`` prefix."""
    return path.relative_to(FIXTURE_DIR).as_posix()


def _chunk_id(path: str, c: dict) -> str:
    """Mirror plugins.chunks_embeddings.embedder._chunk_id (the canonical
    formula used by the live pipeline). Kept inline so this test doesn't
    require the L2 plugin to be importable."""
    sym = c["symbol"]
    if c.get("parent_symbol"):
        sym = f"{c['parent_symbol']}.{sym}"
    return f"{path}#{c['kind']}:{sym}:L{c['line_start']}-L{c['line_end']}"


def _build_chunks() -> tuple[list[dict], dict[str, bytes]]:
    """Re-run the live Rust chunker on the fixture files and attach
    canonical chunk_ids. Returns (chunks, path → bytes)."""
    from plugins.chunks_embeddings.chunker import _chunk_rust

    chunks: list[dict] = []
    blobs: dict[str, bytes] = {}
    for rs_path in sorted(FIXTURE_DIR.rglob("*.rs")):
        rel = _bundle_relative(rs_path)
        content = rs_path.read_bytes()
        blobs[rel] = content
        file_chunks = _chunk_rust(content, rel)
        for c in file_chunks:
            c2 = dict(c, path=rel, chunk_id=_chunk_id(rel, c))
            chunks.append(c2)
    return chunks, blobs


def _build_mock_ctx(chunks: list[dict], blobs: dict[str, bytes]) -> SimpleNamespace:
    return SimpleNamespace(
        indices={
            "l2_10_chunks": chunks,
            # Single-crate workspace; crate_dir is empty because we treat
            # the fixture root as the repo root.
            "host:rust_crates": [{"name": "xref_fixture", "crate_dir": ""}],
        },
        paths_set=set(blobs.keys()),
        read_path=lambda p: blobs[p],
        scratch={},
    )


def _make_record(path: str) -> SimpleNamespace:
    return SimpleNamespace(path=path, language="rust", ast_summary=None)


def _resolve(path: str):
    from plugins.symbol_xrefs.rust_resolver import resolve_rust_calls

    chunks, blobs = _build_chunks()
    ctx = _build_mock_ctx(chunks, blobs)
    record = _make_record(path)
    return resolve_rust_calls(record, ctx), chunks


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_resolver_is_registered() -> None:
    from plugins.symbol_xrefs import _RESOLVERS
    from plugins.symbol_xrefs.rust_resolver import resolve_rust_calls

    assert "rust" in _RESOLVERS, f"rust resolver not in _RESOLVERS keys={list(_RESOLVERS)}"
    assert _RESOLVERS["rust"] is resolve_rust_calls


def test_intra_file_call_emits_edge() -> None:
    (edges, unresolved), chunks = _resolve("src/lib.rs")
    intra = [e for e in edges if e.resolver == "rust_intra_file"]
    assert intra, f"no intra-file edges; all edges: {edges}"
    by_chunk = {c["chunk_id"]: c for c in chunks}
    edge = intra[0]
    assert edge.kind == "calls"
    assert edge.resolution == "exact"
    src = by_chunk[edge.src_chunk_id]
    dst = by_chunk[edge.dst_chunk_id]
    assert src["symbol"] == "main_entry", f"unexpected src: {src['symbol']}"
    assert dst["symbol"] == "helper_sum", f"unexpected dst: {dst['symbol']}"
    assert src["path"] == "src/lib.rs"
    assert dst["path"] == "src/lib.rs"


def test_inter_file_call_emits_edge() -> None:
    (edges, unresolved), chunks = _resolve("src/lib.rs")
    inter = [e for e in edges if e.resolver == "rust_inter_file"]
    assert inter, f"no inter-file edges; all edges: {edges}"
    by_chunk = {c["chunk_id"]: c for c in chunks}
    # Every inter-file edge must target a chunk in helpers.rs.
    for e in inter:
        dst = by_chunk[e.dst_chunk_id]
        assert dst["path"] == "src/helpers.rs", (
            f"inter-file edge targets {dst['path']}, expected src/helpers.rs"
        )
        assert dst["kind"] == "function"
    # The two direct targets we expect: add (twice — once from main_entry,
    # once from run_pipeline) and multiply (once, via the `mul` alias).
    dst_names = sorted(by_chunk[e.dst_chunk_id]["symbol"] for e in inter)
    assert dst_names == ["add", "add", "multiply"], (
        f"unexpected inter-file targets: {dst_names}"
    )


def test_aliased_use_resolves_to_original_symbol() -> None:
    (edges, _), chunks = _resolve("src/lib.rs")
    by_chunk = {c["chunk_id"]: c for c in chunks}
    # Find the multiply edge.
    mul_edges = [
        e for e in edges
        if e.resolver == "rust_inter_file"
        and by_chunk[e.dst_chunk_id]["symbol"] == "multiply"
    ]
    assert len(mul_edges) == 1, (
        f"expected exactly one multiply edge via the `mul` alias; got {len(mul_edges)}"
    )
    # The src chunk should be main_entry — the only caller of `mul`.
    src = by_chunk[mul_edges[0].src_chunk_id]
    assert src["symbol"] == "main_entry"


def test_multiple_call_sites_produce_separate_edges() -> None:
    (edges, _), chunks = _resolve("src/lib.rs")
    by_chunk = {c["chunk_id"]: c for c in chunks}
    # `add` is called from main_entry AND run_pipeline. Each is in a
    # different src chunk, so the resolver must emit two edges with
    # different src_chunk_id but the same dst_chunk_id.
    add_edges = [
        e for e in edges
        if e.resolver == "rust_inter_file"
        and by_chunk[e.dst_chunk_id]["symbol"] == "add"
    ]
    assert len(add_edges) == 2, (
        f"expected 2 inter-file edges to add; got {len(add_edges)}"
    )
    src_ids = {e.src_chunk_id for e in add_edges}
    assert len(src_ids) == 2, (
        f"both add edges came from the same src chunk; src_ids={src_ids}"
    )
    src_names = {by_chunk[sid]["symbol"] for sid in src_ids}
    assert src_names == {"main_entry", "run_pipeline"}, (
        f"unexpected callers of add: {src_names}"
    )


def test_no_unresolved_for_local_names() -> None:
    """Bare names that aren't imports (let bindings, builtins) MUST NOT
    appear in `unresolved`. Surfacing them would bury the signal."""
    (edges, unresolved), _ = _resolve("src/lib.rs")
    # The fixture has no failed imports — every `use` lands in helpers.rs.
    assert unresolved == [], f"unexpected unresolved entries: {unresolved}"


def test_helpers_file_has_no_outbound_edges() -> None:
    """helpers.rs makes no function calls — only contains definitions.
    A resolver that hallucinates an edge here would be a regression."""
    (edges, unresolved), _ = _resolve("src/helpers.rs")
    assert edges == [], f"unexpected edges from helpers.rs: {edges}"
    assert unresolved == [], f"unexpected unresolved from helpers.rs: {unresolved}"


def main() -> int:
    tests = [
        ("resolver is registered", test_resolver_is_registered),
        ("intra-file call emits edge", test_intra_file_call_emits_edge),
        ("inter-file call emits edge", test_inter_file_call_emits_edge),
        ("aliased use resolves to original", test_aliased_use_resolves_to_original_symbol),
        ("multiple call sites separate edges", test_multiple_call_sites_produce_separate_edges),
        ("no unresolved for local names", test_no_unresolved_for_local_names),
        ("helpers file has no outbound edges", test_helpers_file_has_no_outbound_edges),
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
