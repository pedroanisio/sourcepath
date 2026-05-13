"""Phase 5 endpoint tests for symbol-level xrefs.

Builds a deterministic fixture bundle (hash backend, in-tree fixture
repo) and exercises:
  - /api/chunk/{idx} → callers + callees lists
  - /api/file/{path} → xrefs_out + xrefs_in lists (deduped per peer)
  - Backward compat: a bundle with no xrefs.jsonl still serves both
    endpoints with empty lists.

The fixture mirrors Phase 2's: helper + main + recursive + class
method. Three edges expected.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "frontend" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


FIXTURE_SRC = '''\
def helper():
    return 1


def main():
    helper()
    helper()


def recursive(n):
    if n > 0:
        recursive(n - 1)


class User:
    def greet(self):
        helper()
'''


@pytest.fixture(scope="module")
def xref_bundle() -> Path:
    """Build a small bundle with xrefs.jsonl and return its path."""
    work = Path(tempfile.mkdtemp(prefix="backend_xrefs_"))
    try:
        fixture = work / "fixture"
        fixture.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)
        subprocess.run(
            ["git", "-C", str(fixture), "config", "user.email", "t@t"], check=True,
        )
        subprocess.run(
            ["git", "-C", str(fixture), "config", "user.name", "t"], check=True,
        )
        (fixture / "app.py").write_text(FIXTURE_SRC)
        subprocess.run(["git", "-C", str(fixture), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(fixture), "commit", "-q", "-m", "init"], check=True,
        )

        bundle = work / "bundle"
        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
        r = subprocess.run(
            [
                sys.executable, "scripts/run_xrefs.py",
                "--repo", str(fixture),
                "--out", str(bundle),
                "--backend", "hash",
                "--hash-dim", "64",
                "--no-emit-blobs",
            ],
            env=env, cwd=str(REPO_ROOT),
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            pytest.fail(
                f"run_xrefs.py failed: rc={r.returncode}\n"
                f"stderr={r.stderr[-1000:]}"
            )
        assert (bundle / "xrefs.jsonl").stat().st_size > 0, "fixture must produce edges"
        yield bundle
    finally:
        # Keep the bundle around if XREF_TEST_KEEP=1 for debugging
        if not os.environ.get("XREF_TEST_KEEP"):
            import shutil
            shutil.rmtree(work, ignore_errors=True)


@pytest.fixture(scope="module")
def xref_client(xref_bundle: Path):
    os.environ["CBM_OUTPUT_DIR"] = str(xref_bundle)
    import app as app_module  # type: ignore
    app_module.get_bundle.cache_clear()
    with TestClient(app_module.app) as c:
        yield c


def _chunks_by_symbol(client: TestClient) -> dict[str, dict]:
    """Map symbol (parent.method or fn_name) → chunk row."""
    r = client.get("/api/chunks?limit=200").json()
    return {c["symbol"]: c for c in r["chunks"] if c["symbol"]}


# ---------------------------------------------------------------- /api/chunk
def test_chunk_callees_helper_call(xref_client: TestClient):
    """main() calls helper() → main's `callees` includes helper."""
    by_sym = _chunks_by_symbol(xref_client)
    main_idx = by_sym["main"]["idx"]
    body = xref_client.get(f"/api/chunk/{main_idx}").json()
    callee_symbols = [c["symbol"] for c in body["callees"]]
    assert "helper" in callee_symbols, body
    helper_row = next(c for c in body["callees"] if c["symbol"] == "helper")
    assert helper_row["xref_kind"] == "calls"
    assert helper_row["resolution"] == "exact"
    assert helper_row["resolver"] == "python_intra_file"


def test_chunk_callers_helper_called_by(xref_client: TestClient):
    """helper() is called by main() and User.greet() → both appear as callers."""
    by_sym = _chunks_by_symbol(xref_client)
    helper_idx = by_sym["helper"]["idx"]
    body = xref_client.get(f"/api/chunk/{helper_idx}").json()
    caller_symbols = {c["symbol"] for c in body["callers"]}
    assert "main" in caller_symbols
    assert "greet" in caller_symbols
    # Edge provenance survives the round-trip
    for c in body["callers"]:
        assert c["xref_kind"] == "calls"
        assert c["resolution"] == "exact"
        assert c["resolver"] == "python_intra_file"


