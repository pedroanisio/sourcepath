# sourcepath (aka: codebase_mapper)

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](./DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Maps source code repositories into RDF graphs (turtle + JSON sidecars). The
host classifies files, extracts per-language AST summaries, resolves imports,
and infers tests/dependency edges. The pipeline runs through a registry of
pluggable extension points; companion plugins layer chunks/embeddings and a
concept graph on top.

## Goals

**Fully support the TIOBE index top 50 languages, cumulative since 2026.**
Any language that appears in a TIOBE top 50 from 2026 onward joins the goal
set permanently ([docs/goals/tiobe-top50.yaml](./docs/goals/tiobe-top50.yaml)
— baseline: the July 2026 index). "Fully support" means first-class per
[docs/SPEC_FIRST_CLASS_LANGUAGE.md](./docs/SPEC_FIRST_CLASS_LANGUAGE.md):
detection, AST analyzer, import resolver, symbol-level chunking, LLM summary
eligibility, and a test suite.

Progress is not claimed in prose — it is measured. `make check` probes the
codebase mechanically and holds it against the committed ledger, failing on
fabricated support, stale ledger entries, and regressions of already-shipped
languages:

```bash
make check   # language-goal ledger + drift + schema-shape verifiers
```

## Layout

```
codebase_mapper/        host package
├── inspection/         classification, repo sourcing, AST extraction, imports
│   ├── languages/      per-language analyzers and import resolvers
│   └── pipeline.py     map_codebase() — drives inspection registries
├── emission/           bundle emission, reconstruction, regeneration
│   ├── application/    use-case services such as emit_bundle and regenerate
│   └── infrastructure/ RDF, storage, and controlled-vocabulary adapters
├── shared_kernel/      extension protocols, registries, constants, namespaces
├── cli.py, __main__.py CLI entry point
└── ...
plugins/
├── chunks_embeddings/  source chunks + sentence-transformer/hash embeddings
├── concept_graph/      identifier splitting + canonical concept set + SKOS
├── symbol_xrefs/       symbol-level xref edges (cbmxr:Edge)
└── llm_enrich/         L4 LLM-authored annotations (cbml4:* triples) — opt-in
scripts/
├── run_l2.py           host + chunks_embeddings registered
├── run_l3.py           host + chunks_embeddings + concept_graph (--no-l2 skips L2)
├── run_xrefs.py        host + chunks_embeddings + symbol_xrefs (+ --concepts opt-in)
└── run_l4.py           host + L2 + L3 + L4 llm_enrich (Ollama-backed; full L1+L2+L3+L4)
frontend/
├── backend/            FastAPI service that reads an output bundle and serves
│                       summary/graph/chunk/concept JSON to the UI
├── mcp_server/         read-only MCP tools/resources/prompts over bundles
└── ui/                 React UI (scaffold, in progress)
tests/
├── verify_l2.py        chunks_embeddings contract suite
├── verify_l3.py        concept_graph contract + cross-layer (with/without L2)
├── verify_xrefs.py     symbol_xrefs schema/vocab/sidecar
├── verify_llm_enrich*.py  L4 llm_enrich contract suite (9 verifiers: basic,
│                       cache, prompts, file_summary, RDF, aggregator,
│                       determinism, offline, CLI, CI-determinism)
└── verify_drift_p*.py  documentation and contract drift guards
static/
├── schemas/            vendored industry-standard XSDs (IEEE 12207/29148,
│                       IEC 5055, EIC, DDD v3, C4, AST, python-metacode,
│                       ddd-python-bridge); used as classifier fixtures and
│                       a future vocab seed. See static/schemas/ for the
│                       full inventory.
└── proto/dsl/v2/       vendored protobuf contracts from the
                        requirements.engineering.dsl.v2 family (sibling
                        project repo-intel); typed-schema fixture parallel
                        to static/schemas/.
```

## Install

```bash
pip install -e .
```

## Run

```bash
# Host only
python -m codebase_mapper --repo /path/to/repo --out /tmp/out

# Host + chunks/embeddings
python scripts/run_l2.py --repo /path/to/repo --out /tmp/out --backend sbert

# Host + chunks/embeddings + concept graph
python scripts/run_l3.py --repo /path/to/repo --out /tmp/out --backend sbert

# Host + chunks/embeddings + concept graph + symbol xrefs
python scripts/run_xrefs.py --repo /path/to/repo --out /tmp/out --backend hash --concepts

# Full L1+L2+L3+L4 (opt-in LLM enrichment via Ollama; see the "LLM enrichment"
# section below for caveats and PALS's-Law framing).
python scripts/run_l4.py --repo /path/to/repo --out /tmp/out
```

`--backend sbert` uses `sentence-transformers/all-MiniLM-L6-v2` in-process;
`--backend ollama` embeds through a running Ollama server at `$OLLAMA_HOST`
(default model `nomic-embed-text`, override with `--ollama-embed-model`) for
real vectors without the torch stack; `--backend hash` uses a deterministic
SHA-256 fake (no semantics, useful for contract tests). See
[docs/analyze.md](docs/analyze.md#embedding-backends) for the trade-offs.

`--repo` accepts either a local path or a Git URL. GitHub HTTPS, SSH, and the
`github.com/OWNER/REPO` shorthand are supported. Remote repositories are cloned
into a temporary directory, analyzed, and removed when the run exits.

```bash
python scripts/run_xrefs.py \
  --repo https://github.com/OWNER/REPO.git \
  --out _tmp/repo-map \
  --backend hash \
  --concepts

python scripts/run_xrefs.py \
  --repo git@github.com:OWNER/REPO.git \
  --out _tmp/repo-map \
  --backend sbert \
  --concepts

python scripts/run_xrefs.py \
  --repo github.com/OWNER/REPO \
  --out _tmp/repo-map \
  --backend hash \
  --concepts
```

For a complete walkthrough, including Docker usage, branch selection, excludes,
and bundle inspection, see [docs/analyze.md](docs/analyze.md).

### Docker

Build an isolated analyzer image:

```bash
docker build -t codebase-mapper .
```

Run all layers against a GitHub URL:

```bash
mkdir -p _tmp
docker run --rm -v "$PWD/_tmp:/work" codebase-mapper \
  https://github.com/OWNER/REPO.git --out /work/repo-map
```

The default image is optimized for `--backend hash`. Build semantic embedding
dependencies only when needed:

```bash
docker build --build-arg WITH_SBERT=1 -t codebase-mapper:sbert .
```

### Excluding files

Per-invocation: `--exclude PATTERN` (repeatable; POSIX-glob; bare names like
`.repo` also exclude descendants). Available on the host CLI and on
`scripts/run_l2.py` / `scripts/run_l3.py` / `scripts/run_xrefs.py`.

Per-repo: drop a `.cbmignore` at the repo root. One pattern per line, `#`
comments, blank lines OK. Patterns merge with `--exclude` and appear in
`run_manifest.json`'s `exclude_patterns`.

```
# .cbmignore
.repo
vendor/**
docs/_build/**
```

## Visualize

`frontend/backend` is a FastAPI service that reads an output bundle and exposes
JSON endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /api/bundles` | List discoverable bundles and the selected bundle. |
| `GET /api/summary` | Manifest counts, language/type breakdown, embedding backend. |
| `GET /api/file-graph` | Top files by import-degree and their import edges. |
| `GET /api/symbol-graph` | Symbol-level xref graph. |
| `GET /api/concept-graph` | Concept cooccurrence graph. |
| `GET /api/chunks` | Browse or lexically search chunks. |
| `POST /api/chunks/search` | Semantic nearest neighbors or lexical fallback. |
| `GET /api/chunk-blob/{sha}` | Raw chunk text blob. |
| `GET /api/concept/{name}` | Concept detail. |
| `GET /api/file/{path}` | File detail. |
| `GET /api/impact/{path}` | File impact graph. |
| `GET /api/chunk/{idx}` | Chunk detail. |
| `GET /api/healthz` | Health check. |

```bash
# Point at any output dir containing run_manifest.json + inventory.ttl +
# embeddings.npz + embeddings_meta.json + concepts.json.
# The API fails closed: opt in to anonymous access for local dev (as below)
# or set CBM_API_TOKEN=<secret> and authenticate with `Authorization: Bearer`.
CBM_OUTPUT_DIR=_tmp/usl-ng-core-map CBM_ALLOW_ANONYMOUS=1 \
  .venv/bin/uvicorn frontend.backend.app:app --port 8000 --reload
```

Semantic search (`POST /api/chunks/search`) uses cosine NN over the chunk
matrix when the bundle's embedding backend is a sentence-transformer; hash
backends fall back to substring matching. The bundle is loaded once per
process — restart to pick up a new output dir. See
[frontend/backend/README.md](frontend/backend/README.md) for endpoint details.

## Report

One front door for every report generator (each tool also runs standalone;
its own `--help` is the source of truth):

```bash
python scripts/cbm.py report    --bundle _tmp/out   # structural X-ray: HTML / MD / JSON
python scripts/cbm.py report-rs _tmp/out            # Rust-rendered PDF (multi-GB bundles)
python scripts/cbm.py dossier   --bundle _tmp/out   # 100+ page typeset A4 PDF dossier
python scripts/cbm.py pdf docs/reports/<name>.md    # authored Markdown -> themed PDF
python scripts/cbm.py site      --bundle _tmp/out   # offline static site (tree)
python scripts/cbm.py repair    --bundle _tmp/out   # post-hoc data-quality fixes
python scripts/cbm.py cartogram _tmp/out           # interactive D3 map (regions + flows)
python scripts/cbm.py terrain   --bundle _tmp/out   # 3D code-terrain map (one HTML file)
python scripts/cbm.py walkthrough --bundle _tmp/out # narrated five-scene demo page
```

Every generator writes to `CBM_REPORTS_DIR` (default `reports/`, gitignored)
unless you pass an explicit output path. Names are standardized —
`<source>__<kind>__<UTC-timestamp>[.ext]`, with a `-N` bump so two runs never
overwrite each other — and `site` uses the same stem as its tree root. Bundle
directories therefore hold only measured artifacts; every derived render lands
in one predictable place. The one exception is `repair`, which reconstructs a
bundle's own sidecars and must write them beside `inventory.ttl`.

Two structural read paths exist by design: `report`/`dossier` (Python) load
the graph through a per-bundle persistent pyoxigraph store and do the graph
analytics; `report-rs` ([tools/cbm-report](tools/cbm-report/README.md), Rust)
streams `inventory.jsonld` from disk and recounts it independently of the
manifest — built for bundles where a graph load is the bottleneck
(`cargo build --release --manifest-path tools/cbm-report/Cargo.toml` first).

`cartogram` ([tools/cbm-cartogram](tools/cbm-cartogram/README.md)) renders a
self-contained D3 HTML map of any repository — directory regions with import
and test projections — from a bundle's `inventory.jsonld`.

`terrain` renders one self-contained WebGL2 HTML map: geography is a seeded
t-SNE projection of per-directory mean chunk embeddings, elevation is chunk
density, and the L1 import graph supplies roads, build-order layers, impact
floods, dependency path tracing, and stress (strong-import × semantically-
distant) fault lines. Directory roll-up auto-fits large bundles
(kernel-scale ≈ 2 min); the page footer discloses every truncation and marks
derived views. Keep `--seed` fixed per repo so its geography stays
recognizable across regenerations.

`walkthrough` freezes five live MCP-surface queries (orientation →
keystone file → blast radius → concept neighborhood → semantic
question) into one self-contained demo page — the narrated companion
to `report`'s evidence-first X-ray. Each scene names the MCP tool that
answers it live; the only LLM-authored line (the L4 file summary, when
the bundle has one) is shown with model/prompt provenance, never as
fact.

Every generator separates evidence tiers (mechanical facts vs derived
computation vs unverified LLM output) under the shared
"Evidence basis & confidence" framing; the declarative reporting contract
lives in [docs/reporting/](docs/reporting/) (30-component catalog + JSON
Schema, pinned by `tests/verify_report_spec.py`). Authored narrative reports
and their workflow live in `docs/reports/` (gitignored deliverables).

## Decompose & recompose

The decomposer reads a bundle and emits a confidence-tagged structural
decomposition — parts and roles, Martin instability, cycles, architecture
hypotheses, quality gates, and a topological build order; the recomposer
consumes that YAML (never the raw bundle) and emits an ordered
natural-language rebuild plan. Bundle-derived evidence only; see
[decomposer/README.md](decomposer/README.md).

```bash
python -m decomposer _tmp/out --yaml d.yaml --report d.md   # [--symbols s.yaml]
python -m recomposer d.yaml --plan plan.md                  # [--yaml plan.yaml]
```

## Regenerate

`codebase_mapper.regenerate` materializes source from `inventory.ttl` and
`cbm:astSummary` **alone** — no `blobs/` directory required. Companion to
`reconstruct`, with a different fidelity model:

| Path | Source of truth | Fidelity |
|---|---|---|
| `reconstruct` | `inventory.ttl` + `blobs/` | **byte-identical** every file |
| `regenerate` (new) | `inventory.ttl` + `cbm:astSummary` | Python: **semantic** (re-parses to the same AST); TS/JS + Rust: **byte-identical** via leaf-text CST |

Currently supported by `regenerate`: Python, TypeScript (`.ts`, `.tsx`,
`.cts`, `.mts`), JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`), Rust
(`.rs`). Other files (markdown, configs, lockfiles, binaries) are
enumerated in the report under `ast_unsupported` / `no_ast_summary`
and not written to disk.

```bash
codebase-mapper --regenerate \
  --inventory _tmp/out/inventory.ttl \
  --out _tmp/regen \
  --report _tmp/regen-report.json
```

The report records `files_regenerated`, per-language `ok`/`failed` counts,
and the lists of files skipped or errored.

### What's lost

- **Python** (semantic-perfect): comments, blank lines, string-quote style,
  trailing commas. The regenerated source re-parses to the same
  `ast.dump`, but bytes differ. If byte-identical Python from TTL+AST is
  ever required, swap the `ast`-based extractor for a `libcst`-based one
  — `libcst` preserves the concrete syntax tree (comments + whitespace).
- **TS/JS** (byte-perfect): nothing — every leaf token plus interstitial
  gaps and any header/footer bytes are captured. Tree-sitter has no
  `unparse`, so the extractor stores enough of the CST to walk back to
  source.
- **Rust** (byte-perfect): nothing — same approach as TS/JS. The Rust
  extractor stores the full leaf-text CST under `ast_summary.cst_json`
  (schema_version 1) plus optional `header` / `footer` bytes for any
  content outside the parser's root span (rare, but possible with
  shebang lines).

