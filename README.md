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
python tests/verify_l2.py --backend hash    # chunks_embeddings contract
python tests/verify_l3.py                   # concept_graph contract + cross-layer
```

Both verifiers resolve `repo_root` from their own `__file__`, so they run
correctly regardless of the caller's cwd.
