---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-10"
---

# Error-free mapping — engineering plan

Disclosure was phase one; this plan is phase two: **eliminate** the error
classes, not live with them. Every proposal is grounded in mechanical
evidence from the `linux-v23` bundle (the current worst case: 94,841 files,
81.9 M triples) and is designed to hold for **any** codebase — no
kernel-specific lists, no per-repo tuning. Definition of done at the end.
Back to the root [README.md](../../README.md).

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

---

## 0. The error ledger (measured, linux-v23)

| # | Error class | Magnitude today | Target |
|---|---|---|---|
| E1 | tree-sitter parse errors in C-family files | 30,203 / 61,203 source files (49%), 405,660 error nodes | ≤ 2% of files flagged; every flag span-localized |
| E2 | files with no language | 29,012 (31%) | ≤ 3% (binary/data exempt via explicit type) |
| E3 | silent zero-symbol files | 7,380 | 0 by definition (zero symbols ⇒ explicit machine-readable reason) |
| E4 | unresolved imports | 40% of extracted directives (155,615) | hard edges 100% precise; every ambiguous include represented as disclosed candidates |
| E5 | git provenance omitted (shallow clone) | 94,841 files | real history by default; omission only on fetch failure, disclosed |
| E6 | L4 enrichment scope gaps | cpp excluded; 59 of 793,210 concepts described | headers covered (post-F20 they are `c`); concept coverage a deliberate, stated policy |
| E7 | concepts without embedding | 7,394 | 0 (label-embedding fallback, provenance-tagged) |
| E8 | emit robustness/cost | JSON-LD whole-doc in RAM; pySHACL ~2 h | streaming JSON-LD; fast structural gate |
| E9 | errors found by hand-audit, not by the system | this whole flaw map | `cbm verify-bundle` gate + golden corpus fail the build first |

---

## E1 — Parse errors: macro harvest + byte-preserving neutralization

**Evidence.** Median 4 error nodes per flagged file; 60% of flagged files
have ≤ 5; only 1% exceed 100. Error contexts sampled from real blobs show
three patterns, all one root cause — *unexpanded macros that alter C's
token grammar*:

1. annotation macros between type and declarator — `void __iomem *base`
   (`__user`, `__percpu`, `__maybe_unused`, …);
2. iterator macros — `for_each_set_bit(pair, &pair_mask, 4) { … }`;
3. token-pasted digit-leading identifiers inside macro args —
   `phylink_set(port->supported, 1000baseX_Full)`.

**Fix (codebase-agnostic by construction).** Two passes, no hardcoded
macro lists — the repo's own `#define`s are the source of truth:

1. **Macro harvest** during the existing classify pass: regex-collect
   `#define NAME` / `#define NAME(args)` across the repo, keeping the
   body's leading token. The body classifies the macro mechanically:
   empty or `__attribute__…` body → *annotation*; body beginning with
   `for` → *iterator*; everything else untouched.
2. **Byte-preserving neutralization** of a *parse buffer* (never the
   stored blob): annotation-macro tokens → same-length spaces; iterator
   macro names before `(...) {` → `while` + same-length padding (comma
   expressions make `while (a, b, 4) {…}` valid C); digit-leading
   identifiers inside harvested-macro args → first digit becomes `_`.
   Equal lengths mean every node offset and line number stays valid
   against the original content.

