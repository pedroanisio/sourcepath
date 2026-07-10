---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-10"
---

# Features — codebase-mapper (SourcePath)

Complete inventory of user-facing capabilities, verified against source at
commit `1aa19f0` (2026-07-10). Every bullet names its entry point; nothing
here is aspirational. Honest limits are listed at the end. Tool version
0.5.0, vocabulary v1.

## At a glance

SourcePath unfolds a source repository into an inspectable knowledge bundle
(RDF graph + JSON sidecars), then serves it through six report generators, a
REST API, a read-only MCP server, a React UI, an offline static site, and a
3D code-terrain map — with mechanically derived facts, deterministic
derivations, and LLM-authored enrichment kept explicitly separated at every
surface.

## 1. Mapping pipeline (L1)

- One-command mapping: `codebase-mapper --repo <path|git-url> --out <dir>`
  (console script, [codebase_mapper/cli.py](../codebase_mapper/cli.py)).
- Ingestion from local paths, HTTPS/SSH/`file://` git URLs, and
  `github.com/owner/repo` shorthand; branch/tag/SHA via `--state`
  ([inspection/repo_source.py](../codebase_mapper/inspection/repo_source.py)).
- Fast shallow clones by default; opt-in history deepening
  (`CBM_UNSHALLOW=1`) recovers per-file commit-time provenance without
  historical blobs; clone workspace pinning via `CBM_WORK_DIR`.
- Excludes: repeatable `--exclude` globs merged with a per-repo
  `.cbmignore`; patterns recorded in the manifest.
- Parallel AST extraction (`CBM_EXTRACT_WORKERS`, tree-sitter releases the
  GIL) and parallel record enrichment (`CBM_ENRICH_WORKERS`); garbage values
  degrade to serial with a logged warning, identical output either way.
- Error-free mapping guarantees: per-file crash containment (a pathological
  file becomes a disclosed `extraction_errors` entry, never an aborted run),
  C-macro neutralization derived from the repo's own `#define`s
  ([inspection/macro_neutralize.py](../codebase_mapper/inspection/macro_neutralize.py)),
  and disclosed AST-depth truncation.
- Progress banners + throttled per-pass progress lines on stderr.

## 2. Language support

- Full AST + import analysis via tree-sitter: Python, TypeScript/JavaScript,
  Rust, Ruby, Go, Java, Kotlin, C, C++, Objective-C/C++, Swift
  ([inspection/_builtins.py](../codebase_mapper/inspection/_builtins.py)).
- Grammar-free analyzers: Dart, Clojure (s-expression reader), COBOL
  (column-aware).
- Lightweight line-oriented extractors: assembly, Kconfig, devicetree, Make
  ([inspection/languages/lightweight.py](../codebase_mapper/inspection/languages/lightweight.py)).
- Per-language import resolution against in-repo indices (Python module
  index, tsconfig aliases, Rust workspaces, Go module, Swift/Dart/Java/
  Kotlin/C-family indices), separating `imports` from `importsExternal`.
- Cross-file refinement: `.h` retagged Objective-C/C++ by sibling evidence;
  `.m` ObjC-vs-MATLAB content sniff. Dozens more languages get census tags
  without deep analysis.
- Rust extras: `rust_items.jsonl` sidecar of every attribute-bearing item;
  inline-test detection; attribute distribution surfaced downstream.

## 3. The bundle (emitted artifacts & epistemics)

- `inventory.ttl` (+ optional canonical JSON-LD twin), `run_manifest.json`,
  `shapes.shacl.ttl`, `ontology-mapping.ttl`, `ast_coverage.json`,
  content-addressed `blobs/` (skippable), plus plugin sidecars: chunk and
  embedding artifacts, `concepts.json`, `xrefs.jsonl`, `enrichments.jsonl`
  ([emission/application/emit_bundle.py](../codebase_mapper/emission/application/emit_bundle.py)).
- Every artifact self-reports sha256 + size in the manifest; reports
  independently recompute them.
- SHACL self-validation on every emit; skipping it (`--skip-shacl`) is
  recorded as skipped, never passed off as conforming.
