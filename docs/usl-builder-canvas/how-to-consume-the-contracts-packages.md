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
  - tool: "typescript"
    version: "~5.9.3"
  - tool: "ssh-protocol (PROTOCOL_VERSION)"
    version: "1.0.0"
---

# How to Consume the `ssh-protocol` and `workflow-contracts` Packages

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Overview

Two `packages/*` libraries are the shared source of truth across the
process boundary and have zero runtime dependencies on the app:
`@usl-builder/ssh-protocol` (the WebSocket↔SSH wire contract) and
`@usl-builder/workflow-contracts` (integration node types, routes, env keys).
This guide shows how to import and use them from your own client or service.
End state: a typed client that builds protocol messages and resolves an
integration route, both validated by the package's own type guards.

## Audience

Developers writing a client, proxy, or service that must speak the same
protocol/contract as `usl-builder-canvas`.

## Prerequisites

1. The two packages built — verify:
   `ls packages/ssh-protocol/dist packages/workflow-contracts/dist`
   lists compiled output (run `npm run build --prefix packages/ssh-protocol`
   and the same for `workflow-contracts` if missing).
2. TypeScript `~5.9.3` in your consumer — verify: `npx tsc --version`.

## Non-goals

- This guide does not run an SSH connection end to end; see
  [how-to-run-the-ssh-gateway.md](./how-to-run-the-ssh-gateway.md) for that.

## Steps

### Step 1: Import the SSH protocol types and constants

`@usl-builder/ssh-protocol` exports message interfaces, the `ClientMessage` /
`ServerMessage` unions, type guards, and protocol constants. Create
`protocol-client.ts`:

```ts
import {
  type ClientMessage,
  type ServerMessage,
  isStdoutMessage,
  isExitMessage,
  isErrorMessage,
  PROTOCOL_VERSION,
  DEFAULT_CONNECTION_TIMEOUT, // 30000
} from '@usl-builder/ssh-protocol';

console.log('protocol version', PROTOCOL_VERSION); // 1.0.0
```

Verify the import resolves and prints the version:

```bash
npx tsx protocol-client.ts
# protocol version 1.0.0
```

### Step 2: Build a well-typed client message

Each client→server message carries a `connectionId`. The compiler enforces the
discriminated union, so an invalid `type` fails to build.

```ts
const connect: ClientMessage = {
  type: 'connect',
  connectionId: 'conn-1',
  config: {
    host: 'example.internal',
    port: 22,
    username: 'svc',
    authType: 'key',
    privateKeyPath: '/keys/id_ed25519',
  },
};
const run: ClientMessage = { type: 'execute', connectionId: 'conn-1', command: 'uptime' };
```

Verify the union is enforced — this line must NOT compile:

```ts
// @ts-expect-error 'bogus' is not a ClientMessage type
const bad: ClientMessage = { type: 'bogus', connectionId: 'x' };
```

```bash
npx tsc --noEmit protocol-client.ts && echo "types enforced"
```

### Step 3: Narrow server messages with the exported type guards

Use the guards instead of hand-written `msg.type === ...` checks so your code
stays aligned with the contract:

```ts
function handle(msg: ServerMessage): void {
  if (isStdoutMessage(msg)) process.stdout.write(msg.data);
  else if (isErrorMessage(msg)) console.error('gateway error:', msg.error, msg.code);
  else if (isExitMessage(msg)) console.log('exit code', msg.exitCode);
}
```

Verify: feed a sample `stdout` message and confirm it is routed:

```bash
npx tsx -e "import('./protocol-client.ts')" && echo "guards load"
```

### Step 4: Import the integration workflow contract

`@usl-builder/workflow-contracts` is the single source of truth for integration
node types, their HTTP routes, and env-var keys. Create `contracts-demo.ts`:

```ts
import {
  INTEGRATION_NODE_TYPES,          // ['webhook','database','email']
  INTEGRATION_ROUTES,              // { webhook:'/workflow/webhook', ... }
  INTEGRATION_GATEWAY_TARGET_ENV_KEY, // 'INTEGRATION_GATEWAY_TARGET_URL'
  isIntegrationNodeType,
  integrationRouteFor,
  integrationNodeTypeFromPath,
  integrationTargetEnvKey,
  integrationLabel,
} from '@usl-builder/workflow-contracts';
```

### Step 5: Resolve routes and types via the helper functions

These pure functions are the supported API — do not hard-code the route
strings yourself:

```ts
integrationRouteFor('webhook');                 // '/workflow/webhook'
integrationNodeTypeFromPath('/workflow/email'); // 'email'
integrationNodeTypeFromPath('/other');          // undefined
isIntegrationNodeType('database');              // true
integrationTargetEnvKey('database');            // 'INTEGRATION_DATABASE_URL'
integrationLabel('email');                      // 'Email'
```

Run and verify:

```bash
npx tsx -e "import {integrationRouteFor} from '@usl-builder/workflow-contracts'; console.log(integrationRouteFor('webhook'))"
# /workflow/webhook
```

## Verification

End-to-end: a type-check pass plus a runtime resolution prove both contracts
are consumable from outside the app:

```bash
npx tsc --noEmit protocol-client.ts contracts-demo.ts && \
npx tsx -e "import('@usl-builder/ssh-protocol').then(p=>console.log('proto',p.PROTOCOL_VERSION)); import('@usl-builder/workflow-contracts').then(c=>console.log('route',c.integrationRouteFor('email')))"
# proto 1.0.0
# route /workflow/email
```

## Troubleshooting

**Symptom: `Cannot find module '@usl-builder/ssh-protocol'`.** The package is
referenced by `file:` paths within the monorepo (`package.json` shows
`"@usl-builder/workflow-contracts": "file:packages/workflow-contracts"`).
Outside the monorepo, build the package and point your dependency at its
`dist/`, or vendor the source.

**Symptom: type guards accept the wrong message.** You imported a guard but
called it on a `ClientMessage`. The guards are typed for `ServerMessage`; route
client and server messages through separate handlers.

**Symptom: a route string drifts from the gateway.** Never hard-code
`/workflow/...`. Both sides must import `INTEGRATION_ROUTES` /
`integrationRouteFor`. The repo enforces this with contract tests
(`tests/contract/integration-routes-contract.test.ts`); run them after changes:
`npx vitest run tests/contract`.

## Next steps

- [how-to-run-the-ssh-gateway.md](./how-to-run-the-ssh-gateway.md) — the server that consumes these same contracts.
- ← [Building blocks index](./README.md)
