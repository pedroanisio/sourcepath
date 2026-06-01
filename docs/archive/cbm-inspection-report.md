---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.7 (1M context) via Claude Code, using cbm-mcp v0.5.0"
  date: "2026-05-14"
---

# Code Base Mapper — Inspection Report

> **Archive status:** Generated snapshot report for an older bundle/layout.
> Paths and counts below are historical and are not active documentation for the
> current checkout.

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

---

## 1. Bundle Provenance

| Field | Value |
|---|---|
| Bundle name | `code-base-mapper` |
| Repo name | `code-base-mapper` |
| Commit SHA | `c8184fd9891a915db65fbe19eaf602ba10449dd4` |
| Generated at | `2026-05-14T16:33:48Z` |
| Tool version | `cbm 0.5.0` |
| Bundle path | `_tmp/code-base-mapper/` |
| SHACL conforms | **true** |
| Unique blobs written | 246 |

Inspection performed via `cbm-mcp` tool surface (`select_bundle`, `orient_bundle`, `bundle_summary`, `repository_summary`, `list_files`). All numerical claims below are sourced from those calls.

---

## 2. Aggregate Counts

| Dimension | Count |
|---|---|
| Files | **248** |
| Chunks (L2) | **1361** |
| Concepts (L3) | **1898** |
| Import edges (internal) | 337 |
| Import edges (external) | 90 |
| Declares-dependency edges | 39 |
| Pins-dependency edges | 368 |
| Tests edges | 15 |
| AST full bodies — Python | 139 |
| AST full bodies — Rust | 9 |
| AST full bodies — TS/JS | 22 |
| AST summary total bytes | 8,114,083 |

### Language distribution
| Language | Files |
|---|---|
| python | 139 |
| typescript | 22 |
| rust | 9 |
| protobuf | 5 |
| html | 1 |
| css | 1 |
| (none / non-source) | 71 |

### Type distribution
| Type | Files |
|---|---|
| source_code | 100 |
| test_code | 77 |
| configuration | 20 |
| data | 20 |
| documentation | 16 |
| dependency_manifest | 6 |
| container | 4 |
| environment | 2 |
| lockfile | 2 |
| unknown | 1 |

**Test/source ratio:** 77/100 = **0.77** — strong test footprint. Tests-edges (RDF links from test → tested file) is only 15, which is far below the test-file count and is the most visible gap in the L1 graph (see §8).

### Embeddings
- Backend: `sentence-transformers/all-MiniLM-L6-v2`
- Dimension: 384
- SHACL conformance: ✅

---

## 3. Architectural Topology

The repository is a **three-tier code-graph platform**:

```
┌─────────────────────────────────────────────────────────────────┐
│ frontend/ui  (React/TypeScript SPA, Vite, Cytoscape)            │
│   └── App.tsx → views/{Dashboard, FileDetail, ChunkSearch,       │
│                        ConceptGraph, SymbolGraph, ChunkDetail}   │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP/JSON
┌───────────────────────────▼─────────────────────────────────────┐
│ frontend/backend/app.py (FastAPI; 46.7 KB monolith)             │
│ frontend/mcp_server/    (MCP server: stdio + HTTP transports,    │
│                          OAuth, SPARQL, subscriptions,           │
│                          schemas, observability)                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │ reads RDF + blobs
┌───────────────────────────▼─────────────────────────────────────┐
│ codebase_mapper/  (core library — pipeline, classify,            │
│   extensions, rdf_emit, languages/, vocab/)                      │
│ plugins/                                                         │
│   ├── chunks_embeddings/  (L2: NIF spans + 384-d vectors)        │
│   ├── concept_graph/      (L3: SKOS identifier-derived concepts) │
│   ├── symbol_xrefs/       (cross-references: py / tsjs / rust)   │
│   └── llm_enrich/         (LLM-generated summaries — guarded)    │
└─────────────────────────────────────────────────────────────────┘
```

### RDF layers (from `orient_bundle`)
| Layer | Purpose | Key predicates |
|---|---|---|
| **L1 — host** | Files, languages, types, imports, deps, AST summaries | `cbm:path`, `cbm:imports`, `cbm:hasPhase`, `cbm:tests` |
| **L2 — chunks_embeddings** | Per-function/class/file chunks with NIF spans + vectors | `cbml2:inFile`, `cbml2:beginIndex`, `cbml2:endIndex`, `cbml2:embeddingRow` |
| **L3 — concept_graph** | SKOS concepts derived from identifier splitting + co-occurrence | `cbml3:lexicalizes`, `cbml3:composedOf`, `skos:related`, `skos:prefLabel` |

