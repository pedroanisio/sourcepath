# cbm-mcp — MCP server for codebase-mapper bundles

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

Read-only MCP server that exposes a codebase-mapper output bundle as
tools, resources, and prompts. Same handlers, two transports (stdio
and streamable HTTP); pluggable auth (pre-shared key or JWT OAuth);
real-time push notifications when a bundle is re-generated.

## Run

```bash
# stdio (Claude Code, local agents)
python -m frontend.mcp_server

# HTTP (remote agents, OAuth-protected)
CBM_MCP_TOKEN=secret \
  uvicorn frontend.backend.app:app --port 8000
# clients hit POST http://host/mcp/  with  Authorization: Bearer secret
```

## Tool surface (18 tools)

All read-only. Discoverable via `tools/list`; the description for each
includes a "when to use" hint.

| Tool | Purpose |
|---|---|
| `orient_bundle` | First call after connecting. Bundle metadata + layer cheat sheet + suggested first-five-calls. |
| `bundle_summary` | Manifest counts, language/type histogram, embeddings backend. |
| `repository_summary` | Repository-level summary, key files/concepts, and dependency/test coverage hint. |
| `list_bundles` / `select_bundle` | Multi-bundle session control. |
| `list_files` | Filter files by language / type / dir prefix; ranked by import-degree. |
| `items_by_attribute` | Attribute-index lookup over files/chunks/concepts. |
| `file_detail` | Path → metadata + imports both ways + tests + chunks + concepts. |
| `file_impact` | Transitive dependency closure for a file up to `depth` hops. |
| `imports_of` / `imported_by` | One-hop slices. Cheap. |
| `chunk_detail` / `chunk_blob` / `list_chunks` | Chunk navigation. |
| `semantic_neighbors` | Cosine NN if the bundle has real vectors (sbert or `ollama:`); lexical fallback otherwise. |
| `concept_detail` | SKOS concept: frequency, alt-labels, cooccurrence, files, chunks. |
| `concept_neighborhood` | k-hop cooccurrence walk (bounded `depth ≤ 3`). |
| `sparql` | Read-only SPARQL escape hatch, disabled unless `CBM_ENABLE_SPARQL=1`. |

## Resources (`resources/list` + `resources/read`)

URI scheme: **`cbm://...`**. Path-traversal and alien schemes are
rejected before any handler runs.

```
cbm://bundles                                  static  application/json
cbm://bundle/<name>/manifest                   static  application/json   subscribable
cbm://bundle/<name>/summary                    static  application/json
cbm://bundle/<name>/shapes.shacl.ttl           static  text/turtle
cbm://bundle/<name>/ontology-mapping.ttl       static  text/turtle
cbm://bundle/<name>/file/<path>                template
cbm://bundle/<name>/chunk/<idx>                template
cbm://bundle/<name>/concept/<name>             template
```

Per-file/chunk/concept URIs are template-only — enumerating thousands
in `resources/list` would blow the response budget, so they're
advertised via `resources/templates/list` instead.

## Prompts

Three workflow templates. Each renders to a single user message
referencing real tool names (a freshness test asserts every
identifier in the body maps to an existing tool).

| Name | Arg | Purpose |
|---|---|---|
| `orient` | optional `bundle` | First-five-calls exploration plan. |
| `explore_concept` | `concept` (req), `bundle`, `depth` | Deep-dive on a single SKOS concept. |
| `trace_dependency` | `path` (req), `bundle`, `depth` | File impact + tests. |

## Subscription

Only `cbm://bundle/<name>/manifest` URIs are subscribable. A
poll-based filesystem watcher (default 30 s, override with
`CBM_WATCH_INTERVAL`) scans `$CBM_BUNDLES_ROOT/*/run_manifest.json`
mtimes. On change:

1. Bundle cache is invalidated (`get_bundle.cache_clear()`).
2. Every session subscribed to that manifest URI receives a
   `notifications/resources/updated` push.

## Transports

### stdio (Phase 3)

```
python -m frontend.mcp_server
```

* JSON-RPC over stdin/stdout.
* Stdout is reserved for protocol frames — every logger writes to
  stderr (a CI test asserts this stays true).
* Manifest watcher runs in a sibling asyncio task; cancellation on
  client disconnect.

### Streamable HTTP (Phases 8 + 9)

Mounted on the existing FastAPI app at `/mcp/` when `CBM_MCP_TOKEN`
or `CBM_MCP_JWT_AUDIENCE` is set. Auth is enforced via an ASGI
middleware that wraps the raw MCP endpoint (the session manager
writes its own response, so wrapping with a FastAPI route would
double-send).

The middleware maps `AuthError` to RFC 6750 status codes:

| Situation | Status | `WWW-Authenticate` |
|---|---|---|
| No `Authorization` header | **401** | `Bearer realm="cbm-mcp"` |
| Non-Bearer scheme | **400** | `Bearer error="invalid_request"` |
| Wrong/expired/bad-sig token | **401** | `Bearer error="invalid_token", error_description="…"` |
| Wrong audience or issuer | **401** | `Bearer error="invalid_token"` |
| Token valid, scope missing | **403** | `Bearer error="insufficient_scope", scope="bundle:read"` |

## Auth modes

### Static bearer key (Phase 8)

```bash
export CBM_MCP_TOKEN=$(openssl rand -hex 32)
```

