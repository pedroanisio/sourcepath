"""Unit-level tests covering branches the endpoint suite can't exercise on a
real bundle (URI helpers, missing-bundle error path, sbert semantic search).
"""
from __future__ import annotations

import os
import sys
from json import dumps
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app as app_module  # type: ignore
from serving.application import bundle_data, chunks as chunks_app, concepts as concepts_app, graphs as graphs_app, health as health_app


def _fake_bundle(tmp_path: Path) -> SimpleNamespace:
    blob_dir = tmp_path / "blobs"
    blob_dir.mkdir()
    sha = "a" * 64
    (blob_dir / sha).write_text("hello world")
    return SimpleNamespace(
        output_dir=tmp_path,
        embeddings_meta={"backend": {"name": "hash"}},
        chunk_vectors=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype="float32"),
        chunks=[
            {
                "idx": 0,
                "symbol": "schema_loader",
                "kind": "function",
                "file": "a.py",
                "beginLine": 1,
                "endLine": 3,
                "embeddingRow": 0,
                "contentSha256": sha,
            },
            {
                "idx": 1,
                "symbol": "helper",
                "kind": "function",
                "file": "b.py",
                "beginLine": 5,
                "endLine": 6,
                "embeddingRow": 1,
                "contentSha256": None,
            },
        ],
        chunk_concepts={0: ["schema"]},
        xrefs=[{"src_idx": 0, "dst_idx": 1, "kind": "calls", "resolution": "exact", "resolver": "unit"}],
        xrefs_by_src_idx={0: [0]},
        xrefs_by_dst_idx={1: [0]},
        concepts={
            "concepts": {
                "schema": {
                    "label": "schema",
                    "frequency": 2,
                    "file_count": 1,
                    "components": ["shape"],
                }
            },
            "cooccurrence": [["schema", "helper", 4], ["schema", "tiny", 1]],
            "per_path_concepts": {"a.py": ["schema"]},
        },
        cooccur={"schema": [("helper", 4), ("tiny", 1)]},
        concept_chunks={"schema": [0]},
        files=[
            {"path": "a.py", "language": "python", "type": "source_code", "size": 10},
            {"path": "b.py", "language": "python", "type": "source_code", "size": 20},
        ],
        imports=[("a.py", "b.py")],
    )


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

    # Patch _get_model so the test never imports sentence_transformers.
    #
    # The patch target must be the module the CALL SITE resolves the name in.
    # `chunks.search_chunks` calls the module-global `_get_model` defined in
    # serving/application/chunks.py; `app_module` only re-exports it, so
    # patching app_module here rebinds a name nobody reads — the real
    # `_get_model` then ran and raised `TypeError: _FakeModel() takes no
    # arguments` from the SentenceTransformer(name) construction below. The
    # test was skipped in CI (BL-024), so this rot went unobserved. Every other
    # test in this file already patches `chunks_app`; match them.
    monkeypatch.setattr(chunks_app, "_get_model", lambda *_a, **_kw: _FakeModel())
    # The branch also does `from sentence_transformers import SentenceTransformer`
    # inside _get_model — preempt the import so it can never reach the real one.
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


