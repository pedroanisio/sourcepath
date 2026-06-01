---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.7 (1M context) via Claude Code, with cbm-mcp introspection of bundle code-base-mapper@07f38de"
  date: "2026-05-14"
---

# Feature Report — `code-base-mapper`

> **Archive status:** Generated feature snapshot for bundle
> `code-base-mapper @ 07f38de`. Paths, counts, and risk statements below are
> historical and are not active documentation for the current checkout.

> Methodological note: this report is sourced from the CBM bundle
> `code-base-mapper` (commit `07f38dec2fedf2fc4bd689aae30bd629b80da5d3`,
> generated `2026-05-14T20:14:30Z`, tool version `0.5.0`, 278 files), via the
> `cbm-mcp` MCP server. All file/symbol references include
> `path:line` so claims can be audited directly.
> Subject to [@DISCLAIMER.md](../DISCLAIMER.md).

---

## 1. Identity & Shape

| Attribute              | Value                                                    |
| ---------------------- | -------------------------------------------------------- |
| Repo                   | `code-base-mapper`                                       |
| Commit                 | `07f38de` (branch `refactor/ddd-folder-structure`)       |
| Files                  | 278 (167 Python, 22 TypeScript, 9 Rust, 5 Protobuf, etc.) |
| Chunks (L2)            | 1,415                                                    |
| Concepts (L3)          | 1,951                                                    |
| SHACL conformance      | `true`                                                   |
| Source / test ratio    | 127 source files vs 78 test files (~0.61)                |
| Dependency edges       | 350 internal imports, 97 external, 40 declared, 368 pinned |
| Top entry points       | `codebase_mapper/__main__.py`, `codebase_mapper/cli.py`, `frontend/backend/app.py`, `frontend/mcp_server/__main__.py`, `frontend/mcp_server/server.py` |

The codebase is organised as a multi-stage **codebase-knowledge-graph pipeline** with three deployable surfaces: a CLI mapper, a FastAPI/HTTP backend, and an MCP server (stdio + HTTP) with a React UI.

---

## 2. Architectural Layers (DDD-style)

The repo follows a **shared-kernel + plugin** layout. The vocabulary of "L1/L2/L3/L4" is the unit of analysis and is reflected in the RDF namespaces (`cbm`, `cbml2`, `cbml3`, `cbml4`) emitted by the bundle itself.

### 2.1 L1 — Host inventory (file + import graph)

Lives under [codebase_mapper/inspection/](../codebase_mapper/inspection/). Responsible for:

