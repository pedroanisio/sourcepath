---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-09"
---

# Reporting View Model — Data Model & 30-Component Catalog

**Status:** proposal. **Contract:** [`report-spec.schema.json`](./report-spec.schema.json)
(JSON Schema draft 2020-12, `1.0.0`), mechanically validated — see
[Appendix B](#appendix-b--verification-log). **Ground truth:** the persisted
bundle format under `_tmp/` (28 bundles, tool_version 0.5.0), inspected
2026-07-09; every predicate cited below appears in a bundle's
`shapes.shacl.ttl` (`sh:path`) or in an artifact sampled directly.

Schema language note (documented deviation): the contract is JSON Schema, not
TypeScript/Zod, because zod is not a dependency anywhere in this repository and
the contract must be consumed by both the Python backend (`jsonschema` 4.26.0
is already in the environment) and the React UI (TS types are mechanically
derivable via `json-schema-to-typescript`). One source of truth, zero new
dependencies.

---

## 1. What a bundle persists (the reporting substrate)

| Artifact | Layer | Content | Evidence tier |
|---|---|---|---|
| `run_manifest.json` | manifest | artifact hashes/sizes, `commit_sha`, counts, `files_by_language`, extension metadata | fact |
| `inventory.ttl` / `.jsonld` | L1 | RDF: `cbm:Repository/Commit/File/PackageRelease`, edges `imports`, `importsExternal`, `tests`, `declaresDependency`, `pinsDependency`; per-file `path`, `language`, `type`, `sizeBytes`, `gitCommitTime`, `hasPhase`, `astSummary`, `extractionError`, shas | fact |
| `inventory.ttl` (L2) | L2 | `cbml2:Chunk`: `inFile`, `kind`, `symbol`, `parentSymbol`, `beginLine/endLine`, `nif:beginIndex/endIndex`, `contentSha256`, `embeddingArtifact/Row`, `truncatedForEmbedding` | fact |
| `embeddings.npz` + `embeddings_meta.json` | L2 | normalized float32 vectors (dim 384), row-aligned with chunk ids | derived |
| `inventory.ttl` (L3) + `concepts.json` + `concepts_embeddings.npz` | L3 | `skos:Concept` (`prefLabel`, `altLabel`, `cbml3:conceptKind`, `occurrenceCount`, `fileCount`, `composedOf`, `lexicalizes`, `skos:related`), co-occurrence pairs, `per_path_concepts` | derived |
| `inventory.ttl` (L4) + `enrichments.jsonl` | L4 | `cbml4:fileSummary` / `conceptDescription` / `schemaPurpose`, each with `*Model`, `*PromptSha`, `*GeneratedAt` | unverified (LLM) |
| `xrefs.jsonl` | xrefs | `{src_chunk_id, dst_chunk_id, kind: calls|subclassOf|overrides, resolution, resolver}` | fact |
| `rust_items.jsonl` (per-language) | ast_items | `{path, kind, name, parent, line_start/end, is_pub, is_async, attributes}` | fact |
| `blobs/<sha256>` | blobs | content-addressed file bodies | fact |
| `shapes.shacl.ttl`, `ontology-mapping.ttl` | meta | SHACL coverage of every emitted predicate; SPDX alignment | fact |

Two access paths already exist and the model targets both: the **SPARQL read
path** (MCP `sparql`, gated by `CBM_ENABLE_SPARQL=1`, 10 s / 1000 rows) for
graph-backed components, and **direct artifact reads** for JSONL/NPZ/manifest
components.

---

## 2. Data model

Five entities. The full normative definition (constraints, enums, defaults,
conditionals) is in the schema file; this section is orientation.

```
ReportSpec ──composes──▶ ReportBlock (1..100, ordered)
    │                        │ invokes (FK, closed catalog)
    │ pins (association)     ▼
    ▼                    QueryComponent (30 defs, v1.0.0 — §3)
BundleRef                    │ declares: evidence_tier, required layers,
                             │           params schema, result shape
                             ▼ executes to
                         ResultEnvelope ──carries──▶ ResultData (11 shapes)
```

- **`ReportSpec`** — a reporting view: title, audience, `disclaimer_mode`,
  pinned `BundleRef`, authoring provenance, ordered `blocks`. Blocks are
  *composed into* the spec (they die with it).
- **`BundleRef`** — explicit pin: `bundle_name`, `repo_name`, `commit_sha`,
  `manifest_sha256`, `tool_version`. Association only — the report never owns
  the bundle. Pinning is mandatory because session-level bundle selection is
  not honored by the SPARQL read path.
- **`ReportBlock`** — one placed component: opaque `block_id`, `component_id`
  (FK into the closed catalog), typed `params`, a `renderer`, optional
  `narrative` (LLM-authored narrative *must* carry its model id — enforced by
  the schema).
- **`QueryComponent`** — catalog entry (aggregation: shared across specs,
  outlives any one report). Declares its **evidence tier**, **required
  layers**, **params**, **binding** (SPARQL or artifact recipe, §3), and
  **result shape**.
- **`ResultEnvelope`** — execution output: bundle pin, `params_hash` (cache
  key), copied `evidence_tier`, `executed_at`, `duration_ms`, mandatory
  `degradations[]`, and shape-discriminated `data`.

### 2.1 The two discriminators that make mix-and-match work

1. **`component_id`** discriminates the 30 param shapes — a spec cannot pass
   `largest-files` params to `call-graph`.
2. **`shape`** discriminates the 11 result payloads — renderers dispatch on
   shape, never on component. Any component producing `distribution` can feed
   any of `bar_chart | donut_chart | data_table | treemap`; the legal
   renderer set per component is encoded in the schema, so an incompatible
   pairing fails validation before execution (verified negatively, Appendix B).

Result shapes (closed, v1): `metric_group`, `distribution`, `ranked_list`,
`table`, `edge_list`, `tree`, `matrix`, `timeline`, `text_cards`,
`neighbor_list`, `validation_report`.

### 2.2 Evidence tier is structural (PALS's Law placement)

`evidence_tier ∈ {fact, derived, unverified}` is declared per component,
copied into every envelope, and may never be upgraded downstream. `text_cards`
(the only L4 shape) makes `model`, `prompt_sha`, `generated_at` **required per
card** — an undisclosed LLM output is invalid by construction, not by
convention. `disclaimer_mode: evidence_basis_banner` is the standing
operator-approved override for commercial reports; both modes keep unverified
content disclosed.

### 2.3 Disclosed degradation

Every envelope carries `degradations[]` (`layer_absent`, `artifact_missing`,
`hash_mismatch`, `row_limit_truncated`, `walltime_exceeded`,
`resolver_partial`). A component whose required layer is missing returns an
envelope that says so; silent partials are schema-invalid (the field is
required, empty array = full fidelity).

---

## 3. The 30-component catalog (v1.0.0)

Legend — **✓** binding executed against the `graphite` bundle on 2026-07-09
(Appendix B); **○** composed from SHACL-verified predicates using an executed
pattern, not itself run yet. Tier: F = fact, D = derived, U = unverified.
Prefixes: `cbm:`, `cbml2:`, `cbml3:`, `cbml4:`, `skos:` as emitted in every
bundle.

### L1 — structure & history (manifest + inventory)

**1. `repo-overview`** — F · `metric_group` · ✓
Repo name, commit, file count, total bytes, language count.
```sparql
SELECT (COUNT(?f) AS ?files) (SUM(?sz) AS ?bytes) (COUNT(DISTINCT ?lang) AS ?languages)
WHERE { ?f a cbm:File ; cbm:sizeBytes ?sz . OPTIONAL { ?f cbm:language ?lang } }
```
plus `cbm:Repository → cbm:atCommit → cbm:commitSha` and manifest counts.

**2. `language-distribution`** — F · `distribution` · ✓
```sparql
SELECT ?lang (COUNT(?f) AS ?files) (SUM(?sz) AS ?bytes)
WHERE { ?f a cbm:File ; cbm:language ?lang ; cbm:sizeBytes ?sz }
GROUP BY ?lang ORDER BY DESC(?files)
```

**3. `file-type-distribution`** — F · `distribution` · ○
Same pattern over `cbm:type` (objects in the `cbmt:` namespace, e.g.
`cbmt:binary`).

**4. `phase-distribution`** — F · `distribution` · ✓
Same pattern over `cbm:hasPhase` (`cbmp:runtime|build|dev|ci|test`).
Distinguishes shipped surface from scaffolding.

**5. `largest-files`** — F · `ranked_list` · ○
```sparql
SELECT ?path ?sz WHERE { ?f a cbm:File ; cbm:path ?path ; cbm:sizeBytes ?sz }
ORDER BY DESC(?sz) LIMIT {n}
```
Params: `n`, optional `language`, `path_prefix` (`FILTER(STRSTARTS(?path, "{prefix}"))`).

**6. `directory-rollup`** — F · `tree` · ○
Aggregate `cbm:path` by prefix to `max_depth`: files, bytes per directory.
Computed client-side from query 5's unfiltered form (path splitting is not
SPARQL's job).

**7. `commit-recency-timeline`** — F · `timeline` · ✓ (ordering validated)
Bucket `cbm:gitCommitTime` by month/quarter/year → activity profile of the
tree as-committed.

**8. `stale-files`** — F · `ranked_list` · ✓
```sparql
SELECT ?path ?t WHERE { ?f a cbm:File ; cbm:path ?path ; cbm:gitCommitTime ?t }
ORDER BY ASC(?t) LIMIT {n}
```

### L1 — dependency & import graph

**9. `import-edges`** — F · `edge_list` · ✓ (pattern)
```sparql
SELECT ?src ?dst WHERE { ?src cbm:imports ?dst } LIMIT {limit}
```
Optional subtree filter on either endpoint's `cbm:path`.

**10. `import-hubs`** — F · `ranked_list` · ✓
```sparql
SELECT ?dst (COUNT(?src) AS ?fanIn)
WHERE { ?src cbm:imports ?dst }
GROUP BY ?dst ORDER BY DESC(?fanIn) LIMIT {n}
```
`direction: fan_out` swaps the grouped variable. **Monorepo caveat (encoded as
the `include_external` param):** cross-package edges are persisted as
`cbm:importsExternal`, not `cbm:imports` — intra-package-only rankings
understate coupling in monorepos.

**11. `external-import-surface`** — F · `ranked_list` · ✓
```sparql
SELECT ?pkg (COUNT(?f) AS ?n) WHERE { ?f cbm:importsExternal ?pkg }
GROUP BY ?pkg ORDER BY DESC(?n) LIMIT {n}
```
Which third-party packages the code actually touches, weighted by importing
files (graphite: `glam` 146, `kurbo` 35, `serde` 25 — actually-imported ≠
declared).

**12. `dependency-manifest`** — F · `table` · ✓
Declared: `?f cbm:declaresDependency ?pkg` (object = `#pkg/<name>`).
Pinned:
```sparql
SELECT ?pkg ?ver (COUNT(?f) AS ?pinnedBy)
WHERE { ?f cbm:pinsDependency ?rel . ?rel cbm:packageName ?pkg ; cbm:packageVersion ?ver }
GROUP BY ?pkg ?ver
```

**13. `dependency-pin-gap`** — F · `validation_report` · ○
Declared packages with no pinned release and pins with no declaration:
```sparql
SELECT DISTINCT ?pkg WHERE { ?f cbm:declaresDependency ?pkg .
  FILTER NOT EXISTS { ?rel cbm:releaseOf ?pkg . ?g cbm:pinsDependency ?rel } }
```
(and the mirrored query). Lockfile/manifest hygiene in one check.

**14. `orphan-files`** — F · `table` · ○
Code files with no `cbm:imports` edge in either direction and no
`cbm:importsExternal` — dead weight or entry-point candidates:
```sparql
SELECT ?path WHERE { ?f a cbm:File ; cbm:path ?path ; cbm:language ?l .
  FILTER NOT EXISTS { ?f cbm:imports ?x }
  FILTER NOT EXISTS { ?y cbm:imports ?f }
  FILTER NOT EXISTS { ?f cbm:importsExternal ?z } }
```

### L1 — quality & disclosure

**15. `test-coverage-edges`** — F · `table` · ✓
```sparql
SELECT ?tester ?tested WHERE { ?tester cbm:tests ?tested }
```
`facet: untested` inverts: files under `path_prefix` with no incoming
`cbm:tests` edge. Coverage is structural (test-file ↔ subject mapping), not
line coverage — say so in the rendered caption.

**16. `extraction-error-disclosure`** — F · `validation_report` · ○
```sparql
SELECT ?path ?err WHERE { ?f cbm:extractionError ?err ; cbm:path ?path }
```
What the mapper itself could not parse — the report's own blind spots.

**17. `bundle-integrity`** — F · `validation_report` · ○ (artifact binding)
Re-hash every artifact in `run_manifest.json` (`artifacts` + per-extension
`files`), check `embeddings_meta.artifact_sha256` ↔ `embeddings.npz`, report
layer presence. Mirrors `cbm_report.py::verify_hashes`.

### L2 — chunks & symbols

**18. `chunk-kind-distribution`** — F · `distribution` · ✓
```sparql
SELECT ?kind (COUNT(?c) AS ?n) WHERE { ?c a cbml2:Chunk ; cbml2:kind ?kind }
GROUP BY ?kind ORDER BY DESC(?n)
```
(graphite: method 4695, class 2962, function 1506, file 275).

**19. `file-outline`** — F · `tree` · ○
Chunks of one file ordered by `cbml2:beginLine`, nested via
`cbml2:parentSymbol`:
```sparql
SELECT ?symbol ?parent ?kind ?b ?e
WHERE { ?c cbml2:inFile <{fileIri}> ; cbml2:symbol ?symbol ; cbml2:kind ?kind ;
        cbml2:beginLine ?b ; cbml2:endLine ?e .
        OPTIONAL { ?c cbml2:parentSymbol ?parent } } ORDER BY ?b
```

**20. `largest-symbols`** — F · `ranked_list` · ○
Rank non-file chunks by `?e - ?b` (line span); optional `kind` filter
(`function | method | class`).

**21. `embedding-coverage-disclosure`** — F · `validation_report` · ○
Count of `cbml2:truncatedForEmbedding true` chunks and chunks lacking
`cbml2:embeddingRow` — how much of the semantic layer is degraded before any
similarity claim is made.

### AST items & xrefs

**22. `public-api-surface`** — F · `table` · ○ (artifact: `<lang>_items.jsonl`)
Filter `is_pub == true`, group by `kind` × top-level module of `path`;
`is_async` is a free extra column. Degrades with `artifact_missing` for
languages without an items file.

**23. `call-graph`** — F · `edge_list` · ○ (artifact: `xrefs.jsonl`)
Edges with `kind == "calls"`; `resolution: exact` keeps only
resolver-confirmed edges, `any` includes heuristics with each edge still
carrying its `resolution`.

**24. `type-hierarchy`** — F · `edge_list` · ○ (artifact: `xrefs.jsonl`)
`kind ∈ {subclassOf, overrides}` — inheritance/override structure per subtree.

**25. `xref-resolution-disclosure`** — F · `distribution` · ○
`resolution × resolver` counts over `xrefs.jsonl`: what fraction of the
symbol graph is exact vs heuristic, and which resolver produced it.

### L3 — concepts & semantics (derived)

**26. `top-concepts`** — D · `ranked_list` · ✓
```sparql
SELECT ?label ?kind ?occ ?fc
WHERE { ?c a skos:Concept ; skos:prefLabel ?label ; cbml3:conceptKind ?kind ;
        cbml3:occurrenceCount ?occ ; cbml3:fileCount ?fc }
ORDER BY DESC(?occ) LIMIT {n}
```

**27. `concept-cooccurrence`** — D · `matrix` · ○ (artifact: `concepts.json`)
`cooccurrence` triples `[a, b, count]` restricted to the `top_k` concepts →
square matrix for heatmap/adjacency rendering.

**28. `concepts-for-path`** — D · `table` · ○ (artifact: `concepts.json`)
`per_path_concepts` filtered by `path_prefix` — the domain vocabulary of a
subtree; the fastest "what is this directory about" primitive that needs no
LLM.

**29. `semantic-neighbors`** — D · `neighbor_list` · ○ (artifact: `embeddings.npz` / `concepts_embeddings.npz`)
Cosine top-k around a chunk or concept row (vectors are pre-normalized:
dot product suffices). Mirrors the existing `semantic_neighbors` MCP tool.

### L4 — enrichment (unverified)

**30. `enrichment-cards`** — U · `text_cards` · ✓
```sparql
SELECT ?path ?txt ?model ?sha ?at
WHERE { ?f a cbm:File ; cbm:path ?path ; cbml4:fileSummary ?txt ;
        cbml4:fileSummaryModel ?model ; cbml4:fileSummaryPromptSha ?sha ;
        cbml4:fileSummaryGeneratedAt ?at } LIMIT {limit}
```
`kind` param switches to `conceptDescription` / `schemaPurpose` predicate
families. Cards without full provenance are schema-invalid.

---

## 4. Mix-and-match: composed views

A view is just a `ReportSpec` selecting from the catalog. Four worked
compositions (the first exists as a validated example instance):

| View | Blocks (component ids) |
|---|---|
| **Due diligence** ([example](./examples/due-diligence-view.report-spec.json)) | repo-overview · bundle-integrity · language-distribution · dependency-manifest · dependency-pin-gap · test-coverage-edges · extraction-error-disclosure · import-hubs |
| **Architecture map** | directory-rollup · import-edges (subtree) · import-hubs (`include_external: true`) · call-graph · type-hierarchy · top-concepts |
| **Onboarding** | repo-overview · directory-rollup · phase-distribution · concepts-for-path (per major dir) · file-outline (entry points) · enrichment-cards |
| **Honesty audit** (report about the bundle itself) | bundle-integrity · extraction-error-disclosure · embedding-coverage-disclosure · xref-resolution-disclosure · dependency-pin-gap |

The same component appears in different views with different params and
renderers; the same renderer serves different components of one shape. 30
components × shape-legal renderers ≈ 100 valid block types before counting
parameterization.

---

## 5. Versioning & evolution

- Instances carry `schema_version` (`const "1.0.0"`); the catalog version is
  the schema version — adding a component or a renderer widens an enum →
  **minor** bump; removing/renaming components or fields, narrowing enums, or
  changing result shapes → **major** bump; annotation-only → **patch**.
- Components are never deleted inside a major version; a superseded component
  gains `x-deprecated` metadata in its catalog branch pointing at its
  replacement, and is removed only at the next major.
- `bundle.tool_version` + `manifest_sha256` pin results to a bundle
  generation; a re-mapped bundle invalidates caches by construction
  (`params_hash` × `manifest_sha256`).

## 6. Known limits (stated, not hidden)

- **`directory-rollup`, 13, 14, 17, 22–25, 27–29 are not pure SPARQL** — they
  are artifact reads or client-side computations. The catalog binds each to a
  deterministic recipe, but they need executor code, not just a query string.
- **The SPARQL read path caps at 1000 rows / 10 s** — edge-list components on
  large bundles (e.g. flutter, 15 870 files) will truncate; the
  `row_limit_truncated` degradation is the disclosure mechanism, and
  `path_prefix` params are the mitigation.
- **`cbm:tests` edges are sparse** (graphite: 20 edges / 959 files) — the
  `untested` facet is only as good as the mapper's test-linking heuristics;
  captions must not present it as line coverage.
- **Catalog params referencing data-driven value sets** (`language`,
  `concept_kind`, `item_kind`) are typed as open strings by design — the legal
  values are bundle contents, not schema constants.

---

## Appendix A — Schema-design scorecard (self-review)

MUST rules: **19/19 Pass** (Rule 23 PII: not applicable — bundles carry code
metadata; no personal, financial, or health fields exist in the model).
SHOULD rules: **11/11 Pass or documented**.

| # | Rule | Score | Note |
|---|---|---|---|
| 1 | Unambiguous types | Pass | no `unknown`/untyped fields; table cells typed per column |
| 2 | Constraints in schema | Pass | ranges, patterns, caps encoded; renderer↔shape compatibility encoded per branch |
| 3 | Closed, versioned enums | Pass | tiers, shapes, renderers, edge kinds, component ids closed at 1.0.0; data-driven filters deliberately open strings (documented in-schema) |
| 4 | Nullable ≠ optional ≠ absent | Pass | absent filter = no filter (stated); table cell `null` = OPTIONAL non-match (stated) |
| 5 | Arrays: type+cardinality+order | Pass | all arrays bounded; `x-ordering` noted in descriptions |
| 6 | Temporal format | Pass | ISO 8601 UTC (`Z`-anchored pattern), matches persisted `xsd:dateTime` |
| 7 | Numeric units | Pass | closed unit enum on metrics/rank values; `_bytes`/`_ms` suffixed fields |
| 8 | Discriminated polymorphism | Pass | `component_id` (30-way), `shape` (11-way), `metric_kind`, `author_kind` |
| 9 | Defaults declared | Pass | every optional param carries `default` |
| 10 | Stable opaque identity | Pass | UUIDs for specs/blocks; `component_id` is a catalog code (lookup-table exception, Rule 10) |
| 11 | Navigable relationships | Pass | block→component FK; envelope→block_id+component_id+bundle |
| 12 | Lifecycle ownership explicit | Pass | §2: blocks composed into spec; catalog aggregated; bundle associated |
| 13 | FK targets declared | Pass | `bundle_name` marked external (bundle registry); catalog FK is the in-schema enum |
| 14 | Cyclic constraints | Pass | `TreeNode` declared acyclic; `edge_list` declared cycle-tolerant |
| 15 | Single source of truth | Pass | tier lives in catalog; envelope copy annotated `x-denormalized-from` |
| 16 | No bag-of-arrays entities | Pass | every entity carries identity + provenance scalars |
| 17 | Cross-cutting types shared | Pass | `$defs`: BundleRef, PathScope, TopN, IsoDateTimeUtc, Sha256Hex, renderer sets |
| 18 | Computed vs stored | Pass | `params_hash` derivation stated; envelope tier marked denormalized |
| 19 | Explicit versioning | Pass | `const "1.0.0"` in both roots |
| 20 | No duplicate-version entities | Pass | single root per concept |
| 21 | Breaking changes classified | Pass | §5 policy per compatibility matrix |
| 22 | Deprecation annotated | Pass | §5 component-deprecation path (none yet at 1.0.0) |
| 23 | Sensitivity classified | Pass (N/A) | no PII-bearing fields in the model |
| 24 | Identity/provenance immutable | Pass | stated on Uuid/provenance defs |
| 25 | Localization strategy | Pass (documented) | single-locale by design; persisted labels are `@en` |
| 26 | Multi-actor provenance | Pass | authoring provenance + per-card LLM provenance + execution provenance |
| 27 | Consistent naming | Pass | snake_case fields (matches persisted artifacts), kebab-case component ids, `_bytes`/`_ms` units |
| 28 | Mechanically generatable | Pass | validated with `jsonschema` 4.26.0 incl. negative tests (Appendix B) |
| 29 | Intentional extension points | Pass | none typed `unknown`; open-string filters annotated as data-driven |
| 30 | Access patterns inform, not dictate | Pass | logical model normalized; the one denormalization annotated |
| 31 | Standalone readability | Pass | every non-obvious field carries `description` |

## Appendix B — Verification log (2026-07-09)

Executed against the `graphite` bundle (commit `13abf9f`, 959 files) via the
MCP SPARQL read path, bundle pinned explicitly:

1. language distribution (✓ components 1, 2, 5-pattern) — 8 rows, rust 578 files.
2. import fan-in (✓ 9, 10) — top hub `editor/src/messages/prelude.rs`, fan-in 128.
3. chunk-kind counts (✓ 18, 20-pattern) — 4 kinds, 9 438 chunks total.
4. L4 fileSummary + provenance join (✓ 30) — model `qwen2.5-coder:7b` throughout.
5. concept ranking (✓ 26) — top concept `application`, occ 150, 29 files.
6. `cbm:tests` edges (✓ 15) — tester→tested file pairs confirmed.
7. phase distribution (✓ 4) — runtime 858 / build 80 / dev 77 / ci 10 / test 5.
8. `gitCommitTime` ordering (✓ 7, 8) — oldest `LICENSE.txt` 2020-07-12.
9. `pinsDependency → PackageRelease{packageName, packageVersion}` join (✓ 12).
10. `declaresDependency` object shape = `#pkg/<name>` (✓ 12, 13-pattern) — an
    earlier draft of this query assumed release objects and returned 0 rows;
    corrected against the persisted topology.
11. `importsExternal` grouping (✓ 11) — `glam` 146 importing files.
12. `PackageRelease → releaseOf` shape (✓ 13-pattern).

Schema validation: `report-spec.schema.json` passes
`Draft202012Validator.check_schema`; the due-diligence example spec and a
result envelope built from the real query-1 output validate; six negative
cases (incompatible renderer, unknown param, undisclosed LLM narrative,
out-of-catalog component, negative count, missing degradations) are all
rejected. Components marked **○** (13, 14, 16, 17, 19–25, 27–29, plus 3, 6)
have not been executed; their predicates/artifacts are verified present, the
query/recipe text is unexecuted.