# ---------------------------------------- ollama semantic search path
def test_semantic_search_runs_when_backend_is_ollama(client, monkeypatch):
    """An ``ollama:<model>`` bundle takes the semantic branch when the
    query embedding succeeds."""
    bundle = app_module.get_bundle()
    bundle.embeddings_meta["backend"] = {"name": "ollama:nomic-embed-text"}
    if bundle.chunk_vectors is None or len(bundle.chunks) == 0:
        pytest.skip("bundle has no chunk vectors to project against")

    def _fake_embed(model: str, q: str):
        assert model == "nomic-embed-text"  # prefix stripped, model passed through
        return bundle.chunk_vectors[0].astype("float32")

    monkeypatch.setattr(chunks_app, "_embed_query_ollama", _fake_embed)

    r = client.post("/api/chunks/search", json={"q": "anything", "k": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "semantic"
    assert body["backend"] == "ollama:nomic-embed-text"
    assert len(body["chunks"]) > 0
    assert body["chunks"][0]["score"] is not None


def test_ollama_search_degrades_to_lexical_when_embedding_fails(client, monkeypatch):
    """Server down / model missing -> _embed_query_ollama returns None and
    the endpoint answers lexically instead of erroring."""
    bundle = app_module.get_bundle()
    bundle.embeddings_meta["backend"] = {"name": "ollama:nomic-embed-text"}

    monkeypatch.setattr(chunks_app, "_embed_query_ollama", lambda *_a: None)

    r = client.post("/api/chunks/search", json={"q": "anything", "k": 3})
    assert r.status_code == 200
    assert r.json()["mode"] == "lexical"


def test_sbert_search_uses_the_bundles_own_model(client, monkeypatch):
    """The query must be encoded with the model recorded in the bundle.

    The sbert branch used to substitute a hardcoded
    ``sentence-transformers/all-MiniLM-L6-v2`` whenever the recorded backend
    name lacked a ``/`` — while the ``is_sbert`` gate admits any name
    containing ``minilm``. A bundle built with a different MiniLM-family
    model therefore passed the gate and had its query encoded by the wrong
    encoder, scoring the query against a vector space it never belonged to.
    Cosine over two spaces still returns plausible floats, so nothing
    surfaced: the results were simply wrong.
    """
    bundle = app_module.get_bundle()
    bundle.embeddings_meta["backend"] = {"name": "paraphrase-MiniLM-L3-v2"}
    if bundle.chunk_vectors is None or len(bundle.chunks) == 0:
        pytest.skip("bundle has no chunk vectors")
    dim = bundle.chunk_vectors.shape[1]
    seen: list[str] = []

    class _FakeModel:
        def encode(self, _texts, **_kw):
            v = np.zeros((1, dim), dtype="float32")
            v[0] = bundle.chunk_vectors[0]
            return v

    def _record(name):
        seen.append(name)
        return _FakeModel()

    monkeypatch.setattr(chunks_app, "_get_model", _record)

    r = client.post("/api/chunks/search", json={"q": "anything", "k": 3})
    assert r.status_code == 200
    assert seen == ["paraphrase-MiniLM-L3-v2"], (
        f"query encoded with {seen!r}, not the bundle's own model"
    )


def test_sbert_search_degrades_to_lexical_when_model_will_not_load(client, monkeypatch):
    """An unloadable model name must degrade, not fall back to a guess."""
    bundle = app_module.get_bundle()
    bundle.embeddings_meta["backend"] = {"name": "sbert-not-a-real-model"}
    if bundle.chunk_vectors is None:
        pytest.skip("bundle has no chunk vectors")

    def _boom(_name):
        raise OSError("no such model")

    monkeypatch.setattr(chunks_app, "_get_model", _boom)

    r = client.post("/api/chunks/search", json={"q": "anything", "k": 3})
    assert r.status_code == 200
    assert r.json()["mode"] == "lexical"


def test_sbert_search_degrades_to_lexical_on_dimension_mismatch(client, monkeypatch):
    """Same guard the ollama branch has: a mismatched dim ranks noise."""
    bundle = app_module.get_bundle()
    bundle.embeddings_meta["backend"] = {"name": "sentence-transformers/other"}
    if bundle.chunk_vectors is None:
        pytest.skip("bundle has no chunk vectors")
    wrong_dim = bundle.chunk_vectors.shape[1] + 5

    class _WrongDimModel:
        def encode(self, _texts, **_kw):
            return np.ones((1, wrong_dim), dtype="float32")

    monkeypatch.setattr(chunks_app, "_get_model", lambda *_a, **_kw: _WrongDimModel())

    r = client.post("/api/chunks/search", json={"q": "anything", "k": 3})
    assert r.status_code == 200
    assert r.json()["mode"] == "lexical"


def test_ollama_search_degrades_to_lexical_on_dimension_mismatch(client, monkeypatch):
    """A query vector whose dimension differs from the bundle's vectors
    would rank noise — the endpoint must fall back to lexical."""
    bundle = app_module.get_bundle()
    bundle.embeddings_meta["backend"] = {"name": "ollama:nomic-embed-text"}
    if bundle.chunk_vectors is None:
        pytest.skip("bundle has no chunk vectors")
    wrong_dim = bundle.chunk_vectors.shape[1] + 5

    monkeypatch.setattr(
        chunks_app, "_embed_query_ollama",
        lambda *_a: np.ones(wrong_dim, dtype="float32"))

    r = client.post("/api/chunks/search", json={"q": "anything", "k": 3})
    assert r.status_code == 200
    assert r.json()["mode"] == "lexical"


def test_embed_query_ollama_normalizes_and_survives_failure(monkeypatch):
    """Direct unit test of the query embedder: normalization on success,
    None (never an exception) on transport failure."""
    import types

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"embeddings": [[3.0, 4.0]]}

    seen = {}
    fake_httpx = types.ModuleType("httpx")

    def _post(url, json=None, timeout=None):  # noqa: A002 - httpx kwarg name
        seen["url"] = url
        seen["body"] = json
        return _Resp()

    fake_httpx.post = _post  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama-test:11434")

    vec = chunks_app._embed_query_ollama("m", "query text")
    assert seen["url"] == "http://ollama-test:11434/api/embed"
    assert seen["body"] == {"model": "m", "input": ["query text"]}
    assert vec is not None
    assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-6  # 3-4-5 normalized

    def _post_boom(url, json=None, timeout=None):  # noqa: A002
        raise RuntimeError("connection refused")

    fake_httpx.post = _post_boom  # type: ignore[attr-defined]
    assert chunks_app._embed_query_ollama("m", "query text") is None


def test_bundle_data_sidecar_helpers_parse_and_filter(tmp_path: Path):
    rust = tmp_path / "rust_items.jsonl"
    rust.write_text("\nnot-json\n{}\n" + dumps({"path": "src/lib.rs", "name": "demo"}) + "\n")
    items, by_file = bundle_data._load_rust_items(rust)
    assert items == [{"path": "src/lib.rs", "name": "demo"}]
    assert by_file == {"src/lib.rs": [0]}

    enrich = tmp_path / "enrichments.jsonl"
    enrich.write_text(
        "\n".join(
            [
                "",
                "not-json",
                dumps({"kind": "file_summary", "target": "a.py", "text": "A"}),
                dumps({"kind": "concept_description", "target": "schema", "text": "B"}),
                dumps({"kind": "schema_purpose", "target": "schema.sql", "text": "C"}),
                dumps({"kind": "unknown", "target": "ignored", "text": "D"}),
                dumps({"kind": "file_summary"}),
            ]
        )
        + "\n"
    )
    file_summary, concept_description, schema_purpose = bundle_data._load_enrichments(enrich)
    assert file_summary["a.py"]["text"] == "A"
    assert concept_description["schema"]["text"] == "B"
    assert schema_purpose["schema.sql"]["text"] == "C"


def test_bundle_data_xrefs_and_walk_helpers_cover_edge_cases(tmp_path: Path):
    xrefs_path = tmp_path / "xrefs.jsonl"
    xrefs_path.write_text(
        "\n".join(
            [
                "",
                dumps(
                    {
                        "src_chunk_id": "src-1",
                        "dst_chunk_id": "dst-1",
                        "kind": "calls",
                        "resolution": "exact",
                        "resolver": "unit",
                    }
                ),
                dumps(
                    {
                        "src_chunk_id": "missing-src",
                        "dst_chunk_id": "dst-1",
                        "kind": "calls",
                        "resolution": "exact",
                        "resolver": "unit",
                    }
                ),
            ]
        )
        + "\n"
    )
    chunks_by_uri = {
        bundle_data._chunk_id_to_uri("src-1"): 1,
        bundle_data._chunk_id_to_uri("dst-1"): 2,
    }
    xrefs, by_src, by_dst = bundle_data._load_xrefs(xrefs_path, chunks_by_uri)
    assert len(xrefs) == 1
    assert by_src == {1: [0]}
    assert by_dst == {2: [0]}

    walked, truncated = bundle_data.walk_paths(
        "a.py",
        {"a.py": ["b.py", "c.py"], "b.py": ["d.py"], "c.py": ["e.py"]},
        depth=2,
        limit=2,
    )
    assert walked == ["b.py", "c.py"]
    assert truncated is True

    walked_xrefs, truncated_xrefs = bundle_data.walk_xref_chunks(
        [1],
        {1: [0, 1], 2: [2]},
        [
            {"dst_idx": 2, "src_idx": 1},
            {"dst_idx": 3, "src_idx": 1},
            {"dst_idx": 4, "src_idx": 2},
        ],
        "dst_idx",
        depth=2,
        limit=2,
    )
    assert walked_xrefs == [2, 3]
    assert truncated_xrefs is True


def test_application_chunk_services_cover_direct_paths(tmp_path: Path, monkeypatch):
    bundle = _fake_bundle(tmp_path)
    monkeypatch.setattr(chunks_app, "get_bundle", lambda bundle_name=None: bundle)

    lexical = chunks_app.list_chunks_response(q="schema", limit=5, offset=0)
    assert lexical["mode"] == "lexical"
    assert lexical["chunks"]

    searched = chunks_app.search_chunks_response("schema", 5)
    assert searched["chunks"] or searched["total"] == 0

    with pytest.raises(HTTPException) as invalid:
        chunks_app.get_chunk_blob_response("zzz")
    assert invalid.value.status_code == 400

    with pytest.raises(HTTPException) as missing:
        chunks_app.get_chunk_detail_response(10_000_000)
    assert missing.value.status_code == 404

    dim = bundle.chunk_vectors.shape[1]

    class _FakeModel:
        def encode(self, texts, normalize_embeddings: bool = True):  # noqa: ARG002
            v = np.zeros((1, dim), dtype="float32")
            v[0] = bundle.chunk_vectors[0]
            return v

    monkeypatch.setattr(chunks_app, "_get_model", lambda *_a, **_kw: _FakeModel())
    bundle.embeddings_meta["backend"] = {"name": "sentence-transformers/all-MiniLM-L6-v2"}
    semantic = chunks_app.search_chunks_response("anything", 3)
    assert semantic["mode"] == "semantic"
    assert semantic["chunks"]


def test_application_concept_graph_and_health_services(tmp_path: Path, monkeypatch):
    bundle = _fake_bundle(tmp_path)
    monkeypatch.setattr(concepts_app, "get_bundle", lambda bundle_name=None: bundle)
    monkeypatch.setattr(graphs_app, "get_bundle", lambda bundle_name=None: bundle)

    concept = concepts_app.get_concept_detail_response("schema", cooccur_k=5, chunk_k=5, file_k=5)
    assert concept["concept"]["label"] == "schema"
    assert concept["files"]

    with pytest.raises(HTTPException) as missing:
        concepts_app.get_concept_detail_response("__missing__")
    assert missing.value.status_code == 404

    concept_graph = graphs_app.build_concept_graph_response(limit=20, min_edge=2)
    assert concept_graph["nodes"] is not None
    assert concept_graph["edges"] == []
    symbol_graph = graphs_app.build_symbol_graph_response(limit=5, kind="all")
    assert "nodes" in symbol_graph and "edges" in symbol_graph
    assert health_app.health_response() == {"status": "ok"}
