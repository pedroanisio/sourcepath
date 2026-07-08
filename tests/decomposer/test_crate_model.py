"""RED (round-2 review #1 + #3): crate-aware Rust model.

Workspace members become first-class ``crate:`` parts; cross-crate module
edges that exist only through dev-dependencies (legal Cargo cycles) are
classified ``test_only`` and excluded from SCC/build-order computation; a
Cargo-workspace style archetype becomes detectable. Dev/prod scope comes from
parsing the manifest blobs already in the bundle (``manifest_deps``), so no
re-extraction is needed.
"""
from __future__ import annotations

from pathlib import Path

from decomposer.architecture import detect_architecture
from decomposer.evidence import EvidenceGraph
from decomposer.metrics import cycles
from decomposer.parts import build_cross_cutting_parts, build_module_graph, build_module_parts


def _ev(file_specs, imports_out=None, manifest_deps=None):
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
        manifest_deps=manifest_deps or {},
    )


# Two-crate workspace: core is a prod dependency of nothing; helper is core's
# DEV dependency, and a test-gated import core -> helper creates the classic
# dev-cycle (helper also imports core as a prod dep).
_SPECS = [
    ("Cargo.toml", "dependency_manifest", None),
    ("core/Cargo.toml", "dependency_manifest", None),
    ("core/src/lib.rs", "source_code", "rust"),
    ("helper/Cargo.toml", "dependency_manifest", None),
    ("helper/src/lib.rs", "source_code", "rust"),
]
_IMPORTS = {
    "core/src/lib.rs": ["helper/src/lib.rs"],    # #[cfg(test)] use — dev edge
    "helper/src/lib.rs": ["core/src/lib.rs"],    # prod dependency edge
}
_MANIFESTS = {
    "Cargo.toml": {"name": None, "deps": [], "dev_deps": [],
                   "workspace_members": ["core", "helper"]},
    "core/Cargo.toml": {"name": "core", "deps": [],
                        "dev_deps": ["helper"], "workspace_members": []},
    "helper/Cargo.toml": {"name": "helper", "deps": ["core"],
                          "dev_deps": [], "workspace_members": []},
}


def _parts(ev):
    mg = build_module_graph(ev)
    return build_module_parts(ev, mg, set()) + build_cross_cutting_parts(ev, mg), mg


def test_workspace_members_become_crate_parts():
    parts, _ = _parts(_ev(_SPECS, _IMPORTS, _MANIFESTS))
    crates = {p.id: p for p in parts if p.id.startswith("crate:")}
    assert set(crates) == {"crate:core", "crate:helper"}
    assert crates["crate:core"].kind == "library"
    assert crates["crate:core"].overall_confidence.value in {"certain", "strong"}
    assert "core/src/lib.rs" in crates["crate:core"].evidence.files


def test_dev_only_cross_crate_edge_is_classified_test_only():
    parts, _ = _parts(_ev(_SPECS, _IMPORTS, _MANIFESTS))
    core_mod = next(p for p in parts if p.id == "module:core/src")
    # The core -> helper edge exists only through a dev-dependency: it must be
    # in test_only_outgoing, NOT in outgoing (which feeds SCC/build order).
    assert "module:helper/src" not in core_mod.dependencies.outgoing
    assert "module:helper/src" in core_mod.dependencies.test_only_outgoing
    # The prod edge helper -> core stays.
    helper_mod = next(p for p in parts if p.id == "module:helper/src")
    assert "module:core/src" in helper_mod.dependencies.outgoing


def test_module_parts_are_stamped_with_their_crate():
    parts, _ = _parts(_ev(_SPECS, _IMPORTS, _MANIFESTS))
    core_mod = next(p for p in parts if p.id == "module:core/src")
    assert core_mod.metrics.get("crate") == "core"


def test_workspace_style_archetype_detected():
    ev = _ev(_SPECS, _IMPORTS, _MANIFESTS)
    mg = build_module_graph(ev)
    module_cycles = cycles(list(mg.files_of_module), mg.adjacency)
    file_cycles = cycles([f["path"] for f in ev.files], ev.imports_out)
    arch = detect_architecture(ev, mg, module_cycles, file_cycles)
    assert "workspace" in arch.style.lower()
    assert any("workspace" in h.statement.lower() for h in arch.hypotheses)


def test_decompose_scc_excludes_dev_edges(tmp_path):
    """End-to-end at the decompose() level using the module dependency split:
    with the dev edge excluded, core/src and helper/src must NOT share a
    build-order layer as a cycle group."""
    from decomposer.decompose import _prod_adjacency
    ev = _ev(_SPECS, _IMPORTS, _MANIFESTS)
    mg = build_module_graph(ev)
    adj = _prod_adjacency(ev, mg)
    assert "helper/src" not in adj.get("core/src", [])
    assert "core/src" in adj.get("helper/src", [])
    assert cycles(list(mg.files_of_module), adj) == []
