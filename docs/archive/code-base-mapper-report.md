---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.7 (1M context) via Claude Code, using cbm MCP server v0.5.0"
  date: "2026-05-14"
---

# Inspection Report — `code-base-mapper`

> **Archive status:** Generated snapshot report for an older bundle. Paths and
> counts below are historical and are not active documentation for the current
> checkout.

> Source of truth: cbm MCP bundle `code-base-mapper`, path
> `_tmp/code-base-mapper`, commit `715de92f0a2bbcd1966e2ffe64db21816141b6e1`,
> generated 2026-05-14T04:56:02Z by tool_version `0.5.0`.
> Every claim below is derived from cbm tool output, not from reading the
> source files directly.

---

## 1. Executive Summary

`code-base-mapper` is a **Python-first repository-mapping toolkit** that
ingests an arbitrary source tree and emits a **layered RDF knowledge bundle**
(L1 host facts → L2 chunked embeddings → L3 SKOS concept graph), then
exposes that bundle through both a **REST/FastAPI backend** and an
**MCP server** (stdio + HTTP transports) with a **React/Vite/Cytoscape UI**.

Scale and shape (from cbm `bundle_summary`, [_tmp/code-base-mapper/](_tmp/code-base-mapper/)):

| Metric | Value |
|---|---|
| Total files | **181** |
| Chunks (L2) | **968** |
| Concepts (L3) | **1 427** |
| Internal import edges | 267 |
| External import edges | 85 |
| Declares-dependency edges | 39 |
| Pins-dependency edges | 368 |
| Tests edges | 8 |
| Embeddings backend | `sentence-transformers/all-MiniLM-L6-v2` (384-dim) |
| SHACL conformance | **`true`** |
| Tool version | 0.5.0 |

Language histogram: **python 103** · typescript 21 · protobuf 5 · html 1 ·
css 1 · `(none)` 50 (configs, docs, lockfiles, manifests).

Type histogram: source_code 89 · test_code 42 · data 16 · configuration 12 ·
documentation 11 · dependency_manifest 4 · container 4 · lockfile 2 ·
environment 1.

---

## 2. Architecture (three vertical slabs)

cbm `orient_bundle` advertises three RDF layers; the codebase mirrors that
shape physically.

### 2.1 Layer 1 — Host facts (`codebase_mapper/`)
Files, languages, types, imports, dependency manifests, AST summaries.
Namespace `cbm:`, key predicates `cbm:path`, `cbm:imports`, `cbm:hasPhase`,
`cbm:tests`.

### 2.2 Layer 2 — Chunks + embeddings (`plugins/chunks_embeddings/`)
Per-function/class/file chunks with NIF spans and embedding row pointers.
Namespace `cbml2:`, key predicates `cbml2:inFile`, `cbml2:beginIndex`,
`cbml2:endIndex`, `cbml2:embeddingRow`.

### 2.3 Layer 3 — Concept graph (`plugins/concept_graph/`)
SKOS concepts from identifier splitting, with co-occurrence as
`skos:related`. Namespace `cbml3:`, key predicates `cbml3:lexicalizes`,
`cbml3:composedOf`, `skos:related`, `skos:prefLabel`.

The `codebase_mapper/vocab/` subtree (with
[software_primitives.yaml](_tmp/code-base-mapper/codebase_mapper/vocab/software_primitives.yaml))
adds a **controlled vocabulary** layered on top of L3 (only one concept,
`import_statement`, is currently kinded as `structural-primitive` /
broader `code_structure` in this commit — see §5).

A fourth vertical slab, `plugins/symbol_xrefs/`, produces
**cross-language symbol resolution** (Python + TS/JS resolvers, aggregator,
graph writer) — visible via repeated `xref` / `resolver` concepts.

---

## 3. The Spine — top files by import degree

From `repository_summary` (`central_files_limit=15`):

