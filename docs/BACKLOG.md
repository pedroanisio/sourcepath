---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-11"
---

# Backlog

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Deferred work captured so it is not lost. Nothing here blocks current use unless a current task explicitly promotes it.

**System of record:** `docs/backlog.yml`, structured by `docs/schema/backlog.schema.json`. The YAML is canonical; this table is a rendered view. Effort is expressed as **complexity** (["XS","S","M","L","XL"]), never time. `Owner` records the provenance boundary so concurrent work stays attributable.

Validate the backlog:

```bash
node scripts/check-backlog-governance.mjs
```

Summary statistics (counts by status/priority/complexity/category/type/owner;
cross-tabs for status x priority, category x status, and type x status;
complexity-weighted remaining-work size; and the open+critical items). Works
against any backlog.yml, including one in another repo with a different schema
— pass a path before the flag:

```bash
node scripts/check-backlog-governance.mjs --stats
node scripts/check-backlog-governance.mjs /path/to/other/backlog.yml --stats
```

## Items

| ID | Item | Category | Type | Status | Cx | Priority | Owner |
|----|------|----------|------|--------|----|----------|-------|
| BL-001 | Govern the backlog with schema and drift checks | docs | infra | done | S | high | shared |
| BL-002 | Member containment edges and qualified chunk identity | feature | feature | done | S | high | mine |
| BL-003 | Extract class fields and attributes with declared types | feature | feature | ready | M | critical | unassigned |
| BL-004 | Resolve type literals into references xref edges | feature | feature | ready | M | high | unassigned |
| BL-005 | Preserve extends vs implements through the chunk contract | feature | tech-debt | done | S | high | mine |
| BL-006 | Emit abstract, static, and final modifiers as chunk facts | feature | feature | ready | S | medium | unassigned |
| BL-007 | Preserve declaration-kind stereotypes in chunk kinds | feature | tech-debt | ready | M | medium | unassigned |
| BL-008 | Close the xref resolver registry gap for six languages | feature | feature | ready | L | medium | unassigned |
| BL-009 | Record call-site position and order on calls edges | feature | feature | ready | L | medium | unassigned |
| BL-010 | Receiver and dispatch resolution for sequence lifelines | feature | research | parked | XL | low | unassigned |
| BL-011 | Rewire dossier UML views to consume the bundle graph | tooling | tech-debt | done | M | high | mine |
| BL-012 | Chunk nested and inner declarations beyond one level | feature | feature | ready | M | low | unassigned |
| BL-013 | Widen Python xref binding scope | feature | feature | ready | M | medium | unassigned |
| BL-014 | Register the xref layer in the enriched-bundle runners | ops | infra | done | XS | high | mine |
| BL-015 | Dossier class-hierarchy text promises figures the tree filter can drop | tooling | bug | done | XS | medium | mine |
| BL-016 | Eliminate tree-sitter parse errors in C-family files | feature | bug | ready | L | high | unassigned |
| BL-017 | Assign a language or an explicit exemption to every file | feature | bug | ready | M | high | unassigned |
| BL-018 | Give every zero-symbol file a machine-readable reason | feature | bug | ready | M | high | unassigned |
| BL-019 | Resolve or explicitly tier the 40% of unresolved imports | feature | feature | ready | L | high | unassigned |
| BL-020 | Close the L4 enrichment scope gaps | feature | tech-debt | in-progress | M | medium | concurrent |
| BL-021 | Give every concept an embedding or a provenance-tagged fallback | feature | bug | ready | M | medium | unassigned |
| BL-022 | Make emission streaming and validation affordable at kernel scale | ops | tech-debt | ready | L | medium | unassigned |
| BL-023 | Wire the verify-bundle gate and the golden corpus | testing | infra | ready | M | high | unassigned |
| BL-024 | Backend live-bundle validation silently skips in CI | testing | bug | done | XS | critical | unassigned |
| BL-025 | Declared coverage gates are never enforced | testing | tech-debt | ready | M | high | unassigned |
| BL-026 | CI runs a thin verifier slice and no Rust or Node toolchain | testing | infra | ready | M | high | unassigned |
| BL-027 | Lock file freshness is never checked | ops | infra | ready | XS | medium | unassigned |
| BL-028 | The UI test suite runs in no aggregate target and no wiring guard sees it | testing | bug | ready | S | high | unassigned |
| BL-029 | The backend container image cannot start | ops | bug | done | S | critical | unassigned |
| BL-030 | The root image omits packages and silently degrades language support | ops | bug | ready | S | high | unassigned |
| BL-031 | A malformed sidecar produces a bare unlogged 500 | feature | bug | ready | S | medium | unassigned |
| BL-032 | Response schemas are incomplete and partly undeclared | feature | tech-debt | ready | XS | medium | unassigned |
| BL-033 | Enrichment sidecars are loaded into memory but no endpoint reads them | feature | tech-debt | ready | S | medium | unassigned |
| BL-034 | Semantic chunk search hardcodes the embedding model name | feature | bug | ready | XS | high | unassigned |
| BL-035 | The SPARQL escape hatch leaks its on-disk stores | ops | tech-debt | ready | S | low | unassigned |
| BL-036 | Missing tree-sitter grammars degrade silently instead of disclosing | feature | bug | done | M | critical | unassigned |
| BL-037 | PHP extracts no inheritance | feature | bug | done | S | high | mine |
| BL-038 | A malformed composer manifest silently voids PHP import resolution | feature | bug | done | XS | medium | mine |
| BL-039 | The first-class L4 facet contradicts the data-language enrichment boundary | testing | bug | ready | S | high | shared |
| BL-040 | Macro-generated symbols are structurally uncapturable | feature | research | parked | XL | low | unassigned |
| BL-041 | Retire the test-edge stem heuristic in favor of typed-import derivation | feature | tech-debt | ready | M | medium | unassigned |
| BL-042 | The import extractor misses proto imports and dynamic Python imports | feature | bug | ready | M | medium | unassigned |
| BL-043 | Concept canonicalization is prototype-grade | feature | tech-debt | ready | M | low | unassigned |
| BL-044 | Regeneration supports four languages of fifteen | feature | feature | ready | L | medium | unassigned |
| BL-045 | Byte-identical Python regeneration needs a concrete-syntax extractor | feature | research | parked | L | low | unassigned |
| BL-046 | Thirty-five of fifty TIOBE languages are below first class | feature | feature | ready | XL | high | unassigned |
| BL-047 | Seven first-class languages have no dedicated verifier | testing | tech-debt | ready | M | medium | unassigned |
| BL-048 | Embed the cartogram in the React application | tooling | feature | ready | M | medium | mine |
| BL-049 | The cartogram can only see import and test edges | tooling | feature | ready | S | low | mine |
| BL-050 | The cartogram normalizer holds the whole inventory in memory | tooling | tech-debt | ready | XS | low | mine |
| BL-051 | No changelog exists despite conventional-commit discipline | docs | infra | ready | S | medium | unassigned |
| BL-052 | The README directory tree is hand-written | docs | infra | ready | XS | low | unassigned |
| BL-053 | Shipped features are described as unbuilt in their own documentation | docs | tech-debt | ready | XS | medium | unassigned |
| BL-054 | The mandated disclaimer frontmatter is unguarded outside READMEs | docs | tech-debt | ready | XS | medium | unassigned |
| BL-055 | Three known drift risks have no guard | testing | infra | ready | S | medium | unassigned |
| BL-056 | The document retirement decision is prepared but unexecuted | docs | tech-debt | blocked | XS | low | unassigned |
| BL-057 | The build-plan executor mechanization plan is unregistered | feature | feature | ready | XL | medium | unassigned |
| BL-058 | The declarative reporting view model has no executor | feature | feature | ready | L | medium | unassigned |
| BL-059 | Generalize the confidence layer to all derived edges | feature | feature | ready | L | medium | unassigned |
| BL-060 | Four graph-operations substrate gaps remain open | feature | research | parked | XL | low | unassigned |
| BL-061 | Kconfig and devicetree are not first-class languages | feature | feature | parked | M | low | unassigned |
| BL-062 | The repository has no license | docs | infra | ready | XS | medium | unassigned |
| BL-063 | The enrichment record shape is restated in four layers and read by two hand-rolled parsers | feature | tech-debt | ready | M | medium | unassigned |
| BL-064 | The backlog schema's decision-reference fields have no backing registry | docs | infra | ready | S | medium | unassigned |
| BL-065 | No automated FLAM reader or enforcer exists — rules are convention-only | tooling | tech-debt | ready | S | medium | unassigned |
| BL-066 | Extension registries order by name-sort alone, with no dependency declaration | feature | tech-debt | ready | M | medium | unassigned |
| BL-067 | Controlled vocabularies are declared twice and drift-tested, not generated from one source | feature | tech-debt | ready | M | medium | unassigned |
| BL-068 | Add a --stats mode to the backlog governance script | tooling | feature | done | S | medium | mine |
| BL-069 | Generator functions get no distinct tag in the Python AST summary | feature | feature | ready | S | low | unassigned |
| BL-070 | Operator-overloading dunder methods have no protocol-level tag | feature | feature | ready | S | low | unassigned |
| BL-071 | Ollama-served embedding backend for L2, plus capability-aware L4 model resolution | feature | feature | done | M | medium | mine |

