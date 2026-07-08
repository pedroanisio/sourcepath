"""Decomposition orchestrator.

Wires the pipeline together: load evidence → aggregate the module graph →
compute cycles/build-order → extract & classify parts → derive relationships →
detect architecture → run quality gates → assemble a :class:`Decomposition`.

The result is deterministic for a given bundle state: no wall-clock time, random
seeds, or set-iteration leaks into the output (every collection is sorted before
emission), so re-running on the same bundle yields byte-identical YAML.
"""
from __future__ import annotations

from pathlib import Path

from .architecture import detect_architecture
from .crates import CrateMap, _module_crate, build_crate_parts, detect_crates, \
    test_only_module_edges
from .evidence import EvidenceGraph, load_evidence
from .metrics import build_order as _build_order, cycles as _cycles
from .model import (
    Confidence, Decomposition, Part, Relationship,
)
from .parts import (
    ModuleGraph, ROOT, build_cross_cutting_parts, build_module_graph,
    build_module_parts, build_symbol_map, detect_entrypoints,
)
from .quality import run_gates

TOOL_ID = "codebase-mapper decomposer v0.1.0"


def decompose(bundle_dir: str | Path) -> Decomposition:
    return decompose_evidence(load_evidence(bundle_dir))


def decompose_evidence(ev: EvidenceGraph) -> Decomposition:
    """Assemble a Decomposition from an already-loaded evidence graph.

    Split from :func:`decompose` so tests (and callers that already hold an
    :class:`EvidenceGraph`) can run the pipeline without a bundle directory.
    """
    mg = build_module_graph(ev)

    # Crate awareness (Cargo workspaces): dev-only cross-crate edges are legal
    # cycles in Cargo and must not drive SCC/build-order computation.
    cm = detect_crates(ev)
    test_edges = test_only_module_edges(ev, mg, cm)
    crate_of_module = _crate_names_by_module(mg, cm)
    prod_adjacency = _prod_adjacency(ev, mg, cm, test_edges)

    module_names = [m for m in mg.modules() if _has_code(ev, mg, m)]
    module_cycles = _cycles(module_names, prod_adjacency)
    # Cycles at file granularity too: directory aggregation both manufactures
    # cycles (parent/child re-exports) and hides them, so topology claims are
    # only honest when both granularities are computed and reported.
    file_cycles = _cycles([f["path"] for f in ev.files], ev.imports_out)
    cycle_modules = {m for cyc in module_cycles for m in cyc}

    module_parts = build_module_parts(ev, mg, cycle_modules,
                                      crate_of_module, test_edges)
    cross_parts = build_cross_cutting_parts(ev, mg)
    crate_parts = build_crate_parts(ev, mg, cm)
    parts = module_parts + cross_parts + crate_parts

    symbol_map = build_symbol_map(ev, mg)
    for p in module_parts:
        symbols = symbol_map.get(p.id)
        if symbols:
            counts: dict[str, int] = {}
            for s in symbols:
                key = {"class": "classes", "function": "functions",
                       "method": "methods"}.get(s.kind, s.kind)
                counts[key] = counts.get(key, 0) + 1
            p.metrics["symbols"] = dict(sorted(counts.items()))

    relationships = _relationships(ev, mg)
    architecture = detect_architecture(ev, mg, module_cycles, file_cycles)
    gates = run_gates(ev, mg, module_cycles, file_cycles)

    module_part_ids = {p.id for p in module_parts}
    order_layers = _build_order(module_names, prod_adjacency)
    build_order = [
        [f"module:{m}" for m in layer if f"module:{m}" in module_part_ids]
        for layer in order_layers
    ]
    build_order = [layer for layer in build_order if layer]

    repository = _repository_header(ev, parts, module_cycles, file_cycles)
    provenance = _provenance(ev, mg, parts, gates, module_cycles)

    return Decomposition(
        repository=repository,
        parts=parts,
        symbol_map=symbol_map,
        relationships=relationships,
        detected_architecture=architecture,
        quality_gates=gates,
        build_order=build_order,
        cycle_resolutions=_cycle_resolutions(ev, mg, module_cycles),
        provenance=provenance,
    )


def _crate_names_by_module(mg: ModuleGraph, cm: CrateMap) -> dict[str, str]:
    out: dict[str, str] = {}
    for mod in mg.files_of_module:
        crate_dir = _module_crate(mod, mg, cm)
        if crate_dir is not None:
            name = cm.crates[crate_dir].get("name")
            if name:
                out[mod] = name
    return out


