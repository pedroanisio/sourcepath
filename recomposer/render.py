"""Render a BuildPlan as a natural-language Markdown reconstruction guide.

The document is written to be executed top-to-bottom by a competent engineer or
an AI coding agent: each step states its goal, why it comes now, what to create,
which contracts to define, what it depends on, how to validate it, and what the
world looks like afterward. Assumptions are surfaced on the step that carries
them — never buried.
"""
from __future__ import annotations

from collections import defaultdict

from .model import PHASES, BuildPlan, BuildStep

_CONF_ICON = {
    "certain": "🟢 certain", "strong": "🔵 strong", "probable": "🟡 probable",
    "weak": "🟠 weak", "unknown": "⚪ unknown",
}
_MAX_LIST = 12   # cap long file lists in prose; the YAML carries them in full


def to_markdown(plan: BuildPlan) -> str:
    r = plan.repository
    L: list[str] = []
    _frontmatter(L, plan)

    L.append(f"# Reconstruction Build Plan — {r.get('name')}\n")
    L.append("## What this plan is (and is not)\n")
    L.append(
        "This is an **architecture/build-order map**: it fixes *what exists, "
        "in what order, and why* — file inventories, module dependency "
        "topology, public contract names, and construction sequence. It does "
        "**not** carry file contents, API signatures, or behavior; it is meant "
        "to be executed alongside the original sources (or used standalone for "
        "onboarding and architecture review). Byte-exact restoration is a "
        "different tier entirely: the codebase-mapper bundle's blob store "
        "plus its `reconstruct` tool rebuild the tree exactly.\n")
    L.append("## How to use this plan\n")
    L.append(
        "Execute the steps in order. Every `requires` reference points to an "
        "earlier step — the ordering is dependency-safe by construction. Each "
        "file is *created* by exactly one step (its first owner); later steps "
        "that touch it say **modify/verify**. Structural facts (files, "
        "dependencies, build order) are mechanically derived from the original "
        "repository's graph; responsibilities and intent are interpretive and "
        "confidence-tagged. **Assumption blocks must be resolved against the "
        "original sources before or while executing their step.** The full "
        "machine-readable plan (complete, unclipped lists) is the companion "
        "YAML; per-module symbol inventories live in the decomposition's "
        "`evidence.symbols`.\n")

    _intent(L, plan)
    _skipped(L, plan)
    _unassigned(L, plan)

    by_phase: dict[int, list[BuildStep]] = defaultdict(list)
    for s in plan.steps:
        by_phase[s.phase].append(s)

    L.append("## Construction sequence\n")
    total = len(plan.steps)
    L.append(f"_{total} steps across {sum(1 for n, _, _ in PHASES if by_phase.get(n))} "
             f"active phases._\n")
    for n, _key, title in PHASES:
        steps = by_phase.get(n)
        if not steps:
            continue
        L.append(f"### Phase {n} — {title}\n")
        for s in steps:
            _step(L, s)

    _open_assumptions(L, plan)
    L.append("---")
    L.append(f"_Source: {plan.provenance.get('source_decomposition')} over "
             f"`{plan.provenance.get('source_bundle')}`. "
             f"{plan.provenance.get('consumes')}. Deterministic: "
             f"{plan.provenance.get('determinism')}._")
    return "\n".join(L) + "\n"


def _frontmatter(L: list[str], plan: BuildPlan) -> None:
    date = (plan.repository.get("generated_at") or "")[:10]
    L.append("---")
    L.append("disclaimer:")
    L.append("  notice: >-")
    L.append("    Evidence basis & confidence — file inventories, dependencies, and build order")
    L.append("    are mechanically extracted from the original repository's graph; step goals,")
    L.append("    rationale, and responsibilities are interpretive, confidence-tagged, and to be")
    L.append("    validated against the original sources. LLM-authored text is marked unverified.")
    L.append(f'  generated_by: "{plan.provenance.get("tool", "recomposer")}"')
    L.append(f'  date: "{date}"')
    L.append("---\n")


