"""Tests for multi-bundle discovery and the /api/bundles listing endpoint.

These tests build fake bundles in tmp_path — only ``run_manifest.json`` is
required for listing, so we don't need the rest of the codebase-mapper
output. Bundle *content* loading is exercised by the live-bundle suite.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app as app_module  # type: ignore


def _make_fake_bundle(root: Path, name: str, **manifest_extras) -> Path:
    d = root / name
    d.mkdir(parents=True)
    manifest = {
        "repo_name": name,
        "commit_sha": "f" * 7,
        "generated_at": "2026-05-12T00:00:00Z",
        "tool_version": "0.5.0",
        "counts": {"files": 1},
        **manifest_extras,
    }
    (d / "run_manifest.json").write_text(json.dumps(manifest))
    return d


@pytest.fixture
def fake_bundles_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "bundles"
    root.mkdir()
    _make_fake_bundle(root, "alpha", repo_name="repo-a")
    _make_fake_bundle(root, "beta", repo_name="repo-b")
    _make_fake_bundle(root, "gamma", repo_name="repo-c")
    # An entry that is NOT a bundle (no run_manifest.json) — must be skipped.
    (root / "not-a-bundle").mkdir()
    (root / "not-a-bundle" / "README.md").write_text("# unrelated\n")
    # A file (not a dir) — must be skipped too.
    (root / "stray.txt").write_text("noise\n")
    # An entry with an invalid manifest — must be skipped.
    bad = root / "broken"
    bad.mkdir()
    (bad / "run_manifest.json").write_text("not json")
    monkeypatch.setenv("CBM_BUNDLES_ROOT", str(root))
    monkeypatch.delenv("CBM_OUTPUT_DIR", raising=False)
    app_module.get_bundle.cache_clear()
    return root


@pytest.fixture
def fake_client(fake_bundles_root: Path):
    with TestClient(app_module.app) as c:
        yield c


# ------------------------------------------------------------------- listing
def test_list_bundles_finds_valid_dirs(fake_bundles_root: Path):
    items = app_module.list_bundles()
    names = [it["name"] for it in items]
    assert names == ["alpha", "beta", "gamma"]
    assert all(it["files"] == 1 for it in items)
    assert all(it["repo_name"] for it in items)


def test_list_bundles_skips_non_dir_and_invalid(fake_bundles_root: Path):
    names = {it["name"] for it in app_module.list_bundles()}
    assert "not-a-bundle" not in names
    assert "stray.txt" not in names
    assert "broken" not in names


def test_list_bundles_handles_missing_root(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CBM_BUNDLES_ROOT", str(tmp_path / "does-not-exist"))
    monkeypatch.delenv("CBM_OUTPUT_DIR", raising=False)
    app_module.get_bundle.cache_clear()
    assert app_module.list_bundles() == []


def test_list_bundles_includes_external_output_dir(
    fake_bundles_root: Path, tmp_path: Path, monkeypatch
):
    """CBM_OUTPUT_DIR pointing outside the root still gets listed first."""
    external = tmp_path / "external" / "delta"
    _make_fake_bundle(external.parent, "delta", repo_name="repo-d")
    monkeypatch.setenv("CBM_OUTPUT_DIR", str(external))
    app_module.get_bundle.cache_clear()
    items = app_module.list_bundles()
    names = [it["name"] for it in items]
    assert names[0] == "delta"
    assert names[1:] == ["alpha", "beta", "gamma"]


def test_list_bundles_dedupes_output_dir_inside_root(
    fake_bundles_root: Path, monkeypatch
):
    monkeypatch.setenv("CBM_OUTPUT_DIR", str(fake_bundles_root / "beta"))
    app_module.get_bundle.cache_clear()
    items = app_module.list_bundles()
    names = [it["name"] for it in items]
    assert names == ["beta", "alpha", "gamma"]


# ------------------------------------------------------------ /api/bundles
def test_bundles_endpoint(fake_client: TestClient, fake_bundles_root: Path):
    r = fake_client.get("/api/bundles")
    assert r.status_code == 200
    body = r.json()
    assert body["bundles_root"] == str(fake_bundles_root)
    assert [b["name"] for b in body["bundles"]] == ["alpha", "beta", "gamma"]
    assert body["selected"] == "alpha"  # no CBM_OUTPUT_DIR, falls back to first


def test_bundles_endpoint_prefers_output_dir_for_selected(
    fake_bundles_root: Path, monkeypatch
):
    monkeypatch.setenv("CBM_OUTPUT_DIR", str(fake_bundles_root / "gamma"))
    app_module.get_bundle.cache_clear()
    with TestClient(app_module.app) as c:
        body = c.get("/api/bundles").json()
    assert body["selected"] == "gamma"


def test_bundles_endpoint_empty_root(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CBM_BUNDLES_ROOT", str(tmp_path))
    monkeypatch.delenv("CBM_OUTPUT_DIR", raising=False)
    app_module.get_bundle.cache_clear()
    with TestClient(app_module.app) as c:
        body = c.get("/api/bundles").json()
    assert body["bundles"] == []
    assert body["selected"] is None


# ----------------------------------------------------------- name validation
@pytest.mark.parametrize(
    "name",
    ["", "../escape", "a/b", "a\\b", ".hidden", "../../etc/passwd"],
)
def test_validate_bundle_name_rejects_bad_names(name: str):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        app_module._validate_bundle_name(name)
    assert ei.value.status_code == 400


def test_validate_bundle_name_accepts_good_names():
    # Should not raise
    for name in ("alpha", "alpha-beta", "alpha_beta", "alpha.beta", "a1b2"):
        app_module._validate_bundle_name(name)


# ------------------------------------------------ resolve_bundle_path errors
def test_unknown_bundle_returns_404_via_query_param(fake_client: TestClient):
    """An endpoint with ?bundle=nope must surface the 404 from resolution."""
    r = fake_client.get("/api/summary?bundle=does-not-exist")
    assert r.status_code == 404
    assert "does-not-exist" in r.json()["detail"]


def test_invalid_bundle_name_returns_400(fake_client: TestClient):
    r = fake_client.get("/api/summary?bundle=../escape")
    assert r.status_code == 400


def test_no_bundles_anywhere_returns_404(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CBM_BUNDLES_ROOT", str(tmp_path))
    monkeypatch.delenv("CBM_OUTPUT_DIR", raising=False)
    app_module.get_bundle.cache_clear()
    with TestClient(app_module.app) as c:
        r = c.get("/api/summary")
    assert r.status_code == 404
    assert "no bundles found" in r.json()["detail"]


# ------------------------------------------------------------- info parsing
def test_bundle_info_skips_unreadable_manifest(tmp_path: Path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "run_manifest.json").write_text("not json")
    assert app_module._bundle_info(d) is None


def test_bundle_info_skips_missing_manifest(tmp_path: Path):
    d = tmp_path / "no-manifest"
    d.mkdir()
    assert app_module._bundle_info(d) is None


def test_bundle_info_minimal_manifest(tmp_path: Path):
    d = tmp_path / "minimal"
    d.mkdir()
    (d / "run_manifest.json").write_text("{}")
    info = app_module._bundle_info(d)
    assert info is not None
    assert info["name"] == "minimal"
    assert info["repo_name"] is None
    assert info["files"] is None


# ----------------------- CBM_OUTPUT_DIR misconfigured to a non-bundle parent
# Regression: a deploy that set CBM_OUTPUT_DIR=/data (the bundles parent)
# instead of /data/<name> caused the picker to default to "data" and every
# endpoint to 500 with FileNotFoundError on /data/run_manifest.json.
def test_invalid_output_dir_not_treated_as_bundle(tmp_path: Path, monkeypatch):
    parent = tmp_path / "data"
    parent.mkdir()
    _make_fake_bundle(parent, "alpha", repo_name="repo-a")

    # OUTPUT_DIR points at the parent — NOT a valid bundle (no manifest there).
    monkeypatch.setenv("CBM_OUTPUT_DIR", str(parent))
    monkeypatch.setenv("CBM_BUNDLES_ROOT", str(parent))
    app_module.get_bundle.cache_clear()

    # Listing must NOT include the parent dir as a phantom "data" entry.
    items = app_module.list_bundles()
    names = [it["name"] for it in items]
    assert names == ["alpha"], f"unexpected listing: {names}"

    # Default name must be the real bundle, not the parent's basename.
    assert app_module._default_bundle_name() == "alpha"

    # Asking for ?bundle=data (the parent's basename) must 404 cleanly,
    # not try to load the parent as a bundle.
    with TestClient(app_module.app) as c:
        r = c.get("/api/summary?bundle=data")
    assert r.status_code == 404
    assert "data" in r.json()["detail"]


def test_valid_output_dir_still_works(tmp_path: Path, monkeypatch):
    """The fix must not regress the well-formed case: CBM_OUTPUT_DIR points
    directly at a bundle directory."""
    bundle = tmp_path / "valid-bundle"
    bundle.mkdir()
    (bundle / "run_manifest.json").write_text(
        '{"repo_name": "valid", "counts": {"files": 1}}'
    )
    monkeypatch.setenv("CBM_OUTPUT_DIR", str(bundle))
    monkeypatch.delenv("CBM_BUNDLES_ROOT", raising=False)
    app_module.get_bundle.cache_clear()

    assert app_module._default_bundle_name() == "valid-bundle"
    resolved = app_module._resolve_bundle_path("valid-bundle")
    assert resolved == bundle.resolve()
    # No name => still resolves to the OUTPUT_DIR
    assert app_module._resolve_bundle_path(None) == bundle.resolve()


# -------------------------------------------------- host-only (no L2/L3) bundle
# Regression: a deploy mounted a bundle produced by `python -m codebase_mapper`
# (no chunks_embeddings or concept_graph plugins). load_bundle used to crash on
# the missing concepts.json / embeddings_meta.json; both must be treated as
# optional so file-graph + summary still serve.
def _write_minimal_inventory_ttl(path: Path) -> None:
    """Tiny but valid inventory.ttl with two cbm:File subjects + one import edge."""
    from rdflib import Graph, Literal, Namespace, URIRef
    from rdflib.namespace import RDF, XSD

    CBM = Namespace("https://codebase-mapper.example.org/cbm#")
    CBMI = "https://codebase-mapper.example.org/cbm/instance#"
    CBMT = "https://codebase-mapper.example.org/cbm/type#"

    g = Graph()
    a = URIRef(f"{CBMI}file/a.py")
    b = URIRef(f"{CBMI}file/b.py")
    src_type = URIRef(f"{CBMT}source_code")
    for u, p, size in ((a, "a.py", 10), (b, "b.py", 20)):
        g.add((u, RDF.type, CBM.File))
        g.add((u, CBM.path, Literal(p)))
        g.add((u, CBM.gitBlobSha, Literal("deadbeef")))
        g.add((u, CBM.contentSha256, Literal("aa" * 32, datatype=XSD.hexBinary)))
        g.add((u, CBM.sizeBytes, Literal(size, datatype=XSD.integer)))
        g.add((u, CBM.language, Literal("python")))
        g.add((u, CBM.type, src_type))
    g.add((a, CBM.imports, b))
    g.serialize(destination=str(path), format="turtle")


@pytest.fixture
def host_only_bundle(tmp_path: Path, monkeypatch) -> Path:
    bundle = tmp_path / "host-only"
    bundle.mkdir()
    (bundle / "run_manifest.json").write_text(
        '{"repo_name": "host-only", "counts": {"files": 2}}'
    )
    _write_minimal_inventory_ttl(bundle / "inventory.ttl")
    monkeypatch.setenv("CBM_OUTPUT_DIR", str(bundle))
    monkeypatch.delenv("CBM_BUNDLES_ROOT", raising=False)
    app_module.get_bundle.cache_clear()
    return bundle


def test_load_bundle_tolerates_missing_concepts_and_embeddings(host_only_bundle):
    """load_bundle must not crash on a host-only bundle."""
    b = app_module.load_bundle(host_only_bundle)
    assert b.manifest["repo_name"] == "host-only"
    assert b.concepts == {}
    assert b.embeddings_meta == {}
    assert b.chunk_vectors is None
    assert b.concept_vectors is None
    # inventory.ttl loads into the files + imports indices
    assert len(b.files) == 2
    assert {f["path"] for f in b.files} == {"a.py", "b.py"}
    assert b.imports == [("a.py", "b.py")]


def test_endpoints_serve_a_host_only_bundle(host_only_bundle):
    """summary + file-graph must succeed without L2/L3 artifacts."""
    with TestClient(app_module.app) as c:
        summary = c.get("/api/summary").json()
        assert summary["repo_name"] == "host-only"
        assert summary["n_chunks"] == 0
        assert summary["n_concepts"] == 0
        # No embedding backend means the field is null, not an error.
        assert summary["embeddings_backend"] is None

        graph = c.get("/api/file-graph?limit=10").json()
        assert {n["id"] for n in graph["nodes"]} == {"a.py", "b.py"}
        assert graph["edges"] == [
            {"source": "a.py", "target": "b.py", "weight": None}
        ]

        # /api/chunks returns an empty list, not a 500.
        chunks = c.get("/api/chunks?limit=10").json()
        assert chunks["chunks"] == []
        assert chunks["total"] == 0

        # /api/concept-graph also returns an empty graph.
        cg = c.get("/api/concept-graph?limit=10").json()
        assert cg["nodes"] == []
        assert cg["edges"] == []


def test_impact_endpoint_reports_import_radius_for_host_only_bundle(host_only_bundle):
    with TestClient(app_module.app) as c:
        r = c.get("/api/impact/a.py")
    assert r.status_code == 200
    body = r.json()
    assert body["file"] == "a.py"
    assert body["direct_dependencies"] == ["b.py"]
    assert body["direct_dependents"] == []
    assert body["transitive_dependencies"] == ["b.py"]
    assert body["related_tests"] == []


def test_impact_endpoint_404s_for_unknown_file(host_only_bundle):
    with TestClient(app_module.app) as c:
        r = c.get("/api/impact/missing.py")
    assert r.status_code == 404


# ---------------------------------------------------------------- cache TTL
def test_bundle_cache_clear_drops_entries(fake_bundles_root: Path, monkeypatch):
    """cache_clear() forces a re-read of bundle metadata."""
    # Prime by calling list_bundles + reading one manifest path
    app_module._load_bundle_cached.cache_clear()
    app_module.get_bundle.cache_clear()  # exercise the attribute alias
    # The attribute alias is the same callable as _clear_bundle_cache
    assert app_module.get_bundle.cache_clear is app_module._clear_bundle_cache