def _prod_adjacency(
    ev: EvidenceGraph, mg: ModuleGraph,
    cm: CrateMap | None = None,
    test_edges: set[tuple[str, str]] | None = None,
) -> dict[str, list[str]]:
    """Module adjacency with dev/test-only cross-crate edges removed — the
    graph that SCC and build-order math must run on. Identical to
    ``mg.adjacency`` for repositories without crate manifests."""
    if test_edges is None:
        cm = cm or detect_crates(ev)
        test_edges = test_only_module_edges(ev, mg, cm)
    if not test_edges:
        return mg.adjacency
    return {
        m: [d for d in deps if (m, d) not in test_edges]
        for m, deps in mg.adjacency.items()
    }


def _cycle_resolutions(
    ev: EvidenceGraph, mg: ModuleGraph, module_cycles: list[list[str]],
) -> list[dict]:
    """File-level construction order for each directory-granularity SCC.

    The YAML carries only module-granularity relationships, so this is the one
    place file-level topology is preserved for consumers (the Recomposer): a
    topological order over the cycle group's code files, restricted to the
    import edges among them. If the files themselves are cyclic, the order is
    empty and the note says so — never a fabricated linear order.
    """
    out: list[dict] = []
    for cyc in sorted(module_cycles):
        members = sorted(cyc)
        group_files = sorted({
            p for m in members for p in mg.files_of_module.get(m, [])
            if ev.file_by_path.get(p, {}).get("type") in {"source_code", "test_code"}
        })
        in_group = set(group_files)
        adjacency = {
            p: sorted(t for t in ev.imports_out.get(p, []) if t in in_group)
            for p in group_files
        }
        file_cycles = _cycles(group_files, adjacency)
        if file_cycles:
            out.append({
                "members": [f"module:{m}" for m in members],
                "file_order": [],
                "note": (f"{len(file_cycles)} file-level cycle(s) inside the "
                         f"group; no linear file order exists"),
            })
            continue
        layers = _build_order(group_files, adjacency)
        order = [p for layer in layers for p in sorted(layer)]
        out.append({
            "members": [f"module:{m}" for m in members],
            "file_order": order,
            "note": ("topological over the group's internal file imports; "
                     "the directory-level cycle dissolves at file granularity"),
        })
    return out


def _has_code(ev: EvidenceGraph, mg: ModuleGraph, mod: str) -> bool:
    return any(
        ev.file_by_path.get(p, {}).get("type") in {"source_code", "test_code"}
        for p in mg.files_of_module.get(mod, [])
    )


def _relationships(ev: EvidenceGraph, mg: ModuleGraph) -> list[Relationship]:
    rels: list[Relationship] = []

    # module -> module imports (aggregated file edges), CERTAIN.
    for (a, b), w in sorted(mg.edge_weight.items()):
        rels.append(Relationship(
            source=f"module:{a}", target=f"module:{b}", type="imports",
            strength=w, confidence=Confidence.CERTAIN,
            evidence=f"{w} file-level import edge(s) cross {a}->{b}",
        ))

    # module -> external package, CERTAIN.
    ext_w: dict[tuple[str, str], int] = {}
    for path, pkgs in ev.external_imports.items():
        m = mg.module_of_file.get(path, ROOT)
        for pkg in pkgs:
            ext_w[(m, pkg)] = ext_w.get((m, pkg), 0) + 1
    for (m, pkg), w in sorted(ext_w.items()):
        rels.append(Relationship(
            source=f"module:{m}", target=f"ext:{pkg}", type="imports_external",
            strength=w, confidence=Confidence.CERTAIN,
            evidence=f"{w} file(s) in {m} import {pkg}",
        ))

    # module -> module test coverage (from cbm:tests edges), CERTAIN.
    test_w: dict[tuple[str, str], int] = {}
    for subject, tests in ev.tests_for_subject.items():
        subm = mg.module_of_file.get(subject, ROOT)
        for t in tests:
            tm = mg.module_of_file.get(t, ROOT)
            test_w[(tm, subm)] = test_w.get((tm, subm), 0) + 1
    for (tm, subm), w in sorted(test_w.items()):
        rels.append(Relationship(
            source=f"module:{tm}", target=f"module:{subm}", type="tests",
            strength=w, confidence=Confidence.CERTAIN,
            evidence=f"{w} tests edge(s) {tm}->{subm}",
        ))

    # module -> module symbol-level edges (calls / subclassOf / overrides),
    # aggregated across the module boundary. Confidence is CERTAIN only when
    # every contributing xref was resolved exactly; one heuristic edge
    # downgrades the aggregate to PROBABLE (weakest-link rule).
    xref_w: dict[tuple[str, str, str], int] = {}
    xref_exact: dict[tuple[str, str, str], bool] = {}
    for e in ev.xrefs:
        kind = e["kind"]
        if kind not in ("calls", "subclassOf", "overrides"):
            continue
        sm = mg.module_of_file.get(ev.chunks[e["src_idx"]].get("file") or "")
        dm = mg.module_of_file.get(ev.chunks[e["dst_idx"]].get("file") or "")
        if sm and dm and sm != dm:
            key = (kind, sm, dm)
            xref_w[key] = xref_w.get(key, 0) + 1
            xref_exact[key] = (
                xref_exact.get(key, True) and e.get("resolution") == "exact"
            )
    for (kind, sm, dm), w in sorted(xref_w.items()):
        conf = Confidence.CERTAIN if xref_exact[(kind, sm, dm)] else Confidence.PROBABLE
        rels.append(Relationship(
            source=f"module:{sm}", target=f"module:{dm}", type=kind,
            strength=w, confidence=conf,
            evidence=f"{w} cross-module {kind} edge(s) {sm}->{dm}",
        ))
    return rels


