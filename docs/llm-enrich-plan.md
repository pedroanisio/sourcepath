# LLM enrichment via local Ollama — implementation plan

> Status: design. No code on disk yet. Reviewers, push back on the
> architectural commitments below before any step lands — Step 1
> freezes them. A companion proof-of-concept doc lives at
> [docs/llm-enrich-poc.md](llm-enrich-poc.md) — start there if you want
> a 1-day spike before committing to the full plan.

Today every layer in a cbm bundle is mechanically derived: parse the
source, walk the AST, run cooccurrence, attach the controlled
vocabulary. The result is deterministic and SHACL-checkable, but
inferentially shallow — the bundle knows that `code_mapper/models.py`
has 122 classes named `*Behavior` / `*Intent` / `*Contract`, but it
can't tell you *what behavior the user actually wanted those classes
to express*. That gap is what an LLM is good at closing.

This plan adds an opt-in **L4 enrichment layer** that calls a local
Ollama server, caches every response by content hash, and writes
provenance-bearing triples into a new `cbml4:` namespace. The host,
L1, L2, L3, and symbol_xrefs layers are not modified. Bundles built
without `--llm-enrich` are byte-identical to today's.

## Architectural commitments

These are locked by Step 1 and stable through every later step.

1. **New namespace `cbml4:`** (LLM). Not `cbml3:` (concepts) — that
   layer is owned by the controlled-vocab work and must remain
   mechanically derived. `cbml4:` is the namespace where stochastic
   provenance is honest about being stochastic.
2. **Local-first via Ollama.** No cloud API support in v1. The
   transport is Ollama's HTTP API (`/api/chat`, `/api/embed`); the
   default host is `http://localhost:11434`, overridable via
   `OLLAMA_HOST`. This keeps the dependency surface small (one HTTP
   client; `ollama` Python SDK optional) and gives users full data
   sovereignty by default.
3. **Default off, opt-in via flag.** Every bundle today is unchanged.
   `--llm-enrich` on `scripts/run_l3.py` / `scripts/run_xrefs.py` and a
   new `scripts/run_l4.py` are the only entry points that register the
   plugin.
4. **Content-addressed cache on disk, not in the bundle.** Default
   location `~/.cache/cbm-llm/` (override via `CBM_LLM_CACHE`).
   Key = `sha256(model || prompt_template_sha || target_content_sha
   || enrichment_kind)`. Cache hit short-circuits the model call;
   cache miss writes a new entry on success. The cache is **not**
   shipped in the bundle — the bundle ships the *outputs*
   (`enrichments.jsonl`), the cache is a build-time accelerator.
5. **Deterministic provenance, not deterministic generation.** We do
   not promise that the model produces the same string twice. We
   promise that *given a populated cache*, two consecutive runs over
   the same commit produce byte-identical bundles. First-run
   determinism is best-effort: `temperature=0`, fixed seed, but
   subject to whatever the model itself does. The verifier asserts
   warm-cache equality, not cold-cache equality.
6. **Every triple carries provenance.** `cbml4:model`,
   `cbml4:promptSha256`, `cbml4:generatedAt`. Consumers can filter by
   model or detect drift across re-emits. Provenance is per-triple,
   not per-bundle, so a single bundle can carry enrichments from
   multiple models without confusion.
7. **Failure mode is degradation, not breakage.** Ollama unreachable
   ⇒ log + skip ⇒ no `cbml4:*` triples emitted ⇒ SHACL stays green
   (every `cbml4:` predicate has `maxCount` only, no `minCount`). The
   bundle is still useful; it just lacks the enrichment layer.
8. **Sidecar artifact `enrichments.jsonl`** — one enrichment per line,
   sorted by `(target_iri, kind)`. Mirrors the `xrefs.jsonl` /
   `ast_summaries.jsonl` retrofit pattern. The full text of each
   enrichment lives in the sidecar; the inventory carries a literal
   reference plus provenance.
9. **Prompts are versioned files, not inline strings.** Every
   enrichment kind has a corresponding file under
   `plugins/llm_enrich/prompts/<kind>.v<N>.txt`. The prompt file's
   SHA-256 is part of the cache key. Editing a prompt without bumping
   the version invalidates every cache entry built against the old
   version — by design. A verifier catches "prompt file changed but
   `_PROMPT_REGISTRY` version stayed the same".
