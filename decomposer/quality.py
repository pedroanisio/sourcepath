"""Part IV quality gates.

Each gate scans the decomposition for a specific structural hazard and emits
:class:`QualityFinding` records. Confidence is honest about method: a cycle is
``certain`` (graph fact); a dead-code candidate is ``probable`` because dynamic
imports and reflection are invisible to static extraction (the plugin registry
in this very codebase loads modules by name at runtime).
"""
from __future__ import annotations

from pathlib import PurePosixPath

from .evidence import EvidenceGraph
from .model import Confidence, QualityFinding
from .parts import ModuleGraph, ROOT, detect_entrypoints, file_edges_between

# Thresholds — named so they are auditable and tunable.
GOD_CA = 6          # afferent coupling at/above which a module is "widely used"
GOD_CE = 6          # efferent coupling at/above which it is "widely dependent"
WIDE_EXTERNAL = 8   # importer count making an external dep a concentration risk
DUP_JACCARD = 0.6   # concept-set overlap flagged as duplicated responsibility

# Languages with a testing convention the test-gap gate can meaningfully probe.
# Files typed source_code but outside this set (shell wrappers with no language,
# .proto contracts, ...) produce only false-positive "untested" noise.
TESTABLE_LANGUAGES = frozenset({
    "python", "typescript", "javascript", "rust", "go", "java", "kotlin",
    "swift", "dart", "ruby", "c", "cpp", "objective-c", "clojure",
})


def run_gates(
    ev: EvidenceGraph, mg: ModuleGraph,
    module_cycles: list[list[str]], file_cycles: list[list[str]],
) -> list[QualityFinding]:
    out: list[QualityFinding] = []
    out += _circular(ev, mg, module_cycles, file_cycles)
    out += _god_modules(mg)
    out += _dead_code(ev, mg)
    out += _hidden_entrypoints(ev, mg)
    out += _duplicated_responsibilities(ev, mg)
    out += _generated_drives_arch(ev)
    out += _test_gaps(ev, mg)
    out += _missing_evidence(ev)
    out += _ambiguous_ownership(ev, mg)
    return out


def _circular(
    ev: EvidenceGraph, mg: ModuleGraph,
    module_cycles: list[list[str]], file_cycles: list[list[str]],
) -> list[QualityFinding]:
    """Cycles, granularity-qualified.

    File-level cycles are graph facts on extractor output: ``certain``, error.
    Directory-level cycles exist only under the module==directory aggregation —
    a self-declared ``probable`` model — so the finding's confidence is capped
    at the model's, its severity is warning, and the real inducing file edges
    are attached so one grep can adjudicate any of them. When the two
    granularities disagree (file DAG, directory cycles), that divergence is
    itself surfaced: consumers of one artifact must not quote either topology
    claim without its granularity.
    """
    out: list[QualityFinding] = []

    for cyc in file_cycles:
        members = set(cyc)
        edges = sorted(
            f"{a} -> {b}"
            for a in cyc for b in ev.imports_out.get(a, []) if b in members
        )
        out.append(QualityFinding(
            gate="circular_dependencies", severity="error",
            subject=", ".join(cyc),
            description=(f"File-level import cycle among {len(cyc)} files: "
                         + "; ".join(edges) + "."),
            confidence=Confidence.CERTAIN,
            evidence=edges,
        ))

    for cyc in module_cycles:
        members = set(cyc)
        module_edges = [
            (a, b, w) for (a, b), w in sorted(mg.edge_weight.items())
            if a in members and b in members
        ]
        edge_strs = [f"{a} -> {b} (x{w})" for a, b, w in module_edges]
        samples: list[str] = []
        for a, b, _ in module_edges:
            samples += file_edges_between(ev, mg, a, b, limit=2)
        out.append(QualityFinding(
            gate="directory_aggregation_cycle", severity="warning",
            subject=", ".join(f"module:{m}" for m in cyc),
            description=(
                f"{len(cyc)} directories are mutually reachable once file "
                f"imports are aggregated to directories (module==directory is "
                f"a `probable` model). Inducing module edges: "
                + "; ".join(edge_strs) + "."),
            confidence=Confidence.PROBABLE,
            evidence=edge_strs + samples,
        ))

    if module_cycles and not file_cycles:
        out.append(QualityFinding(
            gate="granularity_divergence", severity="info",
            subject="import-graph-topology",
            description=(
                f"The file-level import graph is acyclic (a DAG), while "
                f"{len(module_cycles)} cycle(s) appear at directory "
                f"granularity. Both statements are true at their own "
                f"granularity; neither may be quoted without it."),
            confidence=Confidence.CERTAIN,
            evidence=[f"file_cycles=0", f"directory_cycles={len(module_cycles)}"],
        ))
    return out


