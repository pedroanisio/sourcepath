"""Parity + budget tests for the bundle-loading fast path.

The serving layer projects a fixed set of structures out of the inventory
graph (files, import/test edges, chunks, chunk→concept links). Two parsers can
produce that projection: the fast stdlib JSON-LD path (default) and the rdflib
Turtle fallback. These tests assert the two paths are *equivalent* on a real
bundle — the algorithm swap must not change what the server serves, only how
fast it loads (PALS's Law: the derived output is verified, not trusted).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from frontend.backend.serving.application import bundle_data as bd


def _multiset(pairs) -> Counter:
    return Counter(tuple(p) for p in pairs)


def _adjacency_equiv(a: dict, b: dict) -> bool:
    """Same keys, and per-key value lists equal as multisets (order-agnostic)."""
    if set(a) != set(b):
        return False
    return all(Counter(a[k]) == Counter(b.get(k, [])) for k in a)


@pytest.fixture(scope="module")
def jsonld_projection(bundle_dir: Path):
    jsonld = bundle_dir / "inventory.jsonld"
    if not jsonld.exists():
        pytest.skip(f"no inventory.jsonld at {bundle_dir}")
    return bd._project_from_jsonld(jsonld)


@pytest.fixture(scope="module")
def rdflib_projection(bundle_dir: Path):
    ttl = bundle_dir / "inventory.ttl"
    if not ttl.exists():
        pytest.skip(f"no inventory.ttl at {bundle_dir}")
    return bd._project_from_rdflib(ttl)


def test_projection_paths_are_equivalent(jsonld_projection, rdflib_projection):
    (
        j_files, j_imports, j_io, j_ii, j_tests, j_tfs, j_sft,
        j_chunks, j_cidx, j_cbf, j_cc, j_concept_chunks, j_ext,
    ) = jsonld_projection
    (
        r_files, r_imports, r_io, r_ii, r_tests, r_tfs, r_sft,
        r_chunks, r_cidx, r_cbf, r_cc, r_concept_chunks, r_ext,
    ) = rdflib_projection

    # Deterministically ordered structures must be byte-identical.
    assert j_files == r_files
    assert j_chunks == r_chunks
    assert j_cidx == r_cidx
    assert j_cbf == r_cbf

    # Edge collections are multiset-equal (parsers emit them in different order,
    # but every consumer sorts or counts before observing).
    assert _multiset(j_imports) == _multiset(r_imports)
    assert _multiset(j_tests) == _multiset(r_tests)
    assert _adjacency_equiv(j_io, r_io)
    assert _adjacency_equiv(j_ii, r_ii)
    assert _adjacency_equiv(j_tfs, r_tfs)
    assert _adjacency_equiv(j_sft, r_sft)
    assert _adjacency_equiv(j_concept_chunks, r_concept_chunks)
    # chunk_concepts is keyed by int chunk index.
    assert set(j_cc) == set(r_cc)
    assert all(Counter(j_cc[k]) == Counter(r_cc[k]) for k in j_cc)
    # external imports (file -> package specifiers) are path-keyed, sorted-unique.
    assert j_ext == r_ext


def test_jsonld_projection_tolerates_list_valued_literals(tmp_path: Path):
    """Regression: a chunk node carrying a *repeated* predicate (JSON-LD
    collapses repeats into a list) must not crash the projector.

    Reproduces the production failure on the ``octavia`` bundle, where 4 chunk
    nodes had a list-valued ``cbml2:embeddingRow`` (e.g. ``[3088, 3089]``). The
    old ``literal()`` passed the list straight to ``int()``, raising
    ``TypeError: int() argument ... not 'list'`` during ``get_bundle`` — which
    took down *every* bundle-reading tool (repository_summary, orient_bundle,
    list_files) with an opaque internal_error. PALS's Law: the projection must
    treat generator output as untrusted and degrade gracefully, never abort.
    """
    doc = {
        "@context": {"cbm": "https://cbm.example/", "cbml2": "https://cbm.example/l2/"},
        "@graph": [
            {
                "@id": "cbmi:chunk/dup",
                "@type": "cbml2:Chunk",
                "cbml2:symbol": "ColorSpace.isDefaultDecode",
                "cbml2:kind": "method",
                "cbml2:beginLine": 21,
                "cbml2:endLine": 21,
                "cbml2:embeddingRow": [3088, 3089],
                "cbml2:contentSha256": ["sha-a", "sha-b"],
            }
        ],
    }
    jsonld = tmp_path / "inventory.jsonld"
    jsonld.write_text(json.dumps(doc))

    projection = bd._project_from_jsonld(jsonld)  # must not raise
    chunks = projection[7]
    assert len(chunks) == 1
    # List-valued literals collapse to the first element (deterministic).
    assert chunks[0]["embeddingRow"] == 3088
    assert chunks[0]["contentSha256"] == "sha-a"


def test_jsonld_path_is_faster(bundle_dir: Path):
    """Sanity-check the motivating premise: the JSON-LD parser is faster than
    rdflib for the same projection. Generous threshold to avoid CI flakiness."""
    import time

    jsonld = bundle_dir / "inventory.jsonld"
    ttl = bundle_dir / "inventory.ttl"
    if not (jsonld.exists() and ttl.exists()):
        pytest.skip("need both artifacts to compare")

    t0 = time.perf_counter()
    bd._project_from_jsonld(jsonld)
    json_dt = time.perf_counter() - t0

    t0 = time.perf_counter()
    bd._project_from_rdflib(ttl)
    rdflib_dt = time.perf_counter() - t0

    assert json_dt <= rdflib_dt, f"jsonld {json_dt:.2f}s should beat rdflib {rdflib_dt:.2f}s"


def test_load_bundle_prefers_jsonld(bundle_dir: Path, monkeypatch):
    """``load_bundle`` must route through the JSON-LD projector when the
    artifact is present, never touching the rdflib fallback."""
    if not (bundle_dir / "inventory.jsonld").exists():
        pytest.skip("no inventory.jsonld")

    def _boom(*_a, **_k):
        raise AssertionError("rdflib fallback used despite JSON-LD present")

    monkeypatch.setattr(bd, "_project_from_rdflib", _boom)
    bundle = bd.load_bundle(bundle_dir)
    assert bundle.files  # smoke: real data came through the fast path


def test_cold_load_allowance_scales_with_size(tmp_path: Path, monkeypatch):
    """The allowance grows with the graph artifact size and is zero when the
    bundle can't be resolved."""
    # Unresolvable bundle → no allowance, never raises.
    assert bd.cold_load_allowance_seconds("__no_such_bundle__") == 0.0

    small = tmp_path / "small"
    big = tmp_path / "big"
    small.mkdir()
    big.mkdir()
    (small / "run_manifest.json").write_text("{}")
    (big / "run_manifest.json").write_text("{}")
    (small / "inventory.jsonld").write_bytes(b"x" * 1_000_000)      # 1 MB
    (big / "inventory.jsonld").write_bytes(b"x" * 10_000_000)       # 10 MB

    monkeypatch.setenv("CBM_BUNDLES_ROOT", str(tmp_path))
    monkeypatch.delenv("CBM_OUTPUT_DIR", raising=False)

    small_allow = bd.cold_load_allowance_seconds("small")
    big_allow = bd.cold_load_allowance_seconds("big")
    assert small_allow > 0
    assert big_allow > small_allow * 5  # ~10x size → ~10x allowance


def test_ensure_bundle_exists_validates_without_loading(bundle_dir: Path, monkeypatch):
    """``ensure_bundle_exists`` must confirm a real bundle and reject a missing
    one without ever parsing the graph."""
    from fastapi import HTTPException

    def _boom(*_a, **_k):
        raise AssertionError("graph parsed during existence check")

    monkeypatch.setattr(bd, "_load_graph_projection", _boom)
    assert bd.ensure_bundle_exists(bundle_dir.name) == bundle_dir.name
    with pytest.raises(HTTPException):
        bd.ensure_bundle_exists("__no_such_bundle__")