| Path | deg | out | in | size B | role |
|---|---:|---:|---:|---:|---|
| [codebase_mapper/__init__.py](_tmp/code-base-mapper/codebase_mapper/__init__.py) | 30 | 26 | 4 | 6 405 | Package façade — imports every language adapter + every pipeline phase |
| [codebase_mapper/models.py](_tmp/code-base-mapper/codebase_mapper/models.py) | 25 | 0 | 25 | 2 525 | Pure dataclasses: `FileRecord`, `ImportEdge`, `ImportExternalEdge`, `DeclaresDependencyEdge`, `PinsDependencyEdge`, `TestsEdge`, `SymbolXrefEdge`, `UnresolvedSymbolRef` |
| [codebase_mapper/pipeline.py](_tmp/code-base-mapper/codebase_mapper/pipeline.py) | 23 | 15 | 8 | 9 532 | Orchestrator — single function `map_codebase` (L30-L217) |
| [codebase_mapper/extensions.py](_tmp/code-base-mapper/codebase_mapper/extensions.py) | 20 | 1 | 19 | 11 600 | Plugin/extension contract — 19 inbound callers |
| [codebase_mapper/constants.py](_tmp/code-base-mapper/codebase_mapper/constants.py) | 16 | 0 | 16 | 3 887 | Magic numbers, type tags, MIME/ext maps |
| [frontend/mcp_server/server.py](_tmp/code-base-mapper/frontend/mcp_server/server.py) | 15 | 7 | 8 | 10 469 | MCP server: `build_server`, `run_stdio`, `prewarm_default_bundle`, `manifest_changed` |
| [frontend/ui/src/App.tsx](_tmp/code-base-mapper/frontend/ui/src/App.tsx) | 14 | 10 | 4 | 5 334 | React app shell |
| [frontend/ui/src/api.ts](_tmp/code-base-mapper/frontend/ui/src/api.ts) | 14 | 0 | 14 | 6 714 | Single HTTP client consumed by 14 UI modules |
| [codebase_mapper/_builtins.py](_tmp/code-base-mapper/codebase_mapper/_builtins.py) | 13 | 12 | 1 | 11 494 | Builtin-plugin registry |
| [codebase_mapper/rdf_emit.py](_tmp/code-base-mapper/codebase_mapper/rdf_emit.py) | 11 | 2 | 9 | 10 734 | Turtle/JSON-LD emitter |
| [codebase_mapper/reconstruct.py](_tmp/code-base-mapper/codebase_mapper/reconstruct.py) | 9 | 5 | 4 | 5 060 | Reverse direction — bundle → source tree |
| [codebase_mapper/ts_setup.py](_tmp/code-base-mapper/codebase_mapper/ts_setup.py) | 9 | 0 | 9 | 5 129 | Tree-sitter loader/cache |
| [frontend/mcp_server/__init__.py](_tmp/code-base-mapper/frontend/mcp_server/__init__.py) | 9 | 0 | 9 | 2 685 | MCP package façade |
| [frontend/mcp_server/handlers.py](_tmp/code-base-mapper/frontend/mcp_server/handlers.py) | 9 | 3 | 6 | 29 289 | **The MCP-tool catalogue** — see §4 |
| [frontend/ui/src/bundle-context.ts](_tmp/code-base-mapper/frontend/ui/src/bundle-context.ts) | 9 | 0 | 9 | 430 | UI React context for the active bundle |

Observations:
- **`models.py` is a pure data hub** (25 inbound, 0 outbound) — a healthy
  acyclic dependency root.
- **`__init__.py` is a fan-out cathedral** (26 outbound). Every language
  adapter and pipeline phase is wired here; a change to it touches the
  entire mapper.
- **`handlers.py` is the largest single source file** (29 289 B)
  and holds **22 MCP tool handlers** (`_orient_bundle`, `_bundle_summary`,
  `_repository_summary`, `_list_bundles`, `_select_bundle`, `_list_files`,
  `_file_detail`, `_file_impact`, `_imports_of`, `_imported_by`,
  `_chunk_detail`, `_chunk_blob`, `_list_chunks`, `_semantic_neighbors`,
  `_concept_detail`, `_sparql`, `_concept_neighborhood`, plus internal
  helpers). This is the **public surface of the MCP tier**.

---

## 4. Entry points (declared by cbm)

| Path | Kind | Notes |
|---|---|---|
| [codebase_mapper/__main__.py](_tmp/code-base-mapper/codebase_mapper/__main__.py) | python_main | `python -m codebase_mapper` |
| [codebase_mapper/cli.py](_tmp/code-base-mapper/codebase_mapper/cli.py) | python_cli | CLI dispatcher |
| [frontend/backend/app.py](_tmp/code-base-mapper/frontend/backend/app.py) | python_app | REST/FastAPI backend (39 722 B — the heaviest single source file in the repo) |
| [frontend/mcp_server/__main__.py](_tmp/code-base-mapper/frontend/mcp_server/__main__.py) | python_main | `python -m frontend.mcp_server` |
| [frontend/mcp_server/server.py](_tmp/code-base-mapper/frontend/mcp_server/server.py) | python_app | MCP server bootstrap |

