---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-09"
---

# Linux processing pass — complete flaw map

Single source of truth for fixing the extractor and reprocessing
`torvalds/linux`. Covers the bundle at `_tmp/linux-sandbox/linux/`
(commit `2c7c88a412aa`, emitted 2026-07-09T19:13Z by codebase-mapper 0.5.0,
**before** the fix batch `3e328bc..22812b9` landed) and every report artifact
in [docs/reports/](./). Each flaw cites code as it exists at working-tree
HEAD (`22812b9` + uncommitted perf work). Back to the root
[README.md](../../README.md).

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

---

## 1. Verdict table

| # | Flaw | Severity | Status in code | Reprocess needed |
|---|------|----------|----------------|------------------|
| F1 | 13,537 `.h` files retagged objective-c | serious | **FIXED** @ `3e328bc` (66 tests pass) | yes |
| F2 | Angle includes unresolved — import graph at ~22% recall | serious | **FIXED** @ `a721429` (unique-suffix only, by design) | yes |
| F3 | Shallow clone → 94,841 identical `gitCommitTime` | serious | **FIXED** @ `1e53fdf` (omit + degradation entry; `CBM_UNSHALLOW=1` recovers history) | yes |
| F4 | L4 degradation disclosure never reaches the manifest | critical | **FIXED 2026-07-09** — `emit()` surfaces `ctx.scratch["degradations"]` as `manifest["degradations"]`, always present (`tests/test_degradations_manifest.py`) | yes |
| F5 | Concept-description path self-disables silently | open → fixed | **FIXED 2026-07-09** — `_disable_with_disclosure` on both aggregator paths counts the blast radius | yes |
| F6 | Curated vocabulary matches only 24 of 776,716 kernel concepts | open (scope) | **FIXED 2026-07-09** — 35 systems-domain terms + aliases added to `software_primitives.yaml` (`tests/test_vocab_systems_domain.py`) | yes |
| F7 | Test code undercounted: 626 typed vs 5,161 kselftest files | serious | **FIXED 2026-07-09** — `selftests` path component + kernel `.c` test stems in `classify.py` (`tests/test_kselftest_evidence.py`) | yes |
| F8 | 28,581/49,569 C files flagged `parse_errors_present` (thresholdless) | moderate | **FIXED 2026-07-09** — `parse_error_diagnostics` adds `parse_error_nodes:<N>` in all 10 analyzers; coverage aggregates it (`tests/test_parse_error_granularity.py`) | yes |
| F9 | JSON-LD emit is single-threaded, whole-doc-in-RAM rdflib | moderate | **PARTIAL** — TTL path fixed @ `7d92327` (pyoxigraph); JSON-LD path unchanged; `--no-jsonld` / `CBM_EMIT_JSONLD` exist; survivable on the 235 GB box (second run completed with it) | at emit |
| F10 | SHACL self-check costs ~2 h at 67 M triples | moderate | **FIXED 2026-07-09** — `CBM_SKIP_SHACL` / `CBM_EMIT_JSONLD` env knobs reach every entry point incl. main CLI; skip stays disclosed (`tests/test_emit_env_knobs.py`) | at emit |
| F11 | 29,012 files (30.6%) have no language (Kconfig, devicetree…) | minor (gap) | **UNFIXED** — outside classifier scope; now disclosed by the F15 caveat layer | optional |
| F12 | manifest `n_concepts` vs graph `prefLabel` count differ by +3 | resolved — false alarm | 3 `skos:Collection` labels (`graph_writer.py:166-167`); counter is correct | no |
| F13 | `pytest tests/test_perf_emit_flags.py` fails without `PYTHONPATH=.` | trivial | **FIXED 2026-07-09** — `pythonpath = ["."]` in pyproject pytest config | no |
| F14 | 7,418 concepts (0.95%) have no embedding vector: `concepts_embeddings.npz` holds 769,298 of 776,716 | diagnosed | **FIXED 2026-07-09 (disclosed)** — cause: concepts whose lexicalizing files contributed no embedded chunk row (no vector source exists); manifest now carries `n_concepts_with/without_embedding` (`tests/test_concept_embedding_disclosure.py`) | yes |
| F15 | `linux.html` X-ray presents known-flawed figures as bare FACT (no R1 import-undersampling disclosure; objc mislabel unframed) | serious (report generator) | **FIXED 2026-07-09** — `mechanical_caveats()` layer in `cbm_report.py`, rendered first in HTML+MD (`tests/test_report_caveats.py`) | no (re-render) |
| F16 | `linux.html` rendered without the ABox / decomposition / build-plan inputs ("arc4d3 dimensions — absent", 0 parts, 0 rebuild steps) | minor (wiring) | **FIXED 2026-07-09** — missing companions now produce loud caveats; discovery already auto-globs `*abox*.ttl` beside the bundle | no (re-render) |
| F17 | Test-evidence figures inconsistent across artifacts (139 edges vs 405 typed-import edges vs 5,161 kselftest files; amdgpu.h degree 460 vs 397) | moderate (reconciliation) | **FIXED 2026-07-09** — typed-import fallback is now a pipeline strategy inside `infer_tests_edges` (the canonical number); reports cite it instead of re-deriving | yes |
| F18 | `linux-architecture-report.pdf` is the pre-refine generation ("UNVERIFIED — PENDING SHACL", D02 Unknown) coexisting with the refined scope-A report (SHACL PASS, D02 High) | minor (housekeeping) | **FIXED 2026-07-09** — moved to `docs/archive/linux-architecture-report-superseded-by-scope-a.pdf` | no |
| F19 | `emit()` dies with RecursionError on a CST nested past the recursion ceiling (`json.dumps(r.ast_summary)`, `rdflib_emitter.py`) — a completed run lost at its last step | serious | **FIXED 2026-07-10** — `dump_ast_summary()` retries under a raised ceiling, stubs an un-serializable field with a disclosed marker, and emit registers an `ast_summary_depth_truncated` degradation (`tests/test_deep_ast_summary_emit.py`) | re-run emit |
| F20 | With F1 fixed, the **C++ header retag** claimed the same ~13.5K kernel headers (`cpp: 246 → 13,782` in linux-v23): its project-wide rule needs only one cpp file anywhere + no sibling `.c` | serious | **FIXED 2026-07-10** — pass 2 now requires the header's own C++ content markers (`has_cpp_markers`), mirroring the F1 fix (`tests/test_cpp_header_retag.py`; 42-test cpp verifier green) | yes |

