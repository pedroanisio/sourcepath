"""Performance feature F7 — Rust-backed inventory serialization.

Measured on the real artifacts (Xeon 8592+, 2026-07-09):

- fastapi graph, 255,343 triples: rdflib Turtle serialization 8.5 s;
  rdflib N-Triples 1.0 s; oxigraph (Rust) Turtle 0.25 s.
- Linux-kernel graph, 67,382,898 triples: oxigraph parsed it in 157 s
  and serialized Turtle in 38 s at 23.6 GB peak RSS — the rdflib path
  took the live run tens of minutes at >100 GB RSS for the same stage.

The feature: ``serialize_inventory()`` writes the graph as N-Triples
(rdflib's cheapest serializer) and converts to prefixed Turtle with
pyoxigraph. Contracts pinned here:

- round-trip isomorphism with the rdflib-native output;
- namespace prefixes survive (readability of inventory.ttl);
- byte-determinism across repeated serializations;
- graphs containing blank nodes fall back to rdflib (rdflib bnode
  labels are process-random, which would break the project's
  byte-determinism commitment through the NT intermediate);
- missing pyoxigraph falls back to rdflib;
- emit() records which engine produced the artifact in the manifest
  (provenance-first: the bundle says how it was made).

Run from the repo root:  python -m pytest tests/test_perf_fast_serializer.py
"""
from __future__ import annotations

import subprocess

import pytest
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.compare import isomorphic

from codebase_mapper.emission.infrastructure.rdf import fast_serializer as fs

CBM = Namespace("https://codebase-mapper.example.org/cbm#")


def _sample_graph() -> Graph:
    g = Graph()
    g.bind("cbm", CBM)
    f = URIRef(CBM + "file/a.py")
    g.add((f, CBM.path, Literal("a.py")))
    g.add((f, CBM.sizeBytes, Literal(42)))
    # literal shapes that stress NT escaping
    g.add((f, CBM.summary, Literal('multi\nline "quoted" \\backslash')))
    g.add((f, CBM.note, Literal("ünïcodé ⚡", lang="en")))
    g.add((f, CBM.imports, URIRef(CBM + "file/b%2Fc.py")))
    return g


def test_fast_turtle_roundtrip_is_isomorphic(tmp_path):
    g = _sample_graph()
    dest = tmp_path / "inv.ttl"
    engine = fs.serialize_inventory(g, dest)
    assert engine == "oxigraph"
    back = Graph()
    back.parse(dest, format="turtle")
    assert isomorphic(g, back)


def test_fast_turtle_keeps_prefixes(tmp_path):
    dest = tmp_path / "inv.ttl"
    fs.serialize_inventory(_sample_graph(), dest)
    text = dest.read_text()
    assert "@prefix cbm:" in text


def test_fast_turtle_is_deterministic(tmp_path):
    a, b = tmp_path / "a.ttl", tmp_path / "b.ttl"
    fs.serialize_inventory(_sample_graph(), a)
    fs.serialize_inventory(_sample_graph(), b)
    assert a.read_bytes() == b.read_bytes()


def test_fast_turtle_is_canonical_across_insertion_orders(tmp_path):
    """The CI determinism verifier compares bundle bytes across whole
    pipeline re-runs. The output must therefore be canonical — the same
    triple set gives the same bytes even if triples arrive in a
    different order (and regardless of pyoxigraph's parallel loading)."""
    g1 = _sample_graph()
    g2 = Graph()
    g2.bind("cbm", CBM)
    for t in reversed(list(g1)):
        g2.add(t)
    a, b = tmp_path / "a.ttl", tmp_path / "b.ttl"
    assert fs.serialize_inventory(g1, a) == "oxigraph"
    assert fs.serialize_inventory(g2, b) == "oxigraph"
    assert a.read_bytes() == b.read_bytes()


def test_no_tmp_nt_left_behind(tmp_path):
    dest = tmp_path / "inv.ttl"
    fs.serialize_inventory(_sample_graph(), dest)
    leftovers = [p for p in tmp_path.iterdir() if p != dest]
    assert leftovers == []


def test_bnode_graph_falls_back_to_rdflib(tmp_path):
    g = _sample_graph()
    g.add((URIRef(CBM + "file/a.py"), CBM.related, BNode()))
    dest = tmp_path / "inv.ttl"
    engine = fs.serialize_inventory(g, dest)
    assert engine == "rdflib"
    back = Graph()
    back.parse(dest, format="turtle")
    assert isomorphic(g, back)


def test_missing_pyoxigraph_falls_back_to_rdflib(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "_load_pyoxigraph", lambda: None)
    dest = tmp_path / "inv.ttl"
    engine = fs.serialize_inventory(_sample_graph(), dest)
    assert engine == "rdflib"
    back = Graph()
    back.parse(dest, format="turtle")
    assert isomorphic(_sample_graph(), back)


# ---------------------------------------------------------------------------
# emit() integration
# ---------------------------------------------------------------------------

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "1111111111 +0000",
    "GIT_COMMITTER_DATE": "1111111111 +0000",
    "PATH": "/usr/bin:/bin",
}


def test_emit_uses_fast_engine_and_discloses_it(tmp_path):
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries

    r = tmp_path / "repo"
    r.mkdir()
    (r / "a.py").write_text("import os\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    mapped = map_codebase(r, "HEAD")
    out = tmp_path / "bundle"
    manifest = emit("fixture", mapped, out, emit_blobs_flag=False)
    assert manifest["emit_engines"]["inventory.ttl"] == "oxigraph"
    # the artifact hash in the manifest must describe the fast-path bytes
    import hashlib
    sha = hashlib.sha256((out / "inventory.ttl").read_bytes()).hexdigest()
    assert manifest["artifacts"]["inventory.ttl"]["sha256"] == sha
    # and it still SHACL-conforms end to end
    assert manifest["shacl_self_check"]["conforms"] is True
