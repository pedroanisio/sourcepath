"""Cycle findings must cite real, granularity-qualified evidence.

Regression tests for the v0.1.0 defects found in review:

* The rendered "cycle path" was the *alphabetically sorted* SCC joined with
  arrows — asserting edges that do not exist in any graph.
* Directory-aggregation cycles were graded ``certain``/``error`` although the
  module==directory model is self-declared ``probable`` and the file-level
  graph can be (and, for this repo, is) a clean DAG.
* Error-severity findings carried no inducing edges at all.
"""
from __future__ import annotations

from pathlib import Path

from decomposer.architecture import detect_architecture
from decomposer.evidence import EvidenceGraph
from decomposer.metrics import cycles
from decomposer.model import Confidence
from decomposer.parts import build_module_graph
from decomposer.quality import run_gates


def _ev(file_specs, imports_out=None, chunks=None, xrefs=None):
    """Minimal synthetic EvidenceGraph. file_specs: (path, type, language)."""
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
        chunks=chunks, chunks_by_file=chunks_by_file, xrefs=xrefs or [],
        concepts={}, per_path_concepts={}, collections={},
        file_summaries={}, schema_purposes={}, phases={},
    )


def _graphs(ev):
    mg = build_module_graph(ev)
    module_cycles = cycles(list(mg.files_of_module), mg.adjacency)
    file_cycles = cycles([f["path"] for f in ev.files], ev.imports_out)
    return mg, module_cycles, file_cycles


# A directory-level cycle with NO file-level cycle: a/f1 -> b/g1, b/g2 -> a/f2.
_DIR_CYCLE_SPECS = [
    ("pkg_a/f1.py", "source_code", "python"),
    ("pkg_a/f2.py", "source_code", "python"),
    ("pkg_b/g1.py", "source_code", "python"),
    ("pkg_b/g2.py", "source_code", "python"),
]
_DIR_CYCLE_IMPORTS = {
    "pkg_a/f1.py": ["pkg_b/g1.py"],
    "pkg_b/g2.py": ["pkg_a/f2.py"],
}


def test_directory_cycle_is_probable_warning_with_real_edges():
    ev = _ev(_DIR_CYCLE_SPECS, _DIR_CYCLE_IMPORTS)
    mg, module_cycles, file_cycles = _graphs(ev)
    assert module_cycles and not file_cycles  # the scenario under test

    findings = run_gates(ev, mg, module_cycles, file_cycles)
    dir_findings = [f for f in findings if f.gate == "directory_aggregation_cycle"]
    assert len(dir_findings) == 1
    f = dir_findings[0]
    # Model-capped confidence and demoted severity.
    assert f.severity == "warning"
    assert f.confidence == Confidence.PROBABLE
    # Real inducing file edges are cited.
    assert any("pkg_a/f1.py -> pkg_b/g1.py" in e for e in f.evidence)
    assert any("pkg_b/g2.py -> pkg_a/f2.py" in e for e in f.evidence)
    # No error-grade circular finding for an aggregation-only cycle.
    assert not any(
        q.gate == "circular_dependencies" and q.severity == "error"
        for q in findings
    )


def test_directory_cycle_description_contains_only_real_module_edges():
    ev = _ev(_DIR_CYCLE_SPECS, _DIR_CYCLE_IMPORTS)
    mg, module_cycles, file_cycles = _graphs(ev)
    findings = run_gates(ev, mg, module_cycles, file_cycles)
    f = [q for q in findings if q.gate == "directory_aggregation_cycle"][0]
    # Every "X -> Y" module pair asserted in the description must be a real
    # aggregated edge (the v0.1.0 sorted-join rendering fabricated edges).
    import re
    for a, b in re.findall(r"(pkg_\w+) -> (pkg_\w+)", f.description):
        assert (a, b) in mg.edge_weight, f"fabricated edge {a} -> {b}"


def test_granularity_divergence_is_surfaced_as_a_finding():
    ev = _ev(_DIR_CYCLE_SPECS, _DIR_CYCLE_IMPORTS)
    mg, module_cycles, file_cycles = _graphs(ev)
    findings = run_gates(ev, mg, module_cycles, file_cycles)
    div = [f for f in findings if f.gate == "granularity_divergence"]
    assert len(div) == 1
    assert div[0].confidence == Confidence.CERTAIN
    assert "DAG" in div[0].description


