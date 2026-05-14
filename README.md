# codebase_mapper

Maps source code repositories into RDF graphs (turtle + JSON sidecars). The
host classifies files, extracts per-language AST summaries, resolves imports,
and infers tests/dependency edges. The pipeline runs through a registry of
pluggable extension points; companion plugins layer chunks/embeddings and a
concept graph on top.

## Layout

```
codebase_mapper/        host package
├── languages/          per-language AST extractors and import resolvers
├── extensions.py       seven extension protocols + registries
├── pipeline.py         map_codebase() — drives the registries end-to-end
├── rdf_emit.py         inventory + ontology + SHACL graph builders
├── cli.py, __main__.py CLI entry point
└── ...
plugins/
├── chunks_embeddings/  source chunks + sentence-transformer/hash embeddings
├── concept_graph/      identifier splitting + canonical concept set + SKOS
└── symbol_xrefs/       symbol-level xref edges (cbmxr:Edge) — Phase 1 scaffold
scripts/
├── run_l2.py           host + chunks_embeddings registered
├── run_l3.py           host + chunks_embeddings + concept_graph (--no-l2 skips L2)
└── run_xrefs.py        host + chunks_embeddings + symbol_xrefs (+ --concepts opt-in)
frontend/
├── backend/            FastAPI service that reads an output bundle and serves
│                       summary/graph/chunk/concept JSON to the UI
└── ui/                 React UI (scaffold, in progress)
tests/
├── verify_l2.py        chunks_embeddings contract suite
├── verify_l3.py        concept_graph contract + cross-layer (with/without L2)
├── verify_xrefs.py     symbol_xrefs schema/vocab/sidecar (Phase 1)
└── verify_xsd_fixture.py  static/schemas/ classifier coverage
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
```

`--backend sbert` uses `sentence-transformers/all-MiniLM-L6-v2`; `--backend hash`
uses a deterministic SHA-256 fake (no semantics, useful for contract tests).

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
JSON endpoints (`/api/summary`, `/api/file-graph`, `/api/concept-graph`,
`/api/chunks`, `POST /api/chunks/search`, `/api/concept/{name}`,
`/api/chunk-blob/{sha}`).

```bash
# Point at any output dir containing run_manifest.json + inventory.ttl +
# embeddings.npz + embeddings_meta.json + concepts.json.
CBM_OUTPUT_DIR=_tmp/usl-ng-core-map \
  .venv/bin/uvicorn frontend.backend.app:app --port 8000 --reload
```

Semantic search (`POST /api/chunks/search`) uses cosine NN over the chunk
matrix when the bundle's embedding backend is a sentence-transformer; hash
backends fall back to substring matching. The bundle is loaded once per
process — restart to pick up a new output dir. See
[frontend/backend/README.md](frontend/backend/README.md) for endpoint details.

## Regenerate

`codebase_mapper.regenerate` materializes source from `inventory.ttl` and
`cbm:astSummary` **alone** — no `blobs/` directory required. Companion to
`reconstruct`, with a different fidelity model:

| Path | Source of truth | Fidelity |
|---|---|---|
| `reconstruct` | `inventory.ttl` + `blobs/` | **byte-identical** every file |
| `regenerate` (new) | `inventory.ttl` + `cbm:astSummary` | Python: **semantic** (re-parses to the same AST); TS/JS: **byte-identical** via leaf-text CST |

Currently supported by `regenerate`: Python, TypeScript (`.ts`, `.tsx`,
`.cts`, `.mts`), JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`). Other files
(markdown, configs, lockfiles, binaries) are enumerated in the report
under `ast_unsupported` / `no_ast_summary` and not written to disk.

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

### Size cost

`cbm:astSummary` grows with full-body capture. Measured ratios:

| Language | `ast_summary` JSON / source |
|---|---|
| Python | ~6.6× |
| TypeScript/JS | ~12.5× |

`run_manifest.json` reports `counts.ast_full_bodies_python`,
`counts.ast_full_bodies_tsjs`, and `counts.ast_summary_total_bytes` so
the cost is measurable per run. If TTL size becomes a problem, the
short retrofit is to move the full-body literals to a sidecar
`ast_summaries.jsonl` keyed by `cbm:contentSha256` and reference them
from `inventory.ttl` as URIs.

### Adding a new language

See [docs/regenerate.md](docs/regenerate.md) for the full contract:
the `ast_summary` shape, the `regenerate_<lang>_source(summary) -> str`
signature, registration in `_REGENERATORS`, and the
`verify_regenerate.py` test cases a new language must satisfy.

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
Built-in `LanguageAnalyzer` / `ImportResolver` wrappers for the nine
supported languages (C, Dart, Go, Kotlin, Python, Ruby, Rust, Swift,
TS/JS) auto-register at host import; `reset_registries()` re-registers
them after a clear.

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

## Verify

```bash
python tests/verify_roundtrip.py            # blob-based byte-perfect roundtrip
python tests/verify_regenerate.py           # TTL+AST regenerate (Python semantic + TS/JS byte)
python tests/verify_excludes.py             # --exclude flag + .cbmignore behavior
python tests/verify_repo_source.py          # local path + Git URL --repo handling
python tests/verify_timestamps.py           # atime/mtime/ctime + gitCommitTime
python tests/verify_l2.py --backend hash    # chunks_embeddings contract
python tests/verify_l3.py                   # concept_graph contract + cross-layer
python tests/verify_vocab.py                # controlled-vocab loader
python tests/verify_vocab_emission.py       # controlled-vocab RDF + SHACL
python tests/verify_vocab_wiring.py         # controlled-vocab aggregator wiring
python tests/verify_vocab_pipeline.py       # controlled-vocab end-to-end pipeline
python tests/verify_xrefs.py                # symbol-xref schema/vocab/sidecar (Phase 1)
python tests/verify_xsd_fixture.py          # static/schemas/ classifier coverage
python tests/verify_proto_fixture.py        # static/proto/ classifier + import coverage
python tests/verify_repository_summary.py   # MCP repository_summary tool contract
python tests/verify_rust_ast.py             # Rust deep-AST items + chunker (Stage 1)
python tests/verify_rust_xrefs.py           # Rust intra/inter-file call edges (Stage 2)
python tests/verify_rust_tests_edges.py     # Rust tests-edges use-analysis + inline #[test] (Stage 3)
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
- [docs/symbol-xrefs-plan.md](docs/symbol-xrefs-plan.md) — proposed
  symbol-level xref edge layer (SCIP / Stack Graphs analogue), broken
  into 10 shippable steps. Design only; no code yet.
