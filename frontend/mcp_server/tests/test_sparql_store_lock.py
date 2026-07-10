"""Regression — one pyoxigraph store instance per store directory.

The Rust-backed SPARQL read path persists each bundle's graph in a RocksDB
store dir under ``$TMPDIR/cbm_sparql_store``. RocksDB holds an exclusive
in-process lock on that dir for the lifetime of the ``Store`` *object* —
not of the cache entry. ``clear_sparql_graph_cache()`` (autouse per test,
and wired to manifest-change notifications in production) only drops the
``lru_cache`` reference; any other live reference (an exception traceback
captured by ``pytest.raises``, an unfinished result iterator, ...) keeps
the lock held. Re-running ``_load_graph`` for the same bundle then tried to
construct a *second* ``Store`` on the same dir and died with
``OSError: IO error: lock hold by current process``.

Root cause: a cache lifecycle inherited from the in-memory rdflib engine
(eviction == release) applied to a resource with an exclusive OS lock.
The fix is a process-level registry: one live ``Store`` per store dir,
reused across cache clears; a regenerated bundle maps to a *new* dir
(mtime+size key) and gets a fresh store.

Run from the repo root:
    python -m pytest frontend/mcp_server/tests/test_sparql_store_lock.py
"""
from __future__ import annotations

import os

import pytest

from frontend.mcp_server import sparql as sparql_mod

pytest.importorskip("pyoxigraph", reason="oxigraph engine not installed")


@pytest.fixture(autouse=True)
def _clean_cache():
    sparql_mod.clear_graph_cache()
    yield
    sparql_mod.clear_graph_cache()


def _write_bundle(tmp_path, triples: list[str]):
    (tmp_path / "inventory.ttl").write_text("\n".join(triples) + "\n")
    return tmp_path


def test_reload_after_cache_clear_reuses_live_store(tmp_path):
    """The original crash: cache cleared while the old handle is still
    referenced (here: a plain local; in the wild: a pytest.raises
    traceback). Reconstructing a Store on the locked dir raised OSError;
    the registry must hand back the same live instance instead."""
    bundle = _write_bundle(tmp_path, ['<urn:a> <urn:b> "c" .'])
    first = sparql_mod._load_graph(str(bundle))
    sparql_mod.clear_graph_cache()
    # `first` is still alive and holds the RocksDB lock on the store dir.
    second = sparql_mod._load_graph(str(bundle))
    assert second.store is first.store


def test_query_error_traceback_does_not_wedge_the_next_call(tmp_path):
    """Tool-level shape of the regression: a rejected DESCRIBE leaves a
    ToolError whose traceback references the store; after a cache clear the
    next query on the same bundle must still answer, not raise OSError."""
    bundle = _write_bundle(tmp_path, ['<urn:a> <urn:b> "c" .'])
    handle = sparql_mod._load_graph(str(bundle))
    with pytest.raises(sparql_mod.ToolError) as exc:
        sparql_mod._run_oxigraph(handle, "DESCRIBE <urn:a>")
    assert exc.value.code == sparql_mod.INVALID_ARGUMENT
    sparql_mod.clear_graph_cache()
    payload = sparql_mod._run_oxigraph(
        sparql_mod._load_graph(str(bundle)), "ASK WHERE { ?s ?p ?o }",
    )
    assert payload["ask_result"] is True


def test_regenerated_bundle_gets_a_fresh_store(tmp_path):
    """Reuse must not overcorrect into staleness: a regenerated inventory
    (new mtime+size) maps to a new store dir and is re-loaded, even while a
    handle to the previous generation is still alive."""
    bundle = _write_bundle(tmp_path, ['<urn:a> <urn:b> "c" .'])
    first = sparql_mod._load_graph(str(bundle))
    assert len(first.store) == 1

    _write_bundle(tmp_path, ['<urn:a> <urn:b> "c" .', '<urn:d> <urn:e> "f" .'])
    ttl = bundle / "inventory.ttl"
    st = ttl.stat()
    os.utime(ttl, (st.st_atime, st.st_mtime + 10))  # force a new mtime key
    sparql_mod.clear_graph_cache()

    second = sparql_mod._load_graph(str(bundle))
    assert second.store is not first.store
    assert len(second.store) == 2
