---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
    Verify all steps against your own environment before relying on them.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-06-01"
last_verified: "2026-06-01"
source:
  repo: "usl-builder-canvas"
  commit: "355a43d4143c9df2662bf1ac6fd1b600250a981f"
tool_versions:
  - tool: "node"
    version: ">=18.0.0"
  - tool: "ssh2"
    version: "^1.17.0"
  - tool: "ws"
    version: "^8.18.0"
  - tool: "ssh-protocol (PROTOCOL_VERSION)"
    version: "1.0.0"
---

# How to Run the WebSocket→SSH Gateway

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Overview

The `gateway/` package is a standalone Node server (`usl-builder-ssh-gateway`)
that bridges WebSocket clients to SSH connections, per **ADR-037**. It runs as
its own process with no React dependency. End state: a running gateway you can
reach over `ws://` and a verified startup banner.

## Audience

Operators or backend developers who need workflow `ssh-tool` and integration
nodes to reach real hosts, and who understand the security implications of an
SSH gateway.

## Prerequisites

1. Node ≥ 18 — verify: `node --version` prints `v18.` or higher (the gateway
   `package.json` declares `"engines": { "node": ">=18.0.0" }`).
2. Gateway dependencies installed — verify from repo root:
   `ls gateway/node_modules/ssh2 gateway/node_modules/ws` lists both.
3. The sibling packages build, because the gateway's `build` script compiles
   `packages/ssh-protocol` and `packages/workflow-contracts` first — verify:
   `npm run type-check --prefix gateway` exits `0`.

## Non-goals

- This guide does not configure TLS (`wss://`), rate limiting, or IP
  whitelisting. The gateway's own startup banner warns these are required for
  production; configuring them is out of scope here.

## Steps

### Step 1: Choose the configuration via environment variables

The entry point `gateway/src/index.ts` reads four variables, each with a
default (values transcribed from source):

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8081` | TCP port to listen on |
| `HOST` | `0.0.0.0` | bind address |
| `MAX_CONNECTIONS` | `100` | max concurrent connections |
| `NODE_ENV` | `development` | selects the security-warning banner |

Set only what you need to override. For a local run on port 9090:

```bash
export PORT=9090
export NODE_ENV=development
```

Verify the variables are set:

```bash
echo "PORT=$PORT NODE_ENV=$NODE_ENV"
# PORT=9090 NODE_ENV=development
```

### Step 2: Start the gateway in development

Run directly from TypeScript with the package's `dev` script (it watches and
rebuilds `workflow-contracts` first):

```bash
npm run dev --prefix gateway
```

Expected output:

```
SSH Gateway server running on 0.0.0.0:9090
   Environment: development
   Max connections: 100

⚠️  Security Warning:
   - Development mode: using ws:// (unencrypted)
   - Do NOT use in production without TLS
```

Seeing `SSH Gateway server running on …` confirms the server bound the port.

### Step 3: Confirm the port is listening

In a second terminal:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9090
# 426    (Upgrade Required — the server speaks WebSocket, not plain HTTP)
```

Any response (including `426`) confirms the port is open. Connection refused
means the server is not listening — return to Step 2.

### Step 4: Drive it with a WebSocket client (protocol smoke test)

The wire contract is `@usl-builder/ssh-protocol`. A client sends a
`ConnectMessage` then `ExecuteMessage`; the server replies with
`connected` → `stdout`/`stderr` → `exit`. Minimal client:

```ts
import WebSocket from 'ws';
import type { ClientMessage } from '@usl-builder/ssh-protocol';

const ws = new WebSocket('ws://localhost:9090');
ws.on('open', () => {
  const connect: ClientMessage = {
    type: 'connect',
    connectionId: 'c1',
    config: { host: '127.0.0.1', port: 22, username: 'you', authType: 'agent' },
  };
  ws.send(JSON.stringify(connect));
});
ws.on('message', (raw) => console.log('server:', raw.toString()));
```

Run it:

```bash
npx tsx gateway-smoke.ts
# server: {"type":"connected","connectionId":"c1"}   (when SSH auth succeeds)
```

A `connected` message confirms the full WebSocket→SSH path. An `error` message
means the SSH leg failed (see Troubleshooting).

### Step 5: Build and run for production

For a long-running deployment, compile and run the built output:

```bash
npm run build --prefix gateway && npm run start --prefix gateway
# (build compiles ssh-protocol + workflow-contracts + tsc, then: node dist/index.js)
```

> ⚠ **Production safety (from the gateway's own banner).** With
> `NODE_ENV=production` the server prints that you must use `wss://` (TLS),
> enable rate limiting and IP whitelisting, and prefer SSH-agent auth. This
> guide does not configure those — do not expose the gateway publicly until you
> have.

## Verification

End-to-end: start, confirm the banner, confirm the port, then stop cleanly.

```bash
PORT=9090 npm run dev --prefix gateway &
GW=$!
sleep 3
curl -s -o /dev/null -w "port responds: %{http_code}\n" http://localhost:9090
kill -TERM $GW   # triggers graceful shutdown
```

Expected:

```
SSH Gateway server running on 0.0.0.0:9090
port responds: 426
📡 Received SIGTERM, shutting down gracefully...
✅ Server stopped
```

The `SIGTERM` → `Server stopped` sequence confirms the graceful-shutdown
handler in `gateway/src/index.ts`.

## Troubleshooting

**Symptom: `EADDRINUSE: address already in use :::9090`.** Another process
holds the port. Find it (`lsof -i :9090`) and stop it, or pick a different
`PORT`.

**Symptom: server starts but client gets `{"type":"error", ...}`.** The
WebSocket reached the gateway but the SSH connection failed — wrong host/port,
auth rejected, or no SSH agent. Check the `error` message's `error`/`code`
fields (defined in `ssh-protocol`), and confirm `ssh you@host` works manually
from the gateway host.

**Symptom: client cannot import `@usl-builder/ssh-protocol`.** The package was
not built. Run `npm run build --prefix packages/ssh-protocol` (the gateway
`build` script does this automatically; a hand-run client does not).

**Symptom: integration workflow nodes still fail with the gateway running.**
The engine reaches the gateway via `INTEGRATION_GATEWAY_TARGET_URL`, not the
raw WebSocket port. Confirm that variable points at the gateway and that the
route prefix `/workflow` matches `@usl-builder/workflow-contracts`
(`INTEGRATION_ROUTES`).

## Next steps

- [how-to-consume-the-contracts-packages.md](./how-to-consume-the-contracts-packages.md) — reuse `ssh-protocol`/`workflow-contracts` in your own client.
- ← [Building blocks index](./README.md)