## Clusters

**Backlog governance (BL-001).** Closed 2026-07-11: the registry, rendered view,
schema, validation command, and CI job all exist and are verified by live
evidence checks.

**UML reconstruction (BL-002..BL-015).** The facts the bundle must additionally
capture (or stop dropping) before correct, full class and sequence diagrams can be
derived from a bundle by query alone. Grounding: UML gap analysis of the extraction
pipeline (2026-07-11); each item cites file-level evidence in its `references`.
BL-014/BL-015 were added the same day from the doc-ray dossier review
(`reports/doc-ray__dossier__20260711T214556Z.pdf`). Package diagrams need no new
data — `cbm:imports` / `cbm:importsExternal` suffice today.

**Error-free mapping (BL-016..BL-023).** The error ledger of
`docs/plan/error-free-mapping.md` (E1..E9), registered item by item. E5 (git
provenance) is closed by operator decision and deliberately not registered.

**Enforcement (BL-024..BL-030).** Guarantees that are declared but not enforced:
coverage gates that never run, a CI suite that skips its own tests and reports
green, and container images that cannot start or that silently lose language
support.

**Serving surface (BL-031..BL-035).** Backend and MCP defects, including one
silent-correctness failure (BL-034) where a mismatched embedding model would rank
against the wrong vector space without erroring.

