---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "GPT-5 Codex"
  date: "2026-05-22"
---

# Installing the cbm-mcp Server

The `cbm-mcp` server exposes a codebase-mapper output bundle to MCP clients
(Claude Code, Claude Desktop, remote agents) as read-only tools, resources, and
prompts. This guide covers installing it and wiring it into a client over either
**stdio** (local) or **streamable HTTP** (remote).

For the full tool/resource/prompt surface and security model, see
[frontend/mcp_server/README.md](../frontend/mcp_server/README.md).

---

## 1. Prerequisites

- Python ≥ 3.10
- `git`
- A codebase-mapper output bundle on disk (a directory produced by
  `codebase-mapper`, containing `run_manifest.json`). See
  [docs/analyze.md](analyze.md) if you need to generate one first.
- For HTTP transport: `uvicorn` (installed transitively below).

---

## 2. Install

### 2a. Clone and create a virtualenv

```bash
git clone <this-repo-url> code-base-mapper
cd code-base-mapper
python3 -m venv .venv
source .venv/bin/activate
```

### 2b. Install the package

```bash
pip install -e .
# HTTP backend + MCP server deps
pip install -r frontend/backend/requirements.txt
pip install -r frontend/mcp_server/requirements.txt
```

Verify the module loads:

```bash
python3 -c "import frontend.mcp_server; print('frontend.mcp_server ok')"
```

---

## 3. Point the server at a bundle

The server needs to know where bundles live. Pick one of:

```bash
# Single bundle (highest precedence)
export CBM_OUTPUT_DIR=/abs/path/to/one-bundle

# OR multiple bundles under a root (default: ./_tmp)
export CBM_BUNDLES_ROOT=/abs/path/to/bundles-root
```

`CBM_BUNDLES_ROOT` is scanned for child directories that contain a
`run_manifest.json`. When set, `list_bundles` / `select_bundle` become useful.

Optional:

```bash
export CBM_WATCH_INTERVAL=30   # seconds; manifest-mtime poll for push updates
```

---

## 4. Run the server

### Option A — stdio (recommended for Claude Code / Desktop)

```bash
python3 -m frontend.mcp_server
```

JSON-RPC over stdin/stdout. Logs go to stderr; stdout is reserved for protocol
frames.

### Option B — streamable HTTP (remote clients, OAuth-protected)

Pre-shared bearer key:

```bash
export CBM_MCP_TOKEN=$(openssl rand -hex 32)
uvicorn frontend.backend.app:app --host 0.0.0.0 --port 8000
# clients POST http://host:8000/mcp/  with  Authorization: Bearer <token>
```

JWT bearer (production):

```bash
export CBM_MCP_JWT_AUDIENCE="urn:cbm:mcp"
export CBM_MCP_JWT_ISSUER="https://issuer.example/"
export CBM_MCP_JWT_JWKS_URI="https://issuer.example/.well-known/jwks.json"
export CBM_MCP_REQUIRED_SCOPE="bundle:read"
uvicorn frontend.backend.app:app --host 0.0.0.0 --port 8000
```

The HTTP `/mcp/` endpoint is only mounted when one of `CBM_MCP_TOKEN` or
`CBM_MCP_JWT_AUDIENCE` is set.

---

## 5. Register with a client

### Claude Code (stdio)

Add to `~/.claude.json` (or your project `.mcp.json`) under `mcpServers`:

```json
{
  "mcpServers": {
    "cbm": {
      "command": "/abs/path/to/code-base-mapper/.venv/bin/python",
      "args": ["-m", "frontend.mcp_server"],
      "cwd": "/abs/path/to/code-base-mapper",
      "env": {
        "CBM_BUNDLES_ROOT": "/abs/path/to/bundles-root"
      }
    }
  }
}
```

Restart Claude Code. Confirm the server is connected via `/mcp` in the prompt.

### Claude Desktop (stdio)

Same shape as above, written to
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows).

### Remote agent (HTTP)

Point the client at `http(s)://host:8000/mcp/` and supply the bearer token in
the `Authorization` header. See the
[transport table](../frontend/mcp_server/README.md#streamable-http-phases-8--9)
for the exact 401/403 response shapes.

---

## 6. Smoke test

After connecting, call `orient_bundle` first. It returns bundle metadata, a
layer cheat sheet, and a suggested first-five-calls plan. If that succeeds the
install is healthy.

From a shell, you can also run the test suite to confirm the install:

```bash
.venv/bin/python -m pytest frontend/mcp_server/tests/
```

---

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `No bundles found` on `list_bundles` | `CBM_BUNDLES_ROOT` unset or directory has no `run_manifest.json` children. |
| Client hangs on stdio | Something is printing to stdout (e.g. a `print()` in a custom plugin). Stdout must stay JSON-RPC-clean — write to stderr instead. |
| HTTP `401 Bearer realm="cbm-mcp"` | Missing `Authorization` header. |
| HTTP `401 invalid_token` | Token expired, signature bad, or audience/issuer mismatch. |
| HTTP `403 insufficient_scope` | Token valid but missing `bundle:read` (or whatever `CBM_MCP_REQUIRED_SCOPE` is set to). |
| Tool returns `timeout` | Per-tool budget exceeded. Override with `CBM_MCP_TIMEOUT_<TOOL_NAME>=<seconds>`. |
| `semantic_neighbors` slow first call | sbert model loads ~100 MB on first use; default budget for this tool is 10 s. An `ollama:` bundle instead pays one query-embed round-trip (capped at 6 s, then lexical fallback). |

For the full env-var reference and security model, see
[frontend/mcp_server/README.md](../frontend/mcp_server/README.md).
