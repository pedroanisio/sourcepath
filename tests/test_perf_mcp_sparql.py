"""Performance feature F8b — MCP sparql tool on the Rust store.

The sparql escape hatch parsed inventory.ttl with rdflib inside a 10 s
dispatch budget — on a kernel-scale bundle the parse alone needs tens
of minutes, so the tool could never answer. Pinned here:

- the loader uses a cached pyoxigraph store (instant re-open) and the
  tool's response contract is unchanged: columns/rows/row_count/
  truncated/query_form/ask_result, with all values stringified;
- SELECT and ASK work on the oxigraph engine; CONSTRUCT is rejected;
  the mutating-keyword pre-filter is intact;
- without pyoxigraph the tool falls back to the original rdflib path
  with identical results.

Run from the repo root:  python -m pytest tests/test_perf_mcp_sparql.py
"""
from __future__ import annotations

import pytest
from rdflib import Graph, Literal, Namespace, URIRef

from frontend.mcp_server import sparql
from frontend.mcp_server.validators import ToolError

CBM = Namespace("https://codebase-mapper.example.org/cbm#")


@pytest.fixture()
def bundle_dir(tmp_path, monkeypatch):
    g = Graph()
    g.bind("cbm", CBM)
    for name, size in (("a.py", 10), ("b.py", 20), ("c.py", 30)):
        f = URIRef(CBM + f"file/{name}")
        g.add((f, CBM.path, Literal(name)))
        g.add((f, CBM.sizeBytes, Literal(size)))
    g.serialize(tmp_path / "inventory.ttl", format="turtle")

    class _Bundle:
        output_dir = tmp_path

    monkeypatch.setattr(sparql.backend_bundle_data, "get_bundle",
                        lambda name=None: _Bundle())
    monkeypatch.setenv(sparql.ENABLE_ENV, "1")
    sparql.clear_graph_cache()
    yield tmp_path
    sparql.clear_graph_cache()


def test_select_runs_on_oxigraph_engine(bundle_dir):
    handle = sparql._load_graph(str(bundle_dir))
    assert getattr(handle, "engine", None) == "oxigraph"
    out = sparql.run_sparql(
        "SELECT ?p ?s WHERE { ?f <https://codebase-mapper.example.org/cbm#path> ?p ;"
        " <https://codebase-mapper.example.org/cbm#sizeBytes> ?s } ORDER BY ?p")
    assert out["query_form"] == "SELECT"
    assert out["columns"] == ["p", "s"]
    assert [r["p"] for r in out["rows"]] == ["a.py", "b.py", "c.py"]
    assert out["rows"][0]["s"] == "10"
    assert out["row_count"] == 3
    assert out["truncated"] is False


def test_ask_runs_on_oxigraph_engine(bundle_dir):
    out = sparql.run_sparql(
        "ASK { ?f <https://codebase-mapper.example.org/cbm#path> \"a.py\" }")
    assert out["query_form"] == "ASK"
    assert out["ask_result"] is True
    out = sparql.run_sparql(
        "ASK { ?f <https://codebase-mapper.example.org/cbm#path> \"nope.py\" }")
    assert out["ask_result"] is False


def test_construct_rejected(bundle_dir):
    with pytest.raises(ToolError):
        sparql.run_sparql("CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o }")


def test_mutating_keyword_still_rejected(bundle_dir):
    with pytest.raises(ToolError):
        sparql.run_sparql("DELETE WHERE { ?s ?p ?o }")


def test_rdflib_fallback_gives_identical_results(bundle_dir, monkeypatch):
    q = ("SELECT ?p WHERE { ?f "
         "<https://codebase-mapper.example.org/cbm#path> ?p } ORDER BY ?p")
    fast = sparql.run_sparql(q)
    monkeypatch.setattr(sparql, "_load_pyoxigraph", lambda: None)
    sparql.clear_graph_cache()
    slow = sparql.run_sparql(q)
    assert fast == slow