### Size cost

`cbm:astSummary` grows with full-body capture. Measured ratios:

| Language | `ast_summary` JSON / source |
|---|---|
| Python | ~6.6× |
| TypeScript/JS | ~12.5× |
| Rust | ~10× (typical; varies with attribute density) |

`run_manifest.json` reports `counts.ast_full_bodies_python`,
`counts.ast_full_bodies_tsjs`, `counts.ast_full_bodies_rust`, and
`counts.ast_summary_total_bytes` so the cost is measurable per run.
If TTL size becomes a problem, the short retrofit is to move the
full-body literals to a sidecar `ast_summaries.jsonl` keyed by
`cbm:contentSha256` and reference them from `inventory.ttl` as URIs.

### Adding a new language

See [docs/regenerate.md](docs/regenerate.md) for the full contract:
the `ast_summary` shape, the `regenerate_<lang>_source(summary) -> str`
signature, registration in `_REGENERATORS`, and the
`verify_regenerate.py` test cases a new language must satisfy. The
doc closes with a **"Worked example: Rust (Stages 1-8)"** appendix
that maps every part of the contract to a concrete in-tree file —
copy-from-here when adding the next tree-sitter language.

## Inventory schema

`inventory.ttl` has one typed definition with three synchronized surfaces:

- **SHACL shapes** — `build_shacl_graph()` in
  [`rdflib_emitter.py`](codebase_mapper/emission/infrastructure/rdf/rdflib_emitter.py)
  is the enforcement authority: every emit validates the inventory graph
  against it and serializes it into the bundle as `shapes.shacl.ttl`.
