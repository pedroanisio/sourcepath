"""Part extraction.

Turns an :class:`EvidenceGraph` into :class:`Part` objects at two granularities:

* **modules** (the primary analytical unit) — one per directory subtree that
  contains code. Files are rolled up into their module's evidence rather than
  emitted as hundreds of separate parts; this implements "meaningful parts",
  not "list every file". A handful of structurally significant files
  (entry points, generated artifacts, schemas) are *promoted* to their own
  ``file`` parts because they carry architecture on their own.
* **cross-cutting parts** — applications/services (entry-point-bearing subtrees),
  external dependencies, behavioral entry points, semantic domains (from the
  concept vocabulary), and data schemas.

The module model (module == directory) is an explicit ``probable`` hypothesis:
directory layout is real evidence, but it is not guaranteed to equal a language's
module boundary. Consumers see that confidence on every module part.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from . import classify
from .evidence import EvidenceGraph
from .metrics import instability as _instability
from .model import (
    Classification, Confidence, DepRef, Evidence, Part, SymbolRecord,
)

_CODE_TYPES = frozenset({"source_code", "test_code"})
ROOT = "(root)"


@dataclass
class ModuleGraph:
    """Directory-level aggregation of the file import graph."""

    module_of_file: dict[str, str] = field(default_factory=dict)
    files_of_module: dict[str, list[str]] = field(default_factory=dict)
    adjacency: dict[str, list[str]] = field(default_factory=dict)   # depends-on
    edge_weight: dict[tuple[str, str], int] = field(default_factory=dict)
    ca: dict[str, int] = field(default_factory=dict)
    ce: dict[str, int] = field(default_factory=dict)
    importers: dict[str, list[str]] = field(default_factory=dict)   # reverse adjacency
    interfaces: dict[str, list[str]] = field(default_factory=dict)
    xref_in: dict[str, int] = field(default_factory=dict)
    xref_out: dict[str, int] = field(default_factory=dict)

    def modules(self) -> list[str]:
        return sorted(self.files_of_module)


def _module_of(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return ROOT if parent in (".", "") else parent


def build_module_graph(ev: EvidenceGraph) -> ModuleGraph:
    mg = ModuleGraph()

    # Assign every file to a module; a module exists iff it holds ≥1 code file.
    code_modules: set[str] = set()
    for f in ev.files:
        path = f["path"]
        mod = _module_of(path)
        mg.module_of_file[path] = mod
        mg.files_of_module.setdefault(mod, []).append(path)
        if f.get("type") in _CODE_TYPES:
            code_modules.add(mod)
    # Drop modules with no code (pure docs/asset dirs) from the graph surface;
    # their files still appear in evidence via their nearest code module? No —
    # keep them as standalone supporting modules only if they have no code.
    mg.files_of_module = {m: sorted(fs) for m, fs in mg.files_of_module.items()}

    # Module import edges from file→file internal imports.
    for src, targets in ev.imports_out.items():
        sm = mg.module_of_file.get(src)
        if sm is None:
            continue
        for dst in targets:
            dm = mg.module_of_file.get(dst)
            if dm is None or dm == sm:
                continue
            mg.edge_weight[(sm, dm)] = mg.edge_weight.get((sm, dm), 0) + 1

    adj_sets: dict[str, set[str]] = {}
    rev_sets: dict[str, set[str]] = {}
    for (sm, dm) in mg.edge_weight:
        adj_sets.setdefault(sm, set()).add(dm)
        rev_sets.setdefault(dm, set()).add(sm)
    for m in mg.files_of_module:
        mg.adjacency[m] = sorted(adj_sets.get(m, set()))
        mg.importers[m] = sorted(rev_sets.get(m, set()))
        mg.ce[m] = len(mg.adjacency[m])
        mg.ca[m] = len(mg.importers[m])

    _compute_interfaces(ev, mg)
    return mg


def _compute_interfaces(ev: EvidenceGraph, mg: ModuleGraph) -> None:
    """A module's interface = symbols in it that are called/subclassed from
    another module (cross-boundary xref targets), falling back to the file
    basenames imported from outside when no symbol-level xrefs cross in."""
    iface: dict[str, set[str]] = {}
    xin: dict[str, int] = {}
    xout: dict[str, int] = {}
    for e in ev.xrefs:
        src = ev.chunks[e["src_idx"]]
        dst = ev.chunks[e["dst_idx"]]
        sm = mg.module_of_file.get(src.get("file") or "")
        dm = mg.module_of_file.get(dst.get("file") or "")
        if sm is None or dm is None or sm == dm:
            continue
        xout[sm] = xout.get(sm, 0) + 1
        xin[dm] = xin.get(dm, 0) + 1
        sym = dst.get("symbol")
        if sym and sym != "<file>":
            iface.setdefault(dm, set()).add(sym)

    # Rust fallback: declared public surface from the rust_items sidecar —
    # top-level `pub` items per module. Stronger than filenames: it is the
    # crate's actual exported API, mechanically extracted.
    rust_pub: dict[str, set[str]] = {}
    for item in ev.rust_items:
        if not item.get("is_pub") or item.get("parent"):
            continue
        name = item.get("name")
        m = mg.module_of_file.get(item.get("path") or "")
        if name and m is not None:
            rust_pub.setdefault(m, set()).add(name)

    # Last-resort fallback: files imported across the module boundary are
    # public surface — only for modules with NO symbol-level xrefs crossing in
    # and no declared-API evidence, so a module's interface list is symbols,
    # declared pub items, or file basenames — never a mix.
    fallback: dict[str, set[str]] = {}
    for src, targets in ev.imports_out.items():
        sm = mg.module_of_file.get(src)
        for dst in targets:
            dm = mg.module_of_file.get(dst)
            if dm and sm != dm:
                fallback.setdefault(dm, set()).add(PurePosixPath(dst).name)

    for m in mg.files_of_module:
        surface = iface.get(m) or rust_pub.get(m) or fallback.get(m, set())
        mg.interfaces[m] = sorted(surface)
        mg.xref_in[m] = xin.get(m, 0)
        mg.xref_out[m] = xout.get(m, 0)


# ── symbol map (Tier 1) ──────────────────────────────────────────────────────
def build_symbol_map(
    ev: EvidenceGraph, mg: ModuleGraph,
) -> dict[str, list[SymbolRecord]]:
    """Full symbol inventory per module part: one :class:`SymbolRecord` per
    symbol chunk (``file`` chunks are containers, not symbols — excluded).

    ``is_interface`` marks chunks that are the *target* of a cross-module xref
    (called / subclassed / overridden from another module) — the uncapped,
    per-symbol counterpart of the part's ``interface_symbols`` list.
    Everything here is graph-proven, hence CERTAIN.
    """
    interface_idx: set[int] = set()
    for e in ev.xrefs:
        src = ev.chunks[e["src_idx"]]
        dst = ev.chunks[e["dst_idx"]]
        sm = mg.module_of_file.get(src.get("file") or "")
        dm = mg.module_of_file.get(dst.get("file") or "")
        if sm is not None and dm is not None and sm != dm:
            interface_idx.add(e["dst_idx"])

    out: dict[str, list[SymbolRecord]] = {}
    for mod, files in mg.files_of_module.items():
        records: list[SymbolRecord] = []
        for path in files:
            for i in ev.chunks_by_file.get(path, []):
                c = ev.chunks[i]
                if c.get("kind") == "file":
                    continue
                records.append(SymbolRecord(
                    name=c.get("symbol") or "<unknown>",
                    kind=c.get("kind") or "unknown",
                    file=path,
                    line_start=c.get("beginLine"),
                    line_end=c.get("endLine"),
                    parent=c.get("parentSymbol"),
                    signature=c.get("signature"),
                    params=c.get("params"),
                    returns=c.get("returns"),
                    bases=c.get("bases"),
                    type_params=c.get("typeParams"),
                    visibility=c.get("visibility"),
                    is_async=bool(c.get("isAsync")),
                    decorators=c.get("decorators"),
                    is_interface=i in interface_idx,
                ))
        if records:
            records.sort(key=lambda s: (s.name, s.file, s.line_start or 0))
            out[f"module:{mod}"] = records
    return out


# ── module → Part ─────────────────────────────────────────────────────────────
def build_module_parts(
    ev: EvidenceGraph, mg: ModuleGraph, cycle_modules: set[str],
    crate_of_module: dict[str, str] | None = None,
    test_edges: set[tuple[str, str]] | None = None,
) -> list[Part]:
    parts: list[Part] = []
    for mod in mg.modules():
        files = mg.files_of_module[mod]
        code_files = [p for p in files if ev.file_by_path.get(p, {}).get("type") in _CODE_TYPES]
        if not code_files:
            continue  # non-code directory: represented via evidence elsewhere
        parts.append(_module_part(ev, mg, mod, files, code_files, cycle_modules,
                                  crate_of_module or {}, test_edges or set()))
    return parts


def _module_part(
    ev: EvidenceGraph, mg: ModuleGraph, mod: str,
    files: list[str], code_files: list[str], cycle_modules: set[str],
    crate_of_module: dict[str, str], test_edges: set[tuple[str, str]],
) -> Part:
    ca, ce = mg.ca.get(mod, 0), mg.ce.get(mod, 0)
    inst, stab, stab_conf = classify.classify_stability(ca, ce)

    phases = sorted({ph for p in files for ph in ev.phases.get(p, [])})
    is_runtime = "runtime" in phases or not phases
    layer, layer_conf = classify.layer_of(mod + "/x")  # treat mod as a dir prefix
    file_roles = [
        classify.file_role(ev.file_by_path[p], ev.phases.get(p, []))
        for p in code_files
    ]
    role, role_conf = classify.module_role(file_roles, layer, ca, ce, is_runtime)

    in_cycle = mod in cycle_modules
    n_symbols = sum(len(ev.chunks_by_file.get(p, [])) for p in code_files)
    is_god = ca >= 6 and ce >= 6
    sdp = inst is not None and inst > 0.6 and ca >= 3   # depended-on yet unstable
    risk, risk_reasons, _risk_conf = classify.assess_risk(
        in_cycle=in_cycle, is_god=is_god, sdp_violation=False,
        high_fanin_unstable=sdp,
    )

    is_pkg = any(
        PurePosixPath(p).name in {"__init__.py", "mod.rs", "index.ts", "index.js", "package-info.java"}
        for p in files
    )
    kind = "package" if is_pkg else "module"

    concepts = _module_concepts(ev, code_files)
    responsibility, resp_conf = _module_responsibility(ev, mod, role, concepts, code_files)

    classification = Classification(
        role=role, role_confidence=role_conf,
        layer=layer, layer_confidence=layer_conf,
        instability=inst, stability=stab, stability_confidence=stab_conf,
        reusability=classify.reusability(role, ca, ce, mod),
        risk=risk, risk_reasons=risk_reasons,
    )

    ev_obj = Evidence(
        files=files,
        symbols=_symbol_inventory(ev, code_files),
        graph_nodes=[ev.file_by_path[p]["uri"] for p in code_files if p in ev.file_by_path][:20],
        graph_edges=_module_edge_descriptors(mg, mod),
        signals=_module_signals(ev, mod, files, code_files, phases, ca, ce, in_cycle),
        llm_summaries=_module_llm_summaries(ev, code_files),
    )
    # Dev/test-only edges (e.g. Cargo dev-dependency imports) are carried
    # separately: they are real, but must not drive SCC/build-order math.
    prod_out = [m for m in mg.adjacency.get(mod, []) if (mod, m) not in test_edges]
    test_out = [m for m in mg.adjacency.get(mod, []) if (mod, m) in test_edges]
    deps = DepRef(
        incoming=[f"module:{m}" for m in mg.importers.get(mod, [])],
        outgoing=[f"module:{m}" for m in prod_out],
        test_only_outgoing=[f"module:{m}" for m in test_out],
    )
    metrics = {
        "ca": ca, "ce": ce,
        "instability": inst,
        "n_files": len(files), "n_code_files": len(code_files),
        "n_symbols": n_symbols,
        "size_bytes": sum(ev.file_by_path.get(p, {}).get("size") or 0 for p in files),
        "xref_in": mg.xref_in.get(mod, 0), "xref_out": mg.xref_out.get(mod, 0),
        "phases": phases,
        "languages": sorted({
            lang for p in code_files
            if (lang := ev.file_by_path.get(p, {}).get("language"))
        }),
    }
    if crate_of_module.get(mod):
        metrics["crate"] = crate_of_module[mod]
    return Part(
        id=f"module:{mod}", name=mod, kind=kind, layer=layer,
        responsibility=responsibility, responsibility_confidence=resp_conf,
        evidence=ev_obj, dependencies=deps, classification=classification,
        metrics=metrics, interface_symbols=mg.interfaces.get(mod, []),
        notes=[], overall_confidence=Confidence.weakest(role_conf, resp_conf),
    )


# ── cross-cutting parts ───────────────────────────────────────────────────────
def build_cross_cutting_parts(ev: EvidenceGraph, mg: ModuleGraph) -> list[Part]:
    parts: list[Part] = []
    parts.extend(_entrypoint_parts(ev))
    parts.extend(_application_parts(ev, mg))
    parts.extend(_external_dependency_parts(ev, mg))
    parts.extend(_domain_parts(ev))
    parts.extend(_data_schema_parts(ev, mg))
    parts.extend(_generated_parts(ev, mg))
    parts.extend(_operational_parts(ev))
    parts.extend(_documentation_part(ev))
    parts.extend(_unclassified_part(ev))
    return parts


def detect_entrypoints(ev: EvidenceGraph) -> list[tuple[str, str]]:
    """(path, kind) for every heuristically detected entry point, path-sorted."""
    out = []
    for f in ev.files:
        kind = classify.entrypoint_kind(f["path"], f.get("type"))
        if kind:
            out.append((f["path"], kind))
    out.sort()
    return out


def _entrypoint_parts(ev: EvidenceGraph) -> list[Part]:
    parts: list[Part] = []
    for path, kind in detect_entrypoints(ev):
        rec = ev.file_by_path.get(path, {})
        summary = ev.file_summaries.get(path, {})
        parts.append(Part(
            id=f"entry:{path}", name=path, kind="entrypoint",
            layer=classify.layer_of(path)[0],
            responsibility=f"Runtime entry point ({kind}).",
            responsibility_confidence=Confidence.STRONG,
            evidence=Evidence(
                files=[path],
                symbols=[c.get("symbol") for c in ev.symbols_of(path)
                         if c.get("symbol") and c["symbol"] != "<file>"][:20],
                graph_nodes=[rec.get("uri")] if rec.get("uri") else [],
                signals=[f"entry-point heuristic matched: {kind}",
                         f"language={rec.get('language')}"],
                llm_summaries=[summary["text"]] if summary.get("text") else [],
            ),
            dependencies=DepRef(
                outgoing=sorted({f"module:{_module_of(t)}"
                                 for t in ev.imports_out.get(path, [])}),
            ),
            classification=Classification(
                role="core", role_confidence=Confidence.STRONG,
                reusability="internal", risk="low",
            ),
            metrics={"kind": kind, "language": rec.get("language")},
            overall_confidence=Confidence.STRONG,
        ))
    return parts


def _application_parts(ev: EvidenceGraph, mg: ModuleGraph) -> list[Part]:
    """One application/service per directory that directly holds an entry point."""
    by_dir: dict[str, list[tuple[str, str]]] = {}
    for path, kind in detect_entrypoints(ev):
        by_dir.setdefault(_module_of(path), []).append((path, kind))

    parts: list[Part] = []
    for mod, eps in sorted(by_dir.items()):
        kinds = {k for _, k in eps}
        is_service = any(k in {"python_app", "js_index"} for k in kinds)
        pkind = "service" if is_service else "application"
        subtree = _subtree_files(ev, mod)
        summary = _representative_summary(ev, [p for p, _ in eps])
        parts.append(Part(
            id=f"app:{mod}", name=mod, kind=pkind,
            layer=classify.layer_of(mod + "/x")[0],
            responsibility=(f"{pkind.capitalize()} rooted at {mod}; "
                            f"entry points: {', '.join(sorted(p for p, _ in eps))}."),
            responsibility_confidence=Confidence.STRONG,
            evidence=Evidence(
                files=[p for p, _ in eps],
                signals=[f"contains entry point(s): {sorted(k for _, k in eps)}",
                         f"subtree size: {len(subtree)} files"],
                llm_summaries=[summary] if summary else [],
            ),
            dependencies=DepRef(),  # aggregated at report level; module edges carry detail
            classification=Classification(
                role="core", role_confidence=Confidence.STRONG,
                reusability="internal", risk="low",
            ),
            metrics={"entry_points": [p for p, _ in eps], "subtree_files": len(subtree)},
            overall_confidence=Confidence.STRONG,
        ))
    return parts


def _external_dependency_parts(ev: EvidenceGraph, mg: ModuleGraph) -> list[Part]:
    importers: dict[str, set[str]] = {}
    for path, pkgs in ev.external_imports.items():
        mod = mg.module_of_file.get(path, _module_of(path))
        for pkg in pkgs:
            importers.setdefault(pkg, set()).add(mod)
    parts: list[Part] = []
    for pkg, mods in sorted(importers.items()):
        parts.append(Part(
            id=f"ext:{pkg}", name=pkg, kind="external_dependency",
            layer="external",
            responsibility=f"Third-party/workspace dependency imported by {len(mods)} module(s).",
            responsibility_confidence=Confidence.CERTAIN,
            evidence=Evidence(
                signals=[f"imported by modules: {sorted(mods)[:12]}"],
                graph_edges=[f"importsExternal->{pkg}"],
            ),
            dependencies=DepRef(incoming=sorted(f"module:{m}" for m in mods)),
            classification=Classification(
                role="infrastructure", role_confidence=Confidence.STRONG,
                reusability="external",
                risk="elevated" if len(mods) >= 8 else "low",
                risk_reasons=(["widely depended-upon external package"]
                              if len(mods) >= 8 else []),
            ),
            metrics={"importer_modules": len(mods)},
            overall_confidence=Confidence.CERTAIN,
        ))
    return parts


def _domain_parts(ev: EvidenceGraph) -> list[Part]:
    """Semantic domains from the curated concept vocabulary's ``broader``
    collections. Interpretive by nature → PROBABLE, and empty when the bundle
    carries no typed concepts (older/leaner builds)."""
    parts: list[Part] = []
    for collection, members in sorted(ev.collections.items()):
        # Files that lexicalize these concepts = the domain's material footprint.
        member_set = set(members)
        files = sorted({
            p for p, cs in ev.per_path_concepts.items()
            if member_set & set(cs)
        })
        parts.append(Part(
            id=f"domain:{collection}", name=collection, kind="domain",
            responsibility=(f"Semantic domain grouping {len(members)} concept(s) "
                            f"from the curated vocabulary."),
            responsibility_confidence=Confidence.PROBABLE,
            evidence=Evidence(
                files=files[:60],
                symbols=members[:40],
                signals=[f"skos broader collection with {len(members)} concepts",
                         f"lexicalized across {len(files)} files"],
            ),
            classification=Classification(
                role="supporting", role_confidence=Confidence.PROBABLE,
                reusability="reusable" if len(files) >= 10 else "internal",
                risk="low",
            ),
            metrics={"n_concepts": len(members), "n_files": len(files)},
            overall_confidence=Confidence.PROBABLE,
        ))
    return parts


def _data_schema_parts(ev: EvidenceGraph, mg: ModuleGraph) -> list[Part]:
    """Data/serialization contracts.

    Primary evidence: files the L4 layer tagged with a ``schemaPurpose`` (an LLM
    signal → PROBABLE). Structural corroboration: file type ``data`` for schema-
    shaped extensions.
    """
    seen: set[str] = set()
    parts: list[Part] = []
    for path, purpose in sorted(ev.schema_purposes.items()):
        if path in seen:
            continue
        seen.add(path)
        rec = ev.file_by_path.get(path, {})
        parts.append(Part(
            id=f"schema:{path}", name=path, kind="data_schema",
            layer=classify.layer_of(path)[0],
            responsibility="Data/serialization contract (schema).",
            responsibility_confidence=Confidence.PROBABLE,
            evidence=Evidence(
                files=[path],
                graph_nodes=[rec.get("uri")] if rec.get("uri") else [],
                signals=[f"L4 schemaPurpose present; type={rec.get('type')}"],
                llm_summaries=[purpose["text"]] if purpose.get("text") else [],
            ),
            classification=Classification(
                role="supporting", role_confidence=Confidence.PROBABLE,
                reusability="reusable", risk="low",
            ),
            metrics={"type": rec.get("type"), "language": rec.get("language")},
            overall_confidence=Confidence.PROBABLE,
        ))
    return parts


def _generated_parts(ev: EvidenceGraph, mg: ModuleGraph) -> list[Part]:
    parts: list[Part] = []
    for f in ev.files:
        if f.get("type") != "generated":
            continue
        path = f["path"]
        ca = len(ev.imports_in.get(path, []))
        parts.append(Part(
            id=f"file:{path}", name=path, kind="generated_artifact",
            layer=classify.layer_of(path)[0],
            responsibility="Generated/vendored artifact; must not drive architecture.",
            responsibility_confidence=Confidence.CERTAIN,
            evidence=Evidence(
                files=[path],
                graph_nodes=[f.get("uri")] if f.get("uri") else [],
                signals=[f"file type classified as generated; fan-in={ca}"],
            ),
            classification=Classification(
                role="generated", role_confidence=Confidence.CERTAIN,
                reusability="replaceable",
                risk="elevated" if ca > 0 else "low",
                risk_reasons=(["generated artifact has inbound dependencies"]
                              if ca > 0 else []),
            ),
            metrics={"ca": ca},
            overall_confidence=Confidence.CERTAIN,
        ))
    return parts


# Operational categories, keyed by the extractor's *certain* file-type facts.
# Each category becomes one ``operational`` part so build/CI/deploy/runtime-env
# concerns are first-class parts even when their files live outside any
# code-bearing module (e.g. a root Dockerfile).
_OPERATIONAL_CATEGORIES: list[tuple[str, frozenset[str], str]] = [
    ("build_system", frozenset({"build_script"}),
     "Build orchestration (make/gradle/cmake-style entry tasks)."),
    ("dependency_management", frozenset({"dependency_manifest", "lockfile"}),
     "Dependency declaration and version pinning."),
    ("ci_cd", frozenset({"ci_cd"}),
     "Continuous integration / delivery pipeline definitions."),
    ("deployment", frozenset({"container"}),
     "Container / deployment topology definitions."),
    ("runtime_configuration", frozenset({"configuration", "environment"}),
     "Runtime and tooling configuration, environment variables."),
    ("licensing", frozenset({"license"}),
     "License and legal notices."),
]

# File types owned by non-operational builders (code modules, docs part,
# generated-artifact parts). Anything outside these and the operational
# categories falls into an explicit catch-all part — the coverage invariant:
# no repository file may be silently absent from every part.
_NON_OPERATIONAL_TYPES = frozenset({
    "source_code", "test_code", "documentation", "generated",
})


def _unclassified_part(ev: EvidenceGraph) -> list[Part]:
    """Catch-all for file types no builder claims (coverage invariant)."""
    covered = _NON_OPERATIONAL_TYPES | {
        t for _, types, _ in _OPERATIONAL_CATEGORIES for t in types
    }
    files = sorted(f["path"] for f in ev.files if f.get("type") not in covered)
    if not files:
        return []
    types = sorted({str(ev.file_by_path[p].get("type")) for p in files})
    return [Part(
        id="ops:unclassified_files", name="unclassified_files",
        kind="operational", layer="operational",
        responsibility=("Files whose graph type matches no structural or "
                        "operational category; carried so the decomposition "
                        "accounts for every repository file."),
        responsibility_confidence=Confidence.CERTAIN,
        evidence=Evidence(
            files=files,
            signals=[f"{len(files)} file(s) of uncategorized type(s) {types}"],
        ),
        classification=Classification(
            role="supporting", role_confidence=Confidence.WEAK,
            reusability="internal", risk="low",
        ),
        metrics={"n_files": len(files), "file_types": types},
        overall_confidence=Confidence.CERTAIN,
    )]


def _operational_parts(ev: EvidenceGraph) -> list[Part]:
    parts: list[Part] = []
    for name, types, responsibility in _OPERATIONAL_CATEGORIES:
        files = sorted(f["path"] for f in ev.files if f.get("type") in types)
        if not files:
            continue
        parts.append(Part(
            id=f"ops:{name}", name=name, kind="operational",
            layer="operational",
            responsibility=responsibility,
            responsibility_confidence=Confidence.STRONG,
            evidence=Evidence(
                files=files,
                signals=[f"{len(files)} file(s) of type {sorted(types)}"],
            ),
            classification=Classification(
                role="infrastructure", role_confidence=Confidence.CERTAIN,
                reusability="internal", risk="low",
            ),
            metrics={"n_files": len(files), "file_types": sorted(types)},
            overall_confidence=Confidence.CERTAIN,
        ))
    return parts


def _documentation_part(ev: EvidenceGraph) -> list[Part]:
    files = sorted(f["path"] for f in ev.files if f.get("type") == "documentation")
    if not files:
        return []
    return [Part(
        id="docs:documentation", name="documentation", kind="documentation",
        layer="operational",
        responsibility="Project documentation (READMEs, guides, specs).",
        responsibility_confidence=Confidence.CERTAIN,
        evidence=Evidence(
            files=files,
            signals=[f"{len(files)} documentation file(s)"],
        ),
        classification=Classification(
            role="supporting", role_confidence=Confidence.CERTAIN,
            reusability="internal", risk="low",
        ),
        metrics={"n_files": len(files)},
        overall_confidence=Confidence.CERTAIN,
    )]


# ── helpers ───────────────────────────────────────────────────────────────────
def file_edges_between(
    ev: EvidenceGraph, mg: ModuleGraph, src_mod: str, dst_mod: str, limit: int = 3,
) -> list[str]:
    """The file-level import edges inducing the aggregated ``src_mod -> dst_mod``
    module edge, as ``"src.py -> dst.py"`` strings. This is the evidence every
    cycle/coupling finding must cite: module-level claims are only as good as
    the file edges underneath them."""
    out: list[str] = []
    for src in sorted(ev.imports_out):
        if mg.module_of_file.get(src) != src_mod:
            continue
        for dst in sorted(ev.imports_out[src]):
            if mg.module_of_file.get(dst) == dst_mod:
                out.append(f"{src} -> {dst}")
                if len(out) >= limit:
                    return out
    return out


def _subtree_files(ev: EvidenceGraph, root: str) -> list[str]:
    prefix = "" if root == ROOT else root + "/"
    return [f["path"] for f in ev.files
            if root == ROOT or f["path"] == root or f["path"].startswith(prefix)]


def _module_concepts(ev: EvidenceGraph, code_files: list[str]) -> list[str]:
    from collections import Counter
    c: Counter[str] = Counter()
    for p in code_files:
        for name in ev.per_path_concepts.get(p, []):
            c[name] += 1
    # Prefer typed concepts (domain/structural primitives) as more meaningful.
    ranked = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))
    typed = [n for n, _ in ranked if ev.concepts.get(n, {}).get("kind")]
    plain = [n for n, _ in ranked if not ev.concepts.get(n, {}).get("kind")]
    return (typed[:6] + plain[:6])[:8]


def _module_responsibility(
    ev: EvidenceGraph, mod: str, role: str, concepts: list[str], code_files: list[str],
) -> tuple[str, Confidence]:
    if concepts:
        return (f"{role} module. Dominant concepts: {', '.join(concepts)}.",
                Confidence.PROBABLE)
    return (f"{role} module ({len(code_files)} code files).", Confidence.WEAK)


def _symbol_inventory(ev: EvidenceGraph, code_files: list[str]) -> list[str]:
    """Full (uncapped) symbol inventory of a module: ``file:symbol (kind)``.

    The complete inventory is what makes the decomposition usable as a mapping
    artifact — a silent sample here would masquerade as coverage (no-silent-caps
    rule). Order is (file, first line): the reading order of the module.
    """
    syms: list[str] = []
    for p in code_files:
        chunks = sorted(
            (ev.chunks[i] for i in ev.chunks_by_file.get(p, [])),
            key=lambda c: (c.get("beginLine") or 0, str(c.get("symbol"))),
        )
        for c in chunks:
            s = c.get("symbol")
            if s and s != "<file>":
                kind = c.get("kind") or "symbol"
                syms.append(f"{PurePosixPath(p).name}:{s} ({kind})")
    return syms


def _module_edge_descriptors(mg: ModuleGraph, mod: str) -> list[str]:
    out = [f"imports {mod}->{d} (x{mg.edge_weight.get((mod, d), 0)})"
           for d in mg.adjacency.get(mod, [])]
    return out[:20]


def _module_signals(
    ev: EvidenceGraph, mod: str, files: list[str], code_files: list[str],
    phases: list[str], ca: int, ce: int, in_cycle: bool,
) -> list[str]:
    sig = [
        f"{len(code_files)} code files, {len(files)} total files",
        f"afferent(Ca)={ca}, efferent(Ce)={ce}",
        f"phases={phases or ['<none>']}",
    ]
    if in_cycle:
        sig.append("participates in a module-level import cycle")
    return sig


def _module_llm_summaries(ev: EvidenceGraph, code_files: list[str]) -> list[str]:
    out: list[str] = []
    for p in code_files:
        s = ev.file_summaries.get(p, {})
        if s.get("text"):
            out.append(f"{PurePosixPath(p).name}: {s['text']}")
        if len(out) >= 3:
            break
    return out


def _representative_summary(ev: EvidenceGraph, paths: list[str]) -> str | None:
    for p in paths:
        s = ev.file_summaries.get(p, {})
        if s.get("text"):
            return s["text"]
    return None