The token is compared with `hmac.compare_digest` (constant-time).
Suitable for internal deployments where issuing JWTs is overkill.

### JWT bearer (Phase 9)

```bash
export CBM_MCP_JWT_AUDIENCE="urn:cbm:mcp"
export CBM_MCP_JWT_ISSUER="https://issuer.example/"           # optional
export CBM_MCP_JWT_PUBLIC_KEY="$(cat ./jwt-pub.pem)"           # OR ↓
export CBM_MCP_JWT_JWKS_URI="https://issuer.example/.well-known/jwks.json"
export CBM_MCP_REQUIRED_SCOPE="bundle:read"                    # default
export CBM_MCP_JWT_ALGORITHMS="RS256,ES256"                    # default
```

Token claims required:
* `aud` matches `CBM_MCP_JWT_AUDIENCE`
* `exp` is in the future (30 s leeway)
* `iss` matches `CBM_MCP_JWT_ISSUER` (only when set)
* `scope` (space-string) or `scp` (list) contains the required scope

No token passthrough: the verifier never accepts tokens issued for a
different audience, even if the signature is valid.

## Hardening (Phase 10)

### Per-tool timeouts

Every tool call runs in a worker thread under
`anyio.fail_after(timeout)`. Sync handlers can't be interrupted, so on
budget overrun the wait is cancelled and the worker thread is
abandoned (`abandon_on_cancel=True`). The client receives a prompt
timeout error; the orphan thread eventually completes and is GC'd.

| Tool | Budget |
|---|---|
| (default) | 5 s |
| `semantic_neighbors` | 10 s (sbert loads ~100 MB on first call; an `ollama:` bundle spends one query-embed round-trip, capped at 6 s so it degrades to lexical inside the budget) |

Override per tool via env: `CBM_MCP_TIMEOUT_<TOOL_NAME>=2.5`.

### Audit log

One JSON line per tool invocation on the dedicated `cbm-mcp.audit`
logger:

```json
{
  "ts": "2026-05-13T17:42:01.123Z",
  "transport": "stdio",
  "tool": "file_detail",
  "args_digest": "9c3f8e21a1b0",
  "latency_ms": 4.31,
  "status": "ok"
}
```

* `args_digest` is the first 12 hex chars of `sha256(json.dumps(args, sort_keys=True))` — protects against accidentally leaking long paths or PII into the audit trail.
* `status` ∈ `ok` | `error` | `timeout`. Errors include the `code: message` pair on the `error` field.
* Default sink: stderr (so stdio's stdout stays JSON-RPC-clean).
* Optional rotating file: set `CBM_MCP_AUDIT_LOG_PATH=/var/log/cbm-mcp-audit.log` (10 MB × 5 rotations).

## Configuration reference

| Env var | Default | Purpose |
|---|---|---|
| `CBM_OUTPUT_DIR` | — | Single-bundle override. Highest precedence. |
| `CBM_BUNDLES_ROOT` | `_tmp` | Directory under which multiple bundles live. |
| `CBM_WATCH_INTERVAL` | `30` | Seconds between manifest mtime polls. |
| `CBM_MCP_TOKEN` | — | Pre-shared bearer key (Phase 8). |
| `CBM_MCP_JWT_AUDIENCE` | — | Token's `aud` claim must match. |
| `CBM_MCP_JWT_PUBLIC_KEY` | — | PEM public key for signature verification. |
| `CBM_MCP_JWT_JWKS_URI` | — | JWKS endpoint (alternative to PEM). |
| `CBM_MCP_JWT_ISSUER` | — | Optional `iss` claim check. |
| `CBM_MCP_JWT_ALGORITHMS` | `RS256,ES256` | Comma-separated allowed algorithms. |
| `CBM_MCP_REQUIRED_SCOPE` | `bundle:read` | Required token scope. |
| `CBM_MCP_TIMEOUT_<TOOL>` | per-table | Per-tool budget override (seconds). |
| `CBM_MCP_AUDIT_LOG_PATH` | — | Path to rotating audit log file (also goes to stderr). |

## Tests

```bash
.venv/bin/python -m pytest frontend/mcp_server/tests/
```

357 cases across schemas, handlers, transport (stdio + HTTP),
resources, prompts, subscriptions, OAuth, hardening, and the SPARQL
escape hatch. Gated at **≥90% coverage** by [pytest.ini](pytest.ini).

## Security model summary

* **Read-only by design.** Zero write tools. The `no_write_verbs`
  schema test prevents accidents.
* **Server-side input validation.** Every tool re-validates inputs
  with both JSON Schema (transport layer) and a domain validator
  (handler layer) — defense in depth.
* **Path-traversal guards** on bundle names, file paths, concept
  names. Re-used between the tool surface and the resource URI
  parser.
* **Output sanitization.** Paths returned to the client are
  bundle-relative; blob previews capped at 2 KB on `chunk_detail`
  (vs. 20 KB for the explicit `chunk_blob`).
* **Stdout-only-JSON guarantee** on stdio. Logging is forced onto
  stderr at startup; a subprocess test asserts this.
* **OAuth audience binding.** Tokens issued for other services are
  rejected — no token passthrough.
* **Constant-time secret compare** on the static bearer path.
* **Stable error codes** (`not_found`, `invalid_argument`,
  `insufficient_scope`, `timeout`, …) so clients can branch without
  string-matching the description.