def test_chunk_recursive_self_edge(xref_client: TestClient):
    """recursive() calls itself → it appears as both caller and callee of itself."""
    by_sym = _chunks_by_symbol(xref_client)
    rec_idx = by_sym["recursive"]["idx"]
    body = xref_client.get(f"/api/chunk/{rec_idx}").json()
    callee_idxs = {c["idx"] for c in body["callees"]}
    caller_idxs = {c["idx"] for c in body["callers"]}
    assert rec_idx in callee_idxs, body["callees"]
    assert rec_idx in caller_idxs, body["callers"]


def test_chunk_endpoint_idempotent_with_no_xrefs(xref_client: TestClient):
    """A chunk with neither callers nor callees still returns the keys with [] values."""
    by_sym = _chunks_by_symbol(xref_client)
    # User.chained (parent_symbol == User) is a method that's never called and
    # calls nothing in the fixture; but the fixture didn't define it. Use a
    # chunk that's certainly leaf-ish: the User class chunk itself.
    user_idx = by_sym["User"]["idx"]
    body = xref_client.get(f"/api/chunk/{user_idx}").json()
    assert isinstance(body["callers"], list)
    assert isinstance(body["callees"], list)


# ---------------------------------------------------------------- /api/file
def test_file_xrefs_out_includes_helper(xref_client: TestClient):
    """app.py has chunks (main, greet) that call helper → xrefs_out lists helper."""
    body = xref_client.get("/api/file/app.py").json()
    out_symbols = [r["symbol"] for r in body["xrefs_out"]]
    assert "helper" in out_symbols
    # Recursion edge: recursive→recursive lives in the same file too
    assert "recursive" in out_symbols


def test_file_xrefs_out_is_deduped(xref_client: TestClient):
    """Two chunks in app.py call helper (main and User.greet); the file row
    is deduped per peer — exactly one helper entry."""
    body = xref_client.get("/api/file/app.py").json()
    helper_rows = [r for r in body["xrefs_out"] if r["symbol"] == "helper"]
    assert len(helper_rows) == 1, body["xrefs_out"]


def test_file_xrefs_in_lists_internal_callers(xref_client: TestClient):
    """xrefs_in for app.py: every src chunk in this fixture is also inside
    app.py, so every edge contributes a row (still deduped per src)."""
    body = xref_client.get("/api/file/app.py").json()
    in_symbols = {r["symbol"] for r in body["xrefs_in"]}
    # main + User.greet call helper; recursive calls itself.
    assert {"main", "greet", "recursive"}.issubset(in_symbols), body["xrefs_in"]


def test_file_xrefs_carry_provenance(xref_client: TestClient):
    """Every xref row has xref_kind, resolution, resolver fields."""
    body = xref_client.get("/api/file/app.py").json()
    for r in body["xrefs_out"] + body["xrefs_in"]:
        assert "xref_kind" in r and r["xref_kind"] == "calls"
        assert "resolution" in r and r["resolution"] in {"exact", "heuristic", "ambiguous"}
        assert "resolver" in r and r["resolver"]


# --------------------------------------------------------- /api/symbol-graph
def test_symbol_graph_shape(xref_client: TestClient):
    """Default endpoint returns nodes + edges + truncated flag."""
    r = xref_client.get("/api/symbol-graph")
    assert r.status_code == 200
    body = r.json()
    assert "nodes" in body and "edges" in body
    assert "truncated" in body and "total_nodes_available" in body
    # Every edge's endpoints are present as nodes.
    node_ids = {n["id"] for n in body["nodes"]}
    for e in body["edges"]:
        assert e["source"] in node_ids
        assert e["target"] in node_ids


def test_symbol_graph_node_carries_chunk_meta(xref_client: TestClient):
    """Each node has the fields the UI needs to render: idx, file, kind, lines."""
    body = xref_client.get("/api/symbol-graph").json()
    assert body["nodes"], "fixture must produce at least one xref node"
    for n in body["nodes"]:
        # id is the chunk idx stringified; meta carries the unwrapped idx.
        assert n["id"].isdigit()
        meta = n.get("meta") or {}
        assert meta.get("idx") == int(n["id"])
        assert "file" in meta and meta["file"]
        assert "kind" in meta
        assert n.get("weight") is not None and n["weight"] >= 0


def test_symbol_graph_degree_ranking(xref_client: TestClient):
    """When the limit is tight, the top-degree node is the most-connected one.

    In the fixture, `helper` is called by main + User.greet → degree 2 (in-degree).
    With limit=1 the single returned node should be helper.
    """
    body = xref_client.get("/api/symbol-graph?limit=1").json()
    assert len(body["nodes"]) == 1
    only = body["nodes"][0]
    # weight is degree; for helper it must be at least 2 (called from two chunks).
    assert only["label"] == "helper"
    assert only["weight"] >= 2
    assert body["truncated"] is True
    assert body["total_nodes_available"] > 1