---

## 4. Core Library (`codebase_mapper/`)

34 files. Centrality (import-degree) anchors the "spine":

| File | Size | Imports in | Imports out | Role |
|---|---:|---:|---:|---|
| `__init__.py` | 6.4 KB | 7 | 25 | Public facade re-exporting submodules |
| `models.py` | 2.9 KB | 28 | 0 | Pure data models (sink — depended on by everyone) |
| `constants.py` | 4.0 KB | 24 | 0 | Namespaces, vocab versions, refkinds, phase vocab, extensions |
| `pipeline.py` | 9.8 KB | 8 | 15 | End-to-end mapping orchestrator |
| `extensions.py` | 11.6 KB | 20 | 1 | 7-slot extension/plugin API |
| `_builtins.py` | 11.5 KB | 1 | 12 | Built-in language analyzers + import resolvers |
| `rdf_emit.py` | 12.5 KB | 10 | 2 | RDF graph generation (L1 inventory + relations) |
| `ts_setup.py` | 5.1 KB | 10 | 0 | Tree-sitter parser bootstrap for TS/JS/Rust/Ruby/Go/C/Kotlin/Swift |
| `classify.py` | 15.2 KB | — | — | Type/language classification |
| `emit_bundle.py` | 10.9 KB | — | — | Writes bundle directory layout |
| `reconstruct.py` | 5.1 KB | 4 | 5 | Round-trip materialization + verification |
| `regenerate.py` | 4.3 KB | — | — | Regeneration of source from AST summaries |
| `manifests.py` | 9.5 KB | — | — | Dependency manifest parsing |
| `lockfiles.py` | 7.1 KB | — | — | Lockfile parsing |
| `tests_edges.py` | 7.6 KB | — | — | Heuristics for `test → tested` edges |

### `codebase_mapper/languages/` — Tree-sitter language adapters
| Language | File | Size |
|---|---|---:|
| rust | `rust.py` | 16.7 KB (largest) |
| tsjs | `tsjs.py` | 11.1 KB |
| python | `python.py` | 10.2 KB |
| swift | `swift.py` | 6.3 KB |
| kotlin | `kotlin.py` | 5.3 KB |
| dart | `dart.py` | 5.0 KB |
| go | `go.py` | 4.7 KB |
| ruby | `ruby.py` | 3.7 KB |
| c | `c.py` | 3.6 KB |

The Rust adapter is disproportionately large because it also handles **regeneration** (source reconstitution from AST summary) — see `repository_summary.central_files[10]`.

### `codebase_mapper/vocab/`
SKOS-backed controlled vocabulary for concept kinds. `software_primitives.yaml` (3.8 KB) defines the structural-primitive ontology (e.g. `import_statement` → `code_structure`).

### Entry points
| Path | Kind |
|---|---|
| `codebase_mapper/__main__.py` | python_main |
| `codebase_mapper/cli.py` | python_cli |
| `frontend/backend/app.py` | python_app |
| `frontend/mcp_server/__main__.py` | python_main |
| `frontend/mcp_server/server.py` | python_app |

---

## 5. Plugin Surface (`plugins/`)

Four official plugins, each with the same conventional layout (`__init__.py`, `artifact.py`, `graph_writer.py`, plus domain modules):

### 5.1 `chunks_embeddings/`
| Module | Size | Purpose |
|---|---:|---|
| `chunker.py` | 17.7 KB | Splits files into function/class/file chunks with NIF byte spans |
| `embedder.py` | 3.7 KB | Encodes chunks (sentence-transformers backend) |
| `backends.py` | 3.8 KB | Pluggable embedding backends |
| `graph_writer.py` | 7.0 KB | Emits L2 RDF (`cbml2:*`) |
| `artifact.py` | 2.9 KB | Materializes embeddings file |

### 5.2 `concept_graph/`
| Module | Size | Purpose |
|---|---:|---|
| `concepts.py` | 15.4 KB | SKOS concept construction |
| `splitter.py` | 4.2 KB | Identifier splitting (camel/snake/etc.) |
| `graph_writer.py` | 14.4 KB | L3 RDF emission, co-occurrence as `skos:related` |
| `artifact.py` | 3.8 KB | Concept artifact materializer |

