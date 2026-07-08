---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-08"
---

# Plan: Mechanized Buildplan Executor (`composer/`)

**Status:** proposed · **Owner:** operator · **Complexity:** L (decomposed into S/M subtasks below)

Goal: execute recomposer buildplans (`*.buildplan.yaml`) with a deterministic,
procedural pipeline that builds all files, synthesizes imports, installs
dependencies, derives everything derivable, generates test scaffolding, and
runs lints/gates **in the exact step order the plan stipulates** — reserving
AI agents exclusively for the residue (function bodies, prose) behind a
mechanically gated slot interface.

Evidence basis: empirical inspection (2026-07-08) of
`_tmp/{cbm,tokio,airflow}.buildplan.yaml` (62 / 79 / 2,528 steps, all parsed
in full), their companion `*.decomposition.yaml` files, the `_tmp/cbm` bundle
(blobs `cmp`-verified byte-identical against the working tree), and the
`recomposer/`, `decomposer/`, `codebase_mapper/emission/application/` sources.
Claims marked *(estimate)* or *(design)* are not mechanically verified.

---

## 1. Founding observations (verified)

1. **The buildplan is three machine artifacts interleaved.**
   - EXECUTABLE fields — a program: `step`, `phase`, `requires_steps`,
     `creates`, `creates_ordered`, `modifies`, `parts`.
   - CHECKABLE fields — a verification spec: `contracts`, `tests_required`,
     `expected_result`, `dependencies_introduced`, `evidence`.
   - INTERPRETIVE fields — a prompt payload: `goal`, `rationale`,
     `assumptions`, `confidence`.
   The field schema is identical across all three plans (all 17 fields on
   every step of all 2,669 steps).

2. **The checkable strings are closed template sets, not prose.**
   `tests_required` = 16 templates + 4 finding tags; `expected_result` =
   12 templates (10 machine-checkable). The generating f-strings live in
   `recomposer/plan.py` (lines 287–712). `recomposer.recompose` is a pure
   function of the decomposition, so parsing these templates is safe today;
   §5 item 4 removes even that.

3. **Plan invariants an executor may rely on** (asserted in
   `recomposer/plan.py`, re-verified across all three plans):
   backward-only `requires_steps` (0 forward refs); single file ownership —
   every `modifies` entry references a file created by a strictly earlier
   step (cbm 28/28, tokio 6/6, airflow 1,388/1,388); `creates_ordered: true`
   lists are valid file-level topological orders from
   `decomposer` `cycle_resolutions`; `confidence` is a closed enum.

4. **Evidence tiers determine the mechanical share.**
   - **Tier A** — plan only: structure + gates, no content, no signatures
     (by design; `recomposer/render.py:29-37`).
   - **Tier B** — + decomposition: `build_order`, `cycle_resolutions[].file_order`,
     quality gates, symbol inventories (currently bare `file:name (kind)`
     strings — no signatures).
   - **Tier C** — + bundle: 100% byte-identical copy-through
     (380/380 file nodes → present blobs, verified); statement-level import
     inventories with line numbers per language (`astSummary.imports`);
     complete Python ASTs with bodies; lossless tree-sitter CSTs for TS/Rust;
     lockfiles inlined as blobs.

5. **Tier C is already solved.** `reconstruct()` / `verify_reconstructed()` /
   `verify_roundtrip()` in `codebase_mapper/emission/application/reconstruct.py`
   rebuild the tree byte-identically with zero AI. The executor's value is
   (a) the degraded tiers A/B and (b) the verification harness around agent
   slots at any tier.

6. **Known negative results** (do not design against these):
   - Bundles contain **zero** signature predicates today
     (`grep -c cbml2:signature` = 0 in all three) — the uncommitted L2
     signature work is the fix.
   - `PackageRelease` nodes carry name+version but no integrity hashes and
     no resolution tree — lockfiles cannot be regenerated from the graph;
     copy-through is the only complete path.
   - Java signatures are not derivable from the graph (no `cst_json`, items
     lack signatures) except by parsing blobs.
   - The bundle does not represent empty directories, permissions/exec bits,
     or symlinks.
   - 81/89 tokio `contracts` entries are file names, not symbols — the
     contract gate degrades to file-existence there.

---

## 2. Architecture *(design)*

New package `composer/`, sibling to `decomposer/` and `recomposer/`.
Deterministic end-to-end; agents quarantined behind stage 5.

```
plan.yaml ─► 1 COMPILE ─► 2 SCHEDULE ─► 3 GENERATE ─► 4 GATE ─► 5 SLOT ─► 4' RE-GATE ─► 6 LEDGER
             validate      DAG waves     deterministic  mechanical  agent     same gates,   resumable
             + typed       (requires_    emitters       checks      residue   zero trust    journal
             checks        steps)
```