def test_symbol_graph_kind_filter(xref_client: TestClient):
    """`kind=all` is a superset of `kind=calls` (and equal in this fixture)."""
    a = xref_client.get("/api/symbol-graph?kind=calls").json()
    b = xref_client.get("/api/symbol-graph?kind=all").json()
    a_edge_endpoints = {(e["source"], e["target"]) for e in a["edges"]}
    b_edge_endpoints = {(e["source"], e["target"]) for e in b["edges"]}
    assert a_edge_endpoints.issubset(b_edge_endpoints)


def test_symbol_graph_limit_clamping(xref_client: TestClient):
    """ge=1, le=5000 are enforced by FastAPI."""
    assert xref_client.get("/api/symbol-graph?limit=0").status_code == 422
    assert xref_client.get("/api/symbol-graph?limit=99999").status_code == 422


def test_symbol_graph_stability(xref_client: TestClient):
    """Two identical requests return identical responses (deterministic order)."""
    a = xref_client.get("/api/symbol-graph?limit=10").json()
    b = xref_client.get("/api/symbol-graph?limit=10").json()
    assert a == b


def test_symbol_graph_empty_bundle(tmp_path: Path):
    """A bundle with no xrefs.jsonl returns empty nodes + edges, not 500."""
    # Build a bundle, then remove the sidecar.
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)
    subprocess.run(
        ["git", "-C", str(fixture), "config", "user.email", "t@t"], check=True,
    )
    subprocess.run(
        ["git", "-C", str(fixture), "config", "user.name", "t"], check=True,
    )
    (fixture / "app.py").write_text(FIXTURE_SRC)
    subprocess.run(["git", "-C", str(fixture), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(fixture), "commit", "-q", "-m", "init"], check=True,
    )
    bundle = tmp_path / "bundle"
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    subprocess.run(
        [
            sys.executable, "scripts/run_xrefs.py",
            "--repo", str(fixture), "--out", str(bundle),
            "--backend", "hash", "--hash-dim", "64", "--no-emit-blobs",
        ],
        env=env, cwd=str(REPO_ROOT), check=True, capture_output=True,
    )
    (bundle / "xrefs.jsonl").unlink()

    os.environ["CBM_OUTPUT_DIR"] = str(bundle)
    import app as app_module  # type: ignore
    app_module.get_bundle.cache_clear()
    with TestClient(app_module.app) as c:
        body = c.get("/api/symbol-graph").json()
        assert body["nodes"] == []
        assert body["edges"] == []
        assert body["total_nodes_available"] == 0


# ---------------------------------------------------------------- backward compat
def test_backend_serves_bundle_without_xrefs_jsonl(tmp_path: Path):
    """A bundle missing xrefs.jsonl should still load and serve empty
    callers/callees rather than 500."""
    # Build a fresh bundle, then remove the sidecar to simulate an older
    # bundle produced without symbol_xrefs registered.
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)
    subprocess.run(
        ["git", "-C", str(fixture), "config", "user.email", "t@t"], check=True,
    )
    subprocess.run(
        ["git", "-C", str(fixture), "config", "user.name", "t"], check=True,
    )
    (fixture / "app.py").write_text(FIXTURE_SRC)
    subprocess.run(["git", "-C", str(fixture), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(fixture), "commit", "-q", "-m", "init"], check=True,
    )
    bundle = tmp_path / "bundle"
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    subprocess.run(
        [
            sys.executable, "scripts/run_xrefs.py",
            "--repo", str(fixture), "--out", str(bundle),
            "--backend", "hash", "--hash-dim", "64", "--no-emit-blobs",
        ],
        env=env, cwd=str(REPO_ROOT), check=True, capture_output=True,
    )
    (bundle / "xrefs.jsonl").unlink()

    os.environ["CBM_OUTPUT_DIR"] = str(bundle)
    import app as app_module  # type: ignore
    app_module.get_bundle.cache_clear()
    with TestClient(app_module.app) as c:
        r = c.get("/api/chunks?limit=1").json()
        assert r["chunks"], "fixture must produce at least one chunk"
        idx = r["chunks"][0]["idx"]
        body = c.get(f"/api/chunk/{idx}").json()
        assert body["callers"] == []
        assert body["callees"] == []
        file_body = c.get("/api/file/app.py").json()
        assert file_body["xrefs_out"] == []
        assert file_body["xrefs_in"] == []