### 5.3 `symbol_xrefs/`
Per-language resolvers — this is the largest plugin by code volume:
| Module | Size | Role |
|---|---:|---|
| `tsjs_resolver.py` | 23.0 KB | TS/JS cross-reference resolution |
| `rust_resolver.py` | 22.1 KB | Rust xrefs (paths, use statements, traits) |
| `python_resolver.py` | 20.7 KB | Python xrefs (imports + name binding) |
| `aggregator.py` | 3.8 KB | Combines per-language resolver outputs |
| `graph_writer.py` | 4.9 KB | Emits xref edges to RDF |
| `artifact.py` | 2.4 KB | Materializes xref artifact |

### 5.4 `llm_enrich/`
Generates LLM summaries for files/concepts. **Mandatorily verified** (see PALS's LAW in `CLAUDE.md`).
| Module | Size | Purpose |
|---|---:|---|
| `aggregator.py` | 11.9 KB | Orchestrates enrichment runs |
| `graph_writer.py` | 10.0 KB | Emits `llm_summary` / `llm_description` triples |
| `enricher.py` | 7.9 KB | Per-target enrichment logic |
| `prompts.py` | 6.1 KB | Prompt construction |
| `cache.py` | 6.2 KB | Content-addressed JSON cache (10 imports-in) |
| `client.py` | 5.7 KB | LLM client |
| `artifact.py` | 4.9 KB | Artifact materialization |
| `prompts/` | — | Versioned prompt templates (`*.v1.txt`) + README |

LLM summaries are visible in the repo's own bundle (e.g. the central_files in §4 each carry an `llm_summary` field) — meaning the project **dogfoods** its own enrichment pipeline.

---

## 6. Frontend Surface

### 6.1 `frontend/backend/` (FastAPI)
- `app.py` — **46.7 KB single-file** REST API. This is the largest source file in the repo and an obvious refactor target.
- Two requirements files: `requirements.txt` (115 bytes — minimal) and `requirements-sbert.txt` (241 bytes — adds sbert).
- Containerized (`Dockerfile`, `docker-compose.yml` — both currently dirty in the working tree per `git status`).

### 6.2 `frontend/mcp_server/`
The MCP server implementing the very interface we used for this inspection. Files:
| File | Size | Concern |
|---|---:|---|
| `schemas.py` | 41.6 KB | Tool-call JSON schemas (declarative tool surface) |
| `handlers.py` | 34.6 KB | Tool dispatch + business logic |
| `auth.py` | 10.6 KB | OAuth |
| `server.py` | 10.5 KB | stdio JSON-RPC frame handler |
| `prompts.py` | 9.3 KB | MCP-side prompts |
| `resources.py` | 8.4 KB | MCP resources (e.g. `bundle://`) |
| `http_transport.py` | 8.0 KB | HTTP transport (alternative to stdio) |
| `observability.py` | 7.1 KB | Logging / metrics |
| `subscriptions.py` | 6.5 KB | Subscription / notification surface |
| `sparql.py` | 4.7 KB | SPARQL endpoint |
| `validators.py` | 3.5 KB | Input validation |

Test coverage is *deeper* than the production layer: `tests/test_server.py`, `test_oauth.py`, `test_hardening.py`, `test_http_transport.py`, `test_prompts.py`, `test_resources.py`, `test_subscriptions.py`, `test_coverage_gaps.py`, `test_vocab.py`.

### 6.3 `frontend/ui/` (React SPA)
TypeScript + Vite. Component map:
- **api.ts** (7.6 KB) — typed client (15 importers — pure type sink, like `models.py`).
- **App.tsx** (5.3 KB) — top-level routing.
- **views/** — `Dashboard`, `FileDetail`, `FileGraph`, `ChunkDetail`, `ChunkSearch`, `ConceptDetail`, `ConceptGraph`, `SymbolGraph`.
- **components/** — `CytoscapeGraph`, `LlmEnrichmentCard`.
- **bundle-context.ts** — bundle-selection context.
- **__tests__/** — `bundles`, `views`, `empty-states` (22 KB — extensive!), `cytoscape-graph`, `fixtures`.

Single `package.json` (857 bytes — small dep surface; consistent with Vite + React minimal stack).

---

## 7. Concept-Space (L3) Highlights

Top concepts by frequency (from `repository_summary.key_concepts`, k=30):

| Rank | Concept | Freq | Files | Kind |
|---:|---|---:|---:|---|
| 1 | test | 889 | 46 | — |
| 2 | bundle | 230 | 36 | — |
| 3 | file | 145 | 35 | — |
| 4 | chunk | 143 | 22 | — |
| 5 | concept | 135 | 29 | — |
| 6 | graph | 93 | 22 | — |
| 7 | check | 84 | 23 | — |
| 8 | build | 82 | 31 | — |
| 9 | fixture | 75 | 26 | — |
| 10 | resolve | 74 | 26 | — |
| 11 | verify | 73 | 40 | — |
| 12 | rust | 70 | 15 | — |
| 13 | emit | 66 | 25 | — |
| 14 | llm | 66 | 27 | — |
| 15 | xref | 62 | 20 | — |
| 16 | resolver | 59 | 9 | — |
| 17 | cache | 56 | 23 | — |
| 18 | **import_statement** | 56 | 19 | **structural-primitive** |
| 19 | analyzer | 48 | 3 | — |
| 20 | symbol | 49 | 16 | — |

Observations:
- **`import_statement`** is the only top-30 concept carrying a controlled-vocabulary `kind` (`structural-primitive` → `code_structure`). The other 29 are raw lexical concepts. This indicates the SKOS vocabulary is only sparsely populated — see §8.
- The concept distribution is *meta-heavy*: top concepts (`test`, `bundle`, `file`, `chunk`, `concept`, `graph`) describe **the tool itself**. This is expected for a code-graph tool but worth noting.
- `verify` appears in 40 files — consistent with the project's stated PALS's-LAW commitment that LLM/computed output must be verified.

---

## 8. Findings & Risk Surface

### 8.1 Strengths
1. **Layered RDF model is clean.** L1 (structure) / L2 (chunks+vectors) / L3 (concepts) with stable predicate namespaces (`cbm`, `cbml2`, `cbml3`, `skos`, `nif`). SHACL conformance is asserted.
2. **Plugin contract is consistent.** All four plugins follow the same `__init__ / artifact / graph_writer` shape, suggesting `codebase_mapper.extensions` is a real boundary, not a wrapper.
3. **High test-to-source ratio (0.77).** Dedicated test modules per MCP-server concern (`auth`, `oauth`, `hardening`, `http_transport`, `prompts`, `resources`, `subscriptions`, `vocab`, `coverage_gaps`).
4. **Versioned prompts.** LLM prompts are stored as `*.v1.txt` under `plugins/llm_enrich/prompts/` — explicit promptware versioning.
5. **Round-trip reconstruction.** `codebase_mapper/reconstruct.py` + `self_test.py` indicate the inventory→blobs→file path is testable end-to-end.
6. **Self-application.** The repo's own bundle includes `llm_summary` entries — the project consumes the artifacts it produces.

### 8.2 Risks / Gaps

| # | Finding | Evidence | Severity |
|---|---|---|---:|
| R1 | `frontend/backend/app.py` is a **46.7 KB monolith** — by far the largest source file. | `list_files` | High |
| R2 | `frontend/mcp_server/handlers.py` (34.6 KB) and `schemas.py` (41.6 KB) are similarly oversized. | `list_files` | Medium |
| R3 | **Tests-edges sparse: 15 vs 77 test files.** Most test→tested links are not represented in L1. | `bundle_summary.counts.tests_edges = 15` | Medium |
| R4 | SKOS controlled-vocab is **sparsely populated**: only 1 of top-30 concepts has a `kind`. | `repository_summary.key_concepts` | Medium |
| R5 | **No Rust inline tests captured.** `rust_files_with_inline_tests = 0` despite 9 Rust files and 4 `#[test]` attributes. | `bundle_summary.counts`, `rust_attribute_distribution` | Low |
| R6 | Working tree dirty on container files (`frontend/backend/Dockerfile`, `frontend/docker-compose.yml`). Uncommitted infra drift. | `git status` snapshot | Low |
| R7 | TS/JS xref resolver is the **largest** plugin module (23 KB) — TS resolution is famously fraught (tsconfig paths, aliases, monorepo). Inspect for completeness. | `list_files plugins/` | Medium |
| R8 | LLM enrichment pipeline writes into the bundle (`llm_summary`, `llm_description`). Per project's own PALS's LAW, these MUST be treated as untrusted. Verify that consumers (UI cards, MCP responses) gate them as derived data, not facts. | `frontend/ui/src/components/LlmEnrichmentCard.tsx` exists; cbm-mcp surfaces `llm_summary` in `repository_summary.central_files` | High (architectural) |
| R9 | Two requirements files (`requirements.txt`, `requirements-sbert.txt`) without a hard lock; `pyproject.toml` is the canonical manifest. Three-way drift risk between `pyproject.toml`, `requirements.txt`, and `requirements-sbert.txt`. | `list_files type=dependency_manifest` | Medium |
| R10 | `extensions.py` exposes **7 extension points**, but only 4 plugins ship. Either some slots are unused (dead surface) or the API is intentionally over-provisioned. | `extensions.py` 11.6 KB / 20 importers, plugin count = 4 | Low |

### 8.3 Recommendations (ordered by leverage)

1. **Split `frontend/backend/app.py` and `frontend/mcp_server/handlers.py`** by concern. Both are doing too much for an HTTP/MCP edge. Target: <8 KB per file.
2. **Materialize `cbm:tests` edges from `tests_edges.py` heuristics.** 15 edges for 77 test files is below the floor implied by `codebase_mapper/tests_edges.py` existing as a dedicated module.
3. **Expand `software_primitives.yaml`** to cover the top-20 concepts. Currently `import_statement` is the only one classified — the L3 layer is under-typed.
4. **Audit LLM-derived-fact gating.** Per CLAUDE.md PALS's LAW: every consumer of `llm_summary` / `llm_description` (MCP response shaping, UI rendering, RDF emission) must mark this data as derived and unverified. Confirm `LlmEnrichmentCard.tsx` and `mcp_server/handlers.py` do so.
5. **Resolve the dependency-manifest fan-out.** Consolidate `requirements*.txt` ↔ `pyproject.toml` to one source of truth or document the split (probably: `pyproject.toml` for the library, `requirements*.txt` pinned for the backend container).
6. **Investigate the Rust-inline-tests discrepancy.** Either the parser misses `#[cfg(test)] mod tests { #[test] fn … }` blocks or the fixtures have none — but with 4 `#[test]` attributes counted, the `rust_files_with_inline_tests=0` looks like an emitter bug.
7. **Decide on `extensions.py`'s 7 slots.** Either ship/document the unused ones with example plugins or trim them to match reality.

---

## 9. Suggested Next Inspections (CBM tools)

To go deeper, the natural next calls against this bundle are:

| Tool | Args | Purpose |
|---|---|---|
| `file_detail` | `path="frontend/backend/app.py"` | Confirm split-points in the 46 KB monolith |
| `file_impact` | `path="codebase_mapper/models.py"` | 28 importers — measure blast radius before changing |
| `concept_neighborhood` | `name="import_statement", depth=2` | Inspect the only typed structural-primitive |
| `imports_of` | `path="codebase_mapper/extensions.py"` | Verify which of the 7 slots actually have callers |
| `semantic_neighbors` | `q="LLM output verification"` | Locate every site that consumes LLM-derived data — for the PALS's-LAW audit |
| `sparql` | `cbm:Phase` query | Inspect the phase ontology |
| `items_by_attribute` | filter on `tests_edges` | Identify test files lacking a `tests` edge |

---

## 10. One-Paragraph Executive Summary

`code-base-mapper` is a Python/TypeScript/Rust polyglot tool that ingests a repository and produces a three-layer RDF bundle: **L1** (files, languages, imports, AST summaries), **L2** (semantic chunks with NIF spans and 384-dim sentence-transformer vectors), and **L3** (SKOS-style concept graph from identifier splitting). The codebase ships a core library (`codebase_mapper/`), four plugins (`chunks_embeddings`, `concept_graph`, `symbol_xrefs`, `llm_enrich`), a FastAPI backend, an MCP server (stdio + HTTP, OAuth, SPARQL, subscriptions), and a React/Cytoscape UI. The architecture is layered cleanly with consistent plugin contracts, asserted SHACL conformance, and a strong test footprint (0.77 test/source ratio). The chief structural risks are three oversized files (`frontend/backend/app.py` 46.7 KB, `mcp_server/schemas.py` 41.6 KB, `mcp_server/handlers.py` 34.6 KB), an under-populated SKOS vocabulary (only `import_statement` is typed among the top 30 concepts), and sparse `tests`-edge emission (15 edges for 77 test files). The PALS's-LAW commitment in `CLAUDE.md` makes the LLM-enrichment pipeline an architecturally sensitive surface; every consumer of `llm_summary`/`llm_description` MUST gate that data as derived-and-unverified — a property worth a dedicated audit.
