"""TDD spec — Tier 1: full per-part symbol map with signature evidence.

The decomposer currently discards most of the symbol inventory (interface list
capped at 40). This spec introduces:

  * ``SymbolRecord`` — one graph-derived record per symbol chunk (name, kind,
    file, lines, parent, signature fields when the bundle carries them,
    ``is_interface`` for cross-module xref targets). Confidence is CERTAIN:
    every record is proven by a chunk node in the RDF graph.
  * ``build_symbol_map(ev, mg)`` — part id → [SymbolRecord], module parts only,
    ``file`` chunks excluded (they are containers, not symbols).
  * ``decompose_evidence(ev)`` — same pipeline as ``decompose`` but injectable,
    returning a Decomposition whose ``symbol_map`` is populated and whose
    relationships include aggregated ``subclassOf`` / ``overrides`` edges.
  * ``to_symbols_yaml`` — deterministic sidecar serialization.

Run: python -m pytest tests/decomposer/test_symbol_map.py
"""
from __future__ import annotations

from pathlib import Path

import yaml

from decomposer.evidence import EvidenceGraph
from decomposer.model import Confidence
from decomposer.parts import build_module_graph


def _chunk(idx, file, symbol, kind, **extra):
    c = {
        "uri": f"chunk:{idx}", "idx": idx, "file": file, "symbol": symbol,
        "kind": kind, "beginLine": 1 + idx, "endLine": 2 + idx,
        "embeddingRow": idx, "contentSha256": "00" * 32,
    }
    c.update(extra)
    return c


def _ev() -> EvidenceGraph:
    files = [
        {"path": "pkg/a/x.py", "language": "python", "type": "source_code",
         "size": 100, "uri": "file:a"},
        {"path": "pkg/b/y.py", "language": "python", "type": "source_code",
         "size": 100, "uri": "file:b"},
    ]
    chunks = [
        _chunk(0, "pkg/a/x.py", "Repo", "class",
               signature="class Repo(Base)", bases=["Base"]),
        _chunk(1, "pkg/a/x.py", "get", "method", parentSymbol="Repo",
               signature="def get(self, key: str) -> str | None",
               returns="str | None",
               params=[{"name": "self", "type": None, "default": None},
                       {"name": "key", "type": "str", "default": None}]),
        _chunk(2, "pkg/b/y.py", "Sub", "class",
               signature="class Sub(Repo)", bases=["Repo"]),
        _chunk(3, "pkg/b/y.py", "use", "function", signature="def use()"),
        _chunk(4, "pkg/b/y.py", "<file>", "file"),
    ]
    xrefs = [
        {"src_idx": 3, "dst_idx": 1, "kind": "calls",
         "resolution": "exact", "resolver": "python"},
        {"src_idx": 2, "dst_idx": 0, "kind": "subclassOf",
         "resolution": "exact", "resolver": "python"},
        {"src_idx": 2, "dst_idx": 1, "kind": "overrides",
         "resolution": "heuristic", "resolver": "python"},
    ]
    return EvidenceGraph(
        bundle_dir=Path("/nonexistent"),
        manifest={"repo_name": "fixture", "counts": {"files": 2}},
        files=files,
        file_by_path={f["path"]: f for f in files},
        imports_out={"pkg/b/y.py": ["pkg/a/x.py"]},
        imports_in={"pkg/a/x.py": ["pkg/b/y.py"]},
        external_imports={},
        tests_for_subject={},
        subjects_for_test={},
        chunks=chunks,
        chunks_by_file={
            "pkg/a/x.py": [0, 1],
            "pkg/b/y.py": [2, 3, 4],
        },
        xrefs=xrefs,
        concepts={},
        per_path_concepts={},
        collections={},
        file_summaries={},
        schema_purposes={},
        phases={},
        manifest_sha256="f" * 64,
    )