Two parallel public surfaces ship from the same bundle store:
**REST (`frontend/backend/app.py`)** and **MCP
(`frontend/mcp_server/server.py` → `handlers.py`)**. `handlers.py` imports
`frontend/backend/app.py` directly — i.e. **the MCP server is a thin
adapter over the REST backend's domain functions**, not a duplicate
implementation.

---

## 5. Concept graph signal

`total_concepts = 1 427` across 968 chunks. Top concepts by frequency
(`repository_summary` `key_concepts_limit=30`):

```
test 642 · bundle 206 · chunk 123 · concept 105 · file 92 · graph 89
list 84 · build 54 · name 54 · resolve 54 · detail 53
import_statement 52 [structural-primitive ← code_structure]
resolver 52 · tool 51 · xref 51 · parse 46 · resource 45
analyzer 44 · backend 44 · symbol 44 · verify 44
main 42 · read 42 · fixture 41 · token 41 · uri 40
error 39 · summary 39 · kind 36 · return 36
```

- The dominance of `test` (642 hits across 31 files) reflects the heavy
  test surface, not a "tests" domain concept.
- `import_statement` is the **only kinded concept** in the top-30. The
  controlled-vocab subsystem
  ([vocab/loader.py](_tmp/code-base-mapper/codebase_mapper/vocab/loader.py))
  is wired but, at this commit, only one concept has crossed the curation
  threshold. Recent commit `41e9423` advertises "L3 controlled vocabulary
  v1: typed concepts, default on" — coverage is the obvious follow-up.
- Neighborhood for `bundle` (depth 1, min_weight 2): `test 16, file 11,
  backend 10, concept 10, list 10, unknown 9, name 8, tool 8, include 7,
  path 7, summary 7, anyio 6, anyio_backend 6, chunk 6, error 6`.
  The `anyio_backend` cluster is test-fixture noise from
  pytest-anyio parametrisation, not domain vocabulary.

---

## 6. Tests and coverage hint

- 42 test files vs 89 source files → ratio **0.47** (`test_coverage_hint`).
- Only **8 `tests`-edges** are recorded — the heuristic that links a
  test to its subject under-fires by an order of magnitude (e.g.
  `handlers.py` declares one `tests` edge to `test_handlers.py`, but
  `models.py` has zero tests despite 25 dependents). **This is a known
  weak signal in the bundle**, not a real coverage claim.
- Test layout: three independent test trees plus a top-level
  `tests/verify_*.py` corpus.

| Tree | Count | Focus |
|---|---:|---|
| [tests/verify_*.py](_tmp/code-base-mapper/tests/) (root) | 13 | End-to-end mapper invariants: excludes, L2/L3, regenerate, repo source, roundtrip, timestamps, vocab pipeline/wiring/emission, xrefs (50 944 B — largest single test file), proto/xsd fixtures, repository_summary |
| [frontend/backend/tests/](_tmp/code-base-mapper/frontend/backend/tests/) | 5 | REST: `test_bundles`, `test_endpoints`, `test_unit`, `test_xrefs` |
| [frontend/mcp_server/tests/](_tmp/code-base-mapper/frontend/mcp_server/tests/) | 14 | MCP: coverage_gaps, handlers, hardening, http_transport, oauth, prompts, resources, schemas, server, sparql, subscriptions, vocab |
| [frontend/ui/src/__tests__/](_tmp/code-base-mapper/frontend/ui/src/__tests__/) | 6 | UI (Vitest): bundles, cytoscape-graph, empty-states, views + fixtures/setup |
| `codebase_mapper/self_test.py` | 1 | In-package smoke |

The presence of dedicated `test_hardening`, `test_oauth`, `test_coverage_gaps`,
and `test_schemas` files inside the MCP server tree is a strong signal
that the MCP transport is the most security-scrutinised surface.

---

## 7. Dependency posture

| Edge kind | Count | Reading |
|---|---:|---|
| `imports` (internal) | 267 | Module-to-module |
| `imports_external` | 85 | Module-to-third-party |
| `declares_dependency` | 39 | Manifest-level declarations |
| `pins_dependency` | 368 | Lockfile-level pins |

- **4 dependency manifests** + **2 lockfiles** — the repo ships a Python
  backend (`requirements.txt`, `requirements-sbert.txt`), a UI
  (`package.json`, `package-lock.json` 138 732 B), and probably a top-level
  Python manifest.
