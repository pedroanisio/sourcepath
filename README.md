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
└── concept_graph/      identifier splitting + canonical concept set + SKOS
scripts/
├── run_l2.py           host + chunks_embeddings registered
└── run_l3.py           host + chunks_embeddings + concept_graph (--no-l2 skips L2)
frontend/
├── backend/            FastAPI service that reads an output bundle and serves
│                       summary/graph/chunk/concept JSON to the UI
└── ui/                 React UI (scaffold, in progress)
tests/
├── verify_l2.py        chunks_embeddings contract suite
└── verify_l3.py        concept_graph contract + cross-layer (with/without L2)
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
```

`--backend sbert` uses `sentence-transformers/all-MiniLM-L6-v2`; `--backend hash`
uses a deterministic SHA-256 fake (no semantics, useful for contract tests).

### Excluding files

Per-invocation: `--exclude PATTERN` (repeatable; POSIX-glob; bare names like
`.repo` also exclude descendants). Available on the host CLI and on
`scripts/run_l2.py` / `scripts/run_l3.py`.

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

## Verify

```bash
python tests/verify_roundtrip.py            # blob-based byte-perfect roundtrip
python tests/verify_regenerate.py           # TTL+AST regenerate (Python semantic + TS/JS byte)
python tests/verify_excludes.py             # --exclude flag + .cbmignore behavior
python tests/verify_timestamps.py           # atime/mtime/ctime + gitCommitTime
python tests/verify_l2.py --backend hash    # chunks_embeddings contract
python tests/verify_l3.py                   # concept_graph contract + cross-layer
```

All six verifiers resolve `repo_root` from their own `__file__`, so they
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
