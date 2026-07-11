---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Sonnet 5 via Claude Code"
  date: "2026-07-11"
---

# LLVM-domain gap analysis — codebase_mapper

Back to the root [README.md](../../README.md).

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Method and scope

This analysis evaluates `codebase_mapper` (this repository) against the LLVM
domain — its IR type system, instruction set, SSA form, TableGen/`.def`/`.inc`
code-generation idioms, and template-heavy modern-C++ coding style — as
learned from a live Doc-Ray corpus document (`856095876-LLVM-IR-Quick-Reference`,
doc id `a7810583-6fa6-48dd-ad41-478562a52fcd`) in the same working session.
Every finding below traces to a file, line range, or command run against this
repository; none rests on the Doc-Ray content directly (that content supplied
the domain vocabulary — instructions, types, intrinsics, TableGen, `.def`
tables — used to decide *what to look for* here, not evidence *about* this
codebase).

Findings are cross-checked against this repo's own existing gap-analysis
document, [`docs/plan/error-free-mapping.md`](./error-free-mapping.md)
(2026-07-10), to avoid re-reporting already-tracked work as new.

---

## 1. Executive summary

`codebase_mapper`'s general architecture — extension-registry pipeline,
disclosed-degradation philosophy, dual RDF serialization for scale — would
handle the *general-purpose* source in an LLVM-style compiler-toolchain
repository (C++, Python, CMake) capably; this is not a novel claim, it is
already proven at kernel-scale (`linux-v23`, 67M triples) per
`error-free-mapping.md` and the repo's own README. Evaluated specifically
against the LLVM domain, three concrete, verifiable gaps emerge, all in the
same neighborhood: **compiler-DSL file formats are entirely unclassified**,
**X-macro code-generation headers are invisible to the whole pipeline**, and
**the C++ call-graph resolver silently drops template-function call sites** —
the single most common call shape in modern, LLVM-style C++. A fourth
finding is methodological, not a coverage gap: this repo's own stated
principle that its "unlanguaged families" fix generalizes to "any codebase"
is contradicted by its own derivation (a single reference corpus). No hard
defect (crash, or a bundle asserting something false) was found connected to
this domain — the architecture's fail-soft-and-disclose design means these
gaps manifest as **missing** data, not **wrong** data.

## 2. Domain alignment assessment