def _intent(L: list[str], plan: BuildPlan) -> None:
    ai = plan.architecture_intent
    L.append("## Architectural intent\n")
    L.append(f"Rebuild toward: **{ai.get('style')}** "
             f"({_CONF_ICON.get(ai.get('confidence'), ai.get('confidence'))}).\n")
    for h in ai.get("honor", []):
        L.append(f"- {h}")
    if ai.get("honor"):
        L.append("")
    bad = ai.get("known_violations_to_not_replicate_blindly", [])
    if bad:
        L.append("**Known defects in the original — do not replicate blindly** "
                 "(reproduce only if fidelity is the goal; otherwise fix and document):")
        for v in bad:
            L.append(f"- `{v.get('kind')}`: {v.get('description')}")
        L.append("")


def _skipped(L: list[str], plan: BuildPlan) -> None:
    if not plan.skipped_phases:
        return
    L.append("## Phases skipped (no evidence)\n")
    for s in plan.skipped_phases:
        L.append(f"- **{s['phase']}** — {s['reason']}")
    L.append("")


def _unassigned(L: list[str], plan: BuildPlan) -> None:
    if not plan.unassigned_files:
        return
    L.append(f"## Unassigned files ({len(plan.unassigned_files)})\n")
    L.append(
        "The decomposition carries these files, but no step above creates or "
        "modifies them. Listed explicitly so the omission is a decision, not "
        "an accident — resolve each before treating the plan as complete.\n")
    for u in plan.unassigned_files[:_MAX_LIST]:
        L.append(f"- `{u['path']}` — {u['reason']}")
    if len(plan.unassigned_files) > _MAX_LIST:
        L.append(f"- … (+{len(plan.unassigned_files) - _MAX_LIST} more; "
                 f"see YAML `unassigned_files`)")
    L.append("")


def _step(L: list[str], s: BuildStep) -> None:
    L.append(f"#### Step {s.number} — {s.goal}\n")
    L.append(f"{s.rationale}\n")
    if s.requires:
        L.append(f"- **Requires steps:** {', '.join(str(n) for n in s.requires)}")
    else:
        L.append("- **Requires steps:** none")
    create_label = "Create (in dependency order)" if s.creates_ordered else "Create"
    L.append(f"- **{create_label}:** {_clip([f'`{c}`' for c in s.creates])}")
    if s.modifies:
        L.append(f"- **Modify/verify (files owned by earlier steps):** "
                 f"{_clip([f'`{c}`' for c in s.modifies])}")
    if s.contracts:
        L.append(f"- **Contracts to define:** {_clip([f'`{c}`' for c in s.contracts])}")
    if s.dependencies_introduced:
        L.append(f"- **Dependencies introduced:** "
                 f"{_clip([_dep(d) for d in s.dependencies_introduced])}")
    L.append(f"- **Validate:** {_clip(s.tests_required, sep='; ')}")
    L.append(f"- **Evidence:** {_clip([f'`{e}`' if not e.startswith('LLM') else e for e in s.evidence], sep='; ')}")
    L.append(f"- **Expected result:** {s.expected_result}")
    L.append(f"- **Confidence:** {_CONF_ICON.get(s.confidence, s.confidence)}")
    if s.assumptions:
        L.append("- **⚠ Assumptions to resolve:**")
        for a in s.assumptions:
            L.append(f"  - {a}")
    L.append("")


def _open_assumptions(L: list[str], plan: BuildPlan) -> None:
    L.append("## Open assumptions (plan-wide)\n")
    if not plan.open_assumptions:
        L.append("None.\n")
        return
    for a in plan.open_assumptions:
        L.append(f"- {a}")
    L.append("")


def _dep(d: str) -> str:
    if d.startswith("module:"):
        return f"`{d.split(':', 1)[1]}`"
    return f"`{d}`"


def _clip(items: list[str], sep: str = ", ") -> str:
    if not items:
        return "—"
    if len(items) <= _MAX_LIST:
        return sep.join(items)
    return sep.join(items[:_MAX_LIST]) + f" … (+{len(items) - _MAX_LIST} more; see YAML)"