Post-hoc repair (`scripts/cbm_repair.py`, @ `276e70a`) can rebuild
`run_manifest.json`, `enrichments.jsonl`, `concepts.json` from an existing
`inventory.ttl` — it **cannot** repair F1, F2, F3, F7, F8 (inspection-time
facts baked into the graph). A full reprocess is the only path to a clean
bundle.

---

## 2. Flaw detail

### F1 — Objective-C header retag (fixed)
The kernel's lone `.m` file (`tools/testing/selftests/cgroup/memcg_protection.m`,
an Octave script) armed `refine_objc_header_languages`, which retagged every
`.h` in any directory lacking a sibling `.c`. Now: content sniff
(`classify.py:339-341` → `dot_m_is_objc`, `objc.py:103-123`) tags it `matlab`,
and the retag gates on a genuine `.m`/`.mm` ObjC source plus per-header marker
evidence (`objc.py:632-694`). Consequences baked into the current bundle:
wrong language census, 13,537 headers parsed with the wrong grammar (their
60.8% silent-zero-symbol rate), and silent exclusion from L4 file-summary
scope. Expected after reprocess: `objective-c ≈ 0`, `matlab = 1`,
`c ≈ 63.5K`, and those headers enter L4 scope (longer enrichment run).

### F2 — Angle-include resolution (fixed, bounded)
`resolve_c_includes` treated all `#include <…>` as external: 91,736 edges
from 407,936 directives, and `imported_by(include/linux/fs.h)` returned
empty. Now a repo-wide basename index resolves an angle include when exactly
one path suffix matches (`c.py:221-289`); ambiguous specs (`<asm/io.h>` with
many `arch/*/include` candidates) stay unresolved **by design** — a wrong
edge is worse than a missing one. Expected after reprocess: import edges well
above 91,736 but below 407,936; SCC/cycle analysis extent becomes meaningful
(the scope-A report's R1).

### F3 — Shallow-clone provenance (fixed)
`--depth 1` clone attributed every file to HEAD → 94,841 identical
timestamps. Now shallow trees omit `gitCommitTime` entirely
(`git_plumbing.py:159-160`, `pipeline.py:206-207,269`) and register a
`git_provenance / shallow_clone_no_history` degradation
(`pipeline.py:283-287`). Opt-in `CBM_UNSHALLOW=1` runs
`git fetch --unshallow --filter=blob:none` (`repo_source.py:110-132`) to
recover real history. **Decision needed for the reprocess:** set
`CBM_UNSHALLOW=1` (real commit times, heavier fetch) or accept honest
omission. Note the degradation entry currently dies in memory — see F4.

### F4 — Degradation disclosure not wired into the manifest (open, critical)
`pipeline.py:283` and `enricher.py:199` append to
`ctx.scratch["degradations"]`, and docstrings claim "the manifest emitter
reads ctx.scratch['degradations']" — but `emit_bundle.py` and `run_l4.py`
contain **zero** references to `scratch` or `degradations`
(verified by grep). The disclosure never reaches `run_manifest.json`.
This is an architectural omission under PALS's LAW: the layer records its
own degradation and the bundle still presents itself as whole. Fix before
reprocess: plumb `ctx.scratch["degradations"]` into the manifest (and assert
it in `verify_llm_enrich_degradation.py`, which currently only checks the
scratch side).

### F5 — Concept-description path degrades silently (open)
`LlmAggregator._do_concept_descriptions` catches `OllamaUnreachable` /
`OllamaModelMissing`, logs, sets `self._disabled = True`, and returns
(`plugins/llm_enrich/aggregator.py:217-232`) — no degradation entry, unlike
the file-summary path. In a run where Ollama dies mid-pass, concept
descriptions vanish without a machine-readable trace. Fix alongside F4.

### F6 — Vocabulary coverage: 24 concept descriptions is full scope (open, scope)
Verified mechanically: exactly 24 concepts in `concepts.json` carry a curated
`kind` (8 domain-primitive, 15 structural-primitive, 1 relational-primitive),
and the enricher describes **only** vocab-matched concepts
(`aggregator.py:163-165`). So 24/776,716 was not a degraded run — it is the
curated vocabulary barely intersecting kernel domain language. Raising
concept-description value for the kernel means extending the vocabulary (or
widening enricher scope), which is content work beyond a code fix.

### F7 — kselftest test code invisible (open)
`classify.py:125` types a file as `test_code` only when a path component is
exactly `tests`/`test`/`__tests__`/`spec`; `tools/testing/selftests/…`
(components `testing`, `selftests`) misses, and the C++ `_test` stem rule
excludes `.c`. Result: 626 test files typed against 5,161 in kselftests
alone; 139 `cbm:tests` edges. Any assurance/coverage claim from the bundle is
unreliable (scope-A report R3, D13). Fix: recognize `selftests` (and plural
`testing/selftests` roots) as test-path components for `.c`/`.sh`.

### F8 — Thresholdless parse-error flag (open)
`c.py:186`: `["parse_errors_present"] if tree.root_node.has_error else []` —
one recovery node anywhere flags the whole file. Kernel C with GCC extensions
trips it on 57.7% of C files, destroying the flag's diagnostic value and
casting doubt on symbol/import completeness. Options: record error count /
error-node ratio instead of a boolean, and/or a threshold; consider
tree-sitter grammar tuning for GCC extensions later.

### F9 / F10 — Emit cost controls (partial)
TTL now streams through sort + pyoxigraph (`fast_serializer.py`, ~3.3 min and
~24 GB at 67 M triples vs rdflib's >100 GB); JSON-LD still builds the whole
document in RAM single-threaded (`emit_bundle.py:72-99`) — survivable on the
235 GB box, a crash risk elsewhere. SHACL (~2 h) is skippable only via
`run_l4.py --skip-shacl` (skip is honestly recorded as `conforms: None`).
Neither control is reachable from the main CLI or env. For the reprocess:
keep SHACL on (it is the final gate) and keep JSON-LD (the MCP/report stack
reads it), but budget the time.

### F11 — Unclassified languages (gap)
29,012 files have no language: Kconfig, devicetree, Makefile fragments, data.
They carry no per-language facts and are invisible to L4. Kconfig/devicetree
first-class support would materially improve kernel bundles (Kconfig is the
kernel's variability backbone — scope-A D12 classified it ProductLine from
file counts alone). Enhancement, not a defect.

### F12 — The +3 concept "drift" (resolved: false alarm)
`inventory.jsonld` holds exactly 776,716 node-level `cbmi:concept/` IDs — the
manifest is correct. The 3 extra `skos:prefLabel` occurrences are the
per-kind `skos:Collection` nodes (`cbmi:collection/{code_relations,
code_structure,intent_first_ontology}`, emitted at `graph_writer.py:166-167`).
The initial "emitter counter drift" flag came from `tools/cbm-report`
classifying any prefLabel-bearing node as a concept — fixed there the same
evening (concepts now require occurrence statistics; recount matches the
manifest exactly). Optional cosmetic: add `n_collections` to the manifest.

### F13 — Test-harness quirk (trivial)
`tests/test_perf_emit_flags.py` imports `scripts.run_l4`; without
`PYTHONPATH=.` (no `scripts/__init__.py`, no pytest `pythonpath` config) it
fails at collection. Add `pythonpath = ["."]` to pyproject's pytest config or
an `__init__.py`.

### F14 — Concept-embedding shortfall (open, undiagnosed)
Verified against the artifact: `concepts_embeddings.npz` contains
`vectors (769298, 384)` and `ids (769298,)` while `concepts.json` holds
776,716 concept ids — 7,418 concepts (0.95%) have no centroid vector.
Only `linux.html` surfaced this; no manifest field discloses it. Diagnose in
the concept-embedding builder (likely concepts whose lexicalizing chunks
carry no embedding rows, or an eligibility filter) and either close the gap
or record `n_concepts_without_embedding` in the manifest.

### F15 — The HTML X-ray presents known-flawed figures without caveats (open)
`linux.html` (from `scripts/cbm_report.py`) has a FACT/DERIVED/UNVERIFIED
provenance legend, yet prints `91,736 import edges` with no undersampling
disclosure (no 407,936, no ~22%, no angle-include mention anywhere in the
file) and lists `objective-c 13,537` without defect framing — while the PDF
and ABox both carry these as formal risks. A reader of the HTML alone
inherits the two largest distortions as clean facts. Fix: give
`cbm_report.py` a bundle-caveat layer (derivable mechanically: imports
extracted vs resolved ratio; language-anomaly heuristics), or feed it the
risk findings.

### F16 — HTML rendered without its companion inputs (minor)
The same render reports "arc4d3 dimensions — absent — ABox not provided",
"decomposition YAML not provided", "build plan YAML not provided", 0 parts /
0 rebuild steps — yet `linux-abox.ttl` sits in the same directory.
Regeneration should wire the ABox (and decomposition/build-plan when they
exist) into the dossier invocation.

### F17 — Test-evidence figures don't reconcile across artifacts (moderate)
Three different magnitudes describe the same defect: 139 `cbm:tests` edges
(stem heuristic, shipped), 405 edges (HTML's typed-import derivation, "3×,
mechanically verified", with an explicit PROPOSAL to retire the stem
heuristic), and 5,161 kselftest files (PDF/ABox denominator). Also
`amdgpu.h` is degree-460 in the PDF/ABox but 397 in the HTML chokepoints
table (different metric or pass). Fixing F7 should pick the canonical
test-evidence derivation (typed-import approach looks strictly better) and
make every report cite the same numbers.

### F19 — Deep-CST RecursionError kills emit at the last step (fixed 2026-07-10)
Observed live on a TypeScript re-emit (744 file summaries + 41 concept
descriptions completed, then `json.dumps(r.ast_summary)` raised
RecursionError in `build_inventory_graph`): a full-body `cst_json` mirrors
the parse tree, and one deeply nested expression out-nests Python's
default ceiling. Fix: `shared_kernel/json_safety.dump_ast_summary()` —
serialize normally, retry once under a 20,000 ceiling (preserves the
common overflow band losslessly), and only then stub the offending field
with `{"omitted": "nesting_exceeds_serialization_depth"}`; the emitter
collects affected paths and `emit()` registers an
`emission / ast_summary_depth_truncated` degradation that reaches the
manifest via the F4 wiring. The byte-count site in `emit_bundle.py` uses
the same helper.

### F20 — The cpp header retag was F1's twin (fixed 2026-07-10)
Found by the linux-v23 verification: `objective-c` went to 0 as expected,
but `c` stayed at 50,012 while `cpp` jumped 246 → 13,782. With the objc
retag no longer firing, `refine_cpp_header_languages`' project-wide rule
(any cpp source in the repo + no sibling `.c` → header becomes cpp)
claimed the same header population — armed by 246 genuine C++ files under
`tools/`. Less harmful than F1 (the cpp grammar is a C superset, so
symbols extracted correctly — +78K symbols vs v1), but the census is
wrong and cpp is outside the L4 allowlist, so those headers still get no
file summaries. Fix mirrors F1: pass 2 now requires the header's own
content to carry C++-only markers (`namespace`, `template<`, access
specifiers, `extern "C++"`…); the sibling rule is unchanged, and genuine
C++ include/-vs-src/ splits still retag via content evidence.

### linux-v23 verification (2026-07-10)
Emitted 04:57Z @ `a635d674` after the F1–F18 batch. Deltas vs v1:
`objective-c` 13,537 → **0**, `matlab` **1**, import edges 91,736 →
**231,753** (22% → **60%** resolution), `test_code` 626 → **4,763**,
tests edges 139 → **5,077**, symbols 986,410 → **1,065,186**,
silent-zero 11,031 → **7,380**, `parse_error_nodes` **354,644** (new),
manifest `degradations` present (shallow clone disclosed —
`CBM_UNSHALLOW` was not set), SHACL **conforms**, `emit_engines:
oxigraph`, concept-embedding gap disclosed (7,394 of 793,210). L4:
**59 concept descriptions** (24 → 59, the F6 vocabulary working) +
47,228 file summaries. `cbm-report` recount matches the manifest
exactly. Two findings: F20 above, and a `cbm-report` parser fix (the F8
double diagnostic serializes as a JSON-LD array; the crate's
`extraction_error` field now accepts scalar-or-array — it had silently
skipped 32,197 file nodes, disclosed by its own skip warning).

### F18 — Two generations of the dimensional analysis coexist (minor)
`linux-architecture-report.pdf` is the pre-refine run: cover stamped
"UNVERIFIED — PENDING SHACL VALIDATION", confidence 6H/12M/1L/1U with D02
Unknown. `linux-scope-a-dimensions.md/pdf` is the refined run: SHACL PASS,
21 applications, 7H/14M, D02 promoted to High (Tarjan SCC proof). Nothing
marks the older PDF as superseded. Either delete it, or stamp it superseded
in a masthead line, before the reprocess adds a third generation.

---

## 3. What the existing reports disclose — and hide

Four artifacts in this directory describe the same bundle. Disclosure is
uneven; the matrix below is what a reader of each artifact would learn.

| Disclosure | scope-A md/pdf | architecture pdf | linux-abox.ttl | linux.html |
|---|---|---|---|---|
| R1 import graph ~22% resolution (91,736 / 407,936) | ✅ risk R1 | ✅ formal risk, D02 | ✅ `Risk_ImportGraphUndersampled` | ❌ **absent — 91,736 shown as bare FACT** |
| R2 13,537 objc-mislabeled headers | ✅ risk R2 | ✅ formal risk, D14 | ✅ `Risk_HeaderLanguageMislabel` | ⚠️ number shown, defect framing absent |
| R3 test evidence undercount | ✅ risk R3 (5,161 files) | ✅ formal risk, D13 | ✅ `Risk_TestEvidenceUndetected` | ✅ different figures: 139 vs 405 typed edges + retire-heuristic PROPOSAL |
| LLM-authored content unverified (PALS's Law) | ✅ split banner | ✅ cover stamp | ✅ header comment | ✅ UNVERIFIED tags |
| Shallow git history (depth-1) | implied via §6 | ✅ D20 evidence text | ✅ D20 evidence text | ❌ |
| Parse errors (c 28,581 / objc 3,273), zero-AST languages, 29,012 unlanguaged | ❌ | ❌ | ❌ | ✅ **HTML only** |
| Concept-embedding shortfall 769,298 / 776,716 | ❌ | ❌ | ❌ | ✅ **HTML only** (verified: F14) |
| Concept path coverage 94,811 of 94,841 files | ❌ | ❌ | ❌ | ✅ HTML only |
| ABox/decomposition/build-plan absent from render | n/a | n/a | n/a | ✅ self-reported (F16) |

Two structural take-aways: **(a)** the interpretation-layer artifacts
(PDF/ABox) disclose the extraction risks but not the parser-health floor,
while the X-ray HTML has the parser-health floor but silently omits the
extraction risks (F15) — no single artifact gives a complete picture;
**(b)** the architecture PDF is the pre-refine generation and contradicts
the scope-A report's confidence profile (F18).

---

## 4. Reprocess plan

**Order matters — fix first, then reprocess, then regenerate reports.**

1. **Pre-reprocess fixes — ALL DONE 2026-07-09 (TDD, suites named in §1):**
   F4/F5 degradation disclosure end-to-end; F6 systems vocabulary
   (24 → ~59 typeable concepts); F7 kselftest classification; F8 quantified
   parse errors; F10 emit env knobs; F13 pytest path; F14 embedding-gap
   disclosure; F15/F16 report caveat layer; F17 canonical typed-import
   tests edges; F18 archived. Remaining open by choice: F9 (JSON-LD memory
   profile — mitigated by `CBM_EMIT_JSONLD`/`--no-jsonld` and proven
   survivable on this machine) and F11 (Kconfig/devicetree first-class
   languages — enhancement, now at least disclosed by the caveat layer).
2. **Reprocess command** (24 cores, 235 GB box):
   `CBM_UNSHALLOW=1 CBM_ENRICH_WORKERS=<n> python scripts/run_l4.py --repo torvalds/linux --out _tmp/linux-v2`
   — keep JSON-LD and SHACL on; budget ~2 h for SHACL and a longer L4 pass
   (13,537 recovered C headers enter file-summary scope; warm cache covers
   the previously-enriched 50,405).
3. **Verify the new bundle** (mechanical diff against this map):
   - `objective-c ≈ 0`, `matlab = 1`, `c ≈ 63.5K`
   - import edges ≫ 91,736; `imported_by(include/linux/fs.h)` non-empty
   - `gitCommitTime` values real and plural (unshallow) — not one clone stamp
   - `test_code` ≫ 626 (kselftests recognized)
   - manifest contains `degradations` (empty list on a healthy run),
     `emit_engines`, SHACL `conforms: true`
   - `tools/cbm-report` recount PASS; its quality page should drop F1/F3
     flags
4. **Regenerate downstream artifacts** from the new bundle: the scope-A
   dimensional analysis (its §8 names this as the designed path to tighten
   R1–R3 and re-grade D02/D13/D14), the architecture report, the dossier
   HTML — this time with the ABox, decomposition, and build-plan inputs
   wired in (F16) — and `linux-bundle-report.pdf` via `tools/cbm-report`.