def _repository_header(
    ev: EvidenceGraph, parts: list[Part],
    module_cycles: list[list[str]], file_cycles: list[list[str]],
) -> dict:
    m = ev.manifest
    purpose, purpose_conf = _purpose(ev)
    # Overall confidence: structural decomposition is well-evidenced; downgrade
    # if the graph is thin (no concepts/enrichment) or heavily cyclic.
    overall = Confidence.STRONG
    if not ev.concepts:
        overall = Confidence.PROBABLE
    return {
        "name": m.get("repo_name"),
        "purpose": purpose,
        "purpose_confidence": purpose_conf.value,
        "confidence": overall.value,
        "commit_sha": m.get("commit_sha"),
        "tool_version": m.get("tool_version"),
        "generated_at": m.get("generated_at"),
        "files": (m.get("counts") or {}).get("files"),
        "n_parts": len(parts),
        "n_module_cycles": len(module_cycles),
        "n_file_cycles": len(file_cycles),
    }


def _purpose(ev: EvidenceGraph) -> tuple[str, Confidence]:
    for candidate in ("README.md", "PURPOSE.md", "readme.md"):
        s = ev.file_summaries.get(candidate, {})
        if s.get("text"):
            return (f"(LLM-derived from {candidate}, unverified) {s['text']}",
                    Confidence.WEAK)
    # Fall back to the most frequent typed domain concepts.
    typed = sorted(
        ((n, r) for n, r in ev.concepts.items() if r.get("kind")),
        key=lambda kv: -int(kv[1].get("frequency", 0)),
    )[:8]
    if typed:
        return ("Inferred from dominant domain concepts: "
                + ", ".join(n for n, _ in typed) + ".", Confidence.WEAK)
    return ("Not determinable from bundle evidence.", Confidence.UNKNOWN)


def _provenance(
    ev: EvidenceGraph, mg: ModuleGraph, parts, gates,
    module_cycles: list[list[str]],
) -> dict:
    from collections import Counter
    kinds = Counter(p.kind for p in parts)
    return {
        "tool": TOOL_ID,
        "bundle_dir": str(ev.bundle_dir),
        "run_manifest_sha256": ev.manifest_sha256,
        "bundle_generated_at": ev.manifest.get("generated_at"),
        "bundle_extensions": sorted((ev.manifest.get("extensions") or {}).keys()),
        "module_cycles": [list(c) for c in module_cycles],
        "evidence_basis": (
            "Structural parts, dependencies, coupling metrics (Ca/Ce, Martin "
            "instability), cycles and build order are mechanically derived from "
            "the CBM graph and are evidence-backed. Roles, layers, semantic "
            "domains, responsibilities, and architecture style are interpretive, "
            "confidence-tagged, and must be validated before high-stakes use. "
            "LLM-authored text is surfaced only under evidence.llm_summaries and "
            "is unverified (PALS's Law)."
        ),
        "inputs": {
            "files": len(ev.files),
            "internal_import_edges": sum(len(v) for v in ev.imports_out.values()),
            "external_import_files": len(ev.external_imports),
            "xref_edges": len(ev.xrefs),
            "chunks": len(ev.chunks),
            "concepts": len(ev.concepts),
            "llm_file_summaries": len(ev.file_summaries),
            "phases_available": bool(ev.phases),
        },
        "part_kind_counts": dict(sorted(kinds.items())),
        "n_relationships_note": "relationships aggregated to module granularity",
        "quality_findings": len(gates),
    }