def _god_modules(mg: ModuleGraph) -> list[QualityFinding]:
    out: list[QualityFinding] = []
    for m in mg.modules():
        ca, ce = mg.ca.get(m, 0), mg.ce.get(m, 0)
        if ca >= GOD_CA and ce >= GOD_CE:
            out.append(QualityFinding(
                gate="god_module", severity="warning", subject=f"module:{m}",
                description=(f"Module `{m}` has high fan-in (Ca={ca}) and high "
                             f"fan-out (Ce={ce}); it is both widely used and "
                             f"widely dependent, a change-amplification hub."),
                confidence=Confidence.STRONG,
                evidence=[f"Ca={ca}", f"Ce={ce}"],
            ))
    return out


def _dead_code(ev: EvidenceGraph, mg: ModuleGraph) -> list[QualityFinding]:
    entry = {p for p, _ in detect_entrypoints(ev)}
    out: list[QualityFinding] = []
    for f in ev.files:
        path = f["path"]
        if f.get("type") != "source_code":
            continue
        name = PurePosixPath(path).name
        if name in {"__init__.py", "__main__.py", "conftest.py"} or path in entry:
            continue
        if ev.imports_in.get(path):
            continue
        # 0 internal importers, not an entry point: candidate — but plugins and
        # reflective loaders defeat static reachability, so PROBABLE at most.
        xref_targeted = any(
            ev.chunks[e["dst_idx"]].get("file") == path for e in ev.xrefs
        )
        conf = Confidence.WEAK if xref_targeted else Confidence.PROBABLE
        out.append(QualityFinding(
            gate="dead_code_candidate", severity="info", subject=path,
            description=(f"Source file `{path}` has no internal importers and is "
                         f"not a detected entry point; possibly unused "
                         f"(caveat: dynamic/plugin loading is invisible here)."),
            confidence=conf,
            evidence=[f"imports_in=0", f"xref_targeted={xref_targeted}"],
        ))
    return out


def _hidden_entrypoints(ev: EvidenceGraph, mg: ModuleGraph) -> list[QualityFinding]:
    detected = {p for p, _ in detect_entrypoints(ev)}
    out: list[QualityFinding] = []
    for f in ev.files:
        path = f["path"]
        if f.get("type") != "source_code" or path in detected:
            continue
        phases = ev.phases.get(path, [])
        if "runtime" in phases and not ev.imports_in.get(path) \
                and ev.imports_out.get(path):
            out.append(QualityFinding(
                gate="hidden_entrypoint", severity="info", subject=path,
                description=(f"`{path}` runs at runtime, imports others, yet is "
                             f"imported by nothing and matches no entry-point "
                             f"name — it may be an unregistered entry point."),
                confidence=Confidence.WEAK,
                evidence=[f"phases={phases}", "imports_in=0", "imports_out>0"],
            ))
    return out


def _duplicated_responsibilities(ev: EvidenceGraph, mg: ModuleGraph) -> list[QualityFinding]:
    # Concept-set per module (from typed/plain concepts of its code files).
    sets: dict[str, set[str]] = {}
    for m in mg.modules():
        cs: set[str] = set()
        for p in mg.files_of_module.get(m, []):
            cs |= set(ev.per_path_concepts.get(p, []))
        if len(cs) >= 5:
            sets[m] = cs
    mods = sorted(sets)
    out: list[QualityFinding] = []
    for i, a in enumerate(mods):
        for b in mods[i + 1:]:
            sa, sb = sets[a], sets[b]
            inter = len(sa & sb)
            union = len(sa | sb)
            if union and inter / union >= DUP_JACCARD:
                out.append(QualityFinding(
                    gate="duplicated_responsibility", severity="info",
                    subject=f"module:{a}, module:{b}",
                    description=(f"Modules `{a}` and `{b}` share {inter}/{union} "
                                f"concepts (Jaccard {inter/union:.2f}); their "
                                f"responsibilities may overlap."),
                    confidence=Confidence.WEAK,
                    evidence=[f"jaccard={inter/union:.2f}"],
                ))
    return out


def _generated_drives_arch(ev: EvidenceGraph) -> list[QualityFinding]:
    out: list[QualityFinding] = []
    for f in ev.files:
        if f.get("type") != "generated":
            continue
        ca = len(ev.imports_in.get(f["path"], []))
        if ca > 0:
            out.append(QualityFinding(
                gate="generated_drives_architecture", severity="warning",
                subject=f["path"],
                description=(f"Generated artifact `{f['path']}` has {ca} inbound "
                             f"dependencies; generated code should not be an "
                             f"architectural dependency."),
                confidence=Confidence.STRONG, evidence=[f"imports_in={ca}"],
            ))
    return out