**Extraction truth (BL-036..BL-047).** Where the disclosure contract — degradation
must be disclosed, never silent — is not upheld. BL-036 is the sharpest instance:
a missing grammar produces neither an AST nor an error code. BL-037 and BL-038 are
defects in first-class PHP support introduced by this repository's own recent work
and are owned accordingly.

**Cartogram (BL-048..BL-050).** BL-048 is the operator-selected follow-on to the
standalone tool: the application embed that was chosen at adoption and never built.

**Documentation truth (BL-051..BL-056).** From the doc-hygiene audit (2026-07-10)
and the requirements gap audit. The language-list and tools-listing
recommendations from that audit already shipped (README marker block plus
`tests/verify_readme_coverage.py`) and are deliberately not re-registered.

**Unregistered plans (BL-057..BL-061).** Two complete engineering plans and a
framework roadmap that live in `docs/` and had no entry in the system of record.

**Repository hygiene (BL-062..BL-063).**

**Roadmap-consumption audit (BL-064..BL-065).** Grounded in
`sourcepath-prompts/docs/prompt-consume-roadmap.md` — the process document an
agent uses to select and execute one backlog item — which asserts two live
governance gaps in passing: the backlog schema's `decision_prefix`/
`related_decisions` fields have no backing decisions registry (BL-064), and
FLAM (File-Level Agent Metadata) rules are enforced by convention only, with
no automated reader and only one of five carrying scripts pinned by a
dedicated test (BL-065). Both re-verified directly against this repository
before registration.

**LLVM-lessons architecture audit (BL-066..BL-067).** From a conversational
review of what codebase_mapper's own extension-registry and vocabulary design
could learn from LLVM's own architecture (not its IR content): the PassManager's
explicit `getAnalysisUsage()` dependency declarations vs. this repo's pure
name-sort plugin ordering (BL-066), and TableGen's single-source-of-truth
codegen vs. this repo's declare-twice-and-drift-test pattern for controlled
vocabularies, the same class of problem BL-063 already tracks for the L4
enrichment shape (BL-067). Both re-verified directly against the live source
before registration.

**Backlog tooling (BL-068).** Closed 2026-07-12: a `--stats` mode for
`check-backlog-governance.mjs` (counts by status/priority/complexity/category/
type/owner, a status x priority cross-tab, and complexity-weighted
remaining-work size), covered by a new `node --test` suite and wired into
`make test` and CI.

**Python-fundamentals coverage audit (BL-069..BL-070).** Applied the
`python-fundamentals` skill's solid/glossary-only/absent coverage-ceiling lens
to `codebase_mapper`'s own Python analyzer instead of the reference textbook
it was built from: generator functions (BL-069) and operator-overloading
dunder methods (BL-070) both get zero distinguishing treatment in the
signature schema, the same "thin spot" shape the skill found in the source
material. Related to BL-047 (Python has no dedicated per-language verifier at
all) without duplicating it — these are missing extraction capability, a
prerequisite to any such verifier covering them.

## Provenance of this pass

Items BL-016..BL-063 were added on 2026-07-11 by a completeness audit of the
backlog against the code and the documentation. Sources were the error-free-mapping
plan, the build-plan executor plan, the graph-operations framework, the doc-hygiene
audit, the drift risk map, the kernel-scale flaw map, the language goal ledger, the
untracked `REQUIREMENTS.md` gap audit, and a direct sweep of the code.

Items BL-064..BL-065 were added the same day from a review of
`sourcepath-prompts/docs/prompt-consume-roadmap.md` against the live backlog
schema and tree; both claims were re-verified mechanically (grepped for a
decisions/ADR file and for a FLAM reader tool) before registration.

Items BL-066..BL-067 were added the same day from a conversational
"what would LLVM's own architecture teach us" review; both were re-verified
directly against `codebase_mapper/shared_kernel/extensions.py` and
`tests/test_inventory_schema.py` before registration rather than taken on the
conversation's word alone.

`REQUIREMENTS.md` and the audit reports are themselves LLM-authored and therefore
untrusted by default (PALS's Law). **Every claim adopted here was re-verified
mechanically against the working tree before registration**, and claims that did not
survive verification were not registered — for example, the assertion that
`cbm verify-bundle` does not exist is false (the module exists; it is the command
wiring and the golden corpus that are absent, which is what BL-023 actually says).
Duplicates were dropped rather than re-filed: the "references edge kind declared but
never emitted" gap is already BL-004.

See `backlog.yml` for each item's full description, rationale, acceptance criteria,
dependencies, related decisions, and references.
