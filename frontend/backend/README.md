# frontend/backend

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

FastAPI service that reads a codebase-mapper output bundle and exposes
graph/chunk/concept JSON endpoints to the React UI.

## Run

```bash
# from repo root, with the same .venv that has rdflib/numpy/sentence-transformers
CBM_OUTPUT_DIR=_tmp/usl-ng-core-map \
  .venv/bin/uvicorn frontend.backend.app:app --port 8000 --reload
```

`CBM_OUTPUT_DIR` defaults to `_tmp/usl-ng-core-map`. Point it at any directory
containing `run_manifest.json`, `inventory.ttl`, `embeddings.npz`,
`embeddings_meta.json`, `concepts.json`, and (optionally) `concepts_embeddings.npz`.

## Endpoints

| Path | Purpose |
|---|---|
| `GET /api/bundles` | List discoverable bundles and the selected bundle |
| `GET /api/summary` | Manifest counts, language/type breakdown, embedding backend |
| `GET /api/file-graph?limit=N` | Top-N files by import-degree + their import edges |
| `GET /api/symbol-graph?limit=N&kind=K` | Symbol-level xref graph |
| `GET /api/concept-graph?limit=N&min_edge=K` | Top-N concepts by frequency + cooccurrence edges with weight ≥ K |
| `GET /api/chunks?q=&limit=&offset=` | Browse/lexically search chunks |
| `POST /api/chunks/search` `{q, k}` | Semantic nearest neighbors (sbert backend) or lexical fallback (hash backend) |
| `GET /api/concept/{name}` | Concept detail + the files that lexicalize it |
| `GET /api/chunk-blob/{sha}` | Raw text of a chunk's content blob (truncated to 20 KB) |
| `GET /api/file/{path}` | File detail + imports/tests/chunks/concepts/xrefs |
| `GET /api/impact/{path}` | Transitive impact graph for a file |
| `GET /api/chunk/{idx}` | Chunk detail |
| `GET /api/healthz` | Health check |

## Notes

- Semantic search only does cosine NN if the embeddings backend reports a
  sentence-transformer model. Hash-backend runs fall back to substring
  matching on chunk symbols/paths.
- The first call loads the bundle into memory and is cached for the process
  lifetime. Restart the server to pick up a new output bundle.