1. **Compile** (`composer/compile.py`) — parse YAML; re-assert the §1.3
   invariants; dereference `parts` into the decomposition; compile every
   `tests_required` / `expected_result` string into a typed `Check`.
   **Fail closed on any unrecognized template** — a new template means the
   recomposer changed and the executor is stale.
2. **Schedule** (`composer/schedule.py`) — topological waves over
   `requires_steps`; steps within a wave run in parallel (airflow max
   fan-in: 35). Reuse `decomposer.metrics.build_order` / `tarjan_scc`
   (pure, deterministic, already cited) — do not reimplement.
3. **Generate** (`composer/generators/`) — per artifact class, selecting
   strategy by best available evidence, recording the strategy per file:
   `copy-through` (blob) → `regenerate` (astSummary; byte-identical for CST
   languages, formatting-lossy Python; python/rust/ts/js only today) →
   `stub` (signature inventory) → `skeleton` (path + imports) →
   `agent-required`. Strategy provenance preserves the PURPOSE.md
   mechanical / inference / LLM separation at the **output** level.
4. **Gate** (`composer/gates.py`) — run the step's compiled checks. A step
   is *done* only when green. Gate families:
   - smoke-import/compile: `uv run python -c "import …"`, `tsc --noEmit`,
     `cargo check -p <crate>`;
   - parse/lint: tree-sitter parses per language; formatter/linter hooks;
   - contracts: symbol presence (file existence where contracts are
     filenames, until §5 item 5 lands);
   - symbol count: `expected_result` "N symbols expected" vs decomposition;
   - dependency conformance: built module imports exactly the declared
     internal modules / external packages — catches both missing and
     hallucinated imports;
   - install: `uv sync` / `npm ci` / `cargo fetch` (Tier C only);
   - launch: `launch X and verify it starts`; suite: `full test suite passes`.
5. **Slot** (`composer/slots.py`) — for artifacts the generators could not
   finish, emit a machine-readable task file: stub path, `contracts`,
   compiled acceptance commands, and the step's INTERPRETIVE fields
   (`goal`, `rationale`, `assumptions`, part `responsibility`) as prompt
   context — the only place that text is consumed. Agent returns file
   contents; the executor re-runs the same gates. Reject → retry with error
   transcript (bounded, default 3) → mark `operator-required`, continue other
   DAG branches. **No agent output lands ungated** (PALS's Law as a pipeline
   stage, not a convention).
6. **Ledger** (`composer/ledger.py`) — append-only journal:
   step → strategy, content hashes, gate results. Ordered + checksummed in
   the style of migration runners (Flyway/Alembic); content-addressed
   outputs in the style of Bazel/Nix hermetic actions. Idempotent re-runs;
   resume from any interruption.

CLI *(design)*:

```
uv run python -m composer <plan.yaml> \
    [--decomposition <decomposition.yaml>] [--bundle <dir>] \
    [--until-step N] [--emit-agent-tasks <dir>] [--resume]
```

**Reuse, do not reimplement:** `reconstruct()`, `verify_reconstructed()`,
`verify_roundtrip()`, `regenerate()`, `recomposer.recompose` invariants,
`decomposer.metrics`, `cycle_resolutions.file_order`.

---

## 3. Mechanization ladder — expected agent surface

| Tier | Mechanical | Agent surface |
|---|---|---|
| C (plan+decomposition+bundle) | ~100% — copy-through + `verify_roundtrip` | none (residue: empty dirs, permissions, excluded paths — operator territory) |
| B (+ signature sidecar, §5.1–2) | skeleton, order, manifests (unpinned), signature stubs, import allowlist gates, test skeletons, all gates | function/method bodies + doc prose ≈ chunk count (cbm ≈ 2,047; airflow order 10⁴), each an independently gated small slot |
| A (plan only) | skeleton, empty ordered files, unpinned manifests, full gate harness | whole files against `contracts`; mechanical share ≈ 40% by effort *(estimate)* |

SCC group steps ("build the N-module group together": 3 tokio, 4 airflow)
have provably no linear file order — treat each group as one atomic
generation unit and, if bodies are needed, one agent slot.

---

## 4. Work breakdown

### Phase E0 — prerequisites (outside `composer/`, ranked)

| # | Task | Acceptance | Complexity |
|---|---|---|---|
| 1 | Land L2 signature extraction (uncommitted `plugins/chunks_embeddings/signatures.py` + graph_writer predicates + SHACL); fix or explicitly scope out the 6 red objc tests | signature predicates present in a regenerated bundle; signature test files green (or objc exclusion documented) | M |
| 2 | Land decomposer `--symbols` sidecar (`SymbolRecord`, `build_symbol_map`, `to_symbols_yaml`); fix 4 red crate tests | Tier B usable without the 17 MB inventory graph; `tests/decomposer/` green | M |
| 3 | Land round-2 recomposer TDD reds — `BuildPlan.unassigned_files` first (executor needs every decomposition file owned or excluded-with-reason; airflow gap today: 1,102 files), then `cargo check -p` wording, phase-completeness skips | `tests/recomposer/test_round2_regressions.py` green | M |
| 4 | Recomposer emits typed `checks:` alongside prose templates | executor performs zero string parsing of gate specs | S |
| 5 | Rust `contracts` emit symbols, not file names | contract gate meaningful for Rust plans | S |
| 6 | (Longer-term) bundle completeness: permissions, symlinks, empty dirs; optional integrity hashes on `PackageRelease` | perfect-replay parity documented | M |

