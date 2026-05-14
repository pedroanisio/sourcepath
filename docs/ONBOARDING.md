---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.7 (1M context) via Claude Code, using cbm-mcp v0.5.0"
  date: "2026-05-14"
---

# Onboarding & Key Concepts — `code-base-mapper`

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](./DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

> **Provenance.** Every claim in this document is grounded in the CBM bundle
> `code-base-mapper @ c8184fd` (generated 2026-05-14T16:33:48Z, tool v0.5.0, SHACL conforms ✅).
> Counts come from `bundle_summary`, `repository_summary`, and per-concept `concept_detail` calls.
> Cross-reference any line back to the live graph using the CBM tool listed in the footnotes.

---

## 1. The 90-second pitch

`code-base-mapper` ingests a source-code repository and emits a **layered RDF
bundle** that an LLM, a CLI, an HTTP backend, an MCP server, and a React UI can
all query interchangeably. The bundle is *the* product. Every other component
(CLI, plugins, frontend, MCP server) is either a producer or a consumer of it.

Three layers, one bundle:

| Layer | Predicates | What it captures |
|---|---|---|
| **L1 — host** | `cbm:path`, `cbm:imports`, `cbm:hasPhase`, `cbm:tests` | Files, languages, types, import edges, manifests, AST summaries |
| **L2 — chunks_embeddings** | `cbml2:inFile`, `cbml2:beginIndex`, `cbml2:endIndex`, `cbml2:embeddingRow` | Per-function/class/file chunks with NIF byte spans and 384-d sentence-transformer vectors |
| **L3 — concept_graph** | `cbml3:lexicalizes`, `cbml3:composedOf`, `skos:related`, `skos:prefLabel` | SKOS concepts from identifier splitting + co-occurrence edges |

The repo dogfoods itself: it consumes its own bundle through the same MCP tools
it ships. If something is confusing in the docs, **run the tool against the
repo's own bundle** and the answer is one query away.

---

## 2. The four concepts you must internalize first

These four are load-bearing. Every PR, every feature, every test touches at
least one of them. Internalize them and you can read the codebase.

### 2.1 `bundle` — the unit of currency

> *"A reproducible, content-addressed RDF artifact + blob store produced by
>  running the pipeline against a repository at a specific commit."*

- **Frequency:** 230 occurrences across 36 files (highest non-meta concept).
- **Alt-labels (selected):** `Bundle`, `BundleInfo`, `_emit_bundle`, `_orient_bundle`, `_load_bundle_cached`, `_select_bundle`, `_validate_bundle_name`, `chain_bundle`, `enriched_bundle`, `host_only_bundle`, `prewarm_default_bundle`, `useBundleVersion`.
- **Top co-occurring concepts:** `test (22)`, `file (15)`, `main (13)`, `concept (12)`, `verify (12)`, `backend (11)`.
- **Anchors (live files):** [codebase_mapper/emit_bundle.py](codebase_mapper/emit_bundle.py), [frontend/backend/app.py](frontend/backend/app.py), [frontend/backend/tests/conftest.py](frontend/backend/tests/conftest.py).

**Mental model.** A bundle is a directory on disk:

```
_tmp/<repo-name>/
├── manifest.json          # commit_sha, tool_version, counts, embeddings backend
├── inventory.ttl          # L1 RDF
├── chunks.ttl + vectors   # L2
├── concepts.ttl           # L3
├── blobs/<sha256>         # content-addressed source blobs
├── rust_items.jsonl       # Stage-4 sidecar (Rust attributes)
└── llm/                   # llm_enrich sidecar (when --llm-enrich)
```

`Bundle` (a Pydantic-ish data class in [`frontend/backend/app.py`](frontend/backend/app.py)) is the in-memory cache key. `bundle_name` is the unique handle; **never** trust paths, always trust `bundle_name`. Bundle selection / resolution / cache invalidation is centralized in:

- `_resolve_bundle_path` ([app.py:498-532](frontend/backend/app.py#L498-L532))
- `_load_bundle_cached` ([app.py:535-537](frontend/backend/app.py#L535-L537))
- `_clear_bundle_cache` ([app.py:545-546](frontend/backend/app.py#L545-L546))
- `_select_bundle` ([handlers.py:452-458](frontend/mcp_server/handlers.py#L452-L458))

If you're touching anything bundle-shaped, **start here**.

### 2.2 `chunk` — the unit of meaning

> *"A typed, NIF-spanned slice of a file (function / class / file-level) with
>  a stable `idx`, a `contentSha256`, and exactly one `embeddingRow`."*

- **Frequency:** 143 occurrences across 22 files. **1361 chunks** total in the example bundle.
- **Alt-labels (selected):** `ChunkDetail`, `_ConceptChunk`, `_FileChunk`, `_chunk_python`, `_chunk_rust`, `_chunk_tsjs`, `_chunk_id`, `_whole_file_chunk`, `chunk_iri`, `chunks_embeddings`.
- **Top co-occurring concepts:** `file (10)`, `symbol (9)`, `xref (9)`, `list (8)`, `bundle (7)`, `id (7)`, `chunk_embedding (6)`.
- **Anchors:** [plugins/chunks_embeddings/chunker.py](plugins/chunks_embeddings/chunker.py), [frontend/backend/app.py](frontend/backend/app.py), [frontend/mcp_server/handlers.py](frontend/mcp_server/handlers.py).

**Identity.** A chunk is identified two ways:

1. **`idx`** — the bundle-stable integer (also the `embeddingRow`). Use for tool calls (`chunk_detail`, `chunk_blob`).
2. **URI** — `https://codebase-mapper.example.org/cbm/instance#chunk/<path>%23<kind>%3A<symbol>%3AL<begin>-L<end>` — use for cross-tool references.

If you're computing a chunk identity yourself, you almost certainly want
`_chunk_id` / `_chunk_id_to_uri` ([app.py:305-312](frontend/backend/app.py#L305-L312)). Do not roll your own.

### 2.3 `concept` — the unit of vocabulary

> *"A SKOS concept lexicalized by one or more identifiers, with optional
>  curated `kind` (`domain-primitive` / `structural-primitive` /
>  `relational-primitive`) and SKOS `broader` collection."*

- **Frequency:** 135 occurrences across 29 files. **1898 concepts** in the example bundle.
- **Alt-labels:** `ConceptAggregator`, `ConceptDetail`, `ConceptGraph`, `_ConceptChunk`, `_concept_iri`, `_concept_neighborhood`, `concept_iri`, `_pick_typed_concept`, `_pick_untyped_concept`.
- **Top co-occurring concepts:** `file (13)`, `test (13)`, `bundle (12)`, `graph (11)`, `kind (10)`, `unknown (9)`.
- **Anchors:** [plugins/concept_graph/concepts.py](plugins/concept_graph/concepts.py), [plugins/concept_graph/splitter.py](plugins/concept_graph/splitter.py), [codebase_mapper/vocab/](codebase_mapper/vocab/).

**Read this twice.** The vocabulary is **mostly untyped**: of the top-30
concepts, only `import_statement` carries a curated `kind`. Concepts without a
`kind` are *raw lexical observations*, not authoritative ontology entries. If
you wire UI or LLM logic on top of `skos:related` edges, treat them as **soft
hints**, not contracts.

The curated subset lives in [`codebase_mapper/vocab/software_primitives.yaml`](codebase_mapper/vocab/software_primitives.yaml) (3.8 KB). Extend that file rather than hard-coding kinds in callers.

### 2.4 `import_statement` — the only typed primitive you'll meet on day one

> *"A structural-primitive concept (`broader: code_structure`) representing
>  an import edge in any of the supported languages."*

- **Frequency:** 56 occurrences across 19 files. **Kind:** `structural-primitive`. **Broader:** `code_structure`.
- **Anchors:** [codebase_mapper/extensions.py](codebase_mapper/extensions.py) (the `ImportResolver` Protocol), [codebase_mapper/languages/](codebase_mapper/languages/) (per-language resolvers), [plugins/symbol_xrefs/](plugins/symbol_xrefs/) (cross-file symbol resolution).
- **Top co-occurring concepts:** `resolve (14)`, `language (10)`, `summary (10)`, `ast (9)`, `extract (9)`.

**Why it matters.** `import_statement` is the **first-class extension point**
of the whole project. Adding a new language means implementing an
`ImportResolver`. The `ImportResolver` Protocol is at
[`extensions.py:132-146`](codebase_mapper/extensions.py#L132-L146).

> ⚠ **Known limitation.** The current import-edge extractor does not see:
> - `.proto` `import "…"` statements (all 5 protos appear orphaned — see `cbm-inspection-report.md` §R-orphans).
> - Dynamic Python imports (`importlib.import_module(...)`) — at least one production module (`frontend/mcp_server/sparql.py`) is reachable only via dynamic discovery and shows zero `imports_in` edges.
>
> Treat the import graph as authoritative for **static** edges only.

---

## 3. The architectural topology in one diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ frontend/ui      React/Vite SPA — Cytoscape graphs              │
│   App.tsx → views/{Dashboard, FileDetail, ChunkSearch,           │
│                    ConceptGraph, SymbolGraph, ChunkDetail}       │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP/JSON
┌───────────────────────────▼─────────────────────────────────────┐
│ frontend/backend/app.py    (FastAPI; 46.7 KB monolith)           │
│ frontend/mcp_server/       (MCP server: stdio + HTTP + OAuth +   │
│                             SPARQL + subscriptions + schemas)    │
└───────────────────────────┬─────────────────────────────────────┘
                            │ reads bundle (RDF + blobs)
┌───────────────────────────▼─────────────────────────────────────┐
│ codebase_mapper/   (core library — pipeline, classify,           │
│   extensions, rdf_emit, languages/, vocab/)                      │
│ plugins/                                                         │
│   ├── chunks_embeddings/  → L2  (NIF spans + 384-d MiniLM)       │
│   ├── concept_graph/      → L3  (SKOS identifier-derived)        │
│   ├── symbol_xrefs/       → cross-references (py / tsjs / rust)  │
│   └── llm_enrich/         → llm_summary / llm_description        │
└─────────────────────────────────────────────────────────────────┘
```

**Conway-style read.** The repo splits into four producer/consumer pairs:

1. **Pipeline (producer) ↔ Bundle (artifact)** — `codebase_mapper/pipeline.py` produces, everyone else consumes.
2. **Plugins (producer) ↔ Sidecar files (artifact)** — each plugin writes a small artifact (`<plugin>/artifact.py`) and an RDF graph fragment (`<plugin>/graph_writer.py`).
3. **Backend / MCP server (consumer) ↔ HTTP/JSON-RPC (interface)** — both read bundles and expose them through different transports. They share data models in [`codebase_mapper/models.py`](codebase_mapper/models.py).
4. **UI (consumer) ↔ HTTP (interface)** — Vite/React via `frontend/ui/src/api.ts`.

---

## 4. The pipeline phases — what runs when

CBM defines a phase ontology (`cbm:hasPhase`). The CLI/scripts enforce a strict
order:

| Phase / script | Produces | Anchor |
|---|---|---|
| **L1 host** ([`scripts/run_l2.py`](scripts/run_l2.py)) | File inventory, import edges, AST summaries, manifests, lockfiles | [`codebase_mapper/pipeline.py`](codebase_mapper/pipeline.py) |
| **L2 chunks+embeddings** ([`scripts/run_l2.py`](scripts/run_l2.py)) | Per-function/class/file chunks with NIF spans + MiniLM vectors | [`plugins/chunks_embeddings/`](plugins/chunks_embeddings/) |
| **L3 concept_graph** ([`scripts/run_l3.py`](scripts/run_l3.py)) | SKOS concepts from identifier splitting + co-occurrence | [`plugins/concept_graph/`](plugins/concept_graph/) |
| **Symbol xrefs** ([`scripts/run_xrefs.py`](scripts/run_xrefs.py)) | Cross-references resolved per language | [`plugins/symbol_xrefs/`](plugins/symbol_xrefs/) |
| **L4 llm_enrich** ([`scripts/run_l4.py`](scripts/run_l4.py)) | `llm_summary` / `llm_description` triples + sidecar JSON | [`plugins/llm_enrich/`](plugins/llm_enrich/) |

> **Sequencing matters.** L4 enrichment reads central files chosen by L1
> import-degree, so it depends on L1. L3 concept_graph depends on L1 file
> inventory. L2 is independent of L3 but must complete before semantic search
> works against new chunks.

---

## 5. The plugin contract (read before you touch `plugins/`)

Every plugin in `plugins/<name>/` follows the same shape:

```
plugins/<name>/
├── __init__.py        # registers the plugin via codebase_mapper.extensions
├── artifact.py        # serializes the plugin's sidecar artifact
├── graph_writer.py    # emits RDF triples into the bundle
└── <domain-modules>.py
```

The extension API is in [`codebase_mapper/extensions.py`](codebase_mapper/extensions.py)
— 11.6 KB, 20 inbound importers. It exposes **7 extension points**; only 4
plugins ship today. If you're adding a plugin, your `__init__.py` must hook
into one of those slots; do not bypass `extensions.py`.

**The PALS's-LAW boundary.** The `llm_enrich` plugin is unique: it produces
**untrusted, model-generated text** (`llm_summary`, `llm_description`). Every
consumer of those fields MUST treat them as derived-and-unverified data, per
[`CLAUDE.md`](CLAUDE.md) §LLM-Output-Verification. The current consumers are:

- [`frontend/ui/src/components/LlmEnrichmentCard.tsx`](frontend/ui/src/components/LlmEnrichmentCard.tsx) (UI badge)
- `_llm_payload` ([handlers.py:537-552](frontend/mcp_server/handlers.py#L537-L552))
- `_repository_summary` ([handlers.py:284-440](frontend/mcp_server/handlers.py#L284-L440)) — surfaces it in `central_files[].llm_summary`

When you wire a new consumer, **carry the provenance object through unchanged**
(`{model, prompt_sha, target_sha, generated_at}`) and render it visibly.

---

## 6. Glossary — pin this tab

| Term | Meaning |
|---|---|
| **Bundle** | Reproducible RDF + blob artifact at a specific repo commit. Identified by `bundle_name`. |
| **Chunk** | NIF-spanned slice of a file (function/class/file-level) with `idx`, `contentSha256`, `embeddingRow`. |
| **Concept** | SKOS concept derived from identifier splitting; may have curated `kind`. |
| **Concept kind** | `domain-primitive` / `structural-primitive` / `relational-primitive`. Only present for vocab-curated concepts. |
| **L1 / L2 / L3** | The three RDF layers: host / chunks_embeddings / concept_graph. |
| **xref** | Cross-reference resolved by `plugins/symbol_xrefs` — symbol → symbol, not just file → file. |
| **NIF span** | `beginIndex` / `endIndex` byte offsets from the W3C NIF (NLP Interchange Format) standard, applied to source code. |
| **Phase** | A pipeline stage tagged with `cbm:hasPhase`. Strict ordering is enforced. |
| **Entry point** | A file with `kind=python_main` / `python_cli` / `python_app`. Per `repository_summary.entry_points`. |
| **Import resolver** | Per-language Protocol implementation that resolves a raw import string to a `FileRef`. Lives in `codebase_mapper/languages/<lang>.py`. |
| **Symbol xref** | Cross-file edge between a usage site and a definition site, resolved by `plugins/symbol_xrefs/<lang>_resolver.py`. |
| **SHACL** | The W3C constraint language used to validate the bundle's RDF. `shacl_conforms: true` is a green-light invariant. |
| **PALS's LAW** | The project's architectural rule: LLM output is untrusted by default; absence of verification is a design defect. See [`CLAUDE.md`](CLAUDE.md). |
| **Sidecar** | A JSONL or JSON file inside the bundle that supplements RDF (e.g. `rust_items.jsonl`, `llm/*.json`). |
| **Round-trip** | Reconstructing source files from `inventory.ttl + blobs/` and verifying byte-equality. See [`codebase_mapper/reconstruct.py`](codebase_mapper/reconstruct.py). |

---

## 7. Reading order — the path I recommend

Follow this order, then peel off into whichever sub-system owns your task.

| Step | Read | Why |
|---:|---|---|
| 1 | [`README.md`](README.md) | High-level intent + install. |
| 2 | [`CLAUDE.md`](CLAUDE.md) | The rules — especially PALS's LAW (§LLM Output Verification). |
| 3 | [`DISCLAIMER.md`](DISCLAIMER.md) | Epistemic commitments — applies to every doc and PR description. |
| 4 | [`PURPOSE.md`](PURPOSE.md) | The "why" — never propose changes that conflict with it. |
| 5 | [`docs/vocabulary.md`](docs/vocabulary.md) | The L3 controlled vocab — read before you touch concepts. |
| 6 | [`docs/analyze.md`](docs/analyze.md) | How to run the pipeline locally. |
| 7 | [`codebase_mapper/models.py`](codebase_mapper/models.py) | The data shapes. Small, central, 28 inbound importers. |
| 8 | [`codebase_mapper/constants.py`](codebase_mapper/constants.py) | Namespaces, refkinds, phase vocab. 24 inbound importers. |
| 9 | [`codebase_mapper/extensions.py`](codebase_mapper/extensions.py) | The plugin contract. 20 inbound importers. |
| 10 | [`codebase_mapper/pipeline.py`](codebase_mapper/pipeline.py) | The orchestrator. |
| 11 | [`frontend/mcp_server/handlers.py`](frontend/mcp_server/handlers.py) | The user-facing tool surface — your read-only window into the bundle. |
| 12 | [`docs/regenerate.md`](docs/regenerate.md), [`docs/symbol-xrefs-plan.md`](docs/symbol-xrefs-plan.md), [`docs/llm-enrich.md`](docs/llm-enrich.md) | Per-subsystem deep dives. |

By step 11 you can usefully open a PR.

---

## 8. Hard rules — things you can ship a CI failure with

These are non-negotiable. They're enforced by tests and reviewers.

1. **Every Markdown document MUST have the disclaimer frontmatter** (per `CLAUDE.md` §5). Including this one.
2. **Every README MUST link to `@DISCLAIMER.md`** at the correct relative path.
3. **English (EN-US) by default** for all code, comments, commits, and docs. PT-BR only when the user explicitly requests it.
4. **TypeScript over JavaScript; Markdown over DOCX.** No new `.js` files in TS contexts; no DOCX unless the user asks.
5. **PALS's LAW: LLM output must be treated as untrusted.** If you add a function that calls an LLM, copy the architectural-contract comment block from `CLAUDE.md` verbatim. Every consumer must validate.
6. **No deferrals.** AI agents may not postpone work to a follow-up unless the operator explicitly authorizes it (per `CLAUDE.md` §8).
7. **Round-trip must verify.** If you change anything that touches `rdf_emit.py`, `emit_bundle.py`, or `reconstruct.py`, run `codebase_mapper/self_test.py` and confirm `shacl_conforms: true`.
8. **Test/source ratio ≥ 0.77.** Currently 77/100; do not regress. Add the test in the same PR as the feature.
9. **Never bypass `_resolve_bundle_path`** when accepting a bundle name from a user/transport. It's the security boundary against path traversal.
10. **Never trust `imports_of` for `.proto` or dynamic imports.** Use grep + manual verification (known graph blind spot).

---

## 9. Common gotchas

| Gotcha | Why it happens | What to do |
|---|---|---|
| `imports_of` returns `[]` for a file you can clearly see is imported | Dynamic import, `.proto`, or `.d.ts` ambient type | Cross-check with `grep` before deleting "orphan" code (see `cbm-inspection-report.md` §dead-code) |
| Concept has no `kind` even though it's clearly structural | Vocabulary is sparsely curated (only `import_statement` has a kind in top-30) | Extend `vocab/software_primitives.yaml`, don't hard-code in callers |
| `tests_edges` count looks too low (15 for 77 test files) | Heuristic in [`codebase_mapper/tests_edges.py`](codebase_mapper/tests_edges.py) is conservative | Either extend the heuristic or accept the under-count; never compensate downstream |
| `frontend/backend/app.py` is 46.7 KB and intimidating | Genuine monolith — known refactor target | Don't add a 25th class to it; create a new module first |
| LLM summary contradicts the code | LLM hallucination — expected per PALS's LAW | The code is the ground truth. File an issue if the summary is misleading. |
| Two requirements files (`requirements.txt` + `requirements-sbert.txt`) | sbert is an opt-in backend | Edit `pyproject.toml` first; mirror to the requirements files |
| `_repository_summary` surfaces `llm_summary` in `central_files` | Cross-feature contract — see [`test_repository_summary_central_files_carry_llm_summary`](frontend/mcp_server/tests/test_llm_enrich_surface.py) | If you change either side, update the test in the same PR |

---

## 10. The CBM-MCP cheat sheet — query the repo against itself

The same tool surface you'd use as a downstream consumer is the fastest way to
learn the codebase. Run these against the bundle for `code-base-mapper`.

| Question | Tool call |
|---|---|
| "What does this repo look like?" | `repository_summary {}` |
| "Which files matter most?" | `repository_summary { central_files_limit: 20 }` → sort by `import_degree` |
| "Where is X defined?" | `semantic_neighbors { q: "<natural-language description>", k: 10 }` |
| "What does file X depend on?" | `file_impact { path: "...", depth: 3 }` |
| "What chunks live in file X?" | `file_detail { path: "..." }` |
| "What does this concept mean?" | `concept_detail { name: "<concept>" }` |
| "What concepts cluster around X?" | `concept_neighborhood { name: "<concept>", depth: 2 }` |
| "Show me every #[test] in Rust" | `items_by_attribute { pattern: "#[test]" }` |
| "Find the LLM summary for file X" | `file_detail { path: "..." }` → `llm_summary.text` |
| "Which test exercises file X?" | `file_detail { path: "..." }` → `tests` |

> **Trust ladder.** Trust the import graph for static Python/TS/Rust edges
> only. Trust SHACL conformance as a hard invariant. Treat LLM summaries as
> hints, never facts. Treat concept `kind` only when present.

---

## 11. Where to ask for help

- **Code-level questions:** open an issue tagged `area/<plugin-or-subsystem>`.
- **Architecture questions:** point to [`PURPOSE.md`](PURPOSE.md) and start a discussion before opening a PR.
- **PALS's-LAW audit questions:** ping anyone who's edited [`plugins/llm_enrich/`](plugins/llm_enrich/) in the last 30 days.
- **Vocabulary changes:** open a PR against [`codebase_mapper/vocab/software_primitives.yaml`](codebase_mapper/vocab/software_primitives.yaml) with at least one new concept fully typed.

Welcome.

---

### Footnotes — how each section was sourced

| § | CBM call(s) |
|---|---|
| 1, 3 | `orient_bundle`, `bundle_summary` |
| 2.1–2.4 | `concept_detail { name: "bundle" | "chunk" | "concept" | "import_statement" }` |
| 4 | `repository_summary.entry_points`, `list_files prefix=scripts/` |
| 5 | `list_files prefix=plugins/`, `file_detail` on each plugin `__init__.py` |
| 6 | distilled from §1–5 + `docs/vocabulary.md` |
| 7 | `list_files type=documentation`, `imported_by` to confirm centrality |
| 8 | `CLAUDE.md` §-by-§ + `bundle_summary.shacl_conforms` |
| 9 | `imported_by` + `imports_of` cross-checks; `cbm-inspection-report.md` orphan audit |
| 10 | `ToolSearch select:mcp__cbm__*` |
