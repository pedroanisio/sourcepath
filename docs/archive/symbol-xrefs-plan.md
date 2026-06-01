# Symbol-level xref edges — implementation plan

> **Archive status:** Historical implementation plan. The symbol-xref layer now
> exists under `plugins/symbol_xrefs/`, with backend and verifier coverage. This
> file is retained only as architectural provenance.

Today `cbm:imports` is a file→file edge. Peer tools (SCIP, Kythe, Stack
Graphs) operate at the *symbol* level: this call goes to that function,
this class is subclassed here. Without symbol resolution our graph is a
coarser view of the same code.

The good news: symbol nodes already exist. The L2 plugin emits
[`cbml2:Chunk`](../plugins/chunks_embeddings/graph_writer.py) for every
function / class / method, with `symbol`, `kind`, `inFile`, line range,
and `contentSha256`. What's missing is the *edges between them* and the
user-facing wiring to navigate them.

## Architectural commitments

These are locked by Step 1 and stable through every later step. Cheap to
revise here, expensive to revise once code depends on them.

1. **Edge endpoints are existing `cbml2:Chunk` nodes**, not a new symbol
   type. Reusing chunks keeps the vocabulary flat and gives the UI
   deep-link story for free.
2. **New namespace `cbmxr:`** (xref). Not `cbml3:` — that's owned by the
   concepts plugin. Predicates: `cbmxr:src`, `cbmxr:dst`, `cbmxr:kind`,
   `cbmxr:resolution`.
3. **Edges are reified.** Each edge is a `cbmxr:Edge` node with
   src/dst/kind triples, not a direct `chunk → chunk` predicate. Reason:
   lets us attach provenance (resolver name, confidence, call-site byte
   offset) without bloating the chunk vocabulary. SCIP uses the same
   pattern.
4. **Edge kinds are string literals**, not an `Enum`:
   `Literal["calls", "subclassOf", "overrides", "references"]`. Matches
   the project's style (`type_`, `phases`, `language` are all strings).
5. **Coverage is first-class data.** Every resolver attempt produces
   either an `Edge` or an entry in `unresolved` with
   `{src_chunk, raw_target, reason}`. Reason codes:
   `module_not_in_repo`, `symbol_not_exported`, `ambiguous`,
   `dynamic_dispatch`, `language_unsupported`. Both buckets land in the
   manifest.