def _test_gaps(ev: EvidenceGraph, mg: ModuleGraph) -> list[QualityFinding]:
    """Source modules with no discernible test coverage.

    Coverage proxy: a ``test_code`` file imports a file in the module, or a
    ``cbm:tests`` edge names one. Both signals are sparse (17 tests edges here),
    so absence is PROBABLE, not CERTAIN.
    """
    test_files = {f["path"] for f in ev.files if f.get("type") == "test_code"}
    covered: set[str] = set()
    for tf in test_files:
        for tgt in ev.imports_out.get(tf, []):
            covered.add(mg.module_of_file.get(tgt, ""))
    for subj in ev.tests_for_subject:  # subject path has ≥1 test edge
        covered.add(mg.module_of_file.get(subj, ""))

    out: list[QualityFinding] = []
    for m in mg.modules():
        code = [p for p in mg.files_of_module.get(m, [])
                if ev.file_by_path.get(p, {}).get("type") == "source_code"
                and ev.file_by_path.get(p, {}).get("language") in TESTABLE_LANGUAGES]
        if not code or m in covered:
            continue
        # Skip infra/config-only and root.
        if m == ROOT:
            continue
        out.append(QualityFinding(
            gate="test_gap", severity="warning", subject=f"module:{m}",
            description=(f"Source module `{m}` ({len(code)} files) has no test "
                         f"file importing it and no tests edge; likely untested."),
            confidence=Confidence.PROBABLE,
            evidence=[f"code_files={len(code)}", "no test importer", "no tests edge"],
        ))
    return out


def _missing_evidence(ev: EvidenceGraph) -> list[QualityFinding]:
    out: list[QualityFinding] = []
    unknown = [f["path"] for f in ev.files if f.get("type") == "unknown"]
    if unknown:
        out.append(QualityFinding(
            gate="missing_evidence", severity="info", subject="file-type=unknown",
            description=(f"{len(unknown)} files could not be typed by the "
                         f"extractor; their role is undetermined."),
            confidence=Confidence.CERTAIN, evidence=unknown[:20],
        ))
    no_symbols = [
        f["path"] for f in ev.files
        if f.get("type") == "source_code" and not ev.chunks_by_file.get(f["path"])
    ]
    if no_symbols:
        out.append(QualityFinding(
            gate="missing_evidence", severity="info", subject="source-without-symbols",
            description=(f"{len(no_symbols)} source files yielded no chunks/symbols "
                         f"(unparsed or empty); their internals are opaque."),
            confidence=Confidence.CERTAIN, evidence=no_symbols[:20],
        ))
    if not ev.concepts:
        out.append(QualityFinding(
            gate="missing_evidence", severity="info", subject="concept-layer",
            description=("Bundle carries no concept layer; semantic/domain parts "
                         "are unavailable and were skipped."),
            confidence=Confidence.CERTAIN, evidence=["concepts.json empty/absent"],
        ))
    if not ev.file_summaries:
        out.append(QualityFinding(
            gate="missing_evidence", severity="info", subject="llm-enrichment",
            description=("Bundle carries no LLM file summaries; responsibilities "
                         "rest on concept/naming evidence only."),
            confidence=Confidence.CERTAIN, evidence=["enrichments.jsonl empty/absent"],
        ))
    return out


def _ambiguous_ownership(ev: EvidenceGraph, mg: ModuleGraph) -> list[QualityFinding]:
    """Modules pulled in by ≥2 distinct top-level packages that are not obviously
    shared infrastructure — ownership is ambiguous (who is responsible?)."""
    out: list[QualityFinding] = []
    for m in mg.modules():
        tops = {PurePosixPath(imp).parts[0] for imp in mg.importers.get(m, []) if imp != ROOT}
        own_top = PurePosixPath(m).parts[0] if m != ROOT else ROOT
        external_tops = tops - {own_top}
        segs = set(PurePosixPath(m).parts)
        is_shared = bool(segs & {"shared_kernel", "kernel", "common", "shared"})
        if len(external_tops) >= 2 and not is_shared:
            out.append(QualityFinding(
                gate="ambiguous_ownership", severity="info", subject=f"module:{m}",
                description=(f"Module `{m}` is imported by {len(external_tops)} "
                             f"different top-level packages {sorted(external_tops)} "
                             f"yet is not a designated shared kernel; ownership is unclear."),
                confidence=Confidence.PROBABLE,
                evidence=[f"importing_top_packages={sorted(external_tops)}"],
            ))
    return out