- **Canonical Pydantic schema** —
  [`codebase_mapper/emission/domain/inventory_schema.py`](codebase_mapper/emission/domain/inventory_schema.py)
  is the typed mirror for Python consumers: the same cardinalities,
  datatypes, patterns, closed vocabularies, and edge-target classes,
  expressed as Pydantic validation.
  [`inventory_reader.py`](codebase_mapper/emission/infrastructure/rdf/inventory_reader.py)
  (`read_inventory()`) parses a bundle's inventory graph into it, raising
  `ValidationError` on any shape-violating content.
- **Drift guard** —
  [`tests/test_inventory_schema.py`](tests/test_inventory_schema.py)
  holds the two definitions in lockstep: the schema's predicate registry
  must equal the live shapes graph's `sh:path` set, the `FileType`/`Phase`
  enums must equal the controlled vocabularies in
  [`shared_kernel/vocabulary.py`](codebase_mapper/shared_kernel/vocabulary.py),
  and a roundtrip through the real emitter must validate. Runs with the
  pytest tree (`make test-units`).

## Extension model

`codebase_mapper.extensions` exposes seven protocols:

- `LanguageAnalyzer` — extracts a file's AST summary (first-match-wins by `.matches()`).
- `ImportResolver` — resolves a record's imports to in-repo paths + external packages.
- `RecordEnricher` — runs once per `FileRecord` after AST extraction.
- `Aggregator` — runs once per pipeline with full record visibility; output is stored at `ctx.indices[self.name]`.
- `GraphContributor` — adds triples to the inventory graph.
- `ShapeContributor` — adds SHACL shapes for the introduced classes/properties.
- `ArtifactEmitter` — writes sidecar files; returns a manifest fragment.