def test_file_level_cycle_is_certain_error_with_file_edges():
    # Real file-level cycle inside one directory (no module-level cycle).
    ev = _ev(
        [("pkg_a/f1.py", "source_code", "python"),
         ("pkg_a/f2.py", "source_code", "python")],
        {"pkg_a/f1.py": ["pkg_a/f2.py"], "pkg_a/f2.py": ["pkg_a/f1.py"]},
    )
    mg, module_cycles, file_cycles = _graphs(ev)
    assert file_cycles and not module_cycles

    findings = run_gates(ev, mg, module_cycles, file_cycles)
    errs = [f for f in findings if f.gate == "circular_dependencies"]
    assert len(errs) == 1
    assert errs[0].severity == "error"
    assert errs[0].confidence == Confidence.CERTAIN
    assert any("pkg_a/f1.py -> pkg_a/f2.py" in e for e in errs[0].evidence)
    assert not any(f.gate == "granularity_divergence" for f in findings)


def test_bidirectional_coupling_is_model_capped_and_cites_file_edges():
    ev = _ev(_DIR_CYCLE_SPECS, _DIR_CYCLE_IMPORTS)
    mg, module_cycles, file_cycles = _graphs(ev)
    arch = detect_architecture(ev, mg, module_cycles, file_cycles)
    bidi = [v for v in arch.violations if v.kind == "bidirectional_coupling"]
    assert len(bidi) == 1
    assert bidi[0].confidence == Confidence.PROBABLE
    assert "pkg_a/f1.py -> pkg_b/g1.py" in bidi[0].description
    assert "pkg_b/g2.py -> pkg_a/f2.py" in bidi[0].description


def test_architecture_has_no_fabricated_cycle_chain():
    ev = _ev(_DIR_CYCLE_SPECS, _DIR_CYCLE_IMPORTS)
    mg, module_cycles, file_cycles = _graphs(ev)
    arch = detect_architecture(ev, mg, module_cycles, file_cycles)
    import re
    for v in arch.violations:
        for a, b in re.findall(r"(pkg_\w+) -> (pkg_\w+)", v.description):
            assert (a, b) in mg.edge_weight, f"fabricated edge {a} -> {b} in {v.kind}"


def test_shared_kernel_sibling_import_is_flagged_with_file_edge():
    # Kernel importing a sibling package under the SAME top-level package —
    # exactly the shared_kernel -> inspection case this repo whitelists in
    # import-linter. v0.1.0 only compared top-level segments and missed it.
    ev = _ev(
        [("proj/shared_kernel/x.py", "source_code", "python"),
         ("proj/feature/y.py", "source_code", "python")],
        {"proj/shared_kernel/x.py": ["proj/feature/y.py"]},
    )
    mg, module_cycles, file_cycles = _graphs(ev)
    arch = detect_architecture(ev, mg, module_cycles, file_cycles)
    kernel = [v for v in arch.violations if v.kind == "shared_kernel_not_independent"]
    assert len(kernel) == 1
    assert kernel[0].confidence == Confidence.CERTAIN
    assert "proj/shared_kernel/x.py -> proj/feature/y.py" in kernel[0].description


def test_interface_fallback_applies_only_without_symbol_xrefs():
    # pkg_b receives a cross-module symbol xref -> interface is the symbol,
    # NOT the imported file's basename. pkg_c has no xrefs -> basename fallback.
    chunks = [
        {"symbol": "caller", "kind": "function", "file": "pkg_a/f1.py"},
        {"symbol": "Foo", "kind": "class", "file": "pkg_b/g1.py"},
    ]
    xrefs = [{"src_idx": 0, "dst_idx": 1, "kind": "calls"}]
    ev = _ev(
        [("pkg_a/f1.py", "source_code", "python"),
         ("pkg_b/g1.py", "source_code", "python"),
         ("pkg_c/h1.py", "source_code", "python")],
        {"pkg_a/f1.py": ["pkg_b/g1.py", "pkg_c/h1.py"]},
        chunks=chunks, xrefs=xrefs,
    )
    mg = build_module_graph(ev)
    assert mg.interfaces["pkg_b"] == ["Foo"]          # no "g1.py" mixed in
    assert mg.interfaces["pkg_c"] == ["h1.py"]        # fallback where no xrefs


def test_test_gap_skips_modules_without_testable_language():
    ev = _ev(
        [("docker/cbm-analyze", "source_code", None),
         ("static/proto/x.proto", "source_code", "protobuf"),
         ("app/logic.py", "source_code", "python")],
    )
    mg, module_cycles, file_cycles = _graphs(ev)
    findings = run_gates(ev, mg, module_cycles, file_cycles)
    gaps = {f.subject for f in findings if f.gate == "test_gap"}
    assert "module:app" in gaps
    assert "module:docker" not in gaps
    assert "module:static/proto" not in gaps
