"""Integration tests for every /api/* endpoint, plus cross-layer round-trip
invariants. Driven through fastapi.testclient against the real bundle.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------- /api/healthz
def test_healthz(client):
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------- /api/summary
def test_summary_shape(summary):
    assert summary["counts"]["files"] > 0
    assert summary["n_chunks"] > 0
    assert summary["n_concepts"] > 0
    assert summary["files_by_language"]
    assert summary["files_by_type"]
    assert summary["embeddings_dimension"] > 0
    assert isinstance(summary["shacl_conforms"], bool)
    assert summary["output_dir"]


def test_summary_counts_match_endpoints(client, summary):
    # /api/chunks reports total = n_chunks
    r = client.get("/api/chunks?limit=1")
    assert r.status_code == 200
    assert r.json()["total"] == summary["n_chunks"]


# ------------------------------------------------------------ /api/file-graph
def test_file_graph_default(client):
    r = client.get("/api/file-graph?limit=50")
    assert r.status_code == 200
    g = r.json()
    assert 0 < len(g["nodes"]) <= 50
    # every edge endpoint is a node in the graph
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["source"] in ids
        assert e["target"] in ids
    # weight is non-null and non-negative (import degree)
    assert all(n.get("weight") is not None and n["weight"] >= 0 for n in g["nodes"])
    assert g["truncated"] is (g["total_nodes_available"] > len(g["nodes"]))


def test_file_graph_limit_clamping(client):
    r = client.get("/api/file-graph?limit=5000")
    assert r.status_code == 200
    r = client.get("/api/file-graph?limit=0")
    assert r.status_code == 422  # FastAPI's ge=1 validation


# --------------------------------------------------------- /api/concept-graph
def test_concept_graph(client):
    r = client.get("/api/concept-graph?limit=30&min_edge=2")
    assert r.status_code == 200
    g = r.json()
    assert 0 < len(g["nodes"]) <= 30
    ids = {n["id"] for n in g["nodes"]}
    for e in g["edges"]:
        assert e["source"] in ids
        assert e["target"] in ids
        assert e["weight"] is None or e["weight"] >= 2


def test_concept_graph_min_edge_filter_tightens(client):
    a = client.get("/api/concept-graph?limit=50&min_edge=2").json()
    b = client.get("/api/concept-graph?limit=50&min_edge=20").json()
    # higher threshold => no more edges (and usually strictly fewer)
    assert len(b["edges"]) <= len(a["edges"])


# ---------------------------------------------------------------- /api/chunks
def test_chunks_list_includes_idx(client):
    r = client.get("/api/chunks?limit=10")
    assert r.status_code == 200
    rows = r.json()["chunks"]
    assert len(rows) == 10
    assert all(row["idx"] is not None for row in rows)
    # idx is unique within a page
    assert len({row["idx"] for row in rows}) == 10


def test_chunks_lexical_filter(client):
    r = client.get("/api/chunks?q=schema&limit=10")
    assert r.status_code == 200
    rows = r.json()["chunks"]
    assert len(rows) > 0
    for row in rows:
        sym = (row.get("symbol") or "").lower()
        path = (row.get("file") or "").lower()
        assert "schema" in sym or "schema" in path


def test_chunks_search_lexical_fallback(client, summary):
    backend = (summary.get("embeddings_backend") or "").lower()
    r = client.post("/api/chunks/search", json={"q": "schema", "k": 5})
    assert r.status_code == 200
    body = r.json()
    if "sentence-transformer" in backend or "minilm" in backend:
        assert body["mode"] == "semantic"
    else:
        assert body["mode"] == "lexical"
        # lexical mode requires the query to appear somewhere in each result
        for row in body["chunks"]:
            sym = (row.get("symbol") or "").lower()
            path = (row.get("file") or "").lower()
            assert "schema" in sym or "schema" in path
        assert all(row["idx"] is not None for row in body["chunks"])


# ------------------------------------------------------------ /api/file/{path}
def test_file_detail_round_trip(client):
    # pick a connected file from the file-graph response
    g = client.get("/api/file-graph?limit=5").json()
    path = g["nodes"][0]["id"]
    r = client.get(f"/api/file/{path}")
    assert r.status_code == 200
    body = r.json()
    assert body["file"]["path"] == path
    assert body["file"]["contentSha256"] is not None
    # imports lists are paths (not URIs)
    for p in body["imports_out"] + body["imports_in"]:
        assert "://" not in p
    # chunks are sorted by beginLine
    if len(body["chunks"]) > 1:
        lines = [c["beginLine"] or 0 for c in body["chunks"]]
        assert lines == sorted(lines)
    for c in body["chunks"]:
        assert c["idx"] is not None


def test_file_detail_404(client):
    r = client.get("/api/file/does/not/exist.foo")
    assert r.status_code == 404


# ----------------------------------------------------------- /api/chunk/{idx}
def test_chunk_detail_round_trip(client, summary):
    r = client.get("/api/chunk/0")
    assert r.status_code == 200
    body = r.json()
    assert body["chunk"]["idx"] == 0
    # if the chunk has a file, the reverse map should agree
    file_path = body["chunk"]["file"]
    if file_path:
        f = client.get(f"/api/file/{file_path}").json()
        idxs = [c["idx"] for c in f["chunks"]]
        assert 0 in idxs


def test_chunk_detail_blob_preview_for_file_level(client):
    """File-kind chunks have a materialized blob; function/class chunks may not."""
    r = client.get("/api/chunks?limit=200").json()
    file_chunks = [c for c in r["chunks"] if c["kind"] == "file"]
    if not file_chunks:
        pytest.skip("no file-level chunks in this bundle")
    body = client.get(f"/api/chunk/{file_chunks[0]['idx']}").json()
    assert body["blob_preview"] is not None
    assert isinstance(body["blob_preview"], str)


def test_chunk_detail_404(client, summary):
    n = summary["n_chunks"]
    r = client.get(f"/api/chunk/{n + 10_000}")
    assert r.status_code == 404
    r = client.get("/api/chunk/-1")
    assert r.status_code == 404


# -------------------------------------------------------- /api/concept/{name}
def test_concept_detail_shape(client):
    r = client.get("/api/concept/schema")
    assert r.status_code == 200
    body = r.json()
    assert body["concept"]["label"] == "schema"
    assert body["concept"]["frequency"] > 0
    # cooccurring sorted by weight desc
    weights = [c["weight"] for c in body["cooccurring"]]
    assert weights == sorted(weights, reverse=True)
    # totals reported even if list is paginated
    assert body["file_count_total"] >= len(body["files"])
    assert body["chunk_count_total"] >= len(body["chunks"])


def test_concept_chunk_round_trip(client):
    """concept.chunks[i].idx -> /api/chunk/idx -> concepts should include the concept."""
    body = client.get("/api/concept/schema").json()
    if not body["chunks"]:
        pytest.skip("concept 'schema' lexicalizes no chunks in this bundle")
    idx = body["chunks"][0]["idx"]
    chunk_body = client.get(f"/api/chunk/{idx}").json()
    assert "schema" in chunk_body["concepts"]


def test_concept_detail_404(client):
    r = client.get("/api/concept/__does_not_exist__")
    assert r.status_code == 404


# ---------------------------------------------------------- /api/chunk-blob
def test_chunk_blob_valid(client):
    # pick a file-level chunk whose blob is materialized
    rows = client.get("/api/chunks?limit=200").json()["chunks"]
    file_rows = [c for c in rows if c["kind"] == "file"]
    if not file_rows:
        pytest.skip("no file-level chunks")
    detail = client.get(f"/api/chunk/{file_rows[0]['idx']}").json()
    sha = detail["chunk"]["contentSha256"]
    r = client.get(f"/api/chunk-blob/{sha}")
    assert r.status_code == 200
    assert r.json()["sha256"] == sha
    assert isinstance(r.json()["text"], str)


def test_chunk_blob_invalid_sha(client):
    # not 64 hex chars
    r = client.get("/api/chunk-blob/zzz")
    assert r.status_code == 400
    # 64 chars but with a non-hex byte
    r = client.get("/api/chunk-blob/" + "g" * 64)
    assert r.status_code == 400


def test_chunk_blob_not_found(client):
    r = client.get("/api/chunk-blob/" + "a" * 64)
    assert r.status_code == 404


# ---------------------------------------------------------- ?bundle= wiring
def test_bundle_query_param_loads_named_bundle(client, bundle_dir, summary):
    """Explicit ?bundle=NAME must reach the same bundle as the default."""
    name = bundle_dir.name
    r = client.get(f"/api/summary?bundle={name}")
    assert r.status_code == 200
    assert r.json()["repo_name"] == summary["repo_name"]
    assert r.json()["counts"]["files"] == summary["counts"]["files"]


def test_bundle_query_param_threads_through_graph_endpoint(client, bundle_dir):
    name = bundle_dir.name
    a = client.get("/api/file-graph?limit=5").json()
    b = client.get(f"/api/file-graph?limit=5&bundle={name}").json()
    assert [n["id"] for n in a["nodes"]] == [n["id"] for n in b["nodes"]]


def test_bundle_query_param_unknown_bundle_404(client):
    r = client.get("/api/summary?bundle=__nope__")
    assert r.status_code == 404


def test_bundle_listing_includes_live_bundle(client, bundle_dir):
    r = client.get("/api/bundles")
    assert r.status_code == 200
    names = [b["name"] for b in r.json()["bundles"]]
    assert bundle_dir.name in names
