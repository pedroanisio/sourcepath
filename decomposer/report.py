"""Human-readable Markdown decomposition report.

A concise executive read over the same data the YAML carries: architecture,
parts grouped by role, the coupling/instability leaderboard, quality findings,
and the reconstruction build order. Every interpretive number is shown next to
its confidence so a reader never mistakes a hypothesis for a fact.

The document carries the mandatory disclaimer frontmatter (operator-approved
evidence-basis override): deterministic graph facts are not "hallucinations",
so the banner *splits* the disclosure rather than blanket-flagging everything.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .model import Confidence, Decomposition

_CONF_ICON = {
    "certain": "🟢 certain", "strong": "🔵 strong", "probable": "🟡 probable",
    "weak": "🟠 weak", "unknown": "⚪ unknown",
}


def to_markdown(decomp: Decomposition) -> str:
    r = decomp.repository
    L: list[str] = []
    _frontmatter(L, decomp)

    L.append(f"# Repository Decomposition — {r.get('name')}\n")
    L.append("## Evidence basis & confidence\n")
    L.append(
        "> Structural parts, dependency edges, and coupling metrics (Ca/Ce, "
        "Martin instability), cycles, and build order are **mechanically "
        "derived** from the codebase-mapper graph and are evidence-backed. "
        "Roles, layers, domains, responsibilities, and architecture style are "
        "**interpretive**, confidence-tagged, and to be validated before "
        "high-stakes decisions. LLM-authored text is surfaced only as "
        "*unverified* evidence.\n")
    L.append("Confidence ladder: 🟢 certain > 🔵 strong > 🟡 probable > 🟠 weak > ⚪ unknown.\n")

    _overview(L, decomp)
    _architecture(L, decomp)
    _parts_by_role(L, decomp)
    _coupling_leaderboard(L, decomp)
    _domains_and_externals(L, decomp)
    _quality(L, decomp)
    _build_order(L, decomp)
    _legend(L)
    return "\n".join(L) + "\n"


def _frontmatter(L: list[str], decomp: Decomposition) -> None:
    date = (decomp.repository.get("generated_at") or "")[:10]
    L.append("---")
    L.append("disclaimer:")
    L.append("  notice: >-")
    L.append("    Evidence basis & confidence — structural findings are mechanically extracted")
    L.append("    and evidence-backed; roles, layers, domains, and architecture are LLM/heuristic")
    L.append("    interpretations, confidence-tagged, and to be validated before high-stakes")
    L.append("    decisions. No interpretive statement should be taken for granted.")
    L.append(f'  generated_by: "{decomp.provenance.get("tool", "decomposer")}"')
    L.append(f'  date: "{date}"')
    L.append("---\n")


def _overview(L: list[str], decomp: Decomposition) -> None:
    r = decomp.repository
    prov = decomp.provenance
    inp = prov.get("inputs", {})
    L.append("## Overview\n")
    L.append(f"- **Repository:** {r.get('name')} @ `{(r.get('commit_sha') or '')[:12]}` "
             f"(tool {r.get('tool_version')})")
    exts = ", ".join(prov.get("bundle_extensions") or []) or "none"
    L.append(f"- **Bundle:** `{prov.get('bundle_dir')}` · "
             f"run_manifest sha256 `{(prov.get('run_manifest_sha256') or '')[:12]}` · "
             f"generated {prov.get('bundle_generated_at')} · extensions: {exts}")
    L.append(f"- **Files analyzed:** {r.get('files')} · "
             f"internal import edges: {inp.get('internal_import_edges')} · "
             f"xref edges: {inp.get('xref_edges')} · chunks: {inp.get('chunks')}")
    L.append(f"- **Parts decomposed:** {r.get('n_parts')} · "
             f"file-level import cycles: {r.get('n_file_cycles')} · "
             f"directory-level cycles: {r.get('n_module_cycles')}")
    L.append(f"- **Evidence available:** concepts={inp.get('concepts')}, "
             f"LLM summaries={inp.get('llm_file_summaries')}, "
             f"phases={inp.get('phases_available')}")
    L.append(f"- **Purpose** ({_CONF_ICON.get(r.get('purpose_confidence'))}): "
             f"{r.get('purpose')}\n")


def _architecture(L: list[str], decomp: Decomposition) -> None:
    a = decomp.detected_architecture
    L.append("## Detected architecture\n")
    L.append(f"**Style:** {a.style}  ({_CONF_ICON.get(a.confidence.value)})\n")
    if a.evidence:
        L.append("Evidence:")
        for e in a.evidence:
            L.append(f"- {e}")
        L.append("")
    if a.hypotheses:
        L.append("Candidate styles (hypotheses):")
        for h in a.hypotheses:
            L.append(f"- {_CONF_ICON.get(h.confidence.value)} — {h.statement}")
        L.append("")
    if a.violations:
        L.append("**Architecture violations:**")
        for v in a.violations:
            L.append(f"- {_CONF_ICON.get(v.confidence.value)} — "
                     f"`{v.kind}`: {v.description}")
        L.append("")


def _parts_by_role(L: list[str], decomp: Decomposition) -> None:
    L.append("## Parts by role\n")
    by_role: dict[str, list] = defaultdict(list)
    for p in decomp.parts:
        # Semantic domains are interpretive overlays, not structural parts;
        # they render in their own section, never in the parts inventory.
        if p.kind == "domain":
            continue
        by_role[p.classification.role].append(p)
    kind_counts = Counter(p.kind for p in decomp.parts)
    L.append("Part kinds: " + ", ".join(f"{k}×{v}" for k, v in sorted(kind_counts.items())) + "\n")

    for role in ("core", "adapter", "infrastructure", "supporting", "test", "generated"):
        group = by_role.get(role, [])
        if not group:
            continue
        L.append(f"### {role} ({len(group)})\n")
        L.append("| part | kind | layer | Ca | Ce | instability | reuse | risk | role conf |")
        L.append("|---|---|---|---:|---:|---:|---|---|---|")
        for p in sorted(group, key=_part_sort_key)[:40]:
            m = p.metrics
            inst = m.get("instability")
            L.append(
                f"| `{p.id}` | {p.kind} | {p.layer or '—'} | "
                f"{m.get('ca', '—')} | {m.get('ce', '—')} | "
                f"{inst if inst is not None else '—'} | "
                f"{p.classification.reusability} | {p.classification.risk} | "
                f"{_CONF_ICON.get(p.classification.role_confidence.value)} |")
        if len(group) > 40:
            L.append(f"| … {len(group) - 40} more | | | | | | | | |")
        L.append("")


def _coupling_leaderboard(L: list[str], decomp: Decomposition) -> None:
    mods = [p for p in decomp.parts if p.kind in {"module", "package"}]
    if not mods:
        return
    L.append("## Coupling & stability leaderboard (modules)\n")
    central = sorted(mods, key=lambda p: -(p.metrics.get("ca", 0) + p.metrics.get("ce", 0)))[:12]
    L.append("Most-connected modules (Ca+Ce):\n")
    L.append("| module | Ca | Ce | I | interface symbols |")
    L.append("|---|---:|---:|---:|---|")
    for p in central:
        m = p.metrics
        iface = ", ".join(p.interface_symbols[:5]) or "—"
        L.append(f"| `{p.name}` | {m.get('ca')} | {m.get('ce')} | "
                 f"{m.get('instability')} | {iface} |")
    L.append("")


def _domains_and_externals(L: list[str], decomp: Decomposition) -> None:
    domains = [p for p in decomp.parts if p.kind == "domain"]
    externals = [p for p in decomp.parts if p.kind == "external_dependency"]
    if domains:
        L.append("## Semantic domains (interpretive)\n")
        for p in sorted(domains, key=lambda p: -p.metrics.get("n_files", 0)):
            L.append(f"- {_CONF_ICON.get(p.overall_confidence.value)} **{p.name}** — "
                     f"{p.metrics.get('n_concepts')} concepts across "
                     f"{p.metrics.get('n_files')} files")
        L.append("")
    if externals:
        L.append("## External dependencies\n")
        top = sorted(externals, key=lambda p: -p.metrics.get("importer_modules", 0))[:15]
        L.append("| package | importer modules | risk |")
        L.append("|---|---:|---|")
        for p in top:
            L.append(f"| `{p.name}` | {p.metrics.get('importer_modules')} | {p.classification.risk} |")
        L.append(f"\n_Total external dependencies: {len(externals)}._\n")


def _quality(L: list[str], decomp: Decomposition) -> None:
    L.append("## Quality gates\n")
    if not decomp.quality_gates:
        L.append("No quality-gate findings.\n")
        return
    by_gate: dict[str, list] = defaultdict(list)
    for q in decomp.quality_gates:
        by_gate[q.gate].append(q)
    L.append("| gate | findings | worst severity | examples |")
    L.append("|---|---:|---|---|")
    sev_rank = {"error": 0, "warning": 1, "info": 2}
    for gate in sorted(by_gate, key=lambda g: min(sev_rank.get(x.severity, 3) for x in by_gate[g])):
        items = by_gate[gate]
        worst = min(items, key=lambda x: sev_rank.get(x.severity, 3)).severity
        examples = ", ".join(_truncate(q.subject, 48) for q in items[:2])
        L.append(f"| `{gate}` | {len(items)} | {worst} | {examples} |")
    L.append("")
    # Detail the error/warning findings.
    serious = [q for q in decomp.quality_gates if q.severity in {"error", "warning"}]
    if serious:
        L.append("### Notable findings\n")
        for q in sorted(serious, key=lambda x: sev_rank.get(x.severity, 3))[:30]:
            L.append(f"- **[{q.severity}]** {_CONF_ICON.get(q.confidence.value)} "
                     f"`{q.gate}` — {q.description}")
        L.append("")


def _build_order(L: list[str], decomp: Decomposition) -> None:
    L.append("## Reconstruction build order (module layers)\n")
    L.append("_Layer 0 has no internal dependencies (build first); each later "
             "layer depends only on earlier ones. Directory-cycle groups appear "
             "in braces (`{a ⇄ b}`): their members must be built jointly._\n")
    cycle_of: dict[str, int] = {}
    cycles = decomp.provenance.get("module_cycles") or []
    for ci, cyc in enumerate(cycles):
        for m in cyc:
            cycle_of[m] = ci
    for i, layer in enumerate(decomp.build_order):
        names = [pid.split(":", 1)[1] for pid in layer]
        rendered: list[str] = []
        done_groups: set[int] = set()
        for n in names:
            ci = cycle_of.get(n)
            if ci is None:
                rendered.append(n)
            elif ci not in done_groups:
                done_groups.add(ci)
                members = [m for m in names if cycle_of.get(m) == ci]
                rendered.append("{" + " ⇄ ".join(members) + "}")
        L.append(f"- **Layer {i}** ({len(layer)}): {', '.join(rendered)}")
    L.append("")


def _legend(L: list[str]) -> None:
    L.append("---")
    L.append("_Ca = afferent coupling (fan-in); Ce = efferent coupling (fan-out); "
             "I = Ce/(Ca+Ce), Martin instability (0=maximally stable, 1=maximally "
             "unstable)._\n")
    L.append("_reuse: `reusable` = high fan-in with low fan-out (shared surface); "
             "`internal` = consumed within its own area; `replaceable` = no inbound "
             "importers; `external` = third-party. risk: `low`/`elevated`/`high`, "
             "from cycle participation, god-module shape (Ca≥6 and Ce≥6), and high "
             "fan-in on unstable modules. layer: a directory-naming hypothesis "
             "(`probable`), never proven by the graph._\n")
    L.append("_confidence mapping to the ABox vocabulary: certain/strong ≈ High, "
             "probable ≈ Medium, weak ≈ Low, unknown ≈ Unknown._\n")
    L.append("_Modules == directories that directly contain code files (a `probable` "
             "model; files in subdirectories belong to their own module). Interface "
             "symbols are cross-module xref targets — leading-underscore names there "
             "mean private symbols are consumed across module boundaries, itself a "
             "coupling signal; when no symbol xrefs cross in, imported file basenames "
             "are listed instead. This report is produced by the codebase-mapper "
             "Decomposer._")


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _part_sort_key(p):
    m = p.metrics
    return (-(m.get("ca", 0) + m.get("ce", 0)), p.name)