# ---------------------------------------------------------------------------
# build_symbol_map
# ---------------------------------------------------------------------------
def test_symbol_map_covers_every_symbol_chunk_excluding_file_chunks():
    from decomposer.parts import build_symbol_map
    ev = _ev()
    smap = build_symbol_map(ev, build_module_graph(ev))
    a = {s.name for s in smap["module:pkg/a"]}
    b = {s.name for s in smap["module:pkg/b"]}
    assert a == {"Repo", "get"}
    assert b == {"Sub", "use"}          # "<file>" excluded


def test_symbol_records_carry_signature_evidence_and_certain_confidence():
    from decomposer.parts import build_symbol_map
    ev = _ev()
    smap = build_symbol_map(ev, build_module_graph(ev))
    by = {s.name: s for s in smap["module:pkg/a"]}
    get = by["get"]
    assert get.kind == "method"
    assert get.parent == "Repo"
    assert get.signature == "def get(self, key: str) -> str | None"
    assert get.returns == "str | None"
    assert get.params[1] == {"name": "key", "type": "str", "default": None}
    assert get.confidence is Confidence.CERTAIN
    assert by["Repo"].bases == ["Base"]


def test_cross_module_xref_targets_are_flagged_as_interface():
    from decomposer.parts import build_symbol_map
    ev = _ev()
    smap = build_symbol_map(ev, build_module_graph(ev))
    a = {s.name: s for s in smap["module:pkg/a"]}
    b = {s.name: s for s in smap["module:pkg/b"]}
    assert a["get"].is_interface is True      # called + overridden from pkg/b
    assert a["Repo"].is_interface is True     # subclassed from pkg/b
    assert b["use"].is_interface is False


# ---------------------------------------------------------------------------
# decompose_evidence: symbol_map + inheritance relationships
# ---------------------------------------------------------------------------
def test_decompose_evidence_populates_symbol_map_and_inheritance_edges():
    from decomposer.decompose import decompose_evidence
    d = decompose_evidence(_ev())
    assert set(d.symbol_map) >= {"module:pkg/a", "module:pkg/b"}

    rels = {(r.source, r.target, r.type): r for r in d.relationships}
    sub = rels[("module:pkg/b", "module:pkg/a", "subclassOf")]
    assert sub.strength == 1
    assert sub.confidence is Confidence.CERTAIN
    ovr = rels[("module:pkg/b", "module:pkg/a", "overrides")]
    # any heuristic contributing edge downgrades the aggregate
    assert ovr.confidence is Confidence.PROBABLE


def test_module_parts_carry_symbol_counts_in_metrics():
    from decomposer.decompose import decompose_evidence
    d = decompose_evidence(_ev())
    part = {p.id: p for p in d.parts}["module:pkg/a"]
    assert part.metrics["symbols"] == {"classes": 1, "methods": 1}


# ---------------------------------------------------------------------------
# sidecar serialization
# ---------------------------------------------------------------------------
def test_symbols_yaml_sidecar_is_deterministic_and_complete():
    from decomposer.decompose import decompose_evidence
    from decomposer.serialize import to_symbols_yaml
    d = decompose_evidence(_ev())
    text1, text2 = to_symbols_yaml(d), to_symbols_yaml(d)
    assert text1 == text2
    doc = yaml.safe_load(text1)
    assert doc["provenance"]["run_manifest_sha256"] == "f" * 64
    symbols = doc["symbols"]
    names_a = [s["name"] for s in symbols["module:pkg/a"]]
    assert names_a == sorted(names_a)  # deterministic ordering
    rec = next(s for s in symbols["module:pkg/a"] if s["name"] == "get")
    assert rec["signature"] == "def get(self, key: str) -> str | None"
    assert rec["confidence"] == "certain"
    # omission contract survives serialization
    plain = next(s for s in symbols["module:pkg/b"] if s["name"] == "use")
    for absent in ("returns", "bases", "params", "visibility", "decorators"):
        assert absent not in plain
