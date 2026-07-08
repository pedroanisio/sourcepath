# Repository Recomposer

[⬆ back to project root](../README.md)

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## What it does

Second delivery of the Decomposer/Recomposer system. Consumes a **Decomposer
YAML document** — never the raw bundle or repository — and generates a
**Natural Description Build Plan**: an ordered, dependency-aware,
evidence-grounded sequence of natural-language construction steps.

**Positioning (deliberate):** the plan is an **architecture/build-order map**
— what exists, in what order, and why — to be executed alongside the original
sources, or used standalone for onboarding and architecture review. It does
not carry file contents or API signatures and therefore is *not* a source-free
rebuilder. That tier already exists elsewhere in this project: the bundle's
blob store plus `codebase_mapper`'s `reconstruct` tool restore the tree
byte-exactly, so a lossy signature-level middle tier would duplicate it,
worse. Full per-module symbol inventories (name + kind, uncapped) live in the
decomposition's `evidence.symbols` for consumers that want deeper structure.

```bash
# 1) produce a decomposition (first delivery)
python -m decomposer _tmp/<bundle> --yaml decomposition.yaml

# 2) recompose it into a build plan
python -m recomposer decomposition.yaml --plan buildplan.md --yaml buildplan.yaml
```

With no output flags, `python -m recomposer <yaml>` prints a phase-by-phase
summary.

## How the plan is scheduled

1. **Units.** Module/package parts are the unit of construction. Modules that
   are mutually dependent in the decomposition's own dependency edges (SCCs of
   `dependencies.outgoing`) merge into one **joint step**. When the
   decomposition's `cycle_resolutions` carries a file-level topological order
   for the group (directory cycles usually dissolve at file granularity), the
   joint step lists its files **in dependency order** instead of "build
   together". Cycle detection does *not* parse quality-gate findings, whose
   names/formats are reporting policy; the scheduler depends only on the data
   contract.
2. **Ownership.** Each file is created by exactly one step — its first owner in
   execution order; later steps that touch the same file declare
   `modifies`, so an executing agent never overwrites earlier output.
3. **Nominal phases.** Each unit gets a canonical Part III phase from its
   classification (domain→3, ports/shared-kernel→4, core/supporting→5,
   adapter/infrastructure→6, test→10); fixed steps (skeleton, environment,
   schemas, ops, validation, docs) take phases 1, 2, 3, 9, 11, 12.
4. **Phase relaxation.** Dependency evidence overrides canon: a dependency is
   never scheduled after its dependent (single pass in descending build-order
   layer, correct because dependencies sit at strictly lower layers in the
   SCC-condensed DAG).
5. **Invariant.** Every `requires` reference points to an earlier step; this is
   asserted at generation time (`ValueError` on violation), not assumed.

Each step carries: goal, rationale, required previous steps, files/components
to create, contracts to define, dependencies introduced, tests/validation
required, evidence (part ids + coupling metrics), expected result, a confidence
label (`certain`/`strong`/`probable`/`weak`/`unknown`), and explicit
**assumptions to resolve** (Part IV). Phases with no evidence are reported as
skipped with a reason — never silently.

Output is **byte-deterministic** for a given decomposition document.

## Epistemics (PALS's Law)

File inventories, dependency edges, and build order are mechanically derived
facts carried over from the Decomposer. Step goals, rationale, and
responsibilities are interpretive and confidence-tagged. LLM-authored text
appears only as evidence explicitly marked `LLM (unverified)`. Both emitted
documents (Markdown and YAML) carry an evidence-basis disclaimer banner.

## Layout

| file | responsibility |
|---|---|
| `model.py` | `BuildStep` / `BuildPlan` dataclasses, canonical 12-phase table |
| `plan.py` | scheduler: units, SCC merging, phase relaxation, ordering, requires |
| `render.py` | natural-language Markdown build plan |
| `serialize.py` | machine-readable YAML build plan (full file lists) |
| `cli.py` / `__main__.py` | `python -m recomposer` |

Tests: `tests/recomposer/` (synthetic scheduler tests + end-to-end contract
tests against a real bundle decomposition).

---
*AI-generated (Claude Fable 5 via Claude Code); reviewed under the project's
verification rules.*
