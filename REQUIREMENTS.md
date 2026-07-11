---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Sonnet 5 via Claude Code (codebase-requirements skill, 7-agent parallel forensic pass)"
  date: "2026-07-11"
---

# REQUIREMENTS.md — codebase-mapper

Forensic extraction, not a guarantee. Every claim below traces to a file path
(and usually a line number) in this repository as of commit `80978b8`
(2026-07-11). Several claims — marked "verified empirically" — were confirmed
by actually running code (test suites, live drift-guard scripts, a
reconstructed Docker build step), not just by reading it. Where a claim could
not be verified, that is stated explicitly rather than assumed.

Produced by seven parallel research agents, each scoped to one subsystem
(core pipeline/CLI, plugins, FastAPI backend, React UI, MCP server,
tests+docs, tooling/build/deploy), synthesized into this single document.

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](./DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

---

## 1. Project Overview

**Project name**: `codebase-mapper` (PyPI/console-script name `codebase-mapper`; internal package `codebase_mapper`)
**Current version**: `0.5.0` (`pyproject.toml:8`, mirrored in `codebase_mapper/shared_kernel/constants.py:TOOL_VERSION`, kept in lockstep by `tests/verify_drift_p1.py`)

**Description**: Maps source repositories into RDF (Turtle/JSON-LD) graphs plus JSON sidecars — a "bundle" — capturing file classification, per-language AST summaries, import/dependency edges, source chunks, embeddings, a concept graph, symbol-level cross-references, and optional LLM-authored enrichment. A FastAPI backend, React UI, and read-only MCP server all consume the same bundle directory to make it explorable by humans and agents.

**Primary purpose** (`PURPOSE.md`): turn a repository into an inspectable, queryable knowledge bundle where mechanically-derived facts, generated inferences, and LLM-authored annotations are kept explicitly separated, so downstream consumers can audit every claim's provenance.

**Target audience**: engineers and agents needing a grounded view of a codebase before acting on it — refactoring tools, code reviewers, documentation auditors, architecture inspectors, MCP clients (this very Claude Code session is one).

**Non-goals** (`PURPOSE.md`): not a source-control replacement or byte-perfect archive unless blobs are explicitly retained; not a source of unquestioned truth (inferred/LLM output is always derived data); not a write-capable remote-execution service (the MCP surface is read-only by construction).

**Intended use cases**:
1. One-shot repository mapping (local path or Git URL) into a portable bundle (`codebase-mapper` CLI / `scripts/run_l2/l3/l4/xrefs.py`).
2. Interactive exploration of a bundle via REST (FastAPI), a browser SPA (React+Cytoscape), or an MCP client (Claude Code, other agents).
3. Structural decomposition of a bundle into confidence-tagged architecture facts (`decomposer/`) and a natural-language ordered rebuild plan from that decomposition (`recomposer/`).
4. Static/offline reporting: HTML/MD/JSON structural report, an A4 PDF dossier, a D3 cartogram, a WebGL2 terrain map, a narrated walkthrough (`scripts/cbm.py`).
5. Byte-perfect (`reconstruct`) or AST-only (`regenerate`) source materialization from a bundle, for round-trip verification or partial recovery without a blob store.

### 1.1 High-Level Architecture

A **layered, plugin-registry pipeline** produces the bundle:

```
L1 (host, codebase_mapper/)      → classify files, extract AST per language, resolve imports/deps
L2 (plugins/chunks_embeddings/)  → source chunks + embeddings (sbert or deterministic hash)
L3 (plugins/concept_graph/)      → identifier-derived concept graph + curated SKOS vocabulary
   (plugins/symbol_xrefs/)       → symbol-level call/subclass/override edges (co-resident with L3)
L4 (plugins/llm_enrich/)         → optional Ollama-backed LLM annotations (file summaries, concept
                                    descriptions, schema purposes), fully disclosed provenance
```

Seven extension protocols (`LanguageAnalyzer`, `ImportResolver`, `RecordEnricher`, `Aggregator`,
`GraphContributor`, `ShapeContributor`, `ArtifactEmitter`) mediate every layer; plugins register
name-prefixed instances (`l2_20_embeddings`, `l3_10_xrefs`, `l4_10_enrich`, …) into shared
registries sorted by `.name` to fix load order.

A **bundle** (a plain directory: `run_manifest.json`, `inventory.ttl`/`inventory.jsonld`,
`shapes.shacl.ttl`, `embeddings.npz`+`embeddings_meta.json`, `concepts.json`, `xrefs.jsonl`,
optional `blobs/`, optional `enrichments.jsonl`) is the **only** interface between the mapper and
everything downstream — there is no live database. Three consumer surfaces read it independently:

- **`frontend/backend/`** — FastAPI REST service, 13 endpoints, Bearer-token or opt-in-anonymous auth, serves the React UI.
- **`frontend/ui/`** — React 18 + Cytoscape.js SPA, one view per bundle facet (files, symbols, concepts, chunks, detail pages).
- **`frontend/mcp_server/`** — read-only MCP server (18 tools, 8 resources, 3 prompts), stdio and Streamable-HTTP transports, structurally incapable of writing (no write code path exists in the module).

A separate **reporting/tooling layer** (`scripts/cbm.py` dispatcher; `tools/cbm-report` in Rust for
multi-GB bundles; `tools/cbm-cartogram` in Node/D3) and a **decomposer/recomposer** pair
(bundle → confidence-tagged architecture facts → natural-language rebuild plan) sit on top of the
same bundle, read-only.

**Architecture type**: modular monolith (core mapper) with a plugin-registry extension model, plus
a client-server visualization layer (FastAPI + SPA + MCP) that treats the bundle directory as its
sole datastore. No message queue, no live database, no distributed components.

### 1.2 Technology Stack

| Layer | Technology | Version | Notes |
|---|---|---|---|
| Core language | Python | `>=3.10` (CI runs 3.12) | |
| Parsing | tree-sitter + 12 grammars | unpinned | C, C++, CFML, Go, Java, JS, Kotlin, ObjC, Ruby, Rust, Swift, TS; several other first-class languages (Python, Shell, SQL, HTML, CSS, JSON, YAML, Dart, Clojure, COBOL) use stdlib `ast` or hand-rolled extractors instead |
| RDF (canonical) | rdflib | `>=7.0,<8.0` | authoritative graph API, SHACL shape builder |
| RDF (performance) | pyoxigraph (Rust) | unpinned / `>=0.5,<1.0` (frontend) | bulk Turtle/JSON-LD serialization + SPARQL store; graceful rdflib fallback |
| Validation | pyshacl, custom `fast_shacl.py` | `>=0.27` | fast engine default, pyshacl opt-in via `CBM_SHACL_ENGINE` |
| Schema mirror | Pydantic | `>=2.7,<3.0` | canonical SHACL shape model + inventory schema |
| ML | numpy, sentence-transformers | `2.x`, `3–6.x` | hash vs. sbert embedding backends |
| API | FastAPI + Uvicorn | `>=0.115`, `>=0.30` | |
| MCP | `mcp` SDK | `>=1.27,<2.0` | low-level `Server`, stdio + Streamable HTTP |
| Frontend | React 18, react-router-dom 6, Cytoscape.js 3 (+react-cytoscapejs) | | Vite build, Vitest tests |
| Reporting (perf tier) | Rust (`tools/cbm-report`), Node/D3 (`tools/cbm-cartogram`) | | streaming multi-GB inventory readers |
| Package/dep mgmt | `uv` (`uv.lock`) + setuptools | | |
| Containerization | Docker (root `Dockerfile` + `frontend/docker-compose.yml`) | | |
| CI | GitHub Actions | | 2 workflows (`lint.yml`, `backlog-governance.yml`) |
| Test | pytest, 66 standalone `verify_*.py` scripts, Vitest (UI) | | see §9 |

---

## 2. Provenance and Origin

### 2.1 Authors and Contributors

| Name | Email | Source |
|---|---|---|
| Pedro Anisio Silva | pedroanisio@gmail.com | `git log --format='%aN <%aE>'` |
| Pedro Anisio Silva | pedroanisio@iande.ai | `git log --format='%aN <%aE>'` |

Single human author across all 183 commits; multiple commits/docs carry `generated_by:` frontmatter attributing drafting to various LLM tools (Claude, GPT-5 Codex) under human direction — consistent with the project's own disclosure norms.

### 2.2 Repository Origin

**Source URL**: `https://github.com/pedroanisio/code-base-mapper.git` (`git remote -v`)
**VCS**: git
**Hosting**: GitHub

### 2.3 License

**Type**: none detected. No `LICENSE*`/`LICENCE*`/`COPYING*` file at any depth, no license field in `pyproject.toml`. — flagged as **GAP-001** in §12.

### 2.4 Version History

**Current version**: `0.5.0`
**First commit**: 2026-05-12 ("Refactor code structure for improved readability and maintainability")
**Last commit** (at analysis time): 2026-07-11 ("feat(lang): PHP as a first-class language")
**Commit count**: 183
**Tags**: none
**Changelog file**: none found at root (no `CHANGELOG*`); `docs/BACKLOG.md`/`docs/backlog.yml` serve an adjacent function (a schema-governed deferred-work registry, not a release changelog).

### 2.5 Boilerplate Origin

No generator markers (`.yo-rc.json`, `cookiecutter.json`, `create-react-app` banner, etc.) found. The React UI (`frontend/ui/`) was hand-scaffolded with Vite (`vite.config.ts`, no CRA/other-generator fingerprint).

---

## 3. Complete Asset Inventory

Scale: ~625 hand-authored source/doc/test files outside vendor/cache/build directories
(`codebase_mapper` 79, `plugins` 38, `frontend` 93, `tests` 257, `docs` 38, `scripts` 29, `tools`
37, `decomposer` 16, `recomposer` 8, `static` 27, `.github` 2, plus root config) — this sits in the
301–1000 range, so the inventory below is per-module/per-package with representative components
called out, not per-file. `_tmp/`, `.repo/`, `_site/`, `reports/`, `.venv/`, `.mypy_cache/` are
generated/cache/vendor directories and are excluded (not inventoried).

### 3.1 Directory Structure Overview

```
.
├── codebase_mapper/        L1 host package (inspection → emission → shared_kernel → verification)
│   ├── inspection/         classification, repo sourcing/cloning, AST extraction, import resolution
│   │   └── languages/      one analyzer+resolver module per first-class language
│   ├── emission/           domain (Pydantic inventory schema) / infrastructure (RDF, storage) / application (emit, regenerate, reconstruct)
│   ├── shared_kernel/      7 extension protocols+registries, RDF namespaces, controlled vocab, SHACL spec builder, .env loader
│   ├── verification/       bundle CI gate, fast SHACL engine
│   └── cli.py, __main__.py, extensions.py, regenerate.py
├── plugins/                4 optional extension plugins (chunks_embeddings, concept_graph, symbol_xrefs, llm_enrich)
├── frontend/
│   ├── backend/            FastAPI REST service over a bundle
│   ├── mcp_server/         read-only MCP server (stdio + Streamable HTTP)
│   └── ui/                 React + Cytoscape.js SPA
├── decomposer/             bundle → confidence-tagged structural decomposition (YAML/MD)
├── recomposer/             decomposition YAML → natural-language ordered rebuild plan
├── scripts/                pipeline entry points (run_l2/l3/l4/xrefs.py) + reporting CLIs (cbm.py + 9 generators)
├── tools/
│   ├── cbm-report/         Rust crate — streaming multi-GB PDF report
│   └── cbm-cartogram/      Node/D3 — interactive HTML cartogram
├── tests/                  66 verify_*.py drift/contract guards + 87 pytest test_*.py + per-language fixtures + golden_repo
├── docs/                   goal ledger, reporting contract, vocab/llm-enrich/regenerate references, archived plans, backlog
├── static/                 vendored XSD schemas + protobuf DSL, used as classifier fixtures
├── .github/workflows/      2 CI workflows (lint.yml, backlog-governance.yml)
├── docker/, Dockerfile     single-stage Python 3.11-slim image
├── pyproject.toml, uv.lock, Makefile, .env.example
├── PURPOSE.md, CLAUDE.md, AGENTS.md, DISCLAIMER.md, README.md
```

### 3.2 Asset Inventory (per-module summary)

| Module / Directory | Files | Asset Type | Objective | Key Components |
|---|---|---|---|---|
| `codebase_mapper/` | 79 | Source — core library | L1 pipeline: classify → extract AST (18+ languages) → resolve imports → emit RDF/JSON-LD + SHACL | `inspection/pipeline.py` (`map_codebase`), `emission/application/{emit_bundle,regenerate,reconstruct}.py`, `shared_kernel/extensions.py`, `cli.py` |
| `plugins/chunks_embeddings/` | ~10 | Source — plugin (L2) | Source chunking + embeddings (sbert/hash) | `chunker.py`, `embedder.py`, `backends.py`, `graph_writer.py` |
| `plugins/concept_graph/` | ~9 | Source — plugin (L3) | Identifier splitting, curated SKOS vocabulary, concept co-occurrence | `splitter.py`, `concepts.py`, `graph_writer.py` |
| `plugins/symbol_xrefs/` | ~13 | Source — plugin (L3-adjacent) | Symbol-level call/subclass/override edges, 10 language resolvers | `aggregator.py`, `{python,tsjs,cobol,…}_resolver.py`, `graph_writer.py` |
| `plugins/llm_enrich/` | ~10 + `prompts/` | Source — plugin (L4, opt-in) | Ollama-backed file/concept/schema annotations with full provenance + graceful degradation | `client.py`, `enricher.py`, `aggregator.py`, `cache.py`, `model_resolver.py`, `prompts.py` |
| `frontend/backend/` | ~30 | Source — FastAPI service | 13 REST endpoints over a bundle, Bearer/anonymous auth | `app.py` (487 lines), `serving/application/*.py`, `tests/` (6 files, 103 tests) |
| `frontend/mcp_server/` | ~32 | Source — MCP server | 18 tools, 8 resources, 3 prompts, read-only by construction | `server.py`, `handlers.py`, `schemas.py`, `sparql.py`, `auth.py`, `tests/` (15 files, 203 tests) |
| `frontend/ui/src/` | ~30 | Source — React SPA | 8 routed views over the backend's 13 endpoints | `views/*.tsx`, `components/CytoscapeGraph.tsx`, `components/LlmEnrichmentCard.tsx`, `api.ts` |
| `tests/` | 257 | Test + fixtures | 66 `verify_*.py` drift/contract guards, 87 pytest `test_*.py`, per-language fixtures, `golden_repo` | `verify_drift_p1/p2/p3.py`, `verify_language_goal.py`, `fixtures/golden_repo/`, `fixtures/llm_cache/` |
| `docs/` | 38 | Documentation | TIOBE-50 goal ledger, 30-component reporting contract, vocab/llm-enrich/regenerate maintainer refs, archived plans, backlog | `goals/tiobe-top50.yaml`, `reporting/report-spec.schema.json`, `BACKLOG.md`/`backlog.yml`, `archive/` |
| `scripts/` | 29 | Script — pipeline + reporting CLIs | `run_l2/l3/l4/xrefs.py` pipeline entry points; `cbm.py` dispatcher routing to 9 report/site/repair/verify/terrain/walkthrough generators | `cbm.py`, `cbm_dossier.py` (2346 lines), `generate_static_site.py` (2228 lines), `cbm_report.py` (1446 lines) |
| `tools/cbm-report/` | ~15 | Source — Rust crate | Streams `inventory.jsonld` (5GB+) in 64MB blocks, renders an 8-page PDF independent of the Python graph load | `src/main.rs`, `src/ingest/splitter.rs`, `src/pdf/{pages,charts}.rs` |
| `tools/cbm-cartogram/` | ~22 | Source — Node/D3 tool | Full `JSON.parse` of `inventory.jsonld` → standalone D3 HTML map | `tools/normalize-inventory.mjs`, `tools/build-standalone.mjs`, `src/themes.js` |
| `decomposer/` | 16 | Source — analysis CLI | Bundle → confidence-tagged parts/roles/instability/cycles/architecture/quality-gates/build-order | `model.py`, `metrics.py` (Martin instability, Tarjan SCC), `quality.py` (9 gates), `decompose.py` |
| `recomposer/` | 8 | Source — planning CLI | Decomposition YAML → ordered natural-language rebuild plan | `plan.py` (889 lines, scheduler), `model.py` |
| `static/` | 27 | Data — fixtures | Vendored IEEE/IEC/SPDX/DDD XSDs + protobuf DSL, used as classifier test fixtures | `schemas/`, `proto/dsl/v2/` |
| `docker/`, `Dockerfile`, `.dockerignore` | 3 | Configuration — container | Single-stage `python:3.11-slim` image, `WITH_SBERT` build arg | `Dockerfile`, `docker/cbm-analyze` |
| `.github/workflows/` | 2 | Configuration — CI | `lint.yml` (4 jobs), `backlog-governance.yml` (1 job, path-filtered) | |
| Root config | ~10 | Configuration | Dependency manifest, lockfile, task runner, env template | `pyproject.toml`, `uv.lock`, `Makefile` (333 lines), `.env.example` |
| Root docs | 6 | Documentation | Project governance and purpose | `CLAUDE.md`, `PURPOSE.md`, `AGENTS.md`, `DISCLAIMER.md`, `README.md` (589 lines) |

---

## 4. Functional Requirements

Package-scoped identifiers: `FR-core-*` (host pipeline), `FR-plg-*` (plugins), `FR-be-*`
(backend), `FR-ui-*` (React UI), `FR-mcp-*` (MCP server), `FR-tool-*` (scripts/tools/decomposer/recomposer).

### 4.1 Core host pipeline (`codebase_mapper/`)

| ID | Description | Source Files |
|---|---|---|
| FR-core-001 | `codebase-mapper` CLI: map a local path or Git URL into a bundle (`--repo`, `--out`, `--exclude`, `--name`, `--no-emit-blobs`) | `codebase_mapper/cli.py:18-88` |
| FR-core-002 | `--reconstruct`: byte-perfect file materialization from `inventory.ttl` + `blobs/` | `codebase_mapper/emission/application/reconstruct.py:20` |
| FR-core-003 | `--regenerate`: AST-only materialization from `inventory.ttl` alone (no blobs); Python semantic, TS/JS/Rust byte-identical, all other languages unsupported | `codebase_mapper/emission/application/regenerate.py:40` |
| FR-core-004 | `--verify-roundtrip` / `--self-test`: composes map→emit→reconstruct→diff in one shot for CI/self-verification | `regenerate.py`/`reconstruct.py:92`, `cli.py` |
| FR-core-005 | `map_codebase()`: classify every file, extract AST via 18+ per-language analyzers (first-match-wins), resolve imports (in-repo + external), resolve dependency-manifest and lockfile pins | `inspection/pipeline.py:189` |
| FR-core-006 | Per-file extraction isolation: any analyzer exception or `RecursionError` is caught per-file into `extraction_errors`, never aborts the run | `inspection/pipeline.py:166` (`_safe_extract`) |
| FR-core-007 | `emit()`: build the inventory RDF graph via `GraphContributor`/`ShapeContributor` hooks, serialize Turtle/JSON-LD/SHACL, write blobs, run `ArtifactEmitter`s, self-check SHACL conformance, write `run_manifest.json` | `emission/application/emit_bundle.py:39` |
| FR-core-008 | Dual serialization path: rdflib for small graphs; rdflib→N-Triples→external `sort`→pyoxigraph bulk-load→Turtle dump for large graphs (measured 255K triples: 8.5s rdflib vs 0.25s oxigraph; 67M triples: "tens of minutes/>100GB" vs 157s/38s at 23.6GB peak) | `emission/infrastructure/rdf/fast_serializer.py:54` |
| FR-core-009 | Extension registries: 7 protocols (`LanguageAnalyzer`, `ImportResolver`, `RecordEnricher`, `Aggregator`, `GraphContributor`, `ShapeContributor`, `ArtifactEmitter`), name-sorted load order, `reset_registries()` seam for test isolation | `shared_kernel/extensions.py:42-163` |
| FR-core-010 | Repo source resolution: local path, GitHub HTTPS/SSH, or `github.com/OWNER/REPO` shorthand; remote repos cloned to a temp dir and removed on exit | `inspection/repo_source.py:206` |
| FR-core-011 | `.cbmignore` + repeatable `--exclude` (POSIX glob) merge at repo scope | `inspection/pipeline.py` (merge logic), root `README.md:160-175` |
| FR-core-012 | Bundle CI gate: `check_bundle()` fails on undisclosed degradation, unaccepted SHACL non-conformance, etc.; degradations require explicit `--accept-degradation` acknowledgment | `codebase_mapper/verification/bundle_gate.py:82` |

### 4.2 Plugins (`plugins/`)

| ID | Description | Source Files |
|---|---|---|
| FR-plg-001 | L2 chunking: per-symbol chunks for Python/TS/JS/Rust (re-parsed), items-based chunks for 17 other languages (reuse L1's `ast_summary`), whole-file fallback otherwise | `plugins/chunks_embeddings/chunker.py:61-1943` |
| FR-plg-002 | L2 embeddings: `sentence-transformers/all-MiniLM-L6-v2` (sbert) or a SHA-256 deterministic pseudo-vector (hash, "lies about semantics" by design, for reproducible tests) — both L2-normalized | `plugins/chunks_embeddings/backends.py:35,66` |
| FR-plg-003 | L3 identifier splitting: snake/camel/Pascal/SCREAMING/kebab-case + acronym-boundary tokenization from filenames, directory names, and AST-declared symbols | `plugins/concept_graph/splitter.py:45` |
| FR-plg-004 | L3 curated vocabulary: ~90 canonical terms (domain/structural/relational primitives) with aliases and SKOS-Collection typing, loaded from `software_primitives.yaml`, opt-out via `--no-builtin-vocab` | `emission/infrastructure/vocab/{software_primitives.yaml,loader.py}` |
| FR-plg-005 | Symbol xrefs: `calls`/`subclassOf`/`overrides` edges (declared `references` kind is never emitted — schema-ahead-of-implementation gap) across 10 languages, each edge reified as a `cbmxr:Edge` node | `plugins/symbol_xrefs/{aggregator,graph_writer}.py`, `constants.py:43` |
| FR-plg-006 | L4 LLM enrichment (opt-in): 3 kinds — `fileSummary`, `conceptDescription`, `schemaPurpose` — via local Ollama, every triple carrying model/prompt-SHA/timestamp provenance | `plugins/llm_enrich/{enricher,aggregator,graph_writer}.py` |
| FR-plg-007 | L4 content-addressed cache (`~/.cache/cbm-llm/`, key = sha256 of kind+model+prompt_sha+target_sha); prompt-file edits auto-invalidate stale entries | `plugins/llm_enrich/cache.py` |
| FR-plg-008 | L4 graceful degradation: on Ollama unreachable/model missing, self-disables for the rest of the run and discloses `{component, reason, kind, skipped, error}` in `ctx.scratch["degradations"]`; SHACL stays green (all L4 predicates optional-cardinality) | `plugins/llm_enrich/{enricher.py:184-224,aggregator.py:174-193}` |

### 4.3 FastAPI backend (`frontend/backend/`)

| ID | Description | Source Files |
|---|---|---|
| FR-be-001 | Serve 13 REST endpoints over a loaded bundle (list §7.1) with per-bundle LRU cache (`maxsize=4`) | `app.py`, `serving/application/bundle_data.py:743-750` |
| FR-be-002 | Perimeter auth: global ASGI middleware gates every `/api/*` route except `/api/healthz`; token mode always wins over anonymous opt-in; `secrets.compare_digest` constant-time comparison | `app.py:323-367` |
| FR-be-003 | Multi-bundle support via `?bundle=` query param, path-traversal-safe bundle-name validation | `serving/application/bundle_data.py:652-654` |
| FR-be-004 | Semantic chunk search: cosine NN over stored chunk vectors when the bundle's embedding backend name matches a known sbert signature; substring lexical fallback otherwise | `serving/application/chunks.py:38-92` |
| FR-be-005 | Optional MCP mount at `/mcp/` when `CBM_MCP_TOKEN` is set, wrapped so a mount failure can't take down the REST app | `app.py:381-389` |

### 4.4 React UI (`frontend/ui/`)

| ID | Description | Source Files |
|---|---|---|
| FR-ui-001 | 8 routed views: dashboard, file graph, symbol graph, concept graph, chunk search, file/chunk/concept detail — each backed by one or two REST calls | `App.tsx:92-102`, `views/*.tsx` |
| FR-ui-002 | Shared Cytoscape.js graph component (cose layout, palette-keyed node color, weight-scaled size, tap-for-details, double-tap-to-navigate) reused across all 3 graph views | `components/CytoscapeGraph.tsx` |
| FR-ui-003 | LLM-enrichment disclosure card: visible "AI-enriched" badge + collapsed-by-default provenance panel (model, prompt/target SHA, timestamp) — never presents model output as bare fact | `components/LlmEnrichmentCard.tsx` |
| FR-ui-004 | Bundle picker: persists selection to `localStorage`, self-heals if the persisted bundle no longer exists, propagates a version counter so every view refetches without remounting | `App.tsx:16-70`, `bundle-context.ts` |
| FR-ui-005 | Change-impact panel on file detail: dependencies/dependents/transitive/tests/symbol callers-callees | `views/FileDetail.tsx` |

### 4.5 MCP server (`frontend/mcp_server/`)

| ID | Description | Source Files |
|---|---|---|
| FR-mcp-001 | 18 read-only tools (list §7.2), each schema-validated on both input and output via a `@tool` decorator | `handlers.py`, `schemas.py:1040` |
| FR-mcp-002 | Two transports sharing one `build_server()`: stdio (JSON-RPC, stdout kept pure) and Streamable HTTP (mounted on the FastAPI app) | `server.py:78-297`, `http_transport.py:147-216` |
| FR-mcp-003 | Session-scoped `select_bundle`: persists for the life of one connection/subprocess, verified end-to-end by tests; does not survive a process restart (by design — a subprocess-lifetime fact, not a bug) | `server.py:50-130`, `tests/test_server.py::test_select_bundle_persists_in_session` |
| FR-mcp-004 | `sparql` tool: gated by `CBM_ENABLE_SPARQL`, only `SELECT`/`ASK`, mutating keywords blocked pre-execution, 10K-char query cap, 1,000-row cap with `truncated` flag, 10s budget | `sparql.py:36-156` |
| FR-mcp-005 | 8 MCP resources (`cbm://bundles`, per-bundle manifest/summary/shapes/ontology, templated file/chunk/concept) + 3 prompts (`orient`, `explore_concept`, `trace_dependency`) | `resources.py`, `prompts.py:168-197` |
| FR-mcp-006 | Structural read-only guarantee: zero write-verb-prefixed tool names (enforced by a naming-convention test), zero write syscalls anywhere in the module (confirmed by exhaustive grep) | `tests/test_schemas.py:372-377` |

### 4.6 Tooling (`scripts/`, `tools/`, `decomposer/`, `recomposer/`)

| ID | Description | Source Files |
|---|---|---|
| FR-tool-001 | `scripts/cbm.py`: unified dispatcher for 10 subcommands (`report`, `report-rs`, `dossier`, `pdf`, `site`, `cartogram`, `repair`, `verify`, `terrain`, `walkthrough`), each lazily imported so a missing optional dependency breaks only that command | `scripts/cbm.py:39-101` |
| FR-tool-002 | `tools/cbm-report` (Rust): streams `inventory.jsonld` in 64MB blocks (never a whole-file parse), renders an 8-page PDF including an independent recount cross-checked against the manifest | `tools/cbm-report/src/{main.rs,ingest/splitter.rs}` |
| FR-tool-003 | `tools/cbm-cartogram` (Node/D3): normalizes `inventory.jsonld` (full in-memory `JSON.parse`, not streamed) into a self-contained HTML D3 map with 6 dark/light themes | `tools/cbm-cartogram/tools/normalize-inventory.mjs` |
| FR-tool-004 | `decomposer`: bundle → confidence-tagged parts/roles, Martin instability (`I=Ce/(Ca+Ce)`), Tarjan SCC/cycles, architecture-style hypothesis, 9 quality gates, topological build order | `decomposer/{metrics,quality,decompose}.py` |
| FR-tool-005 | `recomposer`: decomposition YAML (never the raw bundle) → ordered natural-language rebuild plan with a 12-phase schedule and a "no forward reference" invariant enforced at generation time | `recomposer/plan.py:47-282` |

---

## 5. Non-Functional Requirements

### 5.1 Performance

| ID | Description | Evidence |
|---|---|---|
| NFR-001 | Dual RDF serialization path exists specifically to keep multi-GB bundles tractable (measured 67M-triple case: rdflib "tens of minutes / >100GB" vs. pyoxigraph path "157s parse + 38s reserialize / 23.6GB peak") | `codebase_mapper/emission/infrastructure/rdf/fast_serializer.py:54` |
| NFR-002 | `tools/cbm-report` streams `inventory.jsonld` in fixed-size blocks specifically to avoid loading multi-GB inventories into memory (5.2GB Linux-kernel inventory: ~7s on 24 cores) | `tools/cbm-report/src/ingest/splitter.rs` |
| NFR-003 | Per-tool MCP timeout budgets (`observability.py`): default 5s, `sparql`/`semantic_neighbors` 10s, with a size-scaled cold-load allowance for graph-loading tools | `frontend/mcp_server/observability.py:45-99` |
| NFR-004 | Configurable concurrency: `CBM_EXTRACT_WORKERS` (AST extraction, default = CPU count), `CBM_ENRICH_WORKERS` (record enrichers, default 4) | `codebase_mapper/inspection/pipeline.py` |
| NFR-005 | AST-summary size cap (8 MiB) with disclosed truncation rather than unbounded growth or silent loss | `codebase_mapper/shared_kernel/json_safety.py` |
| NFR-006 (gap) | `tools/cbm-cartogram`'s normalizer does a full in-memory `JSON.parse`, not the streaming approach `tools/cbm-report` uses for the same file — a real scale asymmetry between the two "read `inventory.jsonld` directly" tools | `tools/cbm-cartogram/tools/normalize-inventory.mjs:62` |

### 5.2 Security

| ID | Description | Evidence |
|---|---|---|
| NFR-007 | Backend auth: Bearer-token perimeter middleware, fails closed by default (401 on every `/api/*` except `/api/healthz` with no config), `CBM_ALLOW_ANONYMOUS` cannot weaken a token-protected deployment | `frontend/backend/app.py:323-367` |
| NFR-008 | Backend CORS: no wildcard origin ever; default 4 loopback dev origins; methods/headers explicitly restricted | `app.py:313-379` |
| NFR-009 | Path-traversal guards on bundle names (backend) and on MCP resource URIs (`..`, `/`, `\` rejected) | `bundle_data.py:652-654`, `frontend/mcp_server/validators.py` |
| NFR-010 | MCP auth: `StaticTokenVerifier` and `JwtVerifier` (RFC 6750 status mapping, scope enforcement, tamper/expiry/audience/issuer checks) | `frontend/mcp_server/auth.py` |
| NFR-011 | SPARQL tool hardens against mutation: pre-execution regex block on `INSERT/DELETE/UPDATE/DROP/CLEAR/CREATE/LOAD/COPY/MOVE/ADD` (word-boundary safe), result-type check rejects `CONSTRUCT`/`DESCRIBE`; no query-text sanitization beyond the mutating-keyword blocklist (no defense against e.g. keyword-obfuscation inside comments) | `frontend/mcp_server/sparql.py:36-78` |
| NFR-012 | MCP server has no write code path anywhere (confirmed by exhaustive grep for `open(...'w'`/`write_text`/`write_bytes`) — structural, not policy-only, read-only guarantee | `frontend/mcp_server/` (whole module) |

### 5.3 Scalability and Deployment

| ID | Description | Evidence |
|---|---|---|
| NFR-013 | Docker image is single-stage, `WITH_SBERT` build arg toggles the sentence-transformers dependency (hash-only default, ~2GB heavier sbert variant opt-in) | `Dockerfile:19,43` |
| NFR-014 | `frontend/docker-compose.yml` wires backend (internal-only port) behind an nginx frontend service with a healthcheck-gated dependency | (referenced by `Makefile:289-311`, file itself out of per-agent scope but confirmed present) |
| NFR-015 (gap) | Root `Dockerfile`'s dependency list is unpinned and diverges from `pyproject.toml` (omits `tree-sitter-cfml/cpp/java/objc`, `pydantic`, `pyoxigraph`, `tomli`) — the `verify_requirements_mirror.py` guard does not cover this file, only the two `frontend/*/requirements.txt` mirrors | `Dockerfile:27-41` vs `pyproject.toml:17-50` |

### 5.4 Logging, Monitoring, and Error Handling

| ID | Description | Evidence |
|---|---|---|
| NFR-016 | Every degradation (shallow-clone provenance loss, SHACL skip, AST truncation, ambiguous C includes, L4 self-disable) is disclosed in `run_manifest.json["degradations"]`, never silent — a repo-wide architectural pattern ("PALS's Law"), not a one-off | multiple: `pipeline.py`, `json_safety.py`, `llm_enrich/enricher.py:184-224` |
| NFR-017 | MCP server: stdout kept strictly pure JSON-RPC for stdio transport (all logging redirected to stderr); an audit log records tool calls | `server.py:227-242`, `observability.py` |
| NFR-018 (gap) | Backend has no registered `@app.exception_handler`; a malformed bundle sidecar JSON file (e.g. corrupt `concepts.json`) produces a bare, unlogged 500 with no detail — verified by reproduction | `serving/application/bundle_data.py:431-526` |

### 5.5 Accessibility

Not assessed by this pass — no agent was scoped to run an automated a11y audit (axe/Lighthouse) against the React UI. `aria-*`/semantic-HTML usage was not inventoried; flagged as an open item rather than assumed absent.

### 5.6 Internationalization and Localization

Not applicable — no i18n framework, locale files, or translation infrastructure found anywhere in `frontend/ui/`. UI text is English-only, hardcoded.

### 5.7 Compatibility Constraints

Python `>=3.10` (CI on 3.12); Node UI toolchain uses Vite 5/TS 5.6 (no browser-support matrix declared); root `Dockerfile` pins `python:3.11-slim`.

---

## 6. Data Models and Persistence

### 6.1 Storage Mechanisms

| Type | Technology | Notes |
|---|---|---|
| Primary graph store | Turtle (`inventory.ttl`) + JSON-LD (`inventory.jsonld`), rdflib canonical / pyoxigraph performance path | No live database anywhere in the system — a bundle directory *is* the datastore |
| Blob store | Content-addressed `blobs/<sha256>` (optional, `--no-emit-blobs` disables) | enables byte-perfect `reconstruct` |
| Embeddings | `embeddings.npz` (uncompressed `np.savez`, float32) + `embeddings_meta.json` | backend name/dimension/normalization recorded for reproducibility |
| Symbol xrefs | `xrefs.jsonl` | always written, even empty |
| L4 enrichment | `enrichments.jsonl` | one row per (kind, target), sha256-keyed |
| SPARQL cache | pyoxigraph RocksDB store under `$TMPDIR/cbm_sparql_store/<hash>/` | derived query index, not a mutation of bundle source-of-truth |
| L4 response cache | `~/.cache/cbm-llm/<sha256>.json` (overridable via `CBM_LLM_CACHE`) | content-addressed by kind+model+prompt_sha+target_sha |

### 6.2 Data Models

#### DM-001 — Inventory Graph (RDF)

**Source**: `codebase_mapper/shared_kernel/vocabulary.py` (namespaces), `codebase_mapper/emission/infrastructure/rdf/rdflib_emitter.py` (SHACL shape builder), `codebase_mapper/shared_kernel/shacl_spec.py` (spec→RDF renderer).

Namespaces: `cbm:` (core predicates/classes), `cbmt:` (16-term `FileType` vocab), `cbmp:` (7-term `Phase` vocab), `cbmi:` (instance IRIs), `cbmxr:` (symbol xrefs, plugin-owned), `cbml4:` (LLM enrichment, plugin-owned), plus SPDX 3.0.1 mapping IRIs.

Core shapes: `FileShape`, `TestsSubjectShape`, `RepositoryShape`, `CommitShape`, `PackageReleaseShape` (`rdflib_emitter.py:162`). Every property shape gets a deterministic `sha1`-derived IRI — **no blank nodes anywhere**, making Turtle output byte-stable across runs (an enforced invariant, `_has_bnodes()` check triggers rdflib fallback rather than silently emitting non-deterministic bnodes).

#### DM-002 — `FileNode` / `InventoryGraph` (Pydantic mirror)

**Source**: `codebase_mapper/emission/domain/inventory_schema.py:96,121`.

| Field | Type | Constraints | Notes |
|---|---|---|---|
| path | str | — | |
| git_blob_sha | str | — | |
| content_sha256 | str | regex `^[0-9a-f]{64}$` | |
| size_bytes | int | `ge=0` | |
| type | `FileType` enum | 16 controlled terms | |
| phases | list[`Phase`] | `min_length=1` | 7 controlled terms |
| language, ast_summary, timestamps | optional | | |
| edges (imports, deps, tests, …) | stored by natural key (path/pkg-name/`name@version`) | not IRI | |

`_referential_integrity()` (line 134) validates whole-graph consistency: duplicate-path detection, dangling edge targets across every edge kind, and a `TestsSubjectShape` invariant. `PREDICATE_FIELDS` (line 179) is the single predicate-IRI↔field-name registry, held in lockstep with the live SHACL shapes by `tests/test_inventory_schema.py` — a drift guard, not a convention people are trusted to maintain by hand.

#### DM-003 — L2 Chunk (`cbml2:Chunk`)

**Source**: `plugins/chunks_embeddings/graph_writer.py`, `signatures.py:8-24`.

Fields: `kind`, `symbol`, `parentSymbol`, `qualifiedSymbol`, `beginLine`/`endLine`, `nif:beginIndex`/`endIndex`, `contentSha256`, `embeddingRow`+`embeddingArtifact`, optional signature contract (`signature`, `params`, `returns`, `bases`, `type_params`, `visibility`, `is_async`, `decorators` — omitted, never null-padded, when not mechanically derivable). `cbml2:memberOf` links method/field chunks to their enclosing class via tightest-enclosing-byte-span resolution.

#### DM-004 — L3 Concept (`skos:Concept` + `cbml3:*`)

**Source**: `plugins/concept_graph/{concepts,graph_writer}.py`.

Every concept carries co-occurrence edges; vocab-matched concepts additionally carry `cbml3:conceptKind` (`domain-primitive`/`structural-primitive`/`relational-primitive`) and `cbml3:broaderCollection` (a `skos:Collection` per populated `broader` tail — collections appear only when non-empty).

#### DM-005 — Symbol Xref Edge (`cbmxr:Edge`)

**Source**: `codebase_mapper/emission/models.py:20-38`.

Reified edge (not a direct predicate): `src`, `dst` (both `cbml2:Chunk` IRIs), `kind` ∈ `{calls, subclassOf, overrides, references*}` (*declared, never emitted), `resolution` ∈ `{exact, heuristic, ambiguous}`, `resolver`. Edge IRI = `sha1(src|dst|kind|resolver)[:16]` — deterministic across runs.

#### DM-006 — L4 Enrichment Triples (`cbml4:*`)

**Source**: `plugins/llm_enrich/graph_writer.py:60-77,130-149`.

Uniform 4-predicate group per kind: `cbml4:<kind>`, `<kind>Model`, `<kind>PromptSha`, `<kind>GeneratedAt`. All `maxCount 1`, no `minCount` — a bundle with zero or partial enrichment still SHACL-validates.

### 6.3 Migrations

No database migration files — the RDF/Pydantic schema pair evolves in lockstep, guarded by `tests/test_inventory_schema.py` rather than versioned migration scripts. `docs/schema/backlog.schema.json` and `docs/reporting/report-spec.schema.json` are the two other JSON Schema artifacts with drift guards of their own.

### 6.4 Seed Data and Fixtures

| File | Purpose | Target Model(s) |
|---|---|---|
| `tests/fixtures/golden_repo/` + `golden_repo_expected.json` | Hand-derived, never-regenerate-from-pipeline projection used to catch any drift in classify/AST/import/chunk output | DM-001–DM-003 |
| `tests/fixtures/{rust,cpp,dart,java,objc,cobol,css,html,json,sql,shell,yaml,php}/` | Per-language mini source trees | DM-002 |
| `tests/fixtures/llm_cache/` | Pre-seeded, committed L4 cache proving warm-cache determinism without a live Ollama server | DM-006 |

---

## 7. API and Interface Contracts

### 7.1 REST Endpoints (`frontend/backend/app.py`)

All 13 endpoints the root README documents exist 1:1 in code, no extras, nothing missing.

| Method | Route | Handler | Auth | Key Params | Response Model |
|---|---|---|---|---|---|
| GET | `/api/bundles` | `bundles()` | required | — | `BundleListResp` |
| GET | `/api/summary` | `summary()` | required | `bundle?` | `SummaryResp` |
| GET | `/api/file-graph` | `file_graph()` | required | `limit≤5000`, `bundle?` | `GraphResp` |
| GET | `/api/symbol-graph` | `symbol_graph()` | required | `limit≤5000`, `kind`, `bundle?` | `GraphResp` |
| GET | `/api/concept-graph` | `concept_graph()` | required | `limit≤2000`, `min_edge`, `bundle?` | `GraphResp` |
| GET | `/api/chunks` | `chunks()` | required | `q?`, `limit≤500`, `offset`, `bundle?` | `ChunkListResp` |
| POST | `/api/chunks/search` | `chunk_search()` | required | body `{q, k}`, `bundle?` | `ChunkListResp` |
| GET | `/api/chunk-blob/{sha}` | `chunk_blob()` | required | path `sha`, `bundle?` | *(no response_model — bare dict)* |
| GET | `/api/concept/{name}` | `concept_detail()` | required | `cooccur_k`,`chunk_k`,`file_k`, `bundle?` | `ConceptDetailResp` |
| GET | `/api/file/{path:path}` | `file_detail()` | required | `bundle?` | `FileDetailResp` |
| GET | `/api/impact/{path:path}` | `impact()` | required | `depth≤5`, `limit≤1000`, `bundle?` | `ImpactResp` |
| GET | `/api/chunk/{idx}` | `chunk_detail()` | required | path `idx`, `bundle?` | `ChunkDetailResp` |
| GET | `/api/healthz` | `healthz()` | **exempt** | — | *(no response_model — bare dict)* |

Field-level parity between these Pydantic models and `frontend/ui/src/api.ts` is enforced by `tests/verify_api_field_parity.py` — with one confirmed blind spot (§12, GAP-006).

### 7.2 MCP Tools (`frontend/mcp_server/`)

18 tools, matched exactly against this session's own `mcp__cbm__*` surface (no additions/removals/renames found):

`orient_bundle`, `bundle_summary`, `repository_summary`, `list_bundles`, `select_bundle`, `list_files`, `file_detail`, `file_impact`, `imports_of`, `imported_by`, `chunk_detail`, `chunk_blob`, `list_chunks`, `semantic_neighbors`, `concept_detail`, `concept_neighborhood`, `items_by_attribute`, `sparql`.

Most resolve against an in-process `Bundle` object (dict/BFS lookups); `sparql` is the sole tool that queries the RDF store directly (pyoxigraph persistent store, rdflib fallback). Schema-validated on both input and output via a `@tool` decorator (`handlers.py:89-108`). Full per-tool detail in §4.5 (FR-mcp-001–006).

**Resources** (`cbm://` scheme): `cbm://bundles`, per-bundle `manifest`/`summary`/`shapes.shacl.ttl`/`ontology-mapping.ttl`, plus templated `file`/`chunk`/`concept` — all thin wrappers over the identical tool handlers (guaranteed shape parity).

**Prompts**: `orient` (no args), `explore_concept` (`concept` required), `trace_dependency` (`path` required) — each a hardcoded multi-step tool-calling script, freshness-tested against real tool names.

### 7.3 CLI Commands

| Command | Purpose | Source |
|---|---|---|
| `codebase-mapper` (console script) | L1 map / reconstruct / regenerate / verify-roundtrip / self-test | `codebase_mapper/cli.py:main` |
| `python scripts/run_l2.py` | L1+L2 (chunks+embeddings) | |
| `python scripts/run_l3.py` | L1–L3 (+concept graph), `--llm-enrich` shorthand | |
| `python scripts/run_l4.py` | Full L1–L4, all L4 knobs surfaced | |
| `python scripts/run_xrefs.py` | +symbol xrefs, `--concepts` opt-in | |
| `python scripts/cbm.py <cmd>` | Unified reporting dispatcher (10 subcommands, §4.6/FR-tool-001) | `scripts/cbm.py` |
| `python -m decomposer <bundle_dir> [--yaml/--report/--symbols/--stdout]` | Structural decomposition | `decomposer/cli.py:21-74` |
| `python -m recomposer <decomposition.yaml> [--plan/--yaml/--stdout]` | Rebuild-plan generation | `recomposer/cli.py:23-71` |
| `python -m frontend.mcp_server` | Launch MCP server (stdio) | `frontend/mcp_server/server.py` |

---

## 8. Configuration and Environment

### 8.1 Configuration Files

| File | Role |
|---|---|
| `pyproject.toml` | Package metadata, dependency groups (base/frontend/dev/site/dossier), pytest/mypy/import-linter config |
| `.env` / `.env.example` | Environment variable template — `load_env()` (`shared_kernel/settings.py:58`) autoloads on CLI/backend/MCP startup, never overriding a real env var; `.env.example` is the single documented source (enforced by `tests/verify_drift_p1.py`) |
| `Makefile` | 40+ task-runner targets (§11) |
| `frontend/backend/pytest.ini`, `.coveragerc` | Backend test config, `--cov-fail-under=90` |
| `frontend/mcp_server/pytest.ini`, `.coveragerc` | MCP server test config, `--cov-fail-under=90` |
| `frontend/ui/vite.config.ts` | Vitest config, 90% coverage threshold on all 4 metrics (declared but currently unmet — see GAP-011) |

### 8.2 Environment Variables

| Variable | Purpose | Default | Consumed By |
|---|---|---|---|
| `CBM_OUTPUT_DIR` | Bundle directory the backend/tests load | `_tmp/usl-ng-core-map` (test default) | `frontend/backend/`, `tests/conftest.py` |
| `CBM_ALLOW_ANONYMOUS` | Opt in to unauthenticated backend access (must be exact string `"1"`) | unset | `app.py:323-351` |
| `CBM_API_TOKEN` | Bearer token; always wins over anonymous opt-in when set | unset | `app.py` |
| `CBM_CORS_ORIGINS` | Backend CORS allow-list | 4 loopback dev origins | `app.py:313-379` |
| `CBM_MCP_TOKEN` | Gates whether the backend mounts the MCP server at `/mcp/` | unset (mount skipped) | `app.py:381-389` |
| `CBM_ENABLE_SPARQL` | Enables the MCP `sparql` tool | disabled | `sparql.py:51-52` |
| `CBM_WATCH_INTERVAL` | Manifest-watcher poll interval (stdio transport) | 30s | `frontend/mcp_server/subscriptions.py` |
| `OLLAMA_HOST` | Ollama server address for L4 | `http://localhost:11434` | `plugins/llm_enrich/client.py:36-41` |
| `CBM_LLM_CACHE` | Override L4 cache directory | `~/.cache/cbm-llm/` | `plugins/llm_enrich/cache.py:63-66` |
| `CBM_CONCEPT_TOP_N` | L4 concept-description corpus-top-N selection size | 200 | `plugins/llm_enrich/aggregator.py` |
| `CBM_EXTRACT_WORKERS` | AST extraction thread count | CPU count | `codebase_mapper/inspection/pipeline.py` |
| `CBM_ENRICH_WORKERS` | Record-enricher thread count | 4 | `codebase_mapper/inspection/pipeline.py` |
| `CBM_SHACL_ENGINE` | `fast` (default) vs `pyshacl` validation engine | fast | `emission/application/emit_bundle.py` |
| `CBM_SKIP_SHACL` | Skip SHACL self-check (disclosed in manifest, never silent) | off | `emit_bundle.py` |
| `CBM_EMIT_JSONLD` | Toggle JSON-LD emission | on | `emit_bundle.py` |
| `CBM_REPORT_BIN` | Override path to the compiled `tools/cbm-report` Rust binary | `tools/cbm-report/target/{release,debug}/cbm-report` | `scripts/cbm_report_rs.py:56-66` |

`.env.example` is the enforced single source of truth for this list (`tests/verify_drift_p1.py`'s `check_env_inventory`); documenting an env var elsewhere without updating it there is flagged by the drift guard.

---

## 9. Testing and Quality Assurance

### 9.1 Test Inventory

| Category | Framework | Count | Notes |
|---|---|---|---|
| Drift/contract guards | standalone scripts (custom `check()`/PASS-FAIL protocol, not pytest) | 66 (`tests/verify_*.py`) | grouped: 12 drift, 13 core, 4 vocab, 16 per-language, 7 Rust, 8 L4-offline, 4 L4-online, 2 standalone |
| Unit/integration | pytest | 87 (`tests/test_*.py` 70 + `decomposer/` 12 + `recomposer/` 5) | no shared `conftest.py` anywhere under `tests/` |
| Backend | pytest + pytest-cov | 103 tests / 6 files | 90.50% measured coverage (verified by running the suite) |
| MCP server | pytest + pytest-cov | 203 tests / 15 files (README claims 357 parametrized cases) | `--cov-fail-under=90`, real CI-adjacent gate |
| React UI | Vitest + Testing Library | 66 tests / 4 files | 98.99%/83.81%/89.47%/98.99% (stmt/branch/func/line) — measured, does **not** meet its own declared 90% gate on branches/functions |
| Fixtures | hand-authored | `golden_repo` (9 files + hand-derived expected JSON), 13 per-language fixture trees, `llm_cache` (pre-seeded L4 cache) | |

### 9.2 Test Configuration

`make check` (`Makefile:182-186`) = `verify_language_goal.py` + the 12 `DRIFT_VERIFIERS` **only** — narrower than the full offline `make test` (which additionally runs core/vocab/lang/rust/llm-offline/units/backend/docs/report-rs/cartogram targets). This distinction matters: CI's `drift` job runs `make test-drift` (not `make check`), so `make check`'s exact sequence is reproduced nowhere in CI.

### 9.3 Linting and Formatting

| Tool | Config | Scope |
|---|---|---|
| import-linter | `pyproject.toml [tool.importlinter]` | Enforces `shared_kernel/vocabulary.py` framework-freedom, etc. |
| mypy | `pyproject.toml [tool.mypy]` | `codebase_mapper`, `plugins`, `frontend/backend/{app.py,serving}`, `frontend/mcp_server` |

No ESLint/Prettier config found for `frontend/ui/` (TypeScript strictness comes from `tsc --noEmit` in the `build` script only).

### 9.4 Static Analysis / Coverage Assessment

CLAUDE.md mandates 80% (libraries) / 60% (CLIs) coverage. Actual measured state, per-component:

| Component | Gate declared? | Measured (this pass) | Enforced in CI? |
|---|---|---|---|
| `codebase_mapper/` core library | No `[tool.coverage]` anywhere | Not measured — quality bar here is behavioral (the drift/verify suite), not %-line | Partially (see GAP-009) |
| `frontend/backend/` | Yes, 90% | 90.50% (verified by running) | **No** — both CI and `make test-backend` pass `--no-cov` (GAP-005) |
| `frontend/mcp_server/` | Yes, 90% | Not independently re-measured this pass; described by its own suite as gated | Yes (per its own `pytest.ini`) |
| `frontend/ui/` | Yes, 90% all 4 metrics | 83.81% branch / 89.47% func (fails) | **No** — no CI job runs `npm test`/vitest at all (GAP-011) |

---

## 10. Dependencies and Supply Chain

### 10.1 Production Dependencies (`pyproject.toml` base)

| Package | Constraint | Purpose |
|---|---|---|
| rdflib | `>=7.0,<8.0` | Core RDF graph API |
| pyshacl | `>=0.27` | SHACL validation (fallback engine) |
| tree-sitter + 12 grammars | unpinned | Parsing |
| numpy | `>=2.0,<3.0` | Embedding math |
| sentence-transformers | `>=3.0,<6.0` | Optional sbert backend |
| pydantic | `>=2.7,<3.0` | Canonical SHACL shape model |
| pyoxigraph | unpinned | Rust-backed RDF store, large-bundle performance |
| pyyaml | unpinned | YAML I/O |
| tomli | `; python_version < '3.11'` | TOML backport |

### 10.2 Frontend Extra (`pyproject.toml [frontend]`)

`fastapi>=0.115,<1.0`, `uvicorn[standard]>=0.30,<1.0`, `pydantic>=2.7,<3.0`, `mcp>=1.27,<2.0`, `PyJWT>=2.8,<3.0`, `httpx>=0.28,<1.0`, `pyoxigraph>=0.5,<1.0`.

### 10.3 Dev Extra

`import-linter`, `mypy`, `types-PyYAML`, `jsonschema`, `pytest`, `pytest-cov`, `weasyprint`, plus a self-chain to `codebase-mapper[frontend]`.

### 10.4 UI Dependencies (`frontend/ui/package.json`)

`react`, `react-dom` (18.3), `react-router-dom` (6.28), `cytoscape` + `react-cytoscapejs` (3.30/2.0); dev: `vite`, `vitest`, `@vitejs/plugin-react`, `@testing-library/*`, `typescript` (5.6), `jsdom`, `@vitest/coverage-v8`.

### 10.5 Lock File Status

`uv.lock` (root, 830KB) is the resolved-version source of truth for `uv run`/`uv sync`. **Requirements-mirror drift guard** (`tests/verify_requirements_mirror.py`, drift-risk H8) keeps `frontend/backend/requirements{,-sbert}.txt` and `frontend/mcp_server/requirements.txt` pin-identical to `pyproject.toml`'s resolved extras — confirmed consistent by direct comparison. **Blind spot**: the root `Dockerfile`'s inline pip-install list is unpinned and not covered by this guard (GAP-002).

---

## 11. Build, Deployment, and Operations

### 11.1 Build Scripts / Task Runner (`Makefile`, 333 lines, 40+ targets)

Notable targets: `install` (`pip install -e ".[dev]"`), `lint` (import-linter), `check` (§9.2), `test` (full offline umbrella), `test-units`/`test-backend`/`test-report-rs`/`test-cartogram`, `run-l2/l3/l4/xrefs` (passthrough to pipeline scripts), `build-report-rs` (`cargo build --release`), `docker-build`/`docker-build-sbert`/`docker-run`, `frontend-up/down/logs` (docker-compose), `dist-zip` (clean source archive), `clean`/`clean-tmp` (the latter explicitly documented as destructive).

### 11.2 CI/CD Pipeline

Two workflows only — **no workflow runs `make check` verbatim, and none builds/tests the Rust crate or the Node cartogram tool**:

| Workflow | Trigger | Jobs |
|---|---|---|
| `.github/workflows/lint.yml` | push/PR, any branch | `import-linter`, `mypy`, `verify` (individual scripts: `verify_golden_repo.py`, `verify_dimension_shapes.py`, `test_perimeter.py --no-cov`), `drift` (`make test-drift` + a live fixture bundle + `make test-backend`) |
| `.github/workflows/backlog-governance.yml` | push (main)/PR, path-filtered to `docs/BACKLOG.md`/`docs/backlog.yml`/schema/checker script | Node 22, `check-backlog-governance.mjs` |

**Coverage gap**: `LANG_VERIFIERS` (16), `RUST_VERIFIERS` (7), `VOCAB_VERIFIERS` (4), most of `CORE_VERIFIERS` (13, only 2 run individually), `LLM_OFFLINE_VERIFIERS` (8), and the full `test-units` pytest tree (which includes decomposer/recomposer unit tests) have **no CI job invoking them** — green CI does not mean these guards ran.

### 11.3 Container Configuration

Root `Dockerfile`: single stage, `python:3.11-slim`, `WITH_SBERT` build arg, copies only `pyproject.toml`/`README.md`/`codebase_mapper/`/`plugins/`/`scripts/`/`docker/cbm-analyze` — **`decomposer/`/`recomposer/` and `frontend/backend/serving/` are never copied into any image that needs them** (see GAP-002, GAP-003). `frontend/docker-compose.yml` (separate file, uses `frontend/backend/Dockerfile`) wires backend+nginx-frontend services.

### 11.4 Deployment Targets

No cloud-specific deployment manifests (no Helm/Terraform/Vercel/Netlify config) found — deployment is Docker-only, operator-driven.

---

## 12. Identified Gaps, Risks, and Recommendations

Ranked roughly by concreteness/severity; every entry below was directly observed or reproduced by one of the seven research agents, not inferred from documentation alone.

### 12.1 Critical — breaks a stated guarantee

| ID | Gap | Evidence | Recommendation |
|---|---|---|---|
| GAP-001 | **`frontend/backend/Dockerfile` cannot produce a working image.** `COPY frontend/backend/app.py` alone (never `serving/`), and root `pyproject.toml`'s package-discovery doesn't include `frontend.backend.serving` either. Reproduced: `import app` in the container's filesystem layout raises `ModuleNotFoundError: No module named 'serving'`, unhandled, crashing the process — no CI job builds this image. | `frontend/backend/Dockerfile:32`, reproduced by agent | Add `COPY frontend/backend/serving/` (or restructure as an installable package) and add a CI job that builds and smoke-tests the image. Directly contradicts CLAUDE.md's "every commit must be deployable." |
| GAP-002 | **Root `Dockerfile` omits `decomposer/`/`recomposer/` from the build context**, though `pyproject.toml` declares them as installable packages — the built CLI image cannot run either tool. | `Dockerfile:21-25` vs `pyproject.toml:100-103` | Add both directories to the `COPY` list or document the image as core-pipeline-only. |
| GAP-003 | **Root `Dockerfile`'s dependency list is unpinned and diverges from `pyproject.toml`** — omits `tree-sitter-cfml/cpp/java/objc`, `pydantic`, `pyoxigraph`, `tomli`; since the package installs with `--no-deps`, C++/Java/ObjC/CFML support and the pyoxigraph fast-emit path are silently degraded inside the built image, and `pydantic` (required by the SHACL shape model) is absent entirely. Not covered by `verify_requirements_mirror.py`. | `Dockerfile:27-41` | Either generate the Dockerfile's dependency list from `pyproject.toml` or extend the H8 mirror guard to cover it. |
| GAP-004 | **Backend CI "live bundle validation" silently never runs.** The `drift` job's fixture bundle is written to `_tmp/ci-bundle`, but nothing sets `CBM_OUTPUT_DIR` to that path — the test suite's hardcoded default (`_tmp/usl-ng-core-map`) doesn't match, so all 30 `test_endpoints.py` tests (the ones that integration-test all 13 REST endpoints) skip silently. CI reports green regardless. Reproduced by the agent running the exact CI sequence locally. | `.github/workflows/lint.yml:110-117`, `tests/conftest.py:16,29` | Set `CBM_OUTPUT_DIR=_tmp/ci-bundle` in the `drift` job before `make test-backend`. |

### 12.2 High — undermines a testing/quality claim

| ID | Gap | Evidence | Recommendation |
|---|---|---|---|
| GAP-005 | Backend's 90% coverage gate is declared (`pytest.ini`) but **never automated** — both real call sites (`lint.yml:82`, `Makefile:203-205`) pass `--no-cov`. Coverage happens to be 90.50% today (verified) but nothing re-checks it on future changes. | `frontend/backend/pytest.ini`, CI/Makefile invocations | Drop `--no-cov` from at least one CI-run invocation, or add a dedicated coverage job. |
| GAP-006 | `serving/application/files.py` puts `external_imports` into the `/api/file/{path}` response, but `FileDetailResp` never declares that field — it survives only via Pydantic `extra="allow"`, invisible to `verify_api_field_parity.py`'s drift check. | `serving/application/files.py:47` vs `app.py:261-274` | Declare the field explicitly (or add it to the parity guard's allowlist) so a future accidental removal is caught. |
| GAP-007 | The "obvious" way to run the backend test suite (`cd frontend/backend && pytest`) **fails at collection** (`ModuleNotFoundError: No module named 'frontend'`) — it only works via `python -m pytest` from repo root, and this is undocumented in `frontend/backend/README.md`. | `tests/test_projection_parity.py:18`, reproduced by agent | Document the required invocation, or fix `pytest.ini`'s `rootdir`/`sys.path` handling so the naive invocation works. |
| GAP-008 | Backend malformed-bundle handling: a syntactically broken sidecar JSON (e.g. `concepts.json`) produces a **bare, unlogged 500** — no `try/except` around any `json.loads`/rdflib `.parse` in `load_bundle()` and no registered exception handler. Reproduced by building a bundle with broken `concepts.json` and hitting `/api/summary`. | `serving/application/bundle_data.py:431-526` | Wrap sidecar parsing in a handler that returns a descriptive 4xx/5xx with logging. |
| GAP-009 | No line-coverage threshold exists anywhere for `codebase_mapper/` itself (the core library) — CLAUDE.md's mandated 80%/60% figures are aspirational there; the actual quality bar is the behavioral drift-guard suite (a different, not strictly equivalent, property). | `pyproject.toml:182-184` (no `[tool.coverage]`) | Either adopt a measured threshold for the core library or explicitly document the behavioral-guard substitute as the accepted policy (currently implicit). |
| GAP-010 | CI never runs `make check`, nor the bulk of the verifier suite: `LANG_VERIFIERS` (16), `RUST_VERIFIERS` (7), `VOCAB_VERIFIERS` (4), 11 of 13 `CORE_VERIFIERS`, `LLM_OFFLINE_VERIFIERS` (8), and the full `test-units` pytest tree have no CI job. | `.github/workflows/lint.yml` (all 4 jobs enumerated) | Add a job (or matrix) that runs the full `make test` target, or at minimum the currently-unwired verifier groups. |
| GAP-011 | React UI declares a 90%-on-all-4-metrics coverage gate (`vite.config.ts`) that is **currently not met** (83.81% branch, 89.47% function, verified by running) and **has no CI job at all** — `lint.yml` never installs Node deps or runs `vitest` for `frontend/ui/`. | `frontend/ui/vite.config.ts:30-35`, `.github/workflows/lint.yml` | Add a UI test/coverage CI job; either fix the branch/function gaps or relax the declared threshold to match reality. |
| GAP-012 | Rust crate (`tools/cbm-report`) and Node cartogram tool (`tools/cbm-cartogram`) have Makefile targets with disclosed-skip fallbacks, but **no CI job ever has `cargo` or a cartogram-relevant `node` step** — entirely untested/unbuilt in CI. | `Makefile:215-249`, `.github/workflows/` | Add build/test jobs for both, gated appropriately if toolchain install cost is a concern. |

### 12.3 Moderate — documentation/schema drift

| ID | Gap | Evidence | Recommendation |
|---|---|---|---|
| GAP-013 | React UI's "scaffold, in progress" description in the root README is stale — it is a functionally complete, densely tested (66 tests, ~1:1 test:source ratio) SPA covering every backend endpoint. | `README.md:60` vs. measured UI state | Update the README line. |
| GAP-014 | `plugins/symbol_xrefs/__init__.py` and `plugins/llm_enrich/__init__.py` docstrings claim "Phase 1 skeleton, `_RESOLVERS` empty" / "emit zero triples" — both plugins are fully implemented (10 resolvers registered; 3 live enrichment kinds with real Ollama calls). | `plugins/symbol_xrefs/__init__.py:12`, `plugins/llm_enrich/__init__.py:18-22` | Update the module docstrings to reflect current implementation status. |
| GAP-015 | `XREF_KINDS` declares a `"references"` edge kind in the vocabulary/SHACL, but **no resolver across 10 languages ever emits it** — schema ahead of implementation. | `codebase_mapper/emission/models.py` / `constants.py:43`, confirmed via grep | Either implement a resolver path that emits `references`, or remove it from the controlled vocabulary until one exists. |
| GAP-016 | `decomposer/README.md`'s delivery-status table marks the recomposer "🔵 designed" (not yet built), but `recomposer/` is a fully implemented package (889-line scheduler, working CLI, its own README describing shipped behavior) — the two READMEs disagree. | `decomposer/README.md:29-34` vs `recomposer/` contents | Update the status table. |
| GAP-017 | 4 of ~15 top-level `docs/*.md` files lack the CLAUDE.md-mandated YAML disclaimer frontmatter (`docs/analyze.md`, `docs/BACKLOG.md`, `docs/codebase-graph-operations-framework.md`, `docs/typed-graph-diagnostics-framework.md`), and no existing verifier scans arbitrary `docs/*.md` for this — `verify_doc_hygiene.py` only checks files literally named `README.md` for a different, narrower rule. | Confirmed by reading each file's first lines | Add frontmatter to the 4 files; extend (or add) a verifier that scans all `docs/*.md`, not just `README.md`. |
| GAP-018 | Regenerate (AST-only, blob-free materialization) covers only Python/Rust/TypeScript/JavaScript — every other first-class language (including the newly-promoted Shell) has no regenerator and always reports `ast_unsupported`. Reconstruct (blob-based) still works for all languages; this is a narrower, disclosed fidelity ceiling, not a silent one. | `codebase_mapper/emission/application/regenerate.py:28` | Track as a known scope boundary (already partly documented in `docs/regenerate.md`); prioritize the next regenerator by usage if this path matters to consumers. |
| GAP-019 | `tools/cbm-cartogram`'s inventory normalizer does a full in-memory `JSON.parse`, unlike the streaming Rust `cbm-report` reader for the same file type, and this scale limitation is undocumented in its own README. | `tools/cbm-cartogram/tools/normalize-inventory.mjs:62` | Document the current memory ceiling, or port to a streaming JSON parser if kernel-scale cartograms are a real use case. |
| GAP-020 | Backend loads L4 enrichment sidecar data (`rust_items`, `enrichment_file_summary/concept_description/schema_purpose`) into memory on every bundle load, but **no REST endpoint reads any of it** — either staged-but-unwired or dead code. | `serving/application/bundle_data.py:52-56,491-525`, confirmed by exhaustive grep | Wire it into a response (the UI already renders these fields via the MCP-server path's equivalents) or remove the unused load. |

### 12.4 Low — no license file, cosmetic

| ID | Gap | Evidence | Recommendation |
|---|---|---|---|
| GAP-021 | No `LICENSE` file anywhere in the repository, and no license field in `pyproject.toml`. | root directory listing | Add a license file if the project is intended for external use/distribution. |
| GAP-022 | Backend's two coverage exceptions (`chunk_blob`, `healthz`) have no `response_model=`, unlike every other handler — inconsistent but harmless (bare-dict responses aren't validated/filtered by FastAPI). | `app.py:449-451,485-487` | Add explicit response models for OpenAPI-schema completeness. |
| GAP-023 | `/api/chunks/search`'s semantic branch hardcodes the sbert model name rather than reading it from `embeddings_meta` — if a bundle were ever built with a different sentence-transformer model, query embeddings would silently use the wrong vector space (no error, just meaningless rankings). | `frontend/backend/serving/application/chunks.py:67` vs `plugins/chunks_embeddings/backends.py:37` | Read the model name from `embeddings_meta` and only fall back to the hardcoded default when absent. |

---

**End of report.** Source material: seven parallel forensic passes over `codebase_mapper/`, `plugins/`, `frontend/backend/`, `frontend/ui/`, `frontend/mcp_server/`, `tests/`+`docs/`, and `scripts/`+`tools/`+`decomposer/`+`recomposer/`+build/CI/dependencies, synthesized into this single document. Several findings were verified by actually running code (test suites, drift-guard scripts, a reconstructed Docker import) rather than by reading alone — those are marked inline as "verified" or "reproduced."