Each neutralized file carries `ast_summary.parse_buffer =
"macro_neutralized"` (provenance, PALS's Law). Generated-data outliers
(`insn-x86-dat-64.c`, 4,916 error nodes) stay flagged — a bounded, honest
residual. The `parse_error_nodes` metric (F8) is the acceptance
instrument.

Complexity: **M**. Risk: neutralization must never fire inside strings or
comments — the harvester works on lexed lines; the fixture corpus (E9)
pins string/comment cases.

## E2 — Unlanguaged files: close the seven families

**Evidence (measured histogram of the 29,012):** yaml 5,665 · rst 4,011 ·
dts/dtsi/dtso 6,708 · Makefile 3,196 · Kconfig 1,829 · `.s` 1,346 ·
no-ext 2,390 · json 1,072 · txt 982 → seven families ≈ 90%.

**Fix, two tiers.** Language *classification* is decoupled from AST
support — a correct census does not require a parser:

- **Tier 1 (classification only, XS):** `.yaml/.yml → yaml`,
  `.json → json`, `.rst → restructuredtext`, `.txt → text`, `.s/.S → asm`
  (with regex-level label/global symbol extraction — no grammar needed),
  `Makefile*/​*.mk → make`, `Kconfig* → kconfig`, `.dts/.dtsi/.dtso →
  devicetree`. `(none)` then means only genuinely unclassifiable content.
- **Tier 2 (AST extraction where it carries meaning, M):** Kconfig
  (config symbols + `depends on` edges — the kernel's variability model,
  D12 evidence) and DeviceTree (node topology) first; Make targets next.
  PyPI availability verified 2026-07-10: `tree-sitter-devicetree 0.15.0`,
  `tree-sitter-kconfig 1.3.0`, `tree-sitter-yaml 0.7.2`,
  `tree-sitter-make 1.1.1`, `tree-sitter-rst 0.2.0`;
  `tree-sitter-asm` is **not** on PyPI (`tree-sitter-language-pack
  1.12.5` exists; its exact language list must be verified at
  implementation time).

## E3 — Silent zero-symbol files: the query is the gap

**Evidence.** The C query (`ts_setup.py`) captures only
`function_definition` and `struct_specifier`. Macro-only, typedef-only,
enum-only, or declaration-only headers legitimately parse to zero
captures — 7,380 files.

**Fix (S).** Extend the C/C++/ObjC queries with `preproc_def` /
`preproc_function_def` (macro definitions *are* symbols — the kernel's
API surface is substantially macros), `enum_specifier`, `type_definition`
(typedefs), `union_specifier`, and top-level `declaration` of externs.
After this, a source file with zero symbols is an anomaly by definition:
the coverage layer records an explicit reason (`empty`, `all_comments`,
`parse_failed`) — silent zero ceases to exist as a category.

## E4 — Imports: precision stays absolute, ambiguity becomes data

**Evidence.** 60% resolution; the residual is dominated by multi-candidate
angle includes (`<asm/io.h>` exists once per architecture).

**Fix, three parts (M).**
1. **Include-root harvest:** when `compile_commands.json` exists, use its
   `-I` flags per translation unit (exact resolution, any C project);
   otherwise harvest `-I`/`ccflags` from Make/Kbuild fragments — evidence,
   not convention.
2. **Disclosed candidates:** unresolved multi-candidate includes become
   `cbm:possibleImport` edges (one per candidate, each carrying the
   candidate count) in a new vocabulary tier — hard `cbm:imports` stays
   100% precise; recall becomes queryable instead of absent. Schema
   change ⇒ `vocabulary_version` v1 → v2 with SHACL shapes for the new
   property (semver per CLAUDE.md).
3. **Variant folding:** when all candidates differ only by one path
   segment and a `*-generic` sibling exists, add the generic candidate
   edge tagged `variant_generic` — a disclosed heuristic, off by default,
   enabled per-run.

## E5 — Provenance: correct by default

`CBM_UNSHALLOW` exists but defaults off — the error class survives by
default. Flip it: default = attempt `git fetch --unshallow
--filter=blob:none`; on failure fall back to today's disclosed omission;
`CBM_UNSHALLOW=0` forces shallow for air-gapped runs. Complexity **XS** —
the mechanism already exists (`repo_source.py`), only the default and the
failure disclosure change.

## E6 — L4 scope: policy, stated and covered

- Add `cpp` to the file-summary allowlist (**XS**) — post-F20 the kernel's
  headers are `c` again, but genuine C++ projects deserve summaries.
- Concept descriptions: keep vocab-matched concepts as the *typed* tier,
  and add a **corpus-derived tier** — top-N concepts by occurrence ×
  file-spread (N configurable, default 200), each record tagged
  `selection: vocab | corpus_top` so the epistemics stay separated. 59
  descriptions against 793,210 concepts is not an error, but it is a
  scope so narrow it reads like one; making the policy explicit and the
  tier queryable resolves it (**S**).

## E7 — Every concept gets a vector

Concepts whose lexicalizing files contributed no embedded chunk (7,394)
get a **label embedding**: `prefLabel + altLabels` through the same
MiniLM backend, `embedding_source: centroid | label` recorded per concept
(new field, schema v2). Consumers filter by provenance; the npz/ids gap
disappears. Complexity **S**.

## E8 — Emit: no whole-document steps at any scale

- **Streaming JSON-LD (M):** the fast TTL path already produces sorted
  N-Triples; group by subject on the stream and write node objects
  incrementally — same canonical output (fixture-verified byte-equality
  on small graphs), RAM bounded by one node. Removes the F9/F19 class
  entirely (F19's depth stub stays as the last-resort guard).
- **Fast structural gate (M):** translate the bundled SHACL shapes
  (12 KB, cardinalities + closed vocabularies) into SPARQL counts
  executed in oxigraph; run at every emit in seconds. Full pySHACL
  remains the deep gate (opt-in / CI-nightly). Requires an
  **equivalence test**: both gates must agree on every corpus fixture,
  including seeded violations — the fast gate ships only with that proof.

## E9 — The mechanism that makes it hold: gate + corpus

Hand-auditing found F1–F20; the system must find F21 itself.

1. **`cbm verify-bundle <dir>` (M):** independent recount of
   files/chunks/concepts vs the manifest, artifact hash re-check, error
   budgets (table above) evaluated as **failing** checks, degradations
   must be empty or explicitly acknowledged (`--accept-degradation
   git_provenance`), SHACL/structural gate conforms. Non-zero exit on
   any violation. Wired as the last step of `run_l4.py` — **a run that
   ships errors fails**, instead of describing them.
2. **Golden corpus (M, grows forever):** one minimal fixture repo per
   confirmed error family — the Octave `.m` (F1), kselftest layout (F7),
   deep CST (F19), incidental-cpp (F20), each E1 macro pattern, each E2
   family, a macro-only header (E3), a multi-candidate include (E4) —
   with exact expected manifest numbers. `make test-corpus` maps, emits,
   and verify-bundles every fixture. Every future flaw adds a fixture
   before its fix lands (red first) — the flaw map stops being a
   document and becomes a test suite.

---

## Sequencing

| Wave | Items | Rationale |
|---|---|---|
| 1 | E9 gate + corpus skeleton · E1 neutralization · E3 query extension · E2 tier-1 + Kconfig/DTS grammars · E5 default · E6 cpp allowlist | Gate first so every later wave is enforced; then the three biggest graph-quality wins |
| 2 | E7 label embeddings · E4 include roots + possibleImport (schema v2) · E6 corpus-top concepts | Schema v2 lands once, carrying all new fields together |
| 3 | E8 streaming JSON-LD · fast structural gate (with equivalence proof) | Robustness/cost — correctness first, speed second |

**Definition of "error-free mapping":** a run on any repository either
meets every budget in the ledger (§0) or **fails** `cbm verify-bundle`,
with each residual (generated-data parse outliers, fetch-refused
provenance) individually identified, span-localized, and
machine-readable in the bundle itself. Visible-but-tolerated is no
longer a terminal state: visible means either fixed or failing.

**Acceptance run:** after Wave 1, a full kernel re-map must show flagged
C files ≤ 2–5% (from 49%), silent-zeros 0, unlanguaged ≤ 3% (from 31%),
and verify-bundle green with the shallow-clone budget-exception removed
by E5. Those numbers — not this document — are the completion criterion.