- Always-present `degradations` list: shallow-git provenance, LLM
  self-disable, depth truncation, and other partial results are disclosed
  machine-readably (empty list = asserted healthy, PALS's Law).
- `ast_coverage.json` includes the "silent zero" honesty column: files that
  parsed cleanly yet yielded no symbols are listed, not hidden.
- Kernel-scale cost controls: `--no-jsonld`, `--skip-shacl`, oxigraph fast
  emit path with rdflib fallback (engine recorded in the manifest).

## 4. Analysis layers & extension model

- L2 chunks + embeddings ([plugins/chunks_embeddings/](../plugins/chunks_embeddings/)):
  symbol-level chunks (Python/TS/JS) or whole-file chunks; backends
  `sbert` (all-MiniLM-L6-v2, 384-dim normalized) and `hash` (deterministic,
  dependency-free); embedding truncation disclosed per chunk.
- L3 concept graph ([plugins/concept_graph/](../plugins/concept_graph/)):
  identifier splitting → canonical SKOS concept set with co-occurrence
  edges; curated controlled vocabulary (`software_primitives.yaml`) with
  `--concept-vocab` override and `--no-builtin-vocab` opt-out.
- Symbol xrefs ([plugins/symbol_xrefs/](../plugins/symbol_xrefs/)): `calls` /
  `subclassOf` / `overrides` / `references` edges across 8 languages, each
  edge carrying its resolution level (`exact`/`heuristic`/`ambiguous`) and
  unresolved reasons.
- L4 LLM enrichment, opt-in ([plugins/llm_enrich/](../plugins/llm_enrich/)):
  file summaries, concept descriptions, schema purposes via local Ollama;
  content-addressed cache for offline/CI determinism; per-record provenance
  receipts (model, prompt sha, target sha, timestamp); unreachable backend
  → self-disable with a counted degradation, never a broken bundle.
- Plugin architecture: seven registry hook points (language analyzer, import
  resolver, record enricher, aggregator, graph contributor, shape
  contributor, artifact emitter) in
  [shared_kernel/extensions.py](../codebase_mapper/shared_kernel/extensions.py).

## 5. Reconstruction & decomposition

- `--reconstruct`: byte-identical source rebuild from inventory + blobs.
- `--regenerate`: blob-free semantic regeneration from `cbm:astSummary`
  alone, with a JSON report; `--verify-roundtrip` does map→rebuild→compare
  in one shot.
- `cbm.py repair`: streaming post-hoc bundle fixes (concept components,
  datetime normalization, manifest rebuild) at bounded memory.
- Decomposer (`python -m decomposer <bundle>`): architecture-style
  detection, module cycles, quality gates; emits YAML decomposition,
  Markdown report, per-part symbol maps ([decomposer/](../decomposer/)).
- Recomposer (`python -m recomposer <decomposition.yaml>`): ordered
  natural-language build plan (Markdown/YAML) with skipped phases and open
  assumptions disclosed ([recomposer/](../recomposer/)).

## 6. Reports & visualization (unified CLI: [scripts/cbm.py](../scripts/cbm.py))

- `report` — Structural X-Ray in HTML / Markdown / JSON: hash verification,
  census, graph facts, test evidence, concept districts; loads RDF through
  a persistent per-bundle pyoxigraph store
  ([scripts/cbm_report.py](../scripts/cbm_report.py)).
- `report-rs` — Rust-rendered 8-page PDF that streams multi-GB
  `inventory.jsonld` and recounts it independently of the manifest
  ([tools/cbm-report/](../tools/cbm-report/), shim
  [scripts/cbm_report_rs.py](../scripts/cbm_report_rs.py)).
- `dossier` — 100+ page typeset A4 PDF ("Measured Ink" design system)
  ([scripts/cbm_dossier.py](../scripts/cbm_dossier.py)).
- `pdf` — authored Markdown → themed print-quality PDF with callouts,
  confidence pills, vector charts; refuses to render without the
  disclaimer frontmatter ([scripts/report_to_pdf.py](../scripts/report_to_pdf.py)).
- `site` — fully offline static HTML bundle browser reusing the backend's
  loader; provenance tiers on every page
  ([scripts/generate_static_site.py](../scripts/generate_static_site.py)).
- `terrain` — self-contained WebGL2 3D "code terrain": embedding-projected
  districts, chunk-density elevation, import-graph roads, build-tide
  layers, cycle and impact-flood overlays; seeded for stable spatial memory
  ([scripts/cbm_terrain.py](../scripts/cbm_terrain.py)).
- Shared epistemics: one "Evidence basis & confidence" banner across all
  generators (pinned by tests), FACT / DERIVED / UNVERIFIED tier tags, and
  mechanical caveats computed from the manifest itself.
- Declarative reporting contract: 30-component query catalog + JSON Schema
  ([docs/reporting/](../docs/reporting/)), contract-tested; executor not yet
  built (see limits).
- Standardized output naming: `<source>__<kind>__<UTC-timestamp>` under
  `CBM_REPORTS_DIR`, never overwriting a prior run.

## 7. Backend API ([frontend/backend/app.py](../frontend/backend/app.py))

- 13 JSON endpoints: bundle list, summary, file/symbol/concept graphs,
  chunk browse + semantic search, chunk blobs, file/concept/chunk detail,
  transitive impact analysis, health.
- Fail-closed perimeter: bearer token (`CBM_API_TOKEN`) or explicit
  anonymous opt-in; CORS allow-list, never wildcard.
- Multi-bundle: single-dir (`CBM_OUTPUT_DIR`) or root enumeration
  (`CBM_BUNDLES_ROOT`) with per-request `?bundle=` selection.
- Semantic search over sbert vectors with disclosed lexical fallback.

## 8. MCP server ([frontend/mcp_server/](../frontend/mcp_server/))

- 16 read-only tools with strict I/O schemas: orientation
  (`orient_bundle`, `bundle_summary`, `repository_summary`), navigation
  (`list_files`, `file_detail`, `imports_of`, `imported_by`,
  `file_impact`), chunks (`list_chunks`, `chunk_detail`, `chunk_blob`,
  `semantic_neighbors`), concepts (`concept_detail`,
  `concept_neighborhood`), `items_by_attribute` (Rust attributes), and a
  hardened `sparql` tool (opt-in gate, SELECT/ASK only, 10 s / 1000-row /
  10 000-char caps).
- MCP resources (`cbm://` manifest, summary, shapes, files, chunks,
  concepts; manifest subscribable) and prompts (`orient`,
  `explore_concept`, `trace_dependency`).
- Auth: static bearer or full JWT (JWKS / pinned key, audience, issuer,
  scope); stdio + streamable HTTP transports; HTTP refuses to mount
  without a verifier.
- Structured audit log per call (args digest, latency, status) and
  per-tool wall-clock budgets; bundle watcher auto-discovers new bundles.

## 9. UI & deployment

- React UI ([frontend/ui/](../frontend/ui/)): Dashboard, file/symbol/concept
  graph views (Cytoscape), chunk search, file/chunk/concept detail pages,
  bundle picker with persistence, LLM-enrichment card. Vitest suite.
- Docker: one-command analyzer image (hash backend; `WITH_SBERT=1`
  variant) and a compose stack (nginx-served UI + backend, read-only
  bundle mount) ([frontend/docker-compose.yml](../frontend/docker-compose.yml)).
- `.env` autoload at every entry point: nearest `.env` walking up to the
  repo boundary, real environment always wins
  ([shared_kernel/settings.py](../codebase_mapper/shared_kernel/settings.py));
  [.env.example](../.env.example) is the enforced canonical env-var inventory.
- Make workflows for install, lint, every test group, analysis runs,
  ontology/ABox validation, Rust-crate build, docker, packaging
  ([Makefile](../Makefile)); clean timestamped source zips (`dist-zip`).
- L4 model benchmarking harness
  ([scripts/bench_llm_models.py](../scripts/bench_llm_models.py)).

## 10. Verification infrastructure

- ~106 test files: 52 standalone `verify_*.py` verifiers + 54 pytest
  suites, grouped in Make (core, vocab, languages, Rust, LLM
  offline/online, drift, reporting, Rust crate).
- Drift guards keep docs and code in lockstep: README↔verifiers,
  CLI↔README, `.env.example`↔actual env reads, version pins, dependency
  and doc hygiene, reporting contract ↔ view-model catalog.
- Golden-repo E2E with hand-written expected projections; SHACL dimension
  shapes verified with injected violations; byte-identical roundtrip and
  regenerate verifiers.
- FLAM: `__file_meta__` in-file metadata convention (roles, rules,
  severities) on tools with load-bearing constraints.
- CI ([.github/workflows/lint.yml](../.github/workflows/lint.yml)):
  import-boundary linting, mypy, and a thin verify job.

## Known limits (stated, not hidden)

- The declarative reporting view model is a contract-tested **spec without
  an executor**; the UI it implies does not exist.
- Concept canonicalization is prototype-grade (naive plural strip, no
  lemmatizer) — self-documented in the plugin.
- CI covers lint/mypy plus a thin verifier slice; the full `make test`
  surface runs locally, not in CI.
- Stale docstrings in `symbol_xrefs` and `llm_enrich` still describe
  earlier skeleton phases; the features themselves are live and tested.
- Many languages receive census tags only (no deep analysis) — the
  language matrix in §2 is the analyzed set.
