"""Unit-level tests covering branches the endpoint suite can't exercise on a
real bundle (URI helpers, missing-bundle error path, sbert semantic search).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app as app_module  # type: ignore


# ---------------------------------------- pure helpers
def test_concept_name_from_uri_hash_form():
    assert (
        app_module._concept_name_from_uri(
            "https://x.example/cbm/instance#concept/schema"
        )
        == "schema"
    )


def test_concept_name_from_uri_slash_form():
    assert (
        app_module._concept_name_from_uri("https://x.example/concept/auth")
        == "auth"
    )


def test_concept_name_from_uri_unknown_form_returns_none():
    assert app_module._concept_name_from_uri("https://x.example/random#thing") is None
    assert app_module._concept_name_from_uri("not-a-uri") is None


def test_resolve_file_type_uri_paths():
    assert (
        app_module._resolve_file_type_uri("https://x.example/cbm/type#source_code")
        == "source_code"
    )
    # no '#' falls back to '/'
    assert (
        app_module._resolve_file_type_uri("https://x.example/cbm/type/source_code")
        == "source_code"
    )


# ---------------------------------------- missing bundle
def test_load_bundle_missing_dir_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        app_module.load_bundle(tmp_path / "nope")


# ---------------------------------------- semantic search path
def test_semantic_search_runs_when_backend_is_sbert(client, monkeypatch):
    """Patch the cached bundle's metadata so the sbert branch is taken,
    then stub the model + numpy ops so we don't actually load torch."""
    bundle = app_module.get_bundle()
    # Force the chunk-search endpoint to take the semantic branch.
    bundle.embeddings_meta["backend"] = {"name": "sentence-transformers/all-MiniLM-L6-v2"}
    if bundle.chunk_vectors is None or len(bundle.chunks) == 0:
        pytest.skip("bundle has no chunk vectors to project against")

    dim = bundle.chunk_vectors.shape[1]

    class _FakeModel:
        def encode(self, texts, normalize_embeddings: bool = True):  # noqa: ARG002
            # return a vector close to chunk 0 so the top hit is deterministic
            v = np.zeros((1, dim), dtype="float32")
            v[0] = bundle.chunk_vectors[0]
            return v

    # patch _get_model so the test never imports sentence_transformers
    monkeypatch.setattr(app_module, "_get_model", lambda *_a, **_kw: _FakeModel())
    # the function also does `from sentence_transformers import SentenceTransformer`
    # at the top of the branch — preempt the import so it can't fail
    import types

    fake_st = types.ModuleType("sentence_transformers")
    fake_st.SentenceTransformer = _FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    r = client.post("/api/chunks/search", json={"q": "anything", "k": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "semantic"
    assert body["backend"].startswith("sentence-transformers")
    assert len(body["chunks"]) > 0
    assert body["chunks"][0]["score"] is not None


def test_get_model_lazy_import_is_cached(monkeypatch):
    """Cover the body of _get_model and confirm @lru_cache(maxsize=1) caches it."""
    calls = {"n": 0}

    class _FakeModel:
        pass

    def _fake_import(name: str):
        calls["n"] += 1
        return _FakeModel()

    import types

    fake_st = types.ModuleType("sentence_transformers")
    fake_st.SentenceTransformer = lambda name: _fake_import(name)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

    app_module._get_model.cache_clear()
    m1 = app_module._get_model("model-x")
    m2 = app_module._get_model("model-x")
    assert m1 is m2
    assert calls["n"] == 1
