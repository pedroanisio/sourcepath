"""Regression tests for review fix #2 (file-level order inside SCC groups) and
the no-silent-caps fixes on module evidence.

The decomposition YAML carries module-granularity relationships only, so
``cycle_resolutions`` is the single channel preserving file-level topology for
consumers. These tests pin its contract: a valid topological order when the
directory cycle dissolves at file granularity, an explicit empty order + note
when it does not, and full (uncapped) symbol inventories on module parts.
"""
from __future__ import annotations

from pathlib import Path

from decomposer.decompose import _cycle_resolutions
from decomposer.evidence import EvidenceGraph
from decomposer.metrics import cycles
from decomposer.parts import build_module_graph, build_module_parts


def _ev(file_specs, imports_out=None, chunks=None):
    files = [
        {"path": p, "type": t, "language": lang, "size": 1, "uri": f"urn:{p}"}
        for (p, t, lang) in file_specs
    ]
    imports_out = imports_out or {}
    imports_in: dict[str, list[str]] = {}
    for src, tgts in imports_out.items():
        for t in tgts:
            imports_in.setdefault(t, []).append(src)
    chunks = chunks or []
    chunks_by_file: dict[str, list[int]] = {}
    for i, c in enumerate(chunks):
        chunks_by_file.setdefault(c["file"], []).append(i)
    return EvidenceGraph(
        bundle_dir=Path("."), manifest={}, files=files,
        file_by_path={f["path"]: f for f in files},
        imports_out=imports_out, imports_in=imports_in,
        external_imports={}, tests_for_subject={}, subjects_for_test={},
        chunks=chunks, chunks_by_file=chunks_by_file, xrefs=[],
        concepts={}, per_path_concepts={}, collections={},
        file_summaries={}, schema_purposes={}, phases={},
    )


# Directory cycle (a <-> b via different files) whose file graph is a DAG:
#   a/f1 -> b/g1 -> a/f2   (no file cycle)
_DAG_SPECS = [
    ("pkg_a/f1.py", "source_code", "python"),
    ("pkg_a/f2.py", "source_code", "python"),
    ("pkg_b/g1.py", "source_code", "python"),
]
_DAG_IMPORTS = {
    "pkg_a/f1.py": ["pkg_b/g1.py"],
    "pkg_b/g1.py": ["pkg_a/f2.py"],
}


def _resolutions(ev):
    mg = build_module_graph(ev)
    module_cycles = cycles(list(mg.files_of_module), mg.adjacency)
    return _cycle_resolutions(ev, mg, module_cycles), module_cycles


def test_file_order_is_topological_and_complete():
    ev = _ev(_DAG_SPECS, _DAG_IMPORTS)
    res, module_cycles = _resolutions(ev)
    assert module_cycles, "fixture must produce a directory cycle"
    assert len(res) == 1
    order = res[0]["file_order"]
    assert sorted(order) == sorted(p for p, _, _ in _DAG_SPECS)
    pos = {p: i for i, p in enumerate(order)}
    for src, tgts in _DAG_IMPORTS.items():
        for dst in tgts:
            assert pos[dst] < pos[src], f"{dst} must precede its importer {src}"
    assert "dissolves at file granularity" in res[0]["note"]
    assert res[0]["members"] == ["module:pkg_a", "module:pkg_b"]


def test_true_file_cycle_yields_empty_order_with_note():
    ev = _ev(_DAG_SPECS, {
        "pkg_a/f1.py": ["pkg_b/g1.py"],
        "pkg_b/g1.py": ["pkg_a/f1.py"],   # genuine file-level cycle
    })
    res, module_cycles = _resolutions(ev)
    assert module_cycles
    assert len(res) == 1
    assert res[0]["file_order"] == []
    assert "no linear file order exists" in res[0]["note"]


def test_symbol_inventory_is_uncapped():
    """Review finding: the old 30-item sample silently masqueraded as coverage."""
    n = 45
    chunks = [
        {"file": "pkg_a/f1.py", "symbol": f"sym_{i:03d}", "kind": "function",
         "beginLine": i + 1, "endLine": i + 1}
        for i in range(n)
    ]
    ev = _ev([("pkg_a/f1.py", "source_code", "python")], {}, chunks)
    mg = build_module_graph(ev)
    parts = build_module_parts(ev, mg, set())
    part = next(p for p in parts if p.id == "module:pkg_a")
    assert len(part.evidence.symbols) == n
    assert part.evidence.symbols[0] == "f1.py:sym_000 (function)"
    assert part.metrics["languages"] == ["python"]
