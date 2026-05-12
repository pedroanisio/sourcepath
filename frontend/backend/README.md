# frontend/backend

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
| `GET /api/summary` | Manifest counts, language/type breakdown, embedding backend |
| `GET /api/file-graph?limit=N` | Top-N files by import-degree + their import edges |
| `GET /api/concept-graph?limit=N&min_edge=K` | Top-N concepts by frequency + cooccurrence edges with weight ≥ K |
| `GET /api/chunks?q=&limit=&offset=` | Browse/lexically search chunks |
| `POST /api/chunks/search` `{q, k}` | Semantic nearest neighbors (sbert backend) or lexical fallback (hash backend) |
| `GET /api/concept/{name}` | Concept detail + the files that lexicalize it |
| `GET /api/chunk-blob/{sha}` | Raw text of a chunk's content blob (truncated to 20 KB) |

## Notes

- Semantic search only does cosine NN if the embeddings backend reports a
  sentence-transformer model. Hash-backend runs fall back to substring
  matching on chunk symbols/paths.
- The first call loads the bundle into memory and is cached for the process
  lifetime. Restart the server to pick up a new output bundle.