6. **Sidecar artifact `xrefs.jsonl`** — one edge per line, sorted by
   `(src, dst, kind)`. Mirrors the `ast_summaries.jsonl` retrofit
   pattern in [README.md § Size cost](../README.md#size-cost).
   Deterministic byte output, no timestamps.

## The steps

Each step is independently shippable: its own verifier, its own commit,
its own user-visible (or API-visible) increment.

Size annotations: **S** ≈ 1 day · **M** ≈ 2-3 days · **L** ≈ 4-7 days.

### Step 1 — Schema, vocab, empty plumbing (**S**)

Land the contract before any resolver exists.

- **Files**:
  - [`codebase_mapper/models.py`](../codebase_mapper/models.py) — add
    frozen dataclasses `SymbolXrefEdge` and `UnresolvedSymbolRef`.
  - [`codebase_mapper/constants.py`](../codebase_mapper/constants.py) —
    add `CBMXR_NS` and `CBMXR` Namespace.
  - [`codebase_mapper/pipeline.py`](../codebase_mapper/pipeline.py) —
    thread `xref_edges=[]`, `unresolved=[]` through the mapped dict.
  - `plugins/symbol_xrefs/__init__.py` — empty `Aggregator` +
    `GraphContributor` + `ShapeContributor` + `ArtifactEmitter`,
    registered as `l3_10_*`.
- **Contract test** (`tests/verify_xrefs.py`): empty fixture →
  manifest has `counts.xref_edges == 0`, sidecar is `[]`, SHACL
  conforms, vocabulary is registered in the inventory.
- **Why this slice**: locks every name and dataclass before resolvers
  commit to them. Reverting later steps doesn't require touching the
  schema.

### Step 2 — Python intra-file `calls` resolver (**M**)

Smallest end-to-end edge.

- **Files**: `plugins/symbol_xrefs/python_resolver.py`.
- **What it does**: walks `record.ast_summary["ast_json"]`, finds
  `ast.Call` nodes whose `func` resolves to a name defined in the same
  module, emits one `SymbolXrefEdge(src_chunk_id, dst_chunk_id,
  kind="calls", resolution="exact")` per call site.
- **Chunk-ID lookup**: build a `(path, symbol_name) → chunk_id` index
  from `ctx.indices["l2_10_chunks"]`.
- **Quality**: pure function
  `resolve(ast_json, chunks_in_file) → (list[Edge], list[Unresolved])`.
  No globals, no I/O. Unit-testable without a fixture repo.
- **Contract test**: 20-line fixture with `def helper(): pass;
  def main(): helper()` → exactly one edge with the right src/dst
  symbols.
- **Why this slice**: proves the
  Aggregator → GraphContributor → sidecar → SHACL → manifest pipe with
  one language, one edge kind, one scope. Architecture errors surface
  here.

### Step 3 — Real TTL + sidecar + manifest counters (**S**)

Persist what Step 2 produced.

- **Files**: `plugins/symbol_xrefs/graph_writer.py` (reified
  `cbmxr:Edge` triples), `plugins/symbol_xrefs/shapes.py` (SHACL:
  `cbmxr:src`/`dst` target `cbml2:Chunk` exactly once; `cbmxr:kind` is
  `sh:in` the four-string enum), `plugins/symbol_xrefs/artifact.py`
  (writes `xrefs.jsonl`, sorted), and
  [`codebase_mapper/emit_bundle.py`](../codebase_mapper/emit_bundle.py)
  for `counts.xref_edges`, `counts.symbols_resolved`,
  `counts.symbols_unresolved`, `counts.xref_edges_by_kind`.
- **Contract test**: Step 2 fixture — TTL parses, SHACL conforms,
  sidecar JSONL round-trips through `json.loads` and equals the
  in-memory edges, manifest counts match.
- **Why this slice**: separates "produce edges" from "persist edges".
  Step 2 is pure-Python; Step 3 is RDF/serialization. Keeps the
  resolver logic untouched when the storage format is tweaked.

### Step 4 — Python inter-file `calls` resolution (**M**)

Make the resolver useful by walking `from X import Y`.

- **Files**: `plugins/symbol_xrefs/python_resolver.py` (extended).
- **What it does**: reads `ctx.indices["host:python_module_index"]`
  (already populated by
  [`detect_python_source_roots`](../codebase_mapper/languages/python.py))
  and resolves `Name` calls bound to a `from … import` to the chunk in
  the imported module.
- **Quality**: every unresolved call lands in `unresolved` with a
  reason code. No silent drops.
- **Contract test**: `lib.py: def foo(): pass` + `app.py: from lib
  import foo; foo()` → cross-file edge. `lib.py: from external_pkg
  import x; x()` → entry in `unresolved` with reason
  `module_not_in_repo`.
- **Why this slice**: turns the toy into something useful for a real
  Python codebase. TS/JS is deferred to Step 8.

### Step 5 — Backend API exposure (**M**)

Make the data queryable.

- **Files**:
  [`frontend/backend/app.py`](../frontend/backend/app.py).
- **What it does**: `load_bundle` reads `xrefs.jsonl` once and builds
  `xrefs_by_src_chunk_idx: dict[int, list[XrefRow]]` and
  `xrefs_by_dst_chunk_idx`, keyed off the existing chunk array.
  - Extend `GET /api/chunk/{idx}` to return `callers: ChunkRow[]`,
    `callees: ChunkRow[]`.
  - Extend `GET /api/file/{path}` to return `xrefs_out: ChunkRow[]`,
    `xrefs_in: ChunkRow[]` (deduped from all chunks in that file).
- **Contract test** (`frontend/backend/tests/test_xrefs.py`): fixture
  bundle with two known chunks and one known edge; assert both
  endpoints return the expected rows.
- **Why this slice**: data is consumable by API/MCP users before the
  UI lands. Forces a clean response schema before any TSX component
  depends on it.

### Step 6 — UI: callers/callees + xref columns (**M**)

The headline "go to definition / find references" feature.

- **Files**: [`frontend/ui/src/api.ts`](../frontend/ui/src/api.ts)
  (extend `ChunkDetail` and `FileDetail` interfaces);
  [`frontend/ui/src/views/ChunkDetail.tsx`](../frontend/ui/src/views/ChunkDetail.tsx)
  (two new lists with click-through to `/chunk/{idx}`);
  [`frontend/ui/src/views/FileDetail.tsx`](../frontend/ui/src/views/FileDetail.tsx)
  (two new columns next to imports_in/out, symmetric layout).
- **Quality**: no new component file. Resolution reason rendered as a
  small badge (`exact` / `heuristic`).
- **Contract test**: Vitest under
  [`frontend/ui/src/__tests__/`](../frontend/ui/src/__tests__/) —
  render `ChunkDetail` with a mocked API response, assert caller links
  resolve.
- **Why this slice**: ships the feature visible to non-developers.
  Cleanly bounded — no Cytoscape, no graph view.

### Step 7 — Symbol graph view (**M**)

Visual exploration; the SCIP / Stack Graphs analogue.

- **Files**: backend — `GET /api/symbol-graph?limit=400&kind=calls`,
  shape mirrors
  [`/api/file-graph`](../frontend/backend/app.py); nodes = chunks
  ranked by call-degree, edges = `cbmxr:Edge`. Frontend —
  `frontend/ui/src/views/SymbolGraph.tsx`, clone of
  [`FileGraph.tsx`](../frontend/ui/src/views/FileGraph.tsx) swapping
  the data source; reuses
  [`CytoscapeGraph.tsx`](../frontend/ui/src/components/CytoscapeGraph.tsx)
  unchanged.
- **Quality**: zero changes to `CytoscapeGraph.tsx`. New view file
  < 100 LOC.
- **Contract test**: backend pytest asserts degree-ranking stability;
  UI test asserts the view mounts and renders one node per fixture
  chunk.
- **Why this slice**: gives reviewers a "look at the call graph"
  surface. Independent of Steps 8 and 9.

### Step 8 — TS/JS resolver (**L**)

Doubles language coverage.

- **Files**: `plugins/symbol_xrefs/tsjs_resolver.py`.
- **What it does**: uses the byte-perfect tree-sitter CST already in
  `record.ast_summary["cst_json"]`, walks call-expression nodes,
  resolves cross-file via `ctx.indices["host:tsconfigs"]` (already
  populated by
  [`load_tsconfigs`](../codebase_mapper/languages/tsjs.py)).
- **Quality**: resolver dispatch in
  `plugins/symbol_xrefs/__init__.py` is the same registry pattern as
  `_REGENERATORS` — adding a fourth language is one dict entry.
- **Contract test**: TS fixture covering named imports, namespace
  imports, `require()`. Manifest reports per-language
  `symbols_resolved`.
- **Why this slice**: largest single-language impact. Kept separate
  from Python because TS/JS has different `unresolved` buckets (path
  mapping, ambient declarations, type-only imports) that you don't
  want to debug alongside Python edge-cases.
- **Splitting**: if the diff exceeds ~600 LOC, split into 8a
  (intra-file) + 8b (cross-file via tsconfig).

### Step 9 — Symbol-level impact (**S**)

Deeper signal on the existing impact view.

- **Files**: backend — extend
  [`/api/impact/{path}`](../frontend/backend/app.py) with
  `symbol_callers: ChunkRow[]`, `symbol_callees: ChunkRow[]`, computed
  by BFS over `xrefs_by_*` up to `depth`. Frontend — the existing
  impact view renders two more sections.
- **Contract test**: 3-hop call chain across 3 files → symbol-level
  transitive matches the chain; file-level transitive is unchanged.
- **Why this slice**: separates "find references" (Step 6) from "trace
  impact across N hops" (Step 9). Neither step is bloated.

### Step 10 — Subclasses, overrides, optional Stack Graphs (**M-L, optional**)

Deepen edge kinds; defer the stack-graphs integration until the
baseline is proven.

- **What it does**: extend per-language resolvers to emit `subclassOf`
  (class def + base-class resolution) and `overrides` (method
  shadowing a base-class method). The schema from Step 1 already
  supports both kinds — no model changes.
- **Optional sub-step**: pluggable resolver protocol so
  `tree-sitter-stack-graphs` rulesets can drop in via a new entry in
  the dispatcher dict. Treat as an experiment — keep the AST-based
  resolver as the default until stack-graphs measurably beats it on
  the verifier.
- **Why this slice**: subclass edges ship the "class hierarchy" view
  SCIP users expect. Stack-graphs is a research bet, not a baseline
  requirement.

## Cross-cutting quality patterns

Rules to enforce across every step so the layer stays clean:

- **No globals in resolvers.** Every resolver is a pure function of
  `(ast_summary, chunks_in_file, indices)`. The aggregator orchestrates;
  resolvers are stateless. Same discipline as `regenerate_python_source`.
- **Every edge has a reason.** `resolution: Literal["exact",
  "heuristic", "ambiguous"]` is required, not optional. Avoids the "is
  this real?" question on the UI three months from now.
- **`unresolved` is data, not a log.** Bucketed reasons, sortable,
  written to the manifest. Avoids the trap where coverage silently
  degrades and nobody notices.
- **Verifier mutations match the regenerate pattern** — see
  [tests/verify_regenerate.py](../tests/verify_regenerate.py). Drop a
  chunk → its inbound edges land in `unresolved`. Corrupt the
  sidecar → the bundle still loads with `xref_edges_loaded=0` and a
  manifest warning. One bad input never poisons the run.
- **`plugins/symbol_xrefs/` follows the `plugins/chunks_embeddings/`
  file layout exactly** — `__init__.py` (registration),
  `python_resolver.py` / `tsjs_resolver.py` (extractors),
  `graph_writer.py` (RDF), `shapes.py` (SHACL), `artifact.py`
  (sidecar). Future contributors find the right file without reading
  code.
- **Frontend changes never introduce a new top-level type.** Extend
  `ChunkDetail` / `FileDetail` / `FileImpact` interfaces. A new
  top-level shape is a signal the API design is wrong.
- **Each step's PR diff stays under ~600 LOC**, sidecar excluded. If a
  step grows beyond that, split it (Step 8 is the most likely
  candidate).

## Dependency graph

```
Step 1 (schema)
  ├─ Step 2 (Python intra-file)
  │   └─ Step 3 (TTL + sidecar + manifest)
  │       ├─ Step 4 (Python inter-file)
  │       │   └─ Step 8 (TS/JS resolver)
  │       └─ Step 5 (backend API)
  │           ├─ Step 6 (UI: callers/callees)
  │           │   └─ Step 7 (symbol graph view)
  │           └─ Step 9 (symbol-level impact)
  └─ Step 10 (subclasses + stack-graphs, optional)
```

Steps 4 and 5 are parallelizable. So are Steps 7 and 9 once Step 6
lands.

## Related docs

- [README.md § Regenerate](../README.md#regenerate) — the AST-based
  artifact this plan layers on top of.
- [docs/regenerate.md](regenerate.md) — example of the doc style and
  the per-language contract pattern Step 8 will mirror.