Each registry is iterated in `.name` sort order, so plugin authors use
prefixes like `l2_20_embeddings` / `l3_20_concepts` to control load order
across layers (L2 must run before L3 because L3 reads L2's index entry).
Built-in `LanguageAnalyzer` / `ImportResolver` wrappers auto-register at host
import for every supported language; `reset_registries()` re-registers them
after a clear. First-class languages (detection + analyzer + resolver + chunker
+ L4 summary + a verifier) — the TIOBE-50 first-class set tracked in
[docs/goals/tiobe-top50.yaml](docs/goals/tiobe-top50.yaml), plus config/markup
formats — are:

<!-- first-class-langs:start -->
Python, C, C++, JavaScript, TypeScript, Rust, Go, Swift, Ruby, Kotlin, Objective-C, Dart, CFML, PHP, SQL, HTML, CSS/SCSS, JSON, YAML, Shell
<!-- first-class-langs:end -->

`tests/verify_readme_coverage.py` fails `make check` if that list drifts from
the ledger — a newly first-class language cannot be silently undocumented.

## Controlled vocabulary

L3's concept graph ships with a curated set of ~40 atomic terms covering
intent-first domain primitives (`intent`, `behavior`, `contract`,
`effect`, …), universal code structure (`module`, `class`, `method`,
`function`, …), and edge/relation primitives (`edge`, `block_edge`,
`data_flow_edge`, …). Concepts matching a curated term get tagged with
`cbml3:conceptKind` (one of `domain-primitive` / `structural-primitive`
/ `relational-primitive`) plus a `cbml3:broaderCollection` link to a
per-kind `skos:Collection`. Aliases collapse common variants at
canonicalization time (`behaviour` → `behavior`, `func` → `function`,
`params` → `parameter`, …).

The vocabulary is loaded automatically; opt out or override via:

```bash
# Default — bundled software_primitives.yaml is live.
python scripts/run_l3.py --repo /path/to/repo --out /tmp/out

# Override with a custom YAML.
python scripts/run_l3.py --repo … --out … --concept-vocab my_vocab.yaml

# Disable curated tagging entirely (bundles look like pre-v1 L3).
python scripts/run_l3.py --repo … --out … --no-builtin-vocab
```

The MCP server surfaces typing through `concept_detail` (returns
`kind`/`broader` on curated concepts) and `concept_neighborhood`
(accepts an optional `kind` filter and attaches per-neighbor typing).
For the schema, SHACL shapes, extension/stability rules, and the file
map, see [docs/vocabulary.md](docs/vocabulary.md).

## LLM enrichment

L4 is the optional LLM enrichment layer. It calls a local Ollama
server to attach LLM-authored annotations in a new `cbml4:` namespace.
Three enrichment kinds:

- `cbml4:fileSummary` — one sentence per source-code `cbm:File`.
- `cbml4:conceptDescription` — one paragraph per curated `skos:Concept`.
- `cbml4:schemaPurpose` — one paragraph per file under `static/schemas/`.

Every triple carries provenance (`cbml4:*Model`, `cbml4:*PromptSha`,
`cbml4:*GeneratedAt`) and every output is content-addressed in
`~/.cache/cbm-llm/` — re-emits over an unchanged repo are byte-identical
(warm-cache determinism). Ollama unreachable → the bundle degrades
cleanly to L1+L2+L3, SHACL stays green.

Default off; opt in via flag. Default model `qwen2.5-coder:7b` (the
benchmark winner from [docs/llm-baseline-results.md](docs/llm-baseline-results.md)):

```bash
# Install Ollama and pull the default model.
ollama pull qwen2.5-coder:7b

# Full L1+L2+L3+L4 entry point with all knobs surfaced.
python scripts/run_l4.py --repo /path/to/repo --out /tmp/out

# Or short-form opt-in on top of run_l3/run_xrefs.
python scripts/run_l3.py --repo … --out … --llm-enrich
python scripts/run_xrefs.py --repo … --out … --llm-enrich   # implies --concepts

# Narrow the enrichment kinds (default is all three).
python scripts/run_l4.py --repo … --out … --llm-scope files
python scripts/run_l4.py --repo … --out … --llm-scope files,concepts
```

The MCP server surfaces enrichments on `file_detail.llm_summary`,
`file_detail.llm_schema_purpose`, `concept_detail.llm_description`,
plus short-form text on `repository_summary.central_files[*].llm_summary`
and `repository_summary.key_concepts[*].llm_description`. The React UI
renders an "AI-enriched" card with collapsible provenance. For the
RDF schema, SHACL shapes, cache layout, prompt-versioning rules,
extension procedure, and file map, see
[docs/llm-enrich.md](docs/llm-enrich.md).

## Verify

```bash
python tests/verify_roundtrip.py            # blob-based byte-perfect roundtrip
python tests/verify_regenerate.py           # TTL+AST regenerate (Python semantic + TS/JS byte)
python tests/verify_cpp.py                  # C++ classifier/analyzer/xref coverage
python tests/verify_dart.py                 # Dart analyzer/xref/generated-file coverage
python tests/verify_dependency_hygiene.py   # dependency hygiene regression guard
python tests/verify_language_goal.py        # TIOBE-50 goal ledger vs probed language support
python tests/verify_readme_coverage.py      # README language + tools lists vs ledger/tools (drift guard)
python tests/verify_doc_hygiene.py         # README disclaimer + active Markdown local links
python tests/verify_excludes.py             # --exclude flag + .cbmignore behavior
python tests/verify_java.py                 # Java analyzer/xref coverage
python tests/verify_objc.py                 # Objective-C / Objective-C++ coverage
python tests/verify_clojure.py              # Clojure analyzer/chunker/import-resolution coverage
python tests/verify_cobol.py                # COBOL analyzer/chunker/xref coverage (fixed + free format)
python tests/verify_go.py                   # Go analyzer/xref coverage
python tests/verify_sql.py                   # SQL analyzer/resolver/chunker first-class coverage
python tests/verify_html.py                  # HTML element-tree analyzer/resolver/chunker coverage
python tests/verify_css.py                   # CSS/SCSS rule analyzer/resolver/chunker coverage
python tests/verify_json.py                  # JSON recursive-descent AST analyzer/resolver/chunker coverage
python tests/verify_yaml.py                  # YAML (PyYAML node AST) analyzer/resolver/chunker coverage
python tests/verify_shell.py                 # Shell function AST (heredoc/quote/comment safe) analyzer/resolver/chunker
python tests/verify_php.py                   # PHP declaration AST + composer PSR-4 `use` resolution
python tests/verify_repo_source.py          # local path + Git URL --repo handling
python tests/verify_timestamps.py           # atime/mtime/ctime + gitCommitTime
python tests/verify_l2.py --backend hash    # chunks_embeddings contract
python tests/verify_l3.py                   # concept_graph contract + cross-layer
python tests/verify_ast_coverage.py         # per-language AST-extraction coverage guard
python tests/verify_golden_repo.py          # multi-language golden-repo end-to-end fixture
python tests/verify_progress.py             # map_codebase progress-reporting hooks
python tests/verify_dimension_shapes.py     # SPDX/architecture-dimension SHACL shapes
python tests/verify_vocab.py                # controlled-vocab loader
python tests/verify_vocab_emission.py       # controlled-vocab RDF + SHACL
python tests/verify_vocab_wiring.py         # controlled-vocab aggregator wiring
python tests/verify_vocab_pipeline.py       # controlled-vocab end-to-end pipeline
python tests/verify_llm_enrich.py           # L4 back-compat anchor (no-L4 vs L4-registered byte-identity)
python tests/verify_llm_enrich_cache.py     # L4 cache + OllamaClient
python tests/verify_llm_enrich_prompts.py   # L4 prompt-file SHA integrity
python tests/verify_llm_enrich_file_summary.py     # L4 file_summary enricher (requires Ollama)
python tests/verify_llm_enrich_rdf.py       # L4 RDF emission + SHACL + sidecar (requires Ollama)
python tests/verify_llm_enrich_aggregator.py       # L4 concept_description + schema_purpose (requires Ollama)
python tests/verify_llm_enrich_determinism.py      # L4 warm-cache determinism (requires Ollama)
python tests/verify_llm_enrich_offline.py   # L4 graceful degradation when Ollama is unreachable
python tests/verify_llm_enrich_degradation.py       # L4 self-disable disclosure (degradations entry in ctx.scratch)
python tests/verify_llm_enrich_cli.py       # L4 CLI surface (scripts/run_l4 + --llm-enrich flags)
python tests/verify_llm_enrich_ci_determinism.py    # L4 warm-cache determinism via committed cache fixture (no Ollama needed)
python tests/verify_bench_llm_models.py     # L4 model-benchmark harness contract
python tests/verify_xrefs.py                # symbol-xref schema/vocab/sidecar (Phase 1)
python tests/verify_xsd_fixture.py          # static/schemas/ classifier coverage
python tests/verify_proto_fixture.py        # static/proto/ classifier + import coverage
python tests/verify_repository_summary.py   # MCP repository_summary tool contract
python tests/verify_rust_ast.py             # Rust deep-AST items + chunker (Stage 1)
python tests/verify_rust_xrefs.py           # Rust intra/inter-file call edges (Stage 2)
python tests/verify_rust_tests_edges.py     # Rust tests-edges use-analysis + inline #[test] (Stage 3)
python tests/verify_rust_attribute_query.py # Rust attribute queryable surface (Stage 4)
python tests/verify_rust_super_self.py      # Rust self::/super:: use-path resolution (Stage 5)
python tests/verify_rust_regenerate.py      # Rust byte-identical regenerate (Stage 6)
python tests/verify_rust_ast_body_count.py  # Rust ast_full_bodies counter (Stage 7)
python tests/verify_shape_coverage.py       # drift-risk #4/#5 — emitted RDF predicates ⊆ SHACL shapes
python tests/verify_report_spec.py          # reporting contract — schema valid, examples pass, doc catalog ↔ schema enum
python tests/verify_api_field_parity.py     # backend Pydantic models ↔ ui/src/api.ts — field-level parity (drift C1)
python tests/verify_report_predicates.py    # report/dossier queried RDF predicates ⊆ emitter output (drift C3)
python tests/verify_report_rs_contract.py   # Rust cbm-report serde keys ⊆ emitted bundle artifacts (drift C2)
python tests/verify_requirements_mirror.py  # requirements.txt pins == pyproject pins — one resolved world (drift H8)
python tests/verify_ci_live_bundle.py       # CI cannot skip the backend suite and report green (BL-024)
python tests/verify_backend_image.py        # backend image copies every first-party module app.py imports (BL-029)
python tests/verify_grammar_disclosure.py   # a missing tree-sitter grammar discloses, never degrades silently (BL-036)
python tests/verify_make_wiring.py          # every test/verifier on disk is executed by a make target (drift H9)
python tests/verify_drift_p1.py             # drift-risk HIGH findings — port/env/response_model/concepts.json/version
python tests/verify_drift_p2.py             # drift-risk MODERATE findings — README↔verifiers, CLI↔README, langs, plugin-names, MCP tools
python tests/verify_drift_p3.py             # drift-risk LOW findings — meta-presence of cited guards + reinforcement
```

All verifiers resolve `repo_root` from their own `__file__`, so they
run correctly regardless of the caller's cwd.

### File timestamps

Every `cbm:File` carries four optional `xsd:dateTime` predicates:

| Predicate | Source | Notes |
|---|---|---|
| `cbm:atime` | `os.lstat(repo/path).st_atime` | last access |
| `cbm:mtime` | `os.lstat(repo/path).st_mtime` | last content change |
| `cbm:ctime` | `os.lstat(repo/path).st_ctime` | inode change (Linux) / creation (Win) |
| `cbm:gitCommitTime` | author timestamp of the last commit that touched the path | deterministic per commit; renames are not followed |

Captured on every run. `os.lstat` doesn't bump atime, so two consecutive
runs over an untouched working tree still produce byte-identical
`inventory.ttl` (the existing determinism guarantee). For non-HEAD
mappings the working tree may not match the mapped commit; in that case
the filesystem times reflect whatever's on disk and `cbm:gitCommitTime`
remains correct for the mapped commit.

## Design docs

In-flight design work lives under [docs/](docs/):

- [docs/regenerate.md](docs/regenerate.md) — extender's contract for
  adding a new language to `_REGENERATORS`.
- [docs/analyze.md](docs/analyze.md) — local path, GitHub URL, Docker, and
  bundle inspection workflow.
- [docs/vocabulary.md](docs/vocabulary.md) — L3 controlled vocabulary:
  schema, kinds, RDF predicates, SHACL, extension rules, file map.
- [docs/llm-enrich.md](docs/llm-enrich.md) — L4 LLM enrichment layer:
  RDF predicates, SHACL shapes, cache layout, prompt versioning,
  extension procedure, file map.
- [docs/llm-baseline-results.md](docs/llm-baseline-results.md) — the
  5-model benchmark that selected qwen2.5-coder:7b as the default.

Historical implementation plans and generated snapshot reports live under
[docs/archive/](docs/archive/). They are provenance records, not active
implementation guidance.