* Walking the repo, honouring `.cbmignore` / `.gitignore` ([codebase_mapper/inspection/classify.py:303-349](../codebase_mapper/inspection/classify.py#L303-L349)).
* Classifying files into types (`source_code`, `test_code`, `dependency_manifest`, `lockfile`, `container`, `ci_cd`, …) via the 250-line classifier ([codebase_mapper/inspection/classify.py:13](../codebase_mapper/inspection/classify.py#L13)).
* Running pluggable **language analyzers** that produce AST summaries and import statements. Built-ins for **Python, TS/JS, Rust, Ruby, Go, C, Kotlin, Swift, Dart** ([codebase_mapper/inspection/_builtins.py:46-292](../codebase_mapper/inspection/_builtins.py#L46-L292)).
* Resolving imports per language via matching `ImportResolver` implementations.
* Parsing dependency manifests for Python, npm, Cargo, Gem, Go, Pubspec, Gradle, Swift Package ([codebase_mapper/inspection/manifests.py:27-276](../codebase_mapper/inspection/manifests.py#L27-L276)).
* Parsing lockfiles for pinned dependencies ([codebase_mapper/inspection/lockfiles.py](../codebase_mapper/inspection/lockfiles.py)).
* Inferring `tests_edges` between test files and tested subjects ([codebase_mapper/inspection/tests_edges.py](../codebase_mapper/inspection/tests_edges.py)).
* Tree-sitter setup for non-Python grammars: TS/JS, Rust, Ruby, Go, C, Kotlin, Swift ([codebase_mapper/ts_setup.py](../codebase_mapper/ts_setup.py)).

Orchestrated by [codebase_mapper/inspection/pipeline.py](../codebase_mapper/inspection/pipeline.py) — function `map_codebase` (L36–L228). This file has the highest import-degree in the repo (29), confirming it as the L1 hub.

### 2.2 L2 — Chunks + embeddings

Lives under [plugins/chunks_embeddings/](../plugins/chunks_embeddings/) (six modules).

* **`ChunkExtractor` enricher** ([plugins/chunks_embeddings/chunker.py:47-72](../plugins/chunks_embeddings/chunker.py#L47-L72)) splits source files into NIF-spanned chunks:
  * Python via AST (`_chunk_python` L96).
  * TS/JS via tree-sitter (`_chunk_tsjs` L176), including class methods and exported decls.
  * Rust via tree-sitter (`_chunk_rust` L353), including `impl` and `mod` blocks.
  * Whole-file fallback for unsupported languages.
* **`Embedder`** ([plugins/chunks_embeddings/embedder.py](../plugins/chunks_embeddings/embedder.py)) computes embeddings; supports SBERT backend via `frontend/backend/requirements-sbert.txt`.
* **`backends.py`** abstracts the embedding provider ([plugins/chunks_embeddings/backends.py](../plugins/chunks_embeddings/backends.py)).
* **`graph_writer.py`** emits per-chunk RDF (cbml2:inFile / beginIndex / endIndex / embeddingRow).
* Runner: [scripts/run_l2.py](../scripts/run_l2.py).

### 2.3 L3 — Concept graph (SKOS)

Lives under [plugins/concept_graph/](../plugins/concept_graph/).

* **`ConceptAggregator`** ([plugins/concept_graph/concepts.py:127-344](../plugins/concept_graph/concepts.py#L127-L344)) canonicalizes identifiers via `canonicalize` (L84) — snake_case / camelCase / PascalCase splitting + lemmatisation — and builds cooccurrence skos:related edges.
* Curated **controlled vocabulary** of "software primitives" loaded from YAML ([codebase_mapper/emission/infrastructure/vocab/software_primitives.yaml](../codebase_mapper/emission/infrastructure/vocab/software_primitives.yaml)), with kinds `domain-primitive | structural-primitive | relational-primitive`. Loader at [codebase_mapper/emission/infrastructure/vocab/loader.py:78-160](../codebase_mapper/emission/infrastructure/vocab/loader.py#L78-L160).
* `identifier_splitter.py` (Splitter) handles edge cases of identifier segmentation.
* Top concepts in this bundle: `test` (897 occurrences, 46 files), `bundle` (242/38), `chunk`, `file`, `concept`, `graph`, `import_statement` (62/20, structural-primitive), `edge` (40/13, relational-primitive).
* Runner: [scripts/run_l3.py](../scripts/run_l3.py).

### 2.4 L4 — LLM enrichment (optional, advisory)

Lives under [plugins/llm_enrich/](../plugins/llm_enrich/) (8 modules + 3 prompt templates).

* **Ollama client** ([plugins/llm_enrich/client.py:55-156](../plugins/llm_enrich/client.py#L55-L156)) with explicit error types `OllamaUnreachable` / `OllamaModelMissing`.
* **Content-addressed cache** ([plugins/llm_enrich/cache.py:72-168](../plugins/llm_enrich/cache.py#L72-L168)) keyed by `(model, prompt_sha, target_sha)`; supports deterministic replay.
* **Prompts** versioned on disk: `file_summary.v1.txt`, `concept_description.v1.txt`, `schema_purpose.v1.txt`.
* **`LlmAggregator`** ([plugins/llm_enrich/aggregator.py:80-303](../plugins/llm_enrich/aggregator.py#L80-L303)) produces concept descriptions and schema-purpose annotations (post-L3 context).
* **`enricher.py`** computes per-file LLM summaries (per `--llm-scope files`).
* **`LlmGraphWriter`** ([plugins/llm_enrich/graph_writer.py:80-127](../plugins/llm_enrich/graph_writer.py#L80-L127)) emits RDF triples with **full provenance** (`cbml4:fileSummaryModel`, `cbml4:fileSummaryPromptSha`, `cbml4:fileSummaryGeneratedAt`). Each enrichment is treated as untrusted per the project's "LLM output is unverified by default" stance — see [CLAUDE.md](../CLAUDE.md) "PALS's LAW".
* **`LlmShapes`** ([plugins/llm_enrich/graph_writer.py:157-224](../plugins/llm_enrich/graph_writer.py#L157-L224)) emits matching SHACL shapes.
* Runner: [scripts/run_l4.py](../scripts/run_l4.py).
* Test fixtures with deterministic cache: [tests/fixtures/llm_cache/](../tests/fixtures/llm_cache/) (manifest + 7 frozen response files).

### 2.5 Symbol xrefs (cross-cutting plugin)

Lives under [plugins/symbol_xrefs/](../plugins/symbol_xrefs/). Resolves function/class-level cross-references per language:

* `python_resolver.py` (20.7 kB), `rust_resolver.py` (22.1 kB), `tsjs_resolver.py` (23.1 kB) — each a fully self-contained symbol resolver.
* `XrefAggregator` ([plugins/symbol_xrefs/aggregator.py:27-87](../plugins/symbol_xrefs/aggregator.py#L27-L87)) unifies them into edge + unresolved-reason indices, deterministically sorted.
* Emits `SymbolXrefEdge` and "unresolved reason" attributes in the graph.
* Runner: [scripts/run_xrefs.py](../scripts/run_xrefs.py).

### 2.6 Shared kernel & extension points

* [codebase_mapper/shared_kernel/constants.py](../codebase_mapper/shared_kernel/constants.py) — namespaces, reference kinds, type/phase vocabularies, language extensions, default phases. Imported by 23 files.
* [codebase_mapper/shared_kernel/extensions.py](../codebase_mapper/shared_kernel/extensions.py) — defines seven extension protocols and registries: `LanguageAnalyzer`, `ImportResolver`, `RecordEnricher`, `Aggregator`, `GraphContributor`, `ShapeContributor`, `ArtifactEmitter` (L38–L80, with `register_*` / `iter_*` symmetric helpers L92–L159). This is the **plugin contract** of the whole system.

---

## 3. Emission Layer (Hexagonal)

Lives under [codebase_mapper/emission/](../codebase_mapper/emission/). Cleanly split into `application/`, `domain/ports/`, `infrastructure/`.

### 3.1 Application

* **`emit_bundle.emit`** ([codebase_mapper/emission/application/emit_bundle.py:26-187](../codebase_mapper/emission/application/emit_bundle.py#L26-L187)) — the single entry that:
  1. Walks plugin contributors (graph + shape).
  2. Builds inventory graph (cbm + dependencies + tests).
  3. Serialises to **Turtle, N-Triples, JSON-LD** under a per-bundle directory.
  4. Persists file content blobs via the blob-store port.
  5. Optionally writes the **Rust items sidecar** (`rust_items.jsonl`) via `_emit_rust_items_sidecar` (L198), enabling the `items_by_attribute` query without graph traversal.
* **`reconstruct`** ([codebase_mapper/emission/application/reconstruct.py:20-136](../codebase_mapper/emission/application/reconstruct.py#L20-L136)) — materialises files back from inventory + blobs, verifies, and supports `verify_roundtrip` for end-to-end integrity.
* **`regenerate.py`** — regenerates Rust source from AST summaries (testing equivalence).

### 3.2 Domain ports

* `BlobStore` ([codebase_mapper/emission/domain/ports/blob_store.py](../codebase_mapper/emission/domain/ports/blob_store.py))
* `VocabProvider` ([codebase_mapper/emission/domain/ports/vocab_provider.py](../codebase_mapper/emission/domain/ports/vocab_provider.py))

### 3.3 Infrastructure

* **`rdflib_emitter`** ([codebase_mapper/emission/infrastructure/rdf/rdflib_emitter.py](../codebase_mapper/emission/infrastructure/rdf/rdflib_emitter.py)) — builders for `inventory_graph` (L63), `shacl_graph` (L144), and `ontology_mapping_graph` (L267). IRI minting helpers (`file_iri`, `package_iri`, `release_iri`, `type_iri`, `phase_iri`).
* **`FilesystemBlobStore`** — flat content-addressed blob storage on disk.
* **Vocab loader + curated YAML** described in §2.3.

---

## 4. MCP Server Surface

Lives under [frontend/mcp_server/](../frontend/mcp_server/) (15 source modules + 13 test modules — the densest test cluster in the repo).

### 4.1 Server core

* **`server.build_server`** ([frontend/mcp_server/server.py:78-185](../frontend/mcp_server/server.py#L78-L185)) wires:
  * Tool discovery + dispatch via the official MCP Python SDK.
  * Schema validation (input + output) using JSON Schema definitions in `schemas.py` (42 kB — every tool's IO contract).
  * Server-side invariant checks in `validators.py` ([frontend/mcp_server/validators.py:3477 bytes](../frontend/mcp_server/validators.py)).
  * Default-bundle prewarm (`prewarm_default_bundle` L64).
  * `resources/subscribe` capability declaration (L188) — clients can listen for manifest changes.
* Stdio transport: `run_stdio` (L250).
* HTTP transport with **OAuth 2.1 / JWT** ([frontend/mcp_server/http_transport.py:147-216](../frontend/mcp_server/http_transport.py#L147-L216) + [frontend/mcp_server/auth.py](../frontend/mcp_server/auth.py) providing `StaticTokenVerifier`, `JwtVerifier`, JWKS resolver, `build_verifier_from_env`).

### 4.2 Tools (handlers.py, ~33 kB)

All handlers in [frontend/mcp_server/handlers.py](../frontend/mcp_server/handlers.py) are pure functions over a loaded `Bundle`. The dispatch table is built via the `@tool` decorator (L89). Tools exposed:

| Tool                  | Handler                  | Purpose                                                    |
| --------------------- | ------------------------ | ---------------------------------------------------------- |
| `orient_bundle`       | `_orient_bundle` L173    | First-call cheatsheet (layers, namespaces, suggested calls) |
| `bundle_summary`      | `_bundle_summary` L248   | Counts, language histogram, SHACL conformance              |
| `repository_summary`  | `_repository_summary` L302 | One-shot executive read (this report draws on it)        |
| `list_bundles`        | `_list_bundles` L461     | Discovery                                                  |
| `select_bundle`       | `_select_bundle` L468    | Session-state active bundle                                |
| `list_files`          | `_list_files` L477       | Filter by language/type/prefix; rank by import-degree     |
| `file_detail`         | `_file_detail` L517      | Imports both ways, chunks, concepts, optional LLM summary |
| `file_impact`         | `_file_impact` L589      | "What does this file pull in / who pulls it"              |
| `imports_of` / `imported_by` | L615 / L625      | Edge introspection                                         |
| `chunk_detail` / `chunk_blob` / `list_chunks` | L635/L654/L671 | L2 access                                  |
| `semantic_neighbors`  | `_semantic_neighbors` L686 | Embedding similarity search                              |
| `concept_detail`      | `_concept_detail` L700   | SKOS concept + cooccur top-k + LLM description           |
| `concept_neighborhood` | L755                    | BFS over skos:related edges                                |
| `items_by_attribute`  | `_items_by_attribute` L825 | Rust `#[attr]` queries via sidecar                       |
| `sparql`              | `_sparql` L747           | Gated SPARQL escape hatch — see §4.4                       |

### 4.3 Prompts & Resources

* **Prompts** ([frontend/mcp_server/prompts.py](../frontend/mcp_server/prompts.py)) — three "playbook" prompts the LLM can fetch: `orient`, `explore_concept`, `trace_dependency`. Built via `_build_orient` / `_build_explore_concept` / `_build_trace_dependency` (L59-L160).
* **Resources** ([frontend/mcp_server/resources.py](../frontend/mcp_server/resources.py)) — exposes the bundle manifest under URIs the client can subscribe to.
* **Subscriptions** ([frontend/mcp_server/subscriptions.py](../frontend/mcp_server/subscriptions.py)) — `SubscriptionManager` + `ManifestWatcher` poll the filesystem; emit notifications when a bundle changes (`poll_once` L144).

### 4.4 SPARQL escape hatch (advanced, gated)

[frontend/mcp_server/sparql.py:101-154](../frontend/mcp_server/sparql.py#L101-L154):

* Disabled by default — must set `CBM_ENABLE_SPARQL=1`.
* Validates query is `SELECT` or `ASK` only; rejects mutating verbs.
* Hard limits: **10 s walltime, 1000 rows, 10000 chars**.
* Caches loaded `rdflib.Graph` per bundle.

### 4.5 Observability

[frontend/mcp_server/observability.py](../frontend/mcp_server/observability.py):

* Per-tool timeout via `timeout_for` (L65) — overridable via `CBM_TOOL_TIMEOUT_<name>` env.
* `audit_log` (L121) writes structured JSON-line records (tool, args digest, duration, outcome).
* `dispatch_with_budget` (L163) wraps every handler with budget + audit.
* `ToolTimeoutError` (L151) returned to the client when budget exhausted.

---

## 5. HTTP Backend (FastAPI)

[frontend/backend/app.py](../frontend/backend/app.py) (12.5 kB) — a FastAPI app whose handlers delegate to the same shared application package used by the MCP server.

* Pydantic response models: `SummaryResp`, `GraphNode/Edge/Resp`, `ChunkResp`, `BundleListResp`, `ImpactResp`, `ConceptDetailResp`, `FileDetailResp` (L81-L265).
* Endpoints: `/bundles`, `/summary`, `/file-graph`, `/symbol-graph`, `/concept-graph`, `/chunks`, `/chunk-search`, `/chunk-blob`, `/concept-detail`, `/file-detail`, `/impact`, `/chunk-detail`, `/healthz` (L290-L385).
* Shared application layer lives in [frontend/backend/serving/application/](../frontend/backend/serving/application/): one module per resource (`bundles.py`, `chunks.py`, `concepts.py`, `files.py`, `graphs.py`, `impact.py`, `summary.py`, `health.py`).
* The hub for all serving is **`bundle_data.load_bundle`** ([frontend/backend/serving/application/bundle_data.py:64-235](../frontend/backend/serving/application/bundle_data.py#L64-L235)) — assembles a typed `Bundle` (L24) from RDF + sidecars + xrefs + enrichments, with a `_load_bundle_cached` LRU.
* Bundle reload is sensitive: `_resolve_bundle_path` (L420), `_validate_bundle_name` (L359), `_clear_bundle_cache` (L460).
* Containerised via [frontend/backend/Dockerfile](../frontend/backend/Dockerfile).

---

## 6. React UI

Lives under [frontend/ui/](../frontend/ui/). Vite + React + TypeScript + Cytoscape.

* Entrypoint [frontend/ui/src/main.tsx](../frontend/ui/src/main.tsx) → [frontend/ui/src/App.tsx](../frontend/ui/src/App.tsx) (`App` L16, `BundlePicker` L109).
* Bundle context for re-fetch-without-remount [frontend/ui/src/bundle-context.ts](../frontend/ui/src/bundle-context.ts).
* API types in [frontend/ui/src/api.ts](../frontend/ui/src/api.ts).
* **Views**: Dashboard, FileDetail, FileGraph, SymbolGraph, ConceptDetail, ConceptGraph, ChunkSearch, ChunkDetail (8 views).
* **Components**: `CytoscapeGraph.tsx` for force-directed graphs; `LlmEnrichmentCard.tsx` to surface L4 metadata (with model + timestamp).
* Testing via Vitest (`src/__tests__/*.test.tsx`) covering bundle switching, empty states, cytoscape rendering, and view routing.
* Production deploy via nginx ([frontend/ui/nginx.conf](../frontend/ui/nginx.conf), [frontend/ui/Dockerfile](../frontend/ui/Dockerfile)).

---

## 7. CLI

* `python -m codebase_mapper` → [codebase_mapper/__main__.py](../codebase_mapper/__main__.py) → [codebase_mapper/cli.py:17-85](../codebase_mapper/cli.py#L17-L85).
* `main()` orchestrates: inspect → emit → optionally reconstruct → optionally `self_test` ([codebase_mapper/self_test.py](../codebase_mapper/self_test.py)).
* Phase-specific runners in `scripts/`: `run_l2.py`, `run_l3.py`, `run_l4.py`, `run_xrefs.py` — each composes plugins via the shared-kernel registries.

---

## 8. Static schema corpus

[static/schemas/](../static/schemas/) contains 11 large XSD files (IEEE 12207, IEEE 29148, IEC 5055, EIC v1, DDD v2, AST v1, C4 v2, Python-metacode, AST↔tree-sitter bridge). These are first-class fixtures: L4 emits `schemaPurpose` annotations against them and the SHACL conformance check covers them.

---

## 9. Test surface

* 78 test files, 0.61 source/test ratio (above the project's 60 % CLI / 80 % library bars in [CLAUDE.md](../CLAUDE.md)).
* Three test clusters by directory:
  * `tests/verify_*.py` — top-level invariants: roundtrip, regenerate, drift (`p1`/`p2`/`p3`), excludes, timestamps, repo-source, shape coverage, xrefs (`verify_xrefs.py` is the largest test at 54 kB), Rust attribute query, Rust super/self resolution, Rust AST body count, dependency hygiene, vocab, vocab wiring, proto fixture, xsd fixture, LLM-enrich offline/determinism/cache/rdf/prompts/aggregator/file-summary.
  * `frontend/backend/tests/` — endpoint, unit, xrefs, bundle behaviours.
  * `frontend/mcp_server/tests/` — handlers, hardening, HTTP transport, OAuth, prompts, resources, schemas, server, sparql, subscriptions, vocab, coverage gaps, llm_enrich_surface.
* Test fixtures include deterministic LLM cache ([tests/fixtures/llm_cache/](../tests/fixtures/llm_cache/)) and Rust crates ([tests/fixtures/rust/](../tests/fixtures/rust/)).

---

## 10. Cross-cutting features (consolidated)

| Feature                              | Where                                                                                              |
| ------------------------------------ | -------------------------------------------------------------------------------------------------- |
| Multi-language inspection (9 langs)  | `codebase_mapper/inspection/languages/*.py`, `codebase_mapper/inspection/_builtins.py`            |
| Dependency manifest + lockfile parsing | `inspection/manifests.py`, `inspection/lockfiles.py`                                              |
| RDF emission (Turtle/N-Triples/JSON-LD) | `emission/infrastructure/rdf/rdflib_emitter.py`                                                  |
| SHACL shape generation + conformance | `rdflib_emitter.build_shacl_graph`, all plugin `*Shapes` contributors                              |
| Roundtrip integrity (inspect → emit → reconstruct → diff) | `emission/application/reconstruct.py`, `tests/verify_roundtrip.py`            |
| Plugin extension points              | `shared_kernel/extensions.py` (7 protocols, dual register/iter)                                    |
| Content-addressed blob store         | `emission/infrastructure/storage/filesystem_blob_store.py`                                         |
| Curated vocabulary of "software primitives" | `emission/infrastructure/vocab/software_primitives.yaml` + `loader.py`                        |
| Identifier splitting & canonicalization | `plugins/concept_graph/concepts.py:canonicalize`                                                |
| Cooccurrence concept graph           | `plugins/concept_graph/concepts.py:ConceptAggregator`                                              |
| Chunking by AST (Python/TS/JS/Rust)  | `plugins/chunks_embeddings/chunker.py`                                                            |
| Embeddings (SBERT-backed optional)   | `plugins/chunks_embeddings/embedder.py`, `backends.py`                                            |
| Symbol-level xrefs                   | `plugins/symbol_xrefs/{python,rust,tsjs}_resolver.py` + aggregator                                |
| Rust attribute introspection         | `_emit_rust_items_sidecar` + `_items_by_attribute` handler                                        |
| LLM enrichment (Ollama) with provenance | `plugins/llm_enrich/{client,aggregator,enricher,graph_writer,cache,prompts}.py`               |
| Versioned prompt templates           | `plugins/llm_enrich/prompts/*.v1.txt`                                                              |
| LLM cache with deterministic replay  | `plugins/llm_enrich/cache.py` (`compose_key` = `(model, prompt_sha, target_sha)`)                  |
| MCP server with 18+ tools            | `frontend/mcp_server/handlers.py` + `server.py` + `schemas.py`                                     |
| MCP prompts (orient/explore/trace)   | `frontend/mcp_server/prompts.py`                                                                   |
| Resource subscriptions (manifest watch) | `frontend/mcp_server/subscriptions.py`                                                          |
| OAuth 2.1 + JWT bearer-token auth    | `frontend/mcp_server/auth.py`, `http_transport.py`                                                |
| Gated SPARQL escape hatch (10 s / 1k rows) | `frontend/mcp_server/sparql.py`                                                              |
| Per-tool timeout + audit log         | `frontend/mcp_server/observability.py`                                                            |
| FastAPI HTTP backend                 | `frontend/backend/app.py` + `frontend/backend/serving/application/*`                              |
| React + Cytoscape exploration UI     | `frontend/ui/src/**`                                                                              |
| Bundle hot-reload (HTTP + MCP)       | `bundle_data._load_bundle_cached`, `subscriptions.ManifestWatcher`                                |
| Self-test                            | `codebase_mapper/self_test.py` (invoked from CLI)                                                  |
| Docker deploy                        | top-level `Dockerfile`, `docker/`, `frontend/backend/Dockerfile`, `frontend/ui/Dockerfile`        |

---

## 11. Observations & risks (evidence-cited)

These are surfacing observations only; each is keyed to a verifiable artefact, not a judgement.

1. **High coupling around `inspection/pipeline.py`** (import-degree 29). Any L1 schema change ripples through 14 consumers including all `scripts/run_l*.py` runners and 8 verify-* tests. Treat it as a stable interface; new behaviour belongs in plugin contributors, not in `map_codebase`.
2. **`shared_kernel/extensions.py` is the de-facto plugin SPI.** Imported by 26 files. Adding a new pipeline phase without going through one of the 7 protocols is a strong code-smell.
3. **`handlers.py` is the largest non-test file at 32.7 kB and concentrates 27+ tool handlers.** It is tested but ripe for decomposition into per-resource modules mirroring the backend's `serving/application/*.py` layout. Risk is low (no behaviour change), benefit is reviewability.
4. **`frontend/mcp_server/schemas.py` is 41.9 kB of hand-rolled JSON Schema.** It is the single point that defines the MCP wire contract; drift between it and `handlers.py` is caught by `test_schemas.py` and `test_handlers.py`, but a generation strategy (e.g., from Pydantic models or the application-layer dataclasses) would shrink this surface.
5. **LLM outputs are RDF triples but explicitly flagged as advisory in `orient_bundle`** ("Present only when the bundle was built with --llm-enrich; absent or empty otherwise. Treat as advisory."). Consumers must check `cbml4:*Model` / `*PromptSha` / `*GeneratedAt` provenance before relying on the text. This conforms to the PALS's LAW requirement in [CLAUDE.md](../CLAUDE.md) §"LLM Output Verification".
6. **SPARQL is disabled by default** (`CBM_ENABLE_SPARQL=1` required) with hard limits. Good posture; the test suite at `tests/test_sparql.py` exercises the limits explicitly.
7. **Rust tooling is the most developed beyond Python** — 22 kB resolver, 16 kB inspector, dedicated regenerate path, `items_by_attribute` sidecar, six dedicated `verify_rust_*.py` tests, dedicated fixture crates. TS/JS resolver is comparable in size (23 kB) but has no equivalent sidecar.
8. **No web-search / external network in the core inspection path.** Only LLM enrichment hits Ollama, and only when explicitly enabled; the cache + offline test (`tests/verify_llm_enrich_offline.py`) makes a network-free build reproducible.
9. **SHACL conformance is reported as `true` for this bundle** (`bundle_summary` output). The shape graph is generated alongside the inventory and is part of every emit, so drift is a build-time failure rather than a runtime one.

---

## 12. Build & deploy artefacts

* Top-level `pyproject.toml` + `uv.lock` (318 kB — fully pinned via uv).
* Backend `requirements.txt` + `requirements-sbert.txt` (extras for embeddings backend).
* UI `package.json` + `package-lock.json`.
* Containerisation:
  * Root [Dockerfile](../Dockerfile) — CLI / inspection image.
  * [frontend/backend/Dockerfile](../frontend/backend/Dockerfile) — FastAPI HTTP backend.
  * [frontend/ui/Dockerfile](../frontend/ui/Dockerfile) + [nginx.conf](../frontend/ui/nginx.conf) — static UI.
* CI: detected `ci_cd` files (1) — see `.github/`.
* Env scaffolding: [.env.example](../.env.example).
* Doc tooling: per-project READMEs in `frontend/backend/`, `frontend/mcp_server/`, `plugins/llm_enrich/prompts/`.

---

## 13. Flutter-repo ingestion — predicted issues

Flutter mix (Dart 74.7 %, C++ 16.4 %, Objective-C++ 2.8 %, Java 2.8 %, Objective-C 0.7 %, C 0.6 %, ~2 % other). Mapping each band to the bundle's actual code:

> **Update 2026-05-14: Dart is now Tier-1.** §13.1 below describes the *pre-promotion*
> state retained for historical context. The current behaviour, verified by
> [`tests/verify_dart.py`](../tests/verify_dart.py) (52/52), is:
> AST extractor recovers methods/getters/setters/factories with byte spans;
> ``host:dart_packages`` is a multi-pubspec map with ``dart_package_for_path``
> nearest-enclosing lookup; ``part``/``part of``/conditional/deferred imports
> resolve; ``.g.dart`` / ``.freezed.dart`` / ``.pb*.dart`` etc. classify as
> ``generated``; per-class/method/function L2 chunks land in
> ``ctx.indices["l2_10_chunks"]``; symbol-xref resolver emits
> ``calls``/``subclassOf``/``overrides`` edges with ``dart_intra_file`` and
> ``dart_inter_file`` resolver tags. A canonical ``.cbmignore`` template
> for Flutter codebases lives at [docs/cbmignore/dart.txt](cbmignore/dart.txt).

### 13.1 Dart (74.7 %) — partial, regex-based, several real gaps *(historical)*

* **AST extraction is regex-only**, not tree-sitter ([dart.py:19](../codebase_mapper/inspection/languages/dart.py#L19) — `"Dart has no PyPI tree-sitter grammar. Use a regex-based extractor."`).
  * Impact: declarations are anchored to **column 0** ([dart.py:31, 39](../codebase_mapper/inspection/languages/dart.py#L31-L49)). Nested classes, helper functions, methods inside `class { … }`, and the entire `extension on Type { … }` body are dropped from `top_level_functions` / `top_level_classes`.
  * Generics with `<>` and complex return types (`Future<Map<String, dynamic>>`) will silently miss the function-name regex ([dart.py:41-49](../codebase_mapper/inspection/languages/dart.py#L41-L49)).
  * Mixin application (`class X = A with B`), `factory` constructors, getters/setters (`get`/`set` are explicitly blacklisted at [dart.py:51-53](../codebase_mapper/inspection/languages/dart.py#L51-L53)) are not represented as functions.
* **L2 chunking has no Dart branch** ([chunker.py:60-70](../plugins/chunks_embeddings/chunker.py#L60-L70)) — every `.dart` file becomes a **single whole-file chunk** via `_whole_file_chunk`. Embedding-based search and `chunk_detail` will only ever return file-granularity hits for ~75 % of the repo. There are no function- or class-level chunks.
* **No Dart symbol-xref resolver** — only `python_resolver`, `rust_resolver`, `tsjs_resolver` exist under [plugins/symbol_xrefs/](../plugins/symbol_xrefs/). Every Dart cross-reference will surface as `language_unsupported` in `XREF_UNRESOLVED_REASONS` ([constants.py:50](../codebase_mapper/shared_kernel/constants.py#L50)). `SymbolXrefEdge` density for the Flutter app will be effectively zero.
* **Import resolution is correct but narrow** ([dart.py:96-133](../codebase_mapper/inspection/languages/dart.py#L96-L133)):
  * `pubspec.yaml` is read for *one* package name only ([dart.py:77-94](../codebase_mapper/inspection/languages/dart.py#L77-L94)) — if the Flutter repo is a **workspace / monorepo with multiple `pubspec.yaml` files** (very common: `app/`, `packages/foo/`, `examples/`, `melos.yaml`), only the shallowest one becomes recognised. Cross-package imports (`package:foo/x.dart` between sibling packages) will resolve to `external` even though they live in-repo.
  * `dart:` SDK imports are always unresolved (correct, but every Flutter file imports `dart:async` / `dart:ui` / `dart:io` → noisy `external` set).
  * **`part of` directives** are not in the regex ([dart.py:23-26](../codebase_mapper/inspection/languages/dart.py#L23-L26) only matches `import|export|part` *file* clauses, not `part of` declarations) — split-file libraries will lose the back-edge from a part to its parent.
  * Generated `.g.dart` and `.freezed.dart` files (ubiquitous in Flutter with `build_runner`) will be ingested as normal source unless `.cbmignore` lists them, inflating concept counts and import edges. There is **no auto-classification** for codegen outputs in [classify.py:303](../codebase_mapper/inspection/classify.py#L303).
* **`pubspec.lock` is not a recognised lockfile** in [lockfiles.py](../codebase_mapper/inspection/lockfiles.py) (which targets Python/npm/Cargo/etc.). Pinned-dependency counts (`pinsDependency` in the bundle summary) will be 0 for Dart even though `pubspec.lock` is present.
* **No `dart pub` manifest parser** in [manifests.py:27-276](../codebase_mapper/inspection/manifests.py#L27-L276) — `pubspec.yaml` *declared* dependencies do not feed `declaredDependency` edges. The `_DEPENDENCY_MANIFEST` classifier still tags it as `dependency_manifest`, but the parse step is a no-op.

### 13.2 C++ (16.4 %) — *(promoted to Tier-1, 2026-05-14)*

> **Update 2026-05-14: C++ is now Tier-1.** Current behaviour (verified
> by [`tests/verify_cpp.py`](../tests/verify_cpp.py), 42/42):
> tree-sitter-cpp grammar wired into [ts_setup.py](../codebase_mapper/ts_setup.py)
> for every C++ extension (`.cpp .cc .cxx .c++ .hpp .hxx .h++ .ipp .tpp .inl`);
> [`languages/cpp.py`](../codebase_mapper/inspection/languages/cpp.py)
> emits per-type / per-method items with byte spans, namespace-aware
> naming (top-level items carry `namespace = "acme::detail"` for items
> defined inside `namespace acme::detail { ... }`), and `extends` /
> `implements` harvested from `base_class_clause`; out-of-class method
> definitions like `std::string Dog::speak() const { ... }` are
> attributed to class `Dog` with kind `method`; `host:cpp_symbols`
> indexed in [pipeline.py](../codebase_mapper/inspection/pipeline.py)
> covers multi-file (header + impl) class definitions; the new
> [`refine_cpp_header_languages`](../codebase_mapper/inspection/languages/cpp.py)
> retag step runs between classify and AST extraction and applies a
> two-pass heuristic (sibling-dir + project-wide) so `include/foo.h`
> headers are parsed by C++ in mixed repos; classifier marks
> `*_test.cpp`/`*_test.cc`/`FooTest.cpp` as `test_code` while
> rejecting `Latest.cpp`; tests-edges prefers same-extension matches
> when a basename like `dog` matches both `dog.h` and `dog.cpp`;
> L2 chunker emits one chunk per type/method/function with
> declaration-vs-definition deduplication;
> [`plugins/symbol_xrefs/cpp_resolver.py`](../plugins/symbol_xrefs/cpp_resolver.py)
> emits `calls` for bare-name, `Foo::method()` qualified, `new
> Foo(...)`, and direct-init `Foo x(args)` shapes; `subclassOf` for
> `class Dog : public Animal`; and `overrides` for any matching
> method name on a resolved base.

*Historical (pre-promotion) state:*

* Extensions `.cpp .cc .hpp .hxx` map to language `cpp` ([constants.py:77](../codebase_mapper/shared_kernel/constants.py#L77)), **not** `c`. The `CAnalyzer.matches` predicate is `record.language == "c"` ([_builtins.py:107-108](../codebase_mapper/inspection/_builtins.py#L107-L108)), so **no analyzer matches for C++ files**.
  * Result: every `.cpp/.cc/.hpp/.hxx` file gets a `FileRecord` with `language="cpp"` but `ast_summary=None`. They appear in `list_files` and contribute to `imports_in`/`imports_out` counts only via other languages' imports, never as sources of edges.
  * The `CResolver.matches` similarly requires `language == "c"` ([_builtins.py:238-239](../codebase_mapper/inspection/_builtins.py#L238-L239)) → **zero in-repo include edges for C++**.
* No C++ tree-sitter grammar is wired in [ts_setup.py:35-119](../codebase_mapper/ts_setup.py#L35-L119) (only c, go, kotlin, ruby, rust, swift, ts/js). Adding C++ would require: (a) `pip install tree-sitter-cpp`, (b) a `cpp` entry in `_ts_setup`, (c) a `CppAnalyzer`/`CppResolver` pair, (d) extending `_is_cpp` predicates throughout.
* **`.h` headers are tagged `"c"` even when used in C++** ([constants.py:75](../codebase_mapper/shared_kernel/constants.py#L75) `".h": "c"`). For Flutter engine code that mixes `.h/.cc`, headers will route to `CAnalyzer` (extracting C symbols only) while implementations are unparsed.
* L2 chunking: no `cpp` branch — falls to `_whole_file_chunk` ([chunker.py:66-68](../plugins/chunks_embeddings/chunker.py#L66-L68)). Single chunk per file, no method-level granularity.

### 13.3 Objective-C / Objective-C++ (3.5 % combined) — *(promoted to Tier-1, 2026-05-15)*

> **Update 2026-05-15: ObjC and ObjC++ are now Tier-1.** Current behaviour
> (verified by [`tests/verify_objc.py`](../tests/verify_objc.py), 53/53):
> tree-sitter-objc grammar wired into [ts_setup.py](../codebase_mapper/ts_setup.py)
> handles both `.m` (objective-c) and `.mm` (objective-cpp);
> [`languages/objc.py`](../codebase_mapper/inspection/languages/objc.py)
> emits per-type / per-method items with byte spans, full
> selector preservation (`-bumpAge:by:` keeps the multi-segment
> selector intact), import recognition across all three forms
> (`#import "X.h"`, `#import <Framework/X.h>`, `@import Module;`),
> and `extends` / `implements` (protocol conformance) harvested onto
> each class item;
> [`refine_objc_header_languages`](../codebase_mapper/inspection/languages/objc.py)
> runs between classify and AST extraction (BEFORE the C++ retag) so
> `.h` headers in Apple-convention directories are parsed by the ObjC
> analyzer — `@interface` / `@protocol` survive the trip;
> classifier marks `*Test.m`, `*Tests.m`, `*Spec.m`, `*Specs.m`
> (XCTest / Specta / Kiwi) as `test_code` with the `Latest.m`-safe
> CamelCase guard; tests-edges links `FooTests.m` → `Foo.m` using
> the same CamelCase stem rule from Java/C++; L2 chunker emits one
> chunk per `@interface` / `@implementation` / `@protocol` /
> category and one chunk per method with declaration-vs-definition
> deduplication;
> [`plugins/symbol_xrefs/objc_resolver.py`](../plugins/symbol_xrefs/objc_resolver.py)
> emits `calls` for class messages (`[NSString stringWithFormat:...]`),
> `[self method]` (binds same-class), `[super method]` (binds
> superclass), nested `[[Class alloc] init...]`, and C-style free
> function calls; `subclassOf` for `class Dog : Animal` (`exact`) and
> protocol conformance `Dog <NSCopying>` (`heuristic`); and
> `overrides` for matching method names on resolved bases.

*Historical (pre-promotion) state:*

* `.m → objective-c`, `.mm → objective-cpp` ([constants.py:76](../codebase_mapper/shared_kernel/constants.py#L76)). Neither value appeared in any `matches()` predicate in [_builtins.py](../codebase_mapper/inspection/_builtins.py) — there was no Objective-C analyzer at all.
* Files were classified, sized, and content-blob-stored, but contributed **no AST, no imports, no concepts beyond identifier-noise** in path/filename.

### 13.4 Java (2.8 %) — *(promoted to Tier-1, 2026-05-14)*

> **Update 2026-05-14: Java is now Tier-1.** The §13.4 description below
> is retained for historical context. Current behaviour (verified by
> [`tests/verify_java.py`](../tests/verify_java.py), 51/51):
> tree-sitter-java grammar wired into [ts_setup.py](../codebase_mapper/ts_setup.py);
> [`languages/java.py`](../codebase_mapper/inspection/languages/java.py)
> emits per-type / per-method items with byte spans, package + import lists
> (with `static` and `wildcard` flags), and `extends`/`implements`
> harvested onto each type item; host indices `host:java_fqn`,
> `host:java_packages`, `host:java_source_roots` populated in
> [pipeline.py](../codebase_mapper/inspection/pipeline.py);
> [manifests.parse_pom_xml](../codebase_mapper/inspection/manifests.py)
> extracts `<dependency>`, `<parent>`, `<plugin>`, and
> `<dependencyManagement>` coords; classifier marks `*Test.java`,
> `*Tests.java`, `*IT.java` as `test_code` and tests-edges links them
> across `src/main/java` ↔ `src/test/java`; L2 chunker
> emits one chunk per type/method/constructor including inner classes;
> [`plugins/symbol_xrefs/java_resolver.py`](../plugins/symbol_xrefs/java_resolver.py)
> emits `calls` (bare, `this.`, receiver-class, `new Foo(...)`),
> `subclassOf` (`extends` exact, `implements` heuristic), and
> `overrides` edges.

*Historical (pre-promotion) state:*

* `.java → java` ([constants.py:73](../codebase_mapper/shared_kernel/constants.py#L73)) but there was **no `JavaAnalyzer`**. Kotlin had one (relevant for Flutter's `android/` directory where Java + Kotlin coexist); pure Java was untouched.
* No `JavaResolver`; `import com.example.X;` was not parsed.
* No L2 sub-file chunking; whole-file chunk only.
* Gradle ([manifests.py:193-214 `parse_build_gradle`](../codebase_mapper/inspection/manifests.py#L193-L214)) extracted declared dependencies — that path is shared with Kotlin and was the only piece of Java/Kotlin/Android tooling that worked.

### 13.5 C (0.6 %) — works as designed

This is the only non-Dart band that fully exercises the existing analyzer + resolver. Expect functional `#include` edges within `.c/.h` files; system includes (`<stdio.h>`) marked external.

### 13.6 Cross-cutting issues that compound at Flutter scale

1. **Concept-graph distortion.** L3 concepts come from identifier splitting ([concepts.py:84](../plugins/concept_graph/concepts.py#L84)). Whole-file chunks on ~75 % of the repo still feed identifiers in, but **method-level granularity is lost** — concept frequencies will be dominated by file basenames and top-level declarations, with class internals invisible. The cooccurrence graph will be sparser than for a Python/Rust repo of similar size.
2. **Embeddings recall collapses to file-level** for Dart/C++/ObjC/Java. `semantic_neighbors` queries that work on Python repos ("find the function that does X") will return a *file* — useful but coarser than what a user expects from the same query against the bundle's own Python code.
3. **`tests_edges` heuristic targets** `tests/` and `*_test.py` patterns in [tests_edges.py](../codebase_mapper/inspection/tests_edges.py). Flutter uses `*_test.dart` and a top-level `test/` directory; verify the heuristic catches it — if it doesn't, the `tests` ratio will under-report.
4. **Bundle size pressure.** A Flutter monorepo with `build/`, `.dart_tool/`, iOS `Pods/`, Android `.gradle/` directories must be excluded via `.cbmignore` or [`path_excluded`](../codebase_mapper/inspection/classify.py#L303-L324) (which reads `.gitignore` and `.cbmignore`). Without exclusions, expect tens of thousands of generated files to be ingested; emit time scales linearly with file count in [emit_bundle.emit](../codebase_mapper/emission/application/emit_bundle.py#L26-L187).
5. **SHACL conformance risk.** Shape contributors check that emitted RDF matches the schema. The Dart analyzer setting `extraction_method: "regex"` ([dart.py:74](../codebase_mapper/inspection/languages/dart.py#L74)) is a value the L1 shapes don't currently look for, but L4 enrichment may emit predicates against concepts/files that have no chunks — verify via `tests/verify_shape_coverage.py` after a dry-run.
6. **Rust-items sidecar is empty.** `items_by_attribute` ([handlers.py:825-863](../frontend/mcp_server/handlers.py#L825-L863)) will return zero results — there are no Rust files in a typical Flutter app. (Not a defect; just a tool that will appear "broken" to an agent unaware of the scope.)
7. **L4 schema-purpose annotation.** `_is_schema_file` ([aggregator.py:309-315](../plugins/llm_enrich/aggregator.py#L309-L315)) keys on `SCHEMA_PATH_PREFIXES` (`static/schemas/...`). Flutter repos won't match; no schema-purpose enrichments will be emitted, which is correct but means one of three L4 capabilities is silently inactive.

### 13.7 Severity-ranked summary

| Severity | Issue | Concrete user-visible symptom |
| -------- | ----- | ----------------------------- |
| ~~High~~ ✔ resolved | ~~`.mm`/`.m` (Objective-C/C++) have no analyzer match~~ | Tier-1 promotion ships an ObjC analyzer/resolver/chunker/xref. The entire Flutter repo's `Dart + C++ + Java + ObjC/ObjC++` content (~97 %) now has full AST + xref coverage. |
| ~~High~~ ✔ resolved | ~~Dart L2 has no chunker~~ | Tier-1 promotion adds `_chunk_dart` consuming `ast_summary["items"]`. |
| ~~High~~ ✔ resolved | ~~No Dart symbol-xref resolver~~ | `plugins/symbol_xrefs/dart_resolver.py` emits `calls`/`subclassOf`/`overrides`. |
| ~~High~~ ✔ resolved | ~~No ObjC symbol-xref resolver~~ | `plugins/symbol_xrefs/objc_resolver.py` emits `calls`/`subclassOf`/`overrides` edges with `objc_intra_file` / `objc_inter_file` resolver tags. All five Tier-1 languages (Dart, Java, C++, ObjC, ObjC++) have full xref coverage. |
| ~~Med~~ ✔ resolved | ~~`pubspec.yaml` deps + `pubspec.lock` not parsed~~ | Both were already wired in `manifests.py:parse_pubspec_yaml` and `lockfiles.py:parse_pubspec_lock`; the Tier-1 verify suite now exercises them. |
| ~~Med~~ ✔ resolved | ~~Single-package assumption in `detect_dart_package_name`~~ | Replaced by `detect_dart_packages` (multi-pubspec map) + `dart_package_for_path` nearest-enclosing lookup. Legacy scalar kept for back-compat. |
| ~~Med~~ ✔ resolved | ~~Dart regex misses methods, getters, factories, `part of`~~ | Two-phase regex (getter/setter pass + method pass), claimed-range tracking, plus `part`/`part of` directives — verified by [`verify_dart.py`](../tests/verify_dart.py). |
| ~~Med~~ ✔ resolved | ~~Generated files not auto-excluded~~ | Classifier routes `.g.dart`/`.freezed.dart`/`.mocks.dart`/`.pb*.dart` (and 8 more) to `type_='generated'`. Optional `.cbmignore` template at [docs/cbmignore/dart.txt](cbmignore/dart.txt). |
| ~~Low~~ ✔ resolved | ~~`.h` headers route to C analyzer even in C++ codebases~~ | `refine_cpp_header_languages` runs between classify and AST extraction; sibling + project-wide heuristic retags `.h` files in C++ projects to the cpp analyzer. Pure-C repos untouched. |
| ~~Low~~ ✔ resolved | ~~Java is detected but unparsed (Kotlin works)~~ | Tier-1 promotion adds `JavaAnalyzer` + `JavaResolver` + xref resolver; Maven POMs parse; per-type chunks ship. |
| Low  | `items_by_attribute` returns nothing | Cosmetic — Rust-only tool against a non-Rust repo |

### 13.8 Minimum viable mitigations (in priority order)

1. **Add `.cbmignore` entries** for `build/`, `.dart_tool/`, `ios/Pods/`, `android/.gradle/`, `**/*.g.dart`, `**/*.freezed.dart`, `**/*.mocks.dart`, `**/.flutter-plugins*` before first ingestion.
2. **Broaden Dart package detection** to iterate all `pubspec.yaml` files; emit one entry per package into `host:dart_pkg_name` (currently a scalar).
3. **Add a `DartChunker` branch** in [chunker.py:60](../plugins/chunks_embeddings/chunker.py#L60-L70) using the existing regex set to emit class/function chunks. This is the highest leverage change because it unlocks both L2 (embeddings) and L3 (richer concepts) for the dominant language.
4. **Register a `pubspec.yaml` parser** in [manifests.py:27](../codebase_mapper/inspection/manifests.py#L27) to feed `declaredDependency`, and a `pubspec.lock` parser in [lockfiles.py](../codebase_mapper/inspection/lockfiles.py) to feed `pinsDependency`.
5. **Add a C++ analyzer** wired to `tree-sitter-cpp` and a `CppResolver` (modelled on `CAnalyzer/CResolver`) to recover the 16 % C++ band. Same change unlocks `.mm` (Objective-C++ is largely C++ syntax with extensions).
6. **Add Objective-C and Java analyzers** (lower priority — only 6 % combined; tree-sitter grammars exist for both).
7. **Until the above lands**, document the gap so agents using the MCP tools on a Flutter bundle don't interpret empty xref/concept results as "code is simple" rather than "tooling doesn't see it."

---

## 14. Suggested next reads (for an onboarding agent)

1. [CLAUDE.md](../CLAUDE.md) — operating constraints and PALS's LAW.
2. [PURPOSE.md](../PURPOSE.md) and [DISCLAIMER.md](../DISCLAIMER.md).
3. [codebase_mapper/shared_kernel/extensions.py](../codebase_mapper/shared_kernel/extensions.py) — the plugin SPI.
4. [codebase_mapper/inspection/pipeline.py](../codebase_mapper/inspection/pipeline.py) — the orchestrator (`map_codebase`).
5. [codebase_mapper/emission/application/emit_bundle.py](../codebase_mapper/emission/application/emit_bundle.py) — the emission orchestrator (`emit`).
6. [frontend/mcp_server/handlers.py](../frontend/mcp_server/handlers.py) — full tool surface.
7. [frontend/mcp_server/server.py](../frontend/mcp_server/server.py) — server wiring.
8. [plugins/llm_enrich/](../plugins/llm_enrich/) — the L4 reference plugin (also serves as a template for new contributors).

---

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.
