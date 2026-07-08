"""Architecture-style detection and violations.

Style detection is signal-scored: each recognizable structural signal (a
``plugins/`` tree, DDD layer names, a web surface) contributes evidence, the
best-supported style becomes ``detected_architecture.style``, and every
candidate is also emitted as a confidence-tagged hypothesis. Nothing here is
asserted as fact beyond what the graph shows; interpretive leaps are ``probable``.
"""
from __future__ import annotations

from pathlib import PurePosixPath

from .evidence import EvidenceGraph
from .model import Architecture, Confidence, Hypothesis, Violation
from .parts import ModuleGraph, ROOT


def detect_architecture(
    ev: EvidenceGraph, mg: ModuleGraph, module_cycles: list[list[str]]
) -> Architecture:
    top_segments = {_top(m) for m in mg.modules() if m != ROOT}
    all_segments = {seg for m in mg.modules() for seg in PurePosixPath(m).parts}

    hypotheses: list[Hypothesis] = []
    labels: list[tuple[str, Confidence]] = []   # short style tag per hypothesis
    evidence: list[str] = []

    # ── signal: plugin / microkernel ────────────────────────────────────────
    has_plugins = "plugins" in top_segments or "plugins" in all_segments
    has_registry = any(
        f["path"].endswith("extensions.py") or "register_all" in (f.get("path") or "")
        for f in ev.files
    )
    if has_plugins:
        conf = Confidence.STRONG if has_registry else Confidence.PROBABLE
        ev_list = ["top-level `plugins/` package present"]
        if has_registry:
            ev_list.append("extension-registry module (`extensions.py`) present")
        hypotheses.append(Hypothesis(
            "Plugin / microkernel architecture: a host core with pluggable "
            "extension layers.", conf, ev_list))
        labels.append(("plugin/microkernel", conf))
        evidence.extend(ev_list)

    # ── signal: layered / hexagonal (ports & adapters, DDD naming) ───────────
    ddd = all_segments & {"application", "infrastructure", "domain"}
    kernel = all_segments & {"shared_kernel", "kernel"}
    if len(ddd) >= 2 or (ddd and kernel):
        ev_list = [f"DDD layer directories present: {sorted(ddd)}"]
        if kernel:
            ev_list.append(f"shared-kernel package present: {sorted(kernel)}")
        hypotheses.append(Hypothesis(
            "Layered / hexagonal (ports & adapters) organization with an "
            "application/infrastructure split.", Confidence.STRONG, ev_list))
        labels.append(("layered/hexagonal", Confidence.STRONG))
        evidence.extend(ev_list)
    elif kernel:
        hypotheses.append(Hypothesis(
            "Shared-kernel + feature packages (partial DDD).",
            Confidence.PROBABLE, [f"shared-kernel package: {sorted(kernel)}"]))
        labels.append(("shared-kernel", Confidence.PROBABLE))

    # ── signal: multi-surface client/server ─────────────────────────────────
    surfaces = all_segments & {"frontend", "backend", "mcp_server", "ui", "server"}
    has_web = any(
        (f.get("type") == "source_code") and PurePosixPath(f["path"]).name in
        {"app.py", "server.py"} for f in ev.files
    )
    if len(surfaces) >= 2 and has_web:
        ev_list = [f"multiple delivery surfaces: {sorted(surfaces)}",
                   "web/app server entry point present"]
        hypotheses.append(Hypothesis(
            "Multi-surface client/server: one core exposed through several "
            "delivery adapters (HTTP API, MCP server, UI).",
            Confidence.STRONG, ev_list))
        labels.append(("multi-surface client/server", Confidence.STRONG))
        evidence.extend(ev_list)

    # ── choose dominant style ───────────────────────────────────────────────
    style, style_conf = _dominant(labels)

    violations = _violations(ev, mg, module_cycles)
    return Architecture(
        style=style, confidence=style_conf,
        evidence=_dedupe(evidence), violations=violations, hypotheses=hypotheses,
    )


def _dominant(labels: list[tuple[str, Confidence]]) -> tuple[str, Confidence]:
    """Pick the overall style label from the scored candidate tags.

    A single candidate becomes the style directly; multiple candidates compose
    into ``composite (a + b + c)``. Confidence is the strongest supporting
    signal (a well-evidenced microkernel doesn't get diluted by a weaker one).
    """
    if not labels:
        return "undetermined", Confidence.UNKNOWN
    best_conf = min((c for _, c in labels), key=lambda c: c.rank)
    tags = sorted({label for label, _ in labels})
    if len(tags) == 1:
        return tags[0], best_conf
    return f"composite ({' + '.join(tags)})", best_conf


def _violations(
    ev: EvidenceGraph, mg: ModuleGraph, module_cycles: list[list[str]]
) -> list[Violation]:
    out: list[Violation] = []

    for cyc in module_cycles:
        out.append(Violation(
            kind="circular_dependency",
            description=f"Import cycle across {len(cyc)} modules: {' -> '.join(cyc)} -> {cyc[0]}.",
            confidence=Confidence.CERTAIN, subjects=[f"module:{m}" for m in cyc],
        ))

    # Shared-kernel independence: a kernel package should not import outward into
    # sibling feature packages. Any such edge is a graph fact (CERTAIN) and a
    # recognized anti-pattern; the repo's own import-linter encodes the same rule.
    for m in mg.modules():
        segs = set(PurePosixPath(m).parts)
        if not (segs & {"shared_kernel", "kernel"}):
            continue
        kernel_top = _top(m)
        outward = [d for d in mg.adjacency.get(m, [])
                   if _top(d) != kernel_top]
        if outward:
            out.append(Violation(
                kind="shared_kernel_not_independent",
                description=(f"Shared-kernel module `{m}` imports outward into "
                             f"{outward}, coupling the kernel to feature packages."),
                confidence=Confidence.CERTAIN,
                subjects=[f"module:{m}"] + [f"module:{d}" for d in outward],
            ))

    # Bidirectional module coupling (two-way import between packages).
    seen: set[frozenset[str]] = set()
    for (a, b) in mg.edge_weight:
        if (b, a) in mg.edge_weight and frozenset({a, b}) not in seen:
            seen.add(frozenset({a, b}))
            out.append(Violation(
                kind="bidirectional_coupling",
                description=f"Modules `{a}` and `{b}` import each other.",
                confidence=Confidence.CERTAIN,
                subjects=[f"module:{a}", f"module:{b}"],
            ))
    return out


def _top(path: str) -> str:
    return PurePosixPath(path).parts[0] if path and path != ROOT else ROOT


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for x in items:
        if x not in out:
            out.append(x)
    return out