| Dimension | Fit | Basis |
|---|---|---|
| General-purpose source (C, C++, Python) in an LLVM-adjacent repo | **Strong** | All three are first-class languages; C-family macro harvesting + `compile_commands.json` include-root resolution already exist and are well-designed for exactly this kind of codebase (§7 below) |
| Scale (LLVM's own monorepo is comparable in size to the Linux kernel) | **Strong, proven** | Dual serialization path (rdflib → oxigraph) and streaming JSON-LD are already validated at kernel scale, per `error-free-mapping.md` E8 and the root README |
| Compiler-DSL formats (`.td`, `.ll`, `.mir`, `.bc`) | **Absent** | Zero classification, zero analysis — not addressed at any tier (F1) |
| X-macro code-generation headers (`.inc`, `.def`) | **Absent, and silently so** | Fall through to `type: unknown` (F2) |
| Modern-C++ call-graph fidelity (templates, `dyn_cast`/`isa`/`cast`) | **Weak** | Resolver only recognizes two of the relevant callee node shapes (F3), and the gap is untested (F4) |
| Blob-free source recovery for C/C++ | **Absent** (already known, reframed here) | `regenerate()` covers only Python/Rust/TS/JS (F6) |

The pattern across F1–F3 is not "C++ support is weak" — C++ itself is a
first-class, actively-maintained language here. It is specifically the
**DSL/codegen layer that surrounds real-world C++ toolchains** (TableGen,
X-macros, heavy template dispatch) that falls outside what's been built and
tested so far.

## 3. Prioritized findings

Ordered by tier per the requested rubric. Legend: **Classification** ∈
{error, gap, risk, improvement, open question} · **Priority** ∈ {critical,
high, medium, low} · **Confidence** ∈ {high, medium, low}.

### 3.1 Errors / hard violations

**None found connected to this domain.** This is itself a finding, not an
omission: `codebase_mapper`'s architecture treats every one of the gaps
below as a classification/coverage boundary (falls to `type: unknown`, or
silently emits fewer edges) rather than as incorrect output (a wrong RDF
triple, a crash, a corrupted bundle). The project's own
`error-free-mapping.md` names this exact distinction as its design target
("visible-but-tolerated is no longer a terminal state: visible means either
fixed or failing") — for the LLVM domain specifically, today's behavior is
"visible" (an operator can query for `type=unknown` files and see them) but
not yet "failing" in the sense of a budgeted, gated check. See F1/F2 for the
concrete instances.

### 3.2 High-priority gaps

---

**F1 — LLVM-family compiler DSLs have zero classification, at any tier**

- **Classification:** gap
- **Priority:** high
- **Evidence:** `codebase_mapper/shared_kernel/constants.py:55-98` (`LANG_BY_EXT`, the sole extension→language table) has no entry for `.ll`, `.td`, `.mir`, `.bc`, or `.mlir`. A repo-wide grep for `tablegen|TableGen|\.ll\b|\.td\b|\.mir\b|\.bc\b|\.mlir\b` across `codebase_mapper/`, `plugins/`, and `docs/` returns zero matches. `classify()`'s full fallback chain (`codebase_mapper/inspection/classify.py:14-338`) has no path that would catch these extensions before its terminal `return "unknown"` (line 338).
- **Rationale:** These are the concrete, on-disk artifacts through which the LLVM domain vocabulary just learned (SSA-form instructions, the `iN`/`float`/`ptr`/vector type system, target triples, intrinsics) is actually authored and tested. `.td` in particular is how LLVM/Clang/MLIR *define* their own instruction sets — it is the single most instruction-set-dense file format in the entire ecosystem.
- **Impact:** Any repository in the LLVM family (LLVM/Clang/MLIR itself, or any project embedding `.ll`/`.td` fixtures — Rust's `rustc_codegen_llvm` tests, Swift's SIL/LLVM lowering tests, Julia, Zig) maps with these files entirely invisible to the graph: no file count, no language breakdown entry, no chunks, no xrefs, no LLM-summary eligibility.
- **Recommended action:** Add Tier-1 (classification-only) entries for `.ll`, `.td`, `.mir`, `.bc` following the exact precedent already set for `.s`→`asm` in `error-free-mapping.md` E2 (regex-level, no grammar required for a first pass). Track in `docs/goals/` explicitly as either in-scope or deliberately excluded (see F8).
- **Confidence:** high (verified by direct grep and full extension-table review).
- **Dismissal criteria:** if the operator judges LLVM-family DSLs categorically out of scope (they are not TIOBE-ranked general-purpose languages, and PURPOSE.md's stated goal is TIOBE-50 coverage), this converts from "gap" to "accepted non-goal" — but should still be recorded as a stated exclusion, not left silent, per this project's own disclosure norms.

---

**F2 — `.inc`/`.def` X-macro headers fall through to `type: unknown`**

- **Classification:** gap
- **Priority:** high
- **Evidence:** Same grep as F1 — zero references to `.inc`/`.def` anywhere in `codebase_mapper/`. Confirmed by tracing `classify()`'s decision chain (`codebase_mapper/inspection/classify.py:20-338`) line by line: neither extension matches any named-file rule, `LANG_BY_EXT` (line 299), the config/DATA_EXT/ASSET_EXT buckets (lines 302-309), the fixture-path heuristics (lines 320-333), or the `VERSION`-name regex (line 336) — every file with these extensions terminates at `return "unknown"` (line 338).
- **Rationale:** `.def`/`.inc` are not incidental — they are how LLVM/Clang express their *own* instruction/intrinsic/attribute/builtin tables as machine-checkable X-macro lists (e.g. `Instruction.def`-style enumerations), `#include`-d directly into compiled translation units. This is precisely the domain vocabulary (instructions, intrinsics, types) surfaced in the Doc-Ray LLVM document. A codebase-mapper run against such a repo would miss the source-of-truth files for exactly the concepts it would otherwise chunk/xref/summarize from prose documentation.
- **Impact:** Undercounted file/symbol census; these files get zero `cbm:File type=source_code` classification despite being compiled, `#include`-d C/C++ content; zero chunks, zero xrefs, zero LLM-summary eligibility for files that materially define a codebase's own vocabulary.
- **Recommended action:** Classify `.inc`/`.def` as `source_code` with `language=c` or `cpp` (matched to the including translation unit's language where determinable, else a generic fallback), routed through the existing C-family macro/import machinery (§7) rather than treated as a wholly new language.
- **Confidence:** high (verified directly against the full classifier source).
- **Dismissal criteria:** if measurement across representative C/C++ repositories (not just LLVM) shows `.inc`/`.def` volume is negligible outside a small number of compiler projects, this could be reprioritized to medium — but the `unknown` fallback itself remains worth closing regardless, since it is a silent, uncategorized bucket by construction.

---

**F3 — The C++ symbol-xref resolver does not recognize template-function call sites**

- **Classification:** gap
- **Priority:** high
- **Evidence:** `plugins/symbol_xrefs/cpp_resolver.py:339-424` (`_emit_call`) branches only on `func.type == "identifier"` (line 353) or `func.type == "qualified_identifier"` (line 378). Tree-sitter's C++ grammar represents a call like `dyn_cast<Foo>(bar)` with the callee as a `template_function` node (identifier + template-argument-list), not a bare `identifier`. A grep for `template_function` across `plugins/symbol_xrefs/cpp_resolver.py` returns zero matches — no branch handles it, and the module's own explicit "Call shapes in scope" / "Out of scope" docstring (lines 13-32) does not mention template calls in either list, unlike the deliberately-documented receiver-dispatch exclusion (line 28: "Member-function dispatch via a receiver — requires type inference... Matches the Rust/TS/Java Stage-2 posture"). This reads as an oversight, not a disclosed boundary.
- **Rationale:** `dyn_cast<T>()`/`isa<T>()`/`cast<T>()` is LLVM's core RTTI-avoidance idiom, used pervasively throughout its own source — but this is not an LLVM-specific quirk: any templated call (`std::make_unique<T>()`, `std::static_pointer_cast<T>()`, generic algorithms) hits the identical gap in any modern C++ ≥ 11 codebase.
- **Impact:** Systematic undercounting of `calls` edges in template-heavy C++ codebases — the symbol graph would be measurably sparser than reality for exactly the coding style most representative of the LLVM ecosystem and of contemporary C++ generally.
- **Recommended action:** Add a `func.type == "template_function"` branch to `_emit_call` that unwraps to the underlying identifier/qualified-identifier and resolves identically to the existing bare-call path, ignoring template arguments for resolution purposes (matching the existing `cpp_intra_file`/`cpp_inter_file` resolution model).
- **Confidence:** high for the code-path gap (directly verified); medium for real-world impact magnitude (plausible, well-reasoned from language semantics, but not measured against an actual template-heavy corpus in this session — see F4).
- **Dismissal criteria:** if a maintainer confirms this was a deliberate, simply-undocumented v1 scope cut (matching the "Stage-2 posture" already used to exclude receiver dispatch), this should be promoted from "gap" to "documented scope boundary" — the fix priority would then depend on whether Stage-2 parity across languages already tracks this class of exclusion elsewhere (worth checking BL-008, "Close the xref resolver registry gap for six languages," which may already be adjacent).

### 3.3 Conditional risks

---

**F4 — The template-call gap (F3) is entirely untested**

- **Classification:** risk (test-coverage gap enabling F3 to persist undetected)
- **Priority:** medium
- **Evidence:** `tests/fixtures/cpp/` (both `mixed_c_cpp/` and `basic_pkg/`) contains zero template usage — confirmed by grepping every `.cpp`/`.h` file under both fixture trees for `template` and for the `<...>(` call-site pattern; both return no matches. `tests/verify_cpp.py` likewise has no reference to `template`.
- **Rationale:** A resolver gap that ships without a red test is a gap that can regress invisibly and that no future refactor will be warned about.
- **Impact:** F3 (and any future template-call handling, once added) has no regression protection; a well-intentioned refactor of `cpp_resolver.py` could silently reintroduce or worsen the gap with no test signal.
- **Recommended action:** Add a template-call case (at minimum a `dyn_cast`-style free template function and a `std::make_unique<T>()`-style standard-library call) to the C++ golden fixture, with an expected-edges assertion in `verify_cpp.py`, following this project's own stated practice in `error-free-mapping.md` E9 ("every future flaw adds a fixture before its fix lands — red first").
- **Confidence:** high (directly verified: fixture and test both grepped, zero hits).
- **Dismissal criteria:** none plausible — a coverage gap this specific and this cheap to close (one fixture file) has no reasonable case for staying open once F3 is triaged either way.

---

**F5 — The project's own "generalizes to any codebase" claim is contradicted by its measurement methodology**

- **Classification:** improvement (methodological, not a code defect)
- **Priority:** medium
- **Evidence:** `docs/plan/error-free-mapping.md:13-17` states the whole plan "is designed to hold for **any** codebase — no kernel-specific lists, no per-repo tuning." Its E2 section (`error-free-mapping.md:82-99`, "Unlanguaged files: close the seven families") derives its Tier-1/Tier-2 family list entirely from "measured histogram of the 29,012" files in one reference corpus (`linux-v23`): yaml, rst, devicetree, Makefile, Kconfig, `.s`, no-ext, json, txt. LLVM-family DSLs (`.td`, `.inc`, `.def`, `.ll`) do not appear in that histogram — not because they were considered and excluded, but because they essentially do not occur in the Linux kernel (Linux does not use TableGen-style code generation), while they would plausibly form a comparably large "unlanguaged" bucket in an LLVM/Clang/Swift/MLIR-family repository.
- **Rationale:** A methodology that measures "unlanguaged families" against a single reference corpus and then claims the resulting fix generalizes to "any codebase" is making a claim its own evidence doesn't support — the LLVM domain is a concrete, demonstrable counterexample, not a hypothetical one.
- **Impact:** Low immediate impact (F1 already covers the concrete symptom), but this is a process risk: the same methodology, applied next to a different domain (e.g. a Haskell/OCaml-heavy functional-language repo, or a hardware-description-language-heavy repo), would likely reproduce the same blind spot for the same reason.
- **Recommended action:** When next revisiting the "unlanguaged families" work, add at least one non-kernel reference corpus (an LLVM/Clang-family repo is a natural, high-value second data point given F1–F3) before re-asserting the "any codebase" claim, or soften the claim to name its actual evidence base.
- **Confidence:** high (both documents read directly; the histogram and the generalization claim are both explicit, verbatim text in `error-free-mapping.md`).
- **Dismissal criteria:** if a second, non-kernel corpus was already measured elsewhere and simply not cited in this document, this finding should be dismissed in favor of pointing to that evidence.

### 3.4 Nuanced improvements

---

**F6 — `regenerate()` has no C/C++ path — reframed against the LLVM domain**

- **Classification:** gap (previously identified in general terms; reframed here against this specific domain)
- **Priority:** low-medium (domain-specific framing; general priority was already assessed elsewhere in this session's broader `REQUIREMENTS.md` work)
- **Evidence:** `codebase_mapper/emission/application/regenerate.py:28` (`_REGENERATORS`) covers only `python`, `rust`, `typescript`, `javascript`.
- **Rationale:** For an LLVM/Clang-style C++-heavy repository, blob-free (AST-only) source recovery is entirely unavailable — only the blob-store-dependent `reconstruct()` path works. This matters specifically for the LLVM domain because C++ is the dominant language of that ecosystem, and `.td`/`.inc` (once/if added per F1/F2) would compound the gap further as entirely new formats with no regenerate story at all.
- **Impact:** No functional impact today (this is a known, disclosed scope boundary per the root README's regenerate fidelity table) — it is an opportunity, not a defect.
- **Recommended action:** No action required beyond what's already tracked; noted here only because the LLVM-domain lens makes the C++ gap's practical weight more concrete (C++ is this domain's dominant language).
- **Confidence:** high (directly verified against `regenerate.py`, consistent with the root README's own documented fidelity table).
- **Dismissal criteria:** already effectively dismissed by existing documentation; retained here only for completeness of the domain-lens exercise.

---

**F7 — Existing macro/include infrastructure is a genuine strength for this domain (included for balance)**

- **Classification:** improvement (positive finding — not a gap)
- **Priority:** n/a (informational)
- **Evidence:** `codebase_mapper/inspection/pipeline.py:310-326` builds a repo-derived `MacroTable` (via `harvest_macros`) and, when a `compile_commands.json` is present, resolves real `-I`/`-isystem` include roots (`codebase_mapper/inspection/languages/c.py:301-330`, `include_roots_from_compile_commands`) — gracefully skipping absolute (out-of-repo) roots and returning `[]` rather than raising on malformed input.
- **Rationale:** This is exactly the infrastructure a CMake-driven, macro-dense C/C++ codebase (LLVM's own build system is CMake; its source leans heavily on macros) needs for accurate `#include` resolution — and it is already implemented with sound degradation behavior (never raises, never silently trusts an out-of-repo path).
- **Impact:** N/A — this reduces risk elsewhere in the analysis (e.g. F1/F2's absence is not compounded by *also* having broken include resolution for the C/C++ files that already are supported).
- **Recommended action:** None. Recorded so this report doesn't read as one-sided.
- **Confidence:** high (directly verified).
- **Dismissal criteria:** n/a.

---

**F8 — Open question: is LLVM-family DSL support even an intended goal?**

- **Classification:** open question
- **Priority:** n/a (requires an operator decision, not an engineering fix)
- **Evidence:** `docs/goals/tiobe-top50.yaml` and `docs/SPEC_FIRST_CLASS_LANGUAGE.md` were both grepped for `tablegen|TableGen|DSL|LLVM|\.ll\b` — zero matches in either. `PURPOSE.md`'s stated goal is "Fully support the TIOBE index top 50 languages" — LLVM IR and TableGen are not general-purpose application languages and do not appear on TIOBE rankings.
- **Rationale:** F1's absence may be entirely intentional under the letter of the project's stated goal (TIOBE-50 coverage) — but the "unlanguaged families" precedent (E2 in `error-free-mapping.md`) shows the project already extends coverage to non-TIOBE-ranked formats (`.s`/asm, Kconfig, DeviceTree, Make) purely because they show up as real content in real repositories, independent of TIOBE ranking. That precedent argues for at least minimal (Tier-1, classification-only) `.ll`/`.td` recognition on the same rationale — but this is a scope call for the operator, not something an agent should decide unilaterally.
- **Impact:** Determines whether F1 should be prioritized as roadmap work or explicitly recorded as an accepted non-goal.
- **Recommended action:** Operator decision requested — see §7.
- **Confidence:** high (both documents directly grepped).
- **Dismissal criteria:** resolved by an explicit operator answer either way; not dismissible by an agent.

---

**F9 — No LLVM-IR tree-sitter grammar exists to build F1 on**

- **Classification:** improvement / practical constraint
- **Priority:** low
- **Evidence:** `pyproject.toml`'s dependency list (base `dependencies`, lines ~17-50 per this repo's earlier full inventory) includes 12 `tree-sitter-*` grammars — none for LLVM IR, TableGen, or MLIR. No such package was found referenced anywhere in this repository.
- **Rationale:** Unlike most of this project's first-class languages, closing F1 would very likely require a hand-rolled line/regex extractor (the same strategy already used for the Shell analyzer's neutralization state machine, or the "asm" Tier-1 treatment) rather than a drop-in tree-sitter grammar — raising the realistic effort estimate from "small, config-only" to "medium, a new hand-written extractor."
- **Impact:** Affects complexity estimation only, not whether the gap is real.
- **Recommended action:** If F1 is greenlit (per F8), scope it as a Tier-1 (classification + line-oriented extraction, complexity **M**) rather than assuming a tree-sitter grammar is available off the shelf.
- **Confidence:** medium (absence-of-evidence from a dependency-list check; a suitable community grammar may exist outside PyPI that wasn't checked here).
- **Dismissal criteria:** superseded if a maintainer identifies an existing, usable LLVM-IR tree-sitter grammar.

## 4. Evidence-backed gap matrix

| ID | Finding | Class | Priority | Confidence | Primary evidence |
|---|---|---|---|---|---|
| F1 | LLVM DSLs (`.ll`/`.td`/`.mir`/`.bc`) unclassified | gap | high | high | `shared_kernel/constants.py:55-98`; repo-wide grep, 0 hits |
| F2 | `.inc`/`.def` fall to `type: unknown` | gap | high | high | `inspection/classify.py:14-338` full trace |
| F3 | C++ resolver misses `template_function` calls | gap | high | high (code path) / medium (impact) | `symbol_xrefs/cpp_resolver.py:339-424` |
| F4 | Template-call gap untested | risk | medium | high | `tests/fixtures/cpp/*`, `tests/verify_cpp.py` — 0 template hits |
| F5 | "Any codebase" claim vs. single-corpus methodology | improvement | medium | high | `docs/plan/error-free-mapping.md:13-17, 82-99` |
| F6 | No C/C++ `regenerate()` path | gap (known) | low-medium | high | `emission/application/regenerate.py:28` |
| F7 | Macro/include infra already solid | strength | n/a | high | `inspection/pipeline.py:310-326`; `languages/c.py:301-330` |
| F8 | DSL support an intended goal? | open question | n/a | high | `docs/goals/tiobe-top50.yaml`, `SPEC_FIRST_CLASS_LANGUAGE.md` — 0 hits |
| F9 | No off-the-shelf LLVM-IR grammar | improvement | low | medium | `pyproject.toml` dependency list |

## 5. Recommended fix order

1. **F8 (operator decision)** — must resolve first; it gates whether F1/F9 are roadmap work or a recorded non-goal.
2. **F4 (add the missing template-call test fixture)** — cheapest item on this list (one fixture file), and per this project's own E9 practice, tests should precede fixes, not follow them.
3. **F3 (template-function call resolution)** — highest-confidence, highest-value code fix; unblocked by F4 landing first (red-then-green).
4. **F2 (`.inc`/`.def` classification)** — second-highest value; comparatively small (extension-table + routing through existing C-family machinery).
5. **F1 (LLVM DSL classification)** — only after F8 is answered affirmatively; scope per F9's effort note.
6. **F5 (methodology note)** — no urgency; fold into the next revision of `error-free-mapping.md` whenever it's next touched.
7. **F6** — no action; already tracked elsewhere.

## 6. Items safe to defer

- **F5** — a documentation/methodology refinement with no user-facing effect today; safe to bundle into the next natural revision of `error-free-mapping.md` rather than a standalone task.
- **F6** — already a known, disclosed boundary; nothing new to schedule.
- **F9** — not actionable on its own; only relevant once F1/F8 are resolved.

## 7. Items requiring human/operator decision

- **F8** is the load-bearing decision: does LLVM-family DSL support belong on this project's roadmap at all, given its TIOBE-50 charter? A "no" is a legitimate, defensible answer — but per this project's own disclosure norms, it should be recorded as an explicit exclusion (e.g. a line in `docs/goals/tiobe-top50.yaml` or `SPEC_FIRST_CLASS_LANGUAGE.md`), not left silent.
- **F3's dismissal criteria** also needs an operator read: is the missing `template_function` branch a genuine oversight (my read, given it's undocumented unlike the sibling receiver-dispatch exclusion) or an intentional, simply-undocumented v1 scope cut? This changes whether it's prioritized as a bug-like gap or filed as a documented boundary alongside the existing one.

## 8. Suggested next-step format (backlog conversion)

This repository already has a schema-governed backlog: `docs/backlog.yml`
(validated against `docs/schema/backlog.schema.json`, rendered to
`docs/BACKLOG.md`, checked via `node scripts/check-backlog-governance.mjs`
and gated in CI by `.github/workflows/backlog-governance.yml`). **No backlog
entries have been written by this analysis** — per the requested process,
findings are presented here for operator review/dismissal/reprioritization
first. Current registry state: 63 items (`BL-001`..`BL-063`); new entries
would begin at **`BL-064`**.

Below is the exact schema shape (matching `docs/backlog.yml`'s existing
fields) for the two items ready to convert immediately if accepted —
presented as drafts, not yet inserted:

```yaml
  - id: "BL-064"
    title: "Add a template-call C++ xref fixture (red, before the fix)"
    summary: "tests/fixtures/cpp/ has zero template usage; the template-function call-resolution gap (would-be BL-065) is entirely untested."
    description: |
      Add a free template function (dyn_cast-style) and a std::make_unique<T>()-style
      call to the C++ golden fixture, with an expected-edges assertion in
      tests/verify_cpp.py, following this project's own E9 practice in
      docs/plan/error-free-mapping.md: every future flaw adds a fixture before its fix lands.
    category: "testing"
    type: "tech-debt"
    status: "ready"
    complexity: "XS"
    priority: "high"
    rationale: "A resolver gap with no red test can regress invisibly through future refactors."
    acceptance_criteria:
      - "tests/fixtures/cpp/ contains at least one template function declaration and call site."
      - "tests/verify_cpp.py asserts the expected (currently-missing) calls edge and fails today."
    evidence_checks:
      - kind: "grep"
        path: "tests/fixtures/cpp"
        pattern: "template"
    references:
      - "plugins/symbol_xrefs/cpp_resolver.py"
      - "tests/fixtures/cpp/"
      - "tests/verify_cpp.py"
      - "docs/plan/llvm-domain-gap-analysis.md#34-nuanced-improvements"
    owner: "unassigned"
    source: "LLVM-domain gap analysis (2026-07-11), grounded in a Doc-Ray corpus document read via the doc-ray-graphql-maximizer skill."
    tags: ["cpp", "xrefs", "test-coverage", "templates"]

  - id: "BL-065"
    title: "Resolve template-function call sites in the C++ xref resolver"
    summary: "_emit_call only recognizes identifier/qualified_identifier callees; template_function (dyn_cast<T>(), make_unique<T>(), etc.) is unhandled and undocumented as a scope cut."
    description: |
      Add a func.type == "template_function" branch to _emit_call
      (plugins/symbol_xrefs/cpp_resolver.py), unwrapping to the underlying
      identifier and resolving through the existing cpp_intra_file/cpp_inter_file
      paths. Depends on BL-064 landing first (red before green).
    category: "feature"
    type: "feature"
    status: "blocked"
    complexity: "S"
    priority: "high"
    rationale: "Template function calls are the dominant call shape in modern C++ (LLVM's own dyn_cast/isa/cast idiom, and std:: generics generally); missing them systematically undercounts the call graph."
    acceptance_criteria:
      - "BL-064's fixture assertion passes."
      - "template_function callees resolve identically to bare identifier calls (same resolver names, same resolution tiers)."
    evidence_checks:
      - kind: "grep"
        path: "plugins/symbol_xrefs/cpp_resolver.py"
        pattern: "template_function"
    references:
      - "plugins/symbol_xrefs/cpp_resolver.py:339-424"
      - "docs/plan/llvm-domain-gap-analysis.md#32-high-priority-gaps"
    owner: "unassigned"
    source: "LLVM-domain gap analysis (2026-07-11), grounded in a Doc-Ray corpus document read via the doc-ray-graphql-maximizer skill."
    tags: ["cpp", "xrefs", "templates"]
```

F1/F2/F8/F9 are intentionally **not** drafted as backlog items yet — they
depend on the F8 operator decision (§7) and shouldn't be scheduled ahead of
that answer.

---

**Next step, if you want it:** confirm which findings to accept, dismiss, or
redirect (F8 in particular), and I'll either insert the accepted items into
`docs/backlog.yml` via `node scripts/check-backlog-governance.mjs`-validated
edits, or adjust scope/priority per your call.