10. **Three enrichment kinds in v1.** Locked deliberately:
    - `file_summary` — one sentence on every source-code `cbm:File`
    - `concept_description` — one paragraph on every curated
      `cbml3:Concept` (the ones carrying `cbml3:conceptKind`)
    - `schema_purpose` — one paragraph on every `static/schemas/*.xsd`

    Out of scope for v1 (would each be a follow-up plan):
    chunk_summary (would 10×–100× the request count), refactor
    suggestions, security review, test-case generation, README
    generation. Resist scope creep here.

## What gets enriched

| Target | Triple | Cost per repo (≈1k files) | Why |
|---|---|---|---|
| `cbm:File` where `cbm:type == "source_code"` | `cbml4:fileSummary "…"` | ~1k calls @ 1s = 15min on local 8B | Drives `repository_summary` quality; feeds future `concept_explanation` |
| `cbml3:Concept` where `cbml3:conceptKind` present | `cbml4:conceptDescription "…"` | ~25 calls (typed concepts only) | Turns the curated vocab into searchable explanations anchored to the codebase |
| `cbm:File` where `cbm:path` matches `static/schemas/*.xsd` | `cbml4:schemaPurpose "…"` | ~10 calls (one-time, cached forever) | Leverages the schema fixtures as a vocab seed; cheapest possible enrichment with highest semantic payoff |

Total per fresh repo: ~1.05k model calls, ~17 minutes on a local 8B.
Subsequent re-emits over an unchanged repo: 0 calls (warm cache).

## The steps

Each step is independently shippable: its own verifier, its own
commit, its own user-visible (or API-visible) increment. Sizes:
**S** ≈ 1 day · **M** ≈ 2–3 days · **L** ≈ 4–7 days.

### Step 1 — Schema, vocab, empty plumbing (**S**)

Land the contract before any model call exists.

- **Files**:
  - new `plugins/llm_enrich/` package skeleton with empty
    `register_all()`, `OllamaClient` stub, `Cache` stub, prompt
    directory.
  - `codebase_mapper/constants.py` — add `CBML4_NS`, `CBML4`
    namespace; bump `VOCABULARY_VERSION`.
  - `codebase_mapper/rdf_emit.py` — bind `cbml4` prefix (no
    triples yet).
  - `plugins/llm_enrich/shapes.py` — `LlmEnrichmentShape` declaring
    the optional cardinality of every `cbml4:*` predicate.
- **Verifier**: `tests/verify_llm_enrich.py` — registers the
  empty plugin, runs the pipeline, asserts the bundle is
  byte-identical to a non-registered run (the empty plugin has zero
  observable effect). This is the back-compat anchor.

### Step 2 — Ollama client + content-addressed cache (**S**)

The transport layer, before anything depends on it.

- `OllamaClient.chat(model, system, user, temperature=0, seed=…)` —
  thin wrapper over `POST /api/chat`. Timeout 60s default; retries
  zero (let the cache absorb retries on next run).
- `Cache.get(key) / put(key, value)` — flat directory of
  `<sha256>.json` files. Reads return the parsed dict or None. Writes
  are atomic (`.tmp` + rename).
- **Verifier**: `tests/verify_llm_enrich_cache.py` — pure
  cache logic, no Ollama. Asserts hit/miss, atomic writes,
  determinism of the key function. Skips Ollama-dependent tests
  unless `OLLAMA_HOST` env is set + reachable.

### Step 3 — Prompt registry + first enrichment kind (**M**)

`file_summary` is the smallest end-to-end slice.

- `plugins/llm_enrich/prompts/file_summary.v1.txt` — system + user
  template. Asks for one declarative sentence under 30 words.
- `plugins/llm_enrich/prompts.py` — `PROMPT_REGISTRY: dict[kind, PromptTemplate]`
  with `(version, sha256, render(target_content) -> (system, user))`
  shape. The SHA is computed at import time and asserted to match the
  filename's version.
- `plugins/llm_enrich/enricher.py` — `FileEnricher(RecordEnricher)`
  populates `record.scratch["llm:file_summary"]` for each
  source-code file using cache + client.
- **Verifier**: `tests/verify_llm_enrich_prompts.py` — every prompt
  file's SHA matches the registered version; bumping the file body
  without bumping `vN` fails the verifier.

### Step 4 — RDF emission for `file_summary` (**S**)