- The 368 pin edges are dominated by the JS lockfile.

---

## 8. Language support — the strategic surface

Per-language adapters under
[codebase_mapper/languages/](_tmp/code-base-mapper/codebase_mapper/languages/):
`c.py` (3 642 B), `dart.py` (4 960 B), `go.py` (4 720 B),
`kotlin.py` (5 317 B), `python.py` (10 151 B), `ruby.py` (3 739 B),
`rust.py` (6 234 B), `swift.py` (6 277 B), `tsjs.py` (11 078 B).

Python and TS/JS are the two **first-class** adapters (≥ 10 KB each, plus
dedicated symbol-xref resolvers under
[plugins/symbol_xrefs/](_tmp/code-base-mapper/plugins/symbol_xrefs/)).
Other languages get structural parsing only.

---

## 9. Documentation surface

11 documentation files. Repo-level:
[README.md](_tmp/code-base-mapper/README.md) (13 572 B) ·
[CLAUDE.md](_tmp/code-base-mapper/CLAUDE.md) (14 323 B) ·
[DISCLAIMER.md](_tmp/code-base-mapper/DISCLAIMER.md) (10 093 B).
Domain docs:
[docs/analyze.md](_tmp/code-base-mapper/docs/analyze.md),
[docs/mcp-install.md](_tmp/code-base-mapper/docs/mcp-install.md),
[docs/regenerate.md](_tmp/code-base-mapper/docs/regenerate.md),
[docs/symbol-xrefs-plan.md](_tmp/code-base-mapper/docs/symbol-xrefs-plan.md),
[docs/vocabulary.md](_tmp/code-base-mapper/docs/vocabulary.md).
Component READMEs for `frontend/`, `frontend/backend/`,
`frontend/mcp_server/`.

The repo follows its own `CLAUDE.md`: every Markdown carries the
mandated disclaimer frontmatter (visible in this report, which complies
in turn).

---

## 10. Risks and follow-ups (evidence-grounded)

1. **`tests` edge under-population.** Only 8 edges for 42 test files; the
   `0.47` ratio reported by `repository_summary` is real, but the edge
   graph cannot be used to answer "what tests cover X?". Either widen the
   heuristic in `codebase_mapper/tests_edges.py` (2 790 B — small enough
   to extend) or wire it to a coverage report.

2. **Controlled vocabulary coverage is 1/1 427.** Only `import_statement`
   carries a `kind`. The vocab loader and pipeline are wired
   (`vocab/loader.py`, `software_primitives.yaml`), but the yield is
   essentially zero. The recent commit `41e9423` claims the feature
   is "default on" — investigate whether the matching rules are too
   strict or whether more yaml entries are needed.

3. **`__init__.py` fan-out (deg 30).** Any change to the package surface
   touches 26 outbound dependents. Worth keeping in mind for refactors —
   consider lazy/registry-based wiring if churn becomes painful.

4. **`handlers.py` size (29 289 B, 22 handlers, ~800 lines).** The MCP
   tool surface is one file. A per-tool split would shrink blast radius
   and let `test_handlers.py` shrink with it.

5. **`frontend/backend/app.py` size (39 722 B).** Single-file FastAPI
   app. Same observation as `handlers.py`, larger blast radius.

6. **Empty `(none)` language bucket = 50 files.** All accounted for
   (configs, docs, lockfiles, container files) — no orphans, but worth a
   periodic glance.

---

## 11. How this report was generated (audit trail)

Tools called against the `code-base-mapper` bundle via the cbm MCP:

```
mcp__cbm__select_bundle             { bundle: "code-base-mapper" }
mcp__cbm__orient_bundle             {}
mcp__cbm__bundle_summary            {}
mcp__cbm__repository_summary        { central_files_limit:15,
                                      entry_points_limit:15,
                                      key_concepts_limit:30 }
mcp__cbm__file_detail               × 5  (__init__, pipeline, models,
                                          mcp_server/server, mcp_server/handlers)
mcp__cbm__list_files                × 4  (prefix=codebase_mapper, prefix=frontend,
                                          type=test_code, type=documentation)
mcp__cbm__concept_neighborhood      × 2  (root="bundle", root="concept")
```

No file content was read from disk; all facts cite cbm tool output.

---

ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs will always produce some form of error.
Absence of output verification is a design defect, not a runtime bug.
All LLM output must be treated as untrusted and validated explicitly.
