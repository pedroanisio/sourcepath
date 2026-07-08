"""RED (round-2 review #2 + #4a): Rust language semantics.

* File-level cycles inside a Rust crate are legal (the compilation unit is the
  crate) and the parent ``mod.rs`` <-> child pattern is the mandatory module
  idiom — findings must be requalified (info + idiom note), not reported as
  error-severity architecture violations. Python cycles keep error severity
  (import-time hazard).
* Rust module contracts must come from ``rust_items`` pub declarations, not
  degrade to file basenames.
"""
from __future__ import annotations

from pathlib import Path

from decomposer.architecture import detect_architecture
from decomposer.evidence import EvidenceGraph
from decomposer.metrics import cycles
from decomposer.parts import build_module_graph, build_module_parts
from decomposer.quality import run_gates


def _ev(file_specs, imports_out=None, rust_items=None):
    files = [
        {"path": p, "type": t, "language": lang, "size": 1, "uri": f"urn:{p}"}
        for (p, t, lang) in file_specs
    ]
    imports_out = imports_out or {}
    imports_in: dict[str, list[str]] = {}
    for src, tgts in imports_out.items():
        for t in tgts:
            imports_in.setdefault(t, []).append(src)
    return EvidenceGraph(
        bundle_dir=Path("."), manifest={}, files=files,
        file_by_path={f["path"]: f for f in files},
        imports_out=imports_out, imports_in=imports_in,
        external_imports={}, tests_for_subject={}, subjects_for_test={},
        chunks=[], chunks_by_file={}, xrefs=[],
        concepts={}, per_path_concepts={}, collections={},
        file_summaries={}, schema_purposes={}, phases={},
        rust_items=rust_items or [],
    )


def _run(ev):
    mg = build_module_graph(ev)
    module_cycles = cycles(list(mg.files_of_module), mg.adjacency)
    file_cycles = cycles([f["path"] for f in ev.files], ev.imports_out)
    arch = detect_architecture(ev, mg, module_cycles, file_cycles)
    gates = run_gates(ev, mg, module_cycles, file_cycles)
    return arch, gates


# The canonical idiom: parent mod.rs declares the child; child uses super::.
_RUST_SPECS = [
    ("crate_x/src/time/mod.rs", "source_code", "rust"),
    ("crate_x/src/time/instant.rs", "source_code", "rust"),
]
_RUST_CYCLE = {
    "crate_x/src/time/mod.rs": ["crate_x/src/time/instant.rs"],
    "crate_x/src/time/instant.rs": ["crate_x/src/time/mod.rs"],
}


def test_rust_mod_cycle_is_not_an_architecture_violation():
    arch, _ = _run(_ev(_RUST_SPECS, _RUST_CYCLE))
    kinds = [v.kind for v in arch.violations]
    assert "circular_dependency" not in kinds


def test_rust_mod_cycle_finding_is_info_with_idiom_note():
    _, gates = _run(_ev(_RUST_SPECS, _RUST_CYCLE))
    finding = next(q for q in gates if q.gate == "circular_dependencies")
    assert finding.severity == "info"
    assert "crate" in finding.description   # names the compilation-unit semantics


def test_python_cycle_keeps_error_severity():
    specs = [("app/a.py", "source_code", "python"),
             ("app/b.py", "source_code", "python")]
    cyc = {"app/a.py": ["app/b.py"], "app/b.py": ["app/a.py"]}
    arch, gates = _run(_ev(specs, cyc))
    finding = next(q for q in gates if q.gate == "circular_dependencies")
    assert finding.severity == "error"
    assert any(v.kind == "circular_dependency" for v in arch.violations)


def test_rust_contracts_come_from_pub_items_not_filenames():
    items = [
        {"path": "crate_x/src/time/instant.rs", "name": "Instant",
         "kind": "struct", "is_pub": True, "parent": None},
        {"path": "crate_x/src/time/instant.rs", "name": "far_future",
         "kind": "fn", "is_pub": False, "parent": None},          # private
        {"path": "crate_x/src/time/instant.rs", "name": "elapsed",
         "kind": "fn", "is_pub": True, "parent": "Instant"},      # method
    ]
    ev = _ev(_RUST_SPECS, _RUST_CYCLE, rust_items=items)
    mg = build_module_graph(ev)
    parts = build_module_parts(ev, mg, set())
    part = next(p for p in parts if p.id == "module:crate_x/src/time")
    assert "Instant" in part.interface_symbols
    assert not any(s.endswith(".rs") for s in part.interface_symbols)
    assert "far_future" not in part.interface_symbols
