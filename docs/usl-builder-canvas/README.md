---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-06-01"
last_verified: "2026-06-01"
source:
  repo: "usl-builder-canvas"
  commit: "355a43d4143c9df2662bf1ac6fd1b600250a981f"
  derived_from: "code-base-mapper bundle v0.5.0 (generated 2026-06-01T17:16:16Z)"
tool_versions:
  - tool: "node"
    version: ">=18.0.0 (gateway package engines field)"
  - tool: "typescript"
    version: "~5.9.3"
  - tool: "tsx"
    version: "^4.21.0"
  - tool: "vitest"
    version: "^4.0.18"
---

# usl-builder-canvas — Building Blocks & How-To Index

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

> ⚠ **Provenance.** This documentation set was reverse-engineered from a
> read-only `code-base-mapper` bundle of `usl-builder-canvas` at commit
> `355a43d`, not from the project's own authored docs. Every code example
> below is transcribed from source in that bundle. Verify against the live
> repository before relying on any command. Where a fact could not be
> confirmed from source, it is marked **(unverified)**.

---

## Overview

`usl-builder-canvas` is a TypeScript monorepo for building and executing
**node-based workflows** (an AI/integration workflow canvas built on
`@xyflow/react`). It is structured so that **workflow execution is decoupled
from the React editor**: the engine, the CLI, and the SSH gateway all run
without the frontend.

This index decomposes the repository into **independently usable blocks** and
links each to a focused how-to guide.

## The building blocks

| # | Block | Location | Frontend required? | Runtime |
|---|---|---|---|---|
| 1 | **Core workflow engine** (`WorkflowExecutor`) | `src/core/workflow-executor.ts` | No | Node / browser |
| 2 | **Node executors + registry** | `src/core/executors/` | No | Node / browser |
| 3 | **Event bus & logger** (observability) | `src/core/event-bus.ts`, `src/core/logger.ts` | No | Node / browser |
| 4 | **CLI** (`flow`, `execution`, `recipes`, `plugin`, `config`) | `src/cli/` | No | Node (`tsx`) |
| 5 | **SSH gateway** (WebSocket→SSH server) | `gateway/` | No | Node server |
| 6 | **`@usl-builder/ssh-protocol`** (wire contract) | `packages/ssh-protocol/` | No | shared lib |
| 7 | **`@usl-builder/workflow-contracts`** (integration contract) | `packages/workflow-contracts/` | No | shared lib |
| 8 | **React canvas editor** (consumer of 1–7) | `src/components/`, `src/App.tsx` | — | browser (Vite) |

Blocks 1–7 have **zero React imports** in their execution paths. Block 8 is a
*consumer* of the others, not a dependency of them.

### How the blocks relate

```
                 ┌─────────────────────────────┐
                 │  React canvas editor (8)     │  browser only
                 └──────────────┬──────────────┘
                                │ drives
   CLI (4) ─────┐               │               ┌──── ssh-protocol (6)
                ▼               ▼               ▼
        ┌───────────────────────────────────────────┐
        │  Core workflow engine — WorkflowExecutor (1)│
        │  topological sort · retries · checkpoints   │
        └───────┬───────────────────────┬────────────┘
                │ uses                   │ emits
                ▼                        ▼
   Node executors + registry (2)   Event bus + logger (3)
                │ integration nodes POST to
                ▼
        SSH gateway (5)  ◄── speaks ──  ssh-protocol (6)
                ▲
   workflow-contracts (7): routes /workflow/{webhook,database,email}
```

## Which guide do I want?

| I want to… | Guide |
|---|---|
| Run a workflow from Node code, no UI | [how-to-run-a-workflow-headless.md](./how-to-run-a-workflow-headless.md) |
| Add a new node type the engine can execute | [how-to-add-a-custom-node-executor.md](./how-to-add-a-custom-node-executor.md) |
| Drive workflows from the terminal / scripts | [how-to-drive-the-cli.md](./how-to-drive-the-cli.md) |
| Stand up the WebSocket→SSH gateway | [how-to-run-the-ssh-gateway.md](./how-to-run-the-ssh-gateway.md) |
| Reuse the shared contract packages in another service | [how-to-consume-the-contracts-packages.md](./how-to-consume-the-contracts-packages.md) |

## Verified API surface (quick reference)

| Symbol | Signature (transcribed from source) | File |
|---|---|---|
| `WorkflowExecutor` | `new WorkflowExecutor(eventBus, logger)` · `execute(workflow, input) → Promise<{ status: 'completed' \| 'failed'; error? }>` | `src/core/workflow-executor.ts` |
| `EventBus` | `new EventBus({ maxHistory?, delivery?: 'sync'\|'async', clock? })` · `on/onAny/emit/getHistory/replay/onDeadLetter` | `src/core/event-bus.ts` |
| `ConsoleLogger` | `new ConsoleLogger({ maxEntries?, clock? })` · `info/success/warning/error/debug(source, message, data?)` | `src/core/logger.ts` |
| `createDefaultRegistry()` | `→ NodeExecutorRegistry` (8 core + 24 AI executors) | `src/core/executors/node-executor-registry.ts` |
| `NodeExecutor` (interface) | `{ nodeType; execute(node, inputs, ctx); validate(node) }` | `src/core/executors/node-executor.interface.ts` |
| `SSHGatewayServer` | `new SSHGatewayServer({ port, host, maxConnections })` · `start()` · `stop()` | `gateway/src/server.ts` |
| `INTEGRATION_ROUTES` | `{ webhook, database, email }` under `/workflow/*` | `packages/workflow-contracts/src/index.ts` |

> **Caveat.** The `WorkflowExecutor` constructor and `execute()` return shape
> were confirmed from their **call site** in
> `src/services/production-workflow-executor.ts`, not from the class
> declaration directly (the 28 KB source exceeds the bundle's blob read
> limit). Fields beyond `status`/`error` (e.g. blackboard snapshots) exist but
> their exact names are **(unverified)** here — read `src/core/workflow-executor.ts`.

## Architectural notes (read before headless use)

1. **`ProductionWorkflowExecutor` is store-coupled, not a clean headless API.**
   Its `execute()` takes **no arguments** and pulls `nodes`/`edges` from a
   Zustand store (`useWorkflowStore`). For headless use, call the **core**
   `WorkflowExecutor` directly (Guide 1) or use the **CLI** (Guide 4).
2. **Integration nodes need the gateway, never the UI.** `webhook`/`database`/
   `email` nodes POST to `INTEGRATION_GATEWAY_TARGET_URL` (Guide 5), resolved
   via `@usl-builder/workflow-contracts`.
3. **The engine is not yet a published package.** It lives under `src/core`,
   so headless embedding imports from `src/core`, not from an `@usl-builder/engine`.

## Up one level

← Back to repository root [README.md](../../README.md)
