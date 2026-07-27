"""Performance feature F8 — Rust-backed report read path.

The report stack (cbm_report → cbm_dossier, HTML/MD emitters) parsed
inventory.ttl into an in-memory rdflib graph before doing anything.
Measured on the 67,382,898-triple torvalds/linux bundle: rdflib needs
tens of minutes and ~87 GB just to load; a persistent pyoxigraph store
builds once in ~144 s at 2.5 GB, re-opens instantly, and answers the
report's analytics queries in 0.0–1.2 s each.

Pinned here:

- ``load_graph`` returns an oxigraph-backed ``GraphView`` whose
  rdflib-compatible surface (len / subject_objects / objects) yields
  rdflib terms — hashing, string ops, and int() coercion behave
  exactly as with a parsed rdflib graph;
- the store is cached on disk next to the existing NT cache and
  re-opened (not rebuilt) on the second load;
- ``graph_analytics`` output is deeply equivalent between the rdflib
  engine and the GraphView engine on a real emitted bundle;
- without pyoxigraph, ``load_graph`` falls back to the rdflib path.

Run from the repo root:  python -m pytest tests/test_perf_report_store.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import rdflib

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import cbm_report as CR  # noqa: E402

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "1111111111 +0000",
    "GIT_COMMITTER_DATE": "1111111111 +0000",
    "PATH": "/usr/bin:/bin",
}


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    """A real emitted bundle: host pipeline + L2 chunks (hash backend, no
    model downloads) so the analytics' chunk/inFile patterns have data."""
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries
    from plugins import chunks_embeddings

    root = tmp_path_factory.mktemp("report_store")
    repo = root / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("from .a import f\n")
    (repo / "pkg" / "a.py").write_text(
        "import os\n\ndef f():\n    return os.sep\n")
    (repo / "pkg" / "b.py").write_text(
        "from pkg.a import f\n\ndef g():\n    return f()\n")
    (repo / "tests_dir").mkdir()
    (repo / "tests_dir" / "test_a.py").write_text(
        "from pkg.a import f\n\ndef test_f():\n    assert f()\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    chunks_embeddings.register_all(chunks_embeddings.DeterministicHashBackend(32))
    mapped = map_codebase(repo, "HEAD")
    out = root / "bundle"
    emit("report-store-fixture", mapped, out, emit_blobs_flag=False)
    reset_registries()
    return out


def _found(bundle):
    class _A:  # discover()'s args shape
        abox = decomposition = buildplan = None
    return CR.discover(str(bundle), _A())


def test_load_graph_returns_oxigraph_view(bundle, tmp_path):
    g = CR.load_graph(_found(bundle), str(tmp_path))
    assert getattr(g, "engine", None) == "oxigraph"
    ref = rdflib.Graph()
    ref.parse(bundle / "inventory.ttl", format="turtle")
    assert len(g) == len(ref)


def test_view_yields_rdflib_terms(bundle, tmp_path):
    g = CR.load_graph(_found(bundle), str(tmp_path))
    pred = rdflib.URIRef(CR.CBM + "path")
    pairs = list(g.subject_objects(pred))
    assert pairs, "fixture bundle must contain cbm:path triples"
    s0, o0 = pairs[0]
    assert isinstance(s0, rdflib.URIRef)
    assert isinstance(o0, rdflib.Literal)
    # terms interop with rdflib-built sets/dicts (hashing + equality)
    ref = rdflib.Graph()
    ref.parse(bundle / "inventory.ttl", format="turtle")
    assert set(g.subject_objects(pred)) == set(ref.subject_objects(pred))
    # objects(s, p) narrows correctly
    assert list(g.objects(s0, pred)) == [o0]


def test_store_cache_is_reused_not_rebuilt(bundle, tmp_path):
    import pyoxigraph as ox

    CR.load_graph(_found(bundle), str(tmp_path))  # builds the store

    def bomb(*a, **kw):
        raise AssertionError("second load must reopen the cached store")

    orig = ox.Store.bulk_load
    ox.Store.bulk_load = bomb
    try:
        g = CR.load_graph(_found(bundle), str(tmp_path))
        assert len(g) > 0
    finally:
        ox.Store.bulk_load = orig


ANALYTICS_KEYS = ["triples", "ns", "classes", "top_preds", "n_src", "n_tst",
                  "edges", "chokepoints", "interchanges", "tests_edges",
                  "test_evidence", "external", "pins_n", "receipts",
                  "deg_hist", "_metro"]


_ANALYTICS_DRIVER = """
import json, os, sys
sys.path.insert(0, os.path.join({root!r}, "scripts"))
import rdflib
import cbm_report as CR

with open(os.path.join({bundle!r}, "run_manifest.json")) as fh:
    man = json.load(fh)
g = rdflib.Graph()
g.parse(os.path.join({bundle!r}, "inventory.ttl"), format="turtle")
got = CR.graph_analytics(g, man)
print(json.dumps({{k: got[k] for k in {keys!r}}}, sort_keys=True, default=str))
"""


def test_graph_analytics_rankings_survive_hash_randomization(bundle):
    """Ranking ties must not depend on PYTHONHASHSEED.

    Every ranking in ``graph_analytics`` sorted on a count or a degree alone.
    ``Counter.most_common()`` and degree-only ``sorted`` calls leave equal
    entries in the order they were encountered, and that order is set/dict
    iteration order over ``rdflib.URIRef`` keys — which is *str hash* order,
    randomized per process. Two files tied on degree therefore came out in a
    different order run to run, and the rdflib and oxigraph read paths
    disagreed on the same graph.

    ``test_graph_analytics_equivalent_between_engines`` below only catches
    that when the two engines happen to land on different orders in one
    process; it failed roughly one full-suite run in three, reading as
    flakiness rather than as the determinism defect it was. Re-running the
    identical analytics under fixed, differing hash seeds reproduces the
    trigger exactly and fails every time when the total order is missing.

    Byte-identical analytics across seeds is also what the report pipeline
    already promises: `_ranked` carried a comment claiming ties were broken
    "count desc, name asc" so both engines agree, but eight rankings bypassed
    it.
    """
    root = str(REPO_ROOT)
    driver = _ANALYTICS_DRIVER.format(
        root=root, bundle=str(bundle), keys=ANALYTICS_KEYS,
    )
    outputs = {}
    for seed in ("0", "1", "42", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        proc = subprocess.run(
            [sys.executable, "-c", driver],
            capture_output=True, text=True, env=env, cwd=root,
        )
        assert proc.returncode == 0, f"driver failed (seed={seed}): {proc.stderr[-2000:]}"
        outputs[seed] = proc.stdout.strip()

    baseline_seed, baseline = next(iter(outputs.items()))
    for seed, payload in outputs.items():
        if payload == baseline:
            continue
        differing = [
            key for key in ANALYTICS_KEYS
            if json.loads(payload)[key] != json.loads(baseline)[key]
        ]
        raise AssertionError(
            "graph_analytics is hash-seed dependent — ranking ties lack a "
            f"total order. Seed {seed} differs from seed {baseline_seed} on: "
            f"{differing}"
        )


def test_graph_analytics_equivalent_between_engines(bundle, tmp_path):
    with open(bundle / "run_manifest.json") as fh:
        man = json.load(fh)
    ref = rdflib.Graph()
    ref.parse(bundle / "inventory.ttl", format="turtle")
    got_ref = CR.graph_analytics(ref, man)
    view = CR.load_graph(_found(bundle), str(tmp_path))
    got_view = CR.graph_analytics(view, man)

    scalar_keys = ["triples", "ns", "classes", "top_preds", "n_src", "n_tst",
                   "edges", "chokepoints", "interchanges", "tests_edges",
                   "test_evidence", "external", "pins_n", "receipts",
                   "deg_hist", "_metro"]
    for k in scalar_keys:
        assert got_view[k] == got_ref[k], f"analytics diverge on {k!r}"
    dv, dr = got_view["_district"], got_ref["_district"]
    assert dv["files"] == dr["files"]
    assert dv["ftype"] == dr["ftype"]
    assert dv["endl"] == dr["endl"]
    assert dv["row"] == dr["row"]
    assert dv["inFile"] == dr["inFile"]


def test_load_graph_twice_in_one_process(bundle, tmp_path):
    """RocksDB allows one writer per store dir; a second load_graph in
    the same process (report + dossier, or a warm re-load) must reuse
    the open view instead of dying on the LOCK file."""
    g1 = CR.load_graph(_found(bundle), str(tmp_path))
    g2 = CR.load_graph(_found(bundle), str(tmp_path))
    assert len(g1) == len(g2) > 0


def test_load_graph_concurrent_opener_attaches_read_only(bundle, tmp_path):
    """A second *process* would hit the same lock; the loader must
    attach read-only instead of crashing. Simulated by holding a live
    write handle and forcing the loader's cache to forget it."""
    CR.load_graph(_found(bundle), str(tmp_path))  # holds the write lock
    CR._STORE_CACHE.clear()  # loader forgets, handle stays open
    g = CR.load_graph(_found(bundle), str(tmp_path))
    assert len(g) > 0


def test_load_graph_creates_missing_cache_dir(bundle, tmp_path):
    """Callers normally go through resolve_cache_dir, but load_graph must
    not crash when handed a cache path that does not exist yet."""
    g = CR.load_graph(_found(bundle), str(tmp_path / "nested" / "cache"))
    assert len(g) > 0


def test_load_graph_falls_back_to_rdflib(bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(CR, "_load_pyoxigraph", lambda: None)
    g = CR.load_graph(_found(bundle), str(tmp_path))
    assert isinstance(g, rdflib.Graph)
    with open(bundle / "run_manifest.json") as fh:
        man = json.load(fh)
    assert CR.graph_analytics(g, man)["triples"] == len(g)