- `plugins/llm_enrich/graph_writer.py` — `LlmGraphWriter` walks
  `ctx.scratch["llm:*"]` and emits `cbml4:fileSummary`,
  `cbml4:model`, `cbml4:promptSha256`, `cbml4:generatedAt` per
  enriched file.
- `plugins/llm_enrich/artifact.py` — `enrichments.jsonl` sidecar
  emission. One line per `(target_iri, kind)`, sorted.
- Extend `tests/verify_llm_enrich.py` with kind-1 assertions:
  triples emitted match sidecar; provenance fields present; SHACL
  conforms.

### Step 5 — Two more enrichment kinds (**M**)

- `concept_description` — Aggregator (not Enricher), runs after L3 so
  it can see curated concepts. Prompt receives the concept name +
  alt_labels + top 5 cooccurring concepts + first 3 chunks that
  lexicalize it.
- `schema_purpose` — RecordEnricher gated on
  `record.path.startswith("static/schemas/")`. One-time call per
  schema; results cached forever (schemas don't change often).
- Extend the verifier; tests assert each kind emits the right
  predicate, only on the right targets.

### Step 6 — Warm-cache determinism + Ollama-down degradation (**S**)

The two failure-adjacent properties Step 1 promised.

- `tests/verify_llm_enrich_determinism.py` — pre-populate the cache,
  run the pipeline twice, byte-compare every artifact (mirroring
  `verify_l3.py`'s determinism check). Skip if no cache fixture.
- `tests/verify_llm_enrich_offline.py` — set `OLLAMA_HOST` to an
  unreachable URL, run the pipeline, assert: zero `cbml4:*` triples,
  empty (or absent) `enrichments.jsonl`, SHACL still conforms, exit
  code 0. The bundle degrades cleanly.

### Step 7 — MCP / API / UI surface (**M**)

- `frontend/mcp_server/handlers.py`:
  - `file_detail` returns `llm_summary` when present.
  - `concept_detail` returns `llm_description` when present.
  - `repository_summary` uses `cbml4:fileSummary` for central-file
    entries instead of (or alongside) the file's docstring stub.
- `frontend/mcp_server/schemas.py` — output schemas updated with
  optional `llm_summary`, `llm_description`, `llm_provenance` fields.
- `frontend/ui/src/views/{FileDetail,ConceptDetail}.tsx` — render a
  *“AI-enriched”* badge with a hover-tooltip showing model +
  generated_at when provenance is present.
- Tests: extend `frontend/mcp_server/tests/test_vocab.py`-style
  pattern with `test_llm_enrich.py`.

### Step 8 — CLI plumbing + `scripts/run_l4.py` (**S**)

- `scripts/run_l4.py` — registers L1+L2+L3+L4. Default model
  configurable; flags:
  - `--llm-model llama3.1:8b` (default)
  - `--llm-host http://localhost:11434` (override `OLLAMA_HOST`)
  - `--llm-scope files,concepts,schemas` (comma-list of kinds)
  - `--llm-no-cache` (force re-generation; verifier-only)
  - `--llm-cache-dir <path>` (override `CBM_LLM_CACHE`)
- Add `--llm-enrich` to `scripts/run_l3.py` and
  `scripts/run_xrefs.py` (implies the plugin gets registered;
  uses defaults for everything else).
- README section "LLM enrichment (optional)" with the three
  commands above.

### Step 9 — Documentation (**S**)

- `docs/llm-enrich.md` — maintainer's reference. RDF predicates,
  cache layout, prompt versioning rules, stability contract, how to
  add a fourth enrichment kind.
- `docs/llm-enrich-plan.md` (this file) — marked as `Status:
  shipped` and frozen; future plans go in new files.
- README "Design docs" — add the new doc.

### Step 10 — Determinism harness for CI (**S**)

- `tests/verify_llm_enrich_warm.py` — runs the full pipeline twice
  with a pre-seeded cache fixture (small, ~5KB JSON files committed
  under `tests/fixtures/llm_cache/`). Asserts byte-identical bundles
  on the second run. This is the only LLM-dependent test that runs
  in CI; the network-touching tests are gated behind
  `CBM_TEST_OLLAMA=1`.

## Verifier matrix (final state)

| Verifier | Network | Purpose |
|---|---|---|
| `verify_llm_enrich.py` | No | empty plugin = bundle-identical; cardinality |
| `verify_llm_enrich_cache.py` | No | cache key stability, atomic writes |
| `verify_llm_enrich_prompts.py` | No | prompt SHA matches registered version |
| `verify_llm_enrich_warm.py` | No | warm-cache determinism |
| `verify_llm_enrich_offline.py` | No (uses unreachable host) | degrades cleanly |
| `verify_llm_enrich_live.py` | Yes (`CBM_TEST_OLLAMA=1`) | end-to-end real model |

## Sequencing dependencies

```
Step 1 (schema)
  └─ Step 2 (client + cache)
       └─ Step 3 (prompt registry + file_summary)
            └─ Step 4 (RDF emission)
                 ├─ Step 5 (concept_description, schema_purpose)
                 │    └─ Step 7 (MCP/UI surface)
                 └─ Step 6 (determinism + offline)
                      └─ Step 10 (CI harness)
       Step 8 (CLI) — anytime after Step 4
       Step 9 (docs) — last
```

Estimated total: 6–10 working days for a single developer, given the
shape and rigor matches the L3 controlled-vocabulary absorption.

## Out of scope (explicit)

- **Cloud LLM providers.** OpenAI/Anthropic/Gemini are interesting but
  introduce credential management, rate limits, cost accounting, and
  data-residency questions that don't fit a local-first design.
  Possible follow-up: a `CloudClient` that satisfies the same
  interface as `OllamaClient`. Don't build it pre-emptively.
- **Streaming responses.** Ollama supports it; we don't need it.
  Enrichment is batch.
- **Re-summarization on partial changes.** If a file changes, we
  re-call (cache miss on the new `content_sha256`). We do not try to
  diff-summarize against the old version.
- **Per-PR enrichment** ("summarize this change"). That's a different
  product. cbm enriches *snapshots*, not deltas.
- **Editable enrichments.** Users cannot override a generated
  `fileSummary` from outside the LLM. If they could, the bundle would
  carry human-authored content keyed by model+prompt, which breaks
  the cache invariant. If a manual override layer is desired, it
  belongs in `cbml5:` (annotations), not `cbml4:`.

## Risks and open questions

1. **Prompt drift erodes cache value.** Every prompt edit invalidates
   every entry. Mitigated by versioning the prompt files and being
   conservative about edits — but the first months will see churn.
2. **Model availability assumption.** The plan assumes the user has
   Ollama installed and can pull `llama3.1:8b` (or whichever default).
   We document the dependency clearly and degrade gracefully; we
   don't ship Ollama.
3. **Determinism boundary.** Warm-cache determinism is the promise we
   can keep. Cold-cache determinism depends on the model. We need to
   be loud about this in the docs so no one builds workflows on
   cold-cache reproducibility.
4. **Output quality variance across models.** A `llama3.1:8b`
   `fileSummary` and a `qwen2.5:32b` `fileSummary` are different
   artifacts. Provenance fields exist precisely so consumers can
   filter by model — but the UI should not mix outputs across models
   without showing which one wrote each line.
5. **What if a future Ollama feature breaks the cache key?** New
   sampling params or features (e.g., reasoning tokens) would alter
   outputs without changing the key. Mitigation: bump
   `PROMPT_REGISTRY` version any time we change *how* we call the
   model, not just *what* we send.
6. **Bundle size impact.** Each enrichment is ~50–300 bytes in the
   sidecar. ~1k files × 200B ≈ 200KB per bundle. Trivial vs. the
   existing AST sidecar.

## What "shipped" looks like

A user runs:

```bash
ollama pull llama3.1:8b
scripts/run_l4.py --repo /path/to/repo --out _tmp/out
```

…and 15 minutes later has a bundle where:

- `concept_detail("behavior")` via MCP returns the existing curated
  metadata *plus* a paragraph explaining what "behavior" means in
  this specific codebase, anchored to file paths and example chunks.
- `file_detail("path/to/important.py")` returns the file's
  one-sentence purpose alongside the existing imports/chunks.
- `repository_summary` reads as a curated digest, not a stats dump.
- A second `run_l4.py` invocation over the unchanged repo finishes
  in seconds, byte-identical to the first.
- Disabling Ollama and re-running succeeds with the bundle silently
  losing its enrichment layer, no errors.

That is the bar. Anything that doesn't hit it should be deferred.
