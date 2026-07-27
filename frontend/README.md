# frontend

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

React + FastAPI visualizer for codebase-mapper output bundles in `_tmp/`.

## Layout

```
frontend/
├── backend/        FastAPI service (reads inventory.ttl + embeddings + concepts.json)
│   └── tests/      pytest suite over /api/*  (driven through fastapi.testclient)
└── ui/             Vite + React + TS + cytoscape SPA
    └── src/__tests__/  vitest + RTL smoke tests with mocked fetch
```

## Tests

```bash
# backend: fastapi.testclient suite, coverage-gated by pytest.ini
.venv/bin/python -m pytest frontend/backend/tests/

# mcp server: schemas, handlers, stdio + HTTP transport, resources, prompts,
# subscriptions, OAuth, hardening, SPARQL; coverage-gated by pytest.ini
.venv/bin/python -m pytest frontend/mcp_server/tests/

# ui: vitest + RTL with mocked /api/*; coverage-gated by package config
cd frontend/ui && npm test -- --coverage
```

Coverage thresholds live in each test configuration. Avoid hand-maintaining
test counts in this README; use the test runner output as the source of truth.

The backend tests auto-skip if `_tmp/usl-ng-core-map/run_manifest.json` is
missing (override the path with `CBM_OUTPUT_DIR=...`). Thresholds are
configured in `frontend/backend/pytest.ini` and `frontend/ui/vite.config.ts`.

## Docker (recommended for delivery)

```bash
cd frontend
cp .env.example .env            # edit CBM_BUNDLE to point at your bundle
docker compose up --build       # http://localhost:8080
```

`CBM_BUNDLE` is bind-mounted read-only at `/data` in the backend container.
The backend exposes nothing on the host; nginx in the frontend container
proxies `/api/*` to `backend:8765` over the internal compose network.

Knobs (all optional, see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `FRONTEND_PORT` | `8080` | Host port for the UI |
| `CBM_BUNDLE` | `../_tmp/usl-ng-core-map` | Path to the bundle to visualize |
| `WITH_SBERT` | `0` | Build with sentence-transformers (real semantic search; +~2 GB) |
| `TAG` | `latest` | Image tag for `cbm-backend` and `cbm-frontend` |

To switch to a different bundle without rebuilding:

```bash
CBM_BUNDLE=/abs/path/to/another-run docker compose up
```

To rebuild with semantic search enabled:

```bash
WITH_SBERT=1 docker compose build backend && docker compose up
```

## One-shot dev (two terminals)

```bash
# terminal 1: backend
# The API fails closed: pass CBM_ALLOW_ANONYMOUS=1 for local dev, or set
# CBM_API_TOKEN=<secret> and send `Authorization: Bearer <secret>`.
# uvicorn binds 127.0.0.1 by default — add --host only if you mean to expose it.
CBM_OUTPUT_DIR=_tmp/usl-ng-core-map CBM_ALLOW_ANONYMOUS=1 \
  .venv/bin/uvicorn frontend.backend.app:app --port 8765 --reload

# terminal 2: ui
cd frontend/ui
npm install
npm run dev
# open the URL vite prints (http://localhost:5173)
```

The vite dev server proxies `/api/*` to `127.0.0.1:8765`.

## Pointing at a different output

The backend reads `CBM_OUTPUT_DIR` (default `_tmp/usl-ng-core-map`). Set it
to any directory containing a codebase-mapper output bundle and restart.

## Views

| Path | What it shows |
|---|---|
| `/dashboard` | repo + run metadata, counts, language/type histograms, SHACL status |
| `/files` | top-N files by import degree, force-directed; colored by language |
| `/concepts` | top-N concepts by frequency with skos:related cooccurrence edges |
| `/chunks` | search chunks; semantic NN if the bundle carries real vectors, lexical otherwise |

## Notes

- For real semantic search, regenerate the bundle with `--backend sbert`
  or `--backend ollama`. Hash-backend bundles fall back to substring
  matching on chunk symbols, as does an `ollama:` bundle whose server is
  unreachable — the response's `mode` field always says which path answered.
- The cytoscape `cose` layout struggles past ~1500 nodes; the file graph
  is server-side ranked by import degree before truncation.