### Phase E1 — executor core

| # | Task | Acceptance | Complexity |
|---|---|---|---|
| 1 | `compile.py`: schema + invariants + typed checks; fail-closed on unknown templates | property tests over all three real plans; malformed-plan fixtures rejected | M |
| 2 | `schedule.py`: DAG waves reusing `decomposer.metrics` | wave order respects `requires_steps` on all three plans | S |
| 3 | `ledger.py`: journal, hashing, resume | kill/resume test reproduces identical state | M |
| 4 | `gates.py`: smoke-import, parse-lint, contracts, symbol-count, dependency-conformance | each gate has red and green fixtures | M |

### Phase E2 — generators

| # | Task | Acceptance | Complexity |
|---|---|---|---|
| 1 | Skeleton + ordered file creation (+ SCC groups as atomic units) | directory tree matches step 1 + `creates` closure | S |
| 2 | Manifest copy-through (Tier C) / unpinned emission (Tier A, flagged) | `dependency install succeeds` gate green at Tier C | S |
| 3 | Import-block synthesis from `astSummary.imports` (Python regroup-by-lineno; Rust `raw`; TS/Java exact) | emitted stubs import-clean before any body exists | M |
| 4 | Signature stubs (Python from `ast_json` / sidecar; TS+Rust from CST or sidecar; bodies `raise NotImplementedError` / `todo!()` / `throw`) | contract + symbol-count gates green on stub-only tree | M (S per language after the first) |
| 5 | Test skeletons from chunks + `xrefs.jsonl` calls + `rust_items.jsonl` test items | generated test files parse; obligations compile to gates | M |
| 6 | Copy-through / `regenerate` integration (Tier C fast path) | see E3.1 | S |

### Phase E3 — verification milestones (golden tests)

| # | Milestone | Acceptance |
|---|---|---|
| 1 | **Tier C golden:** run executor over `cbm.buildplan.yaml` with bundle | `verify_roundtrip` green; **zero** agent slots emitted |
| 2 | **Tier B golden:** cbm plan + decomposition + signature sidecar, no blobs | 100% of steps reach stub-green gates; agent-slot list == body-only work |
| 3 | **Scale check:** airflow plan compile + schedule + skeleton (no bodies) | 2,528 steps compile; wave schedule deterministic across runs |
| 4 | **Slot loop:** one module's slots filled by an agent, gates enforce accept/reject | rejected output demonstrably never lands; retry transcript recorded |

### Phase E4 — agent-slot runtime integration

Task-file format consumable by any agent runtime (Claude Code, cron, CI).
Deliberately decoupled: `composer` emits/validates; it never calls a model
itself. Complexity: S.

---

## 5. Failure modes and policy

Determinism breaks at: macro-generated symbols (Rust `#[derive]`,
`cfg_attr`), dynamic imports, Python `__getattr__` re-exports, struct/impl →
"class" flattening in symbol inventories, extraction errors
(`cbm:extractionError`, 3 in cbm). Uniform policy: **never guess — halt the
step, record why in the ledger, downgrade to agent slot or
operator-required.** Silent plausible-but-wrong scaffolding would recreate
the failure mode PALS's Law exists to prevent, one layer down.

Deferral rule: nothing in this plan is deferred by agent discretion; phase
ordering above is dependency-driven, and any actual postponement is an
operator call (CLAUDE.md Rule 8).

---

## 6. References

- `recomposer/plan.py` — plan generator, template strings (lines 287–712),
  invariant assertions (96–116).
- `recomposer/render.py:29-37` — plans carry no contents/signatures by design.
- `codebase_mapper/emission/application/reconstruct.py` — byte-exact
  materialization + roundtrip verification; `regenerate.py` — blob-free
  rebuild (python/rust/ts/js).
- `decomposer/metrics.py` — `build_order`, `tarjan_scc` (Tarjan 1972),
  `instability` (Martin); `decomposer/decompose.py:134-174` —
  `_cycle_resolutions` file-level orders.
- Study artifacts: `_tmp/{cbm,tokio,airflow}.buildplan.yaml`,
  `_tmp/*.decomposition.yaml`, `_tmp/cbm/` bundle.
- Prior-art properties borrowed *(design, well-known systems)*: ordered
  checksummed migration ledgers (Flyway, Alembic); hermetic
  content-addressed actions (Bazel, Nix).
