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
  - tool: "typescript"
    version: "~5.9.3"
  - tool: "tsx"
    version: "^4.21.0"
---

# How to Run a Workflow Headlessly with the Core `WorkflowExecutor`

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Overview

This guide shows how to execute a workflow graph from a Node script with **no
React frontend and no Zustand store** — by instantiating the core
`WorkflowExecutor` directly. The end state: a script that runs a two-node
workflow and prints the result `status`.

## Audience

TypeScript developers who can already build and run the `usl-builder-canvas`
repo (`npm install`, `npm run test` pass) and want to embed workflow execution
in a service, script, or test.

## Prerequisites

1. The repo cloned at commit `355a43d` and dependencies installed —
   verify: `npm ls @xyflow/react` returns without `UNMET DEPENDENCY`.
2. Node ≥ 18 — verify: `node --version` prints `v18.` or higher.
3. `tsx` available to run TypeScript directly — verify:
   `npx tsx --version` prints a `4.x` version.
4. You have read the **(unverified)** caveat on `execute()`'s return shape in
   [README.md](./README.md#verified-api-surface-quick-reference).

## Non-goals

- This guide does **not** cover integration nodes (`webhook`/`database`/
  `email`) — those require the SSH gateway; see
  [how-to-run-the-ssh-gateway.md](./how-to-run-the-ssh-gateway.md).
- It does **not** cover defining new node types; see
  [how-to-add-a-custom-node-executor.md](./how-to-add-a-custom-node-executor.md).

## Steps

### Step 1: Create the script file

Create `scripts/run-headless.ts` at the repo root. This is the file you will
fill in across the next steps.

```bash
touch scripts/run-headless.ts
```

Verify the file exists:

```bash
ls scripts/run-headless.ts
# scripts/run-headless.ts
```

### Step 2: Construct the engine dependencies

The engine needs an `EventBus` and a `ConsoleLogger`. Both are plain classes
with zero UI dependencies. Add to `scripts/run-headless.ts`:

```ts
import { EventBus } from '@/core/event-bus';
import { ConsoleLogger } from '@/core/logger';
import { WorkflowExecutor } from '@/core/workflow-executor';
import type { Workflow } from '@/types/workflow';

const eventBus = new EventBus({ delivery: 'sync' });
const logger = new ConsoleLogger();
const executor = new WorkflowExecutor(eventBus, logger);
```

The `@/` alias maps to `src/` in this repo (Vite/tsconfig path alias). If your
runner does not resolve `@/`, use a relative import (`../src/core/...`).

Verify the imports resolve:

```bash
npx tsc --noEmit -p tsconfig.json 2>&1 | grep run-headless || echo "no type errors in script"
```

### Step 3: Subscribe to execution events (observability)

Attach a listener before executing so you can see node-level progress. The
event bus delivers typed `WorkflowEvent`s; `onAny` receives every event.

```ts
eventBus.onAny((event) => {
  logger.info('headless', `event: ${event.type}`, { source: event.source });
});
```

Verify: this step has no output yet — it registers a handler. The handler
fires in Step 5.

### Step 4: Define a minimal workflow object

Build a `Workflow`. The shape below is transcribed from the object constructed
in `src/services/production-workflow-executor.ts`. Replace `nodes`/`edges`
with your graph; the **full `Node`/`Edge`/`NodeType` schema lives in
`src/types/`** — read it for valid `type` values and edge/branch fields.

```ts
const workflow: Workflow = {
  id: 'wf-demo-1',
  name: 'demo',
  version: '1.0.0',
  nodes: [
    // Node fields confirmed from src/core/executors/base-node-executor.ts:
    //   { id, type, name, data }
    { id: 'n1', type: 'trigger',   name: 'Start',     data: {} } as any,
    { id: 'n2', type: 'transform', name: 'Transform', data: {} } as any,
  ],
  edges: [
    // Minimal edge. The Edge schema supports conditional/branch ports —
    // read src/types/workflow.ts for the full shape. (field names beyond
    // source/target are unverified here.)
    { id: 'e1', source: 'n1', target: 'n2' } as any,
  ],
  variables: {},
  settings: { errorHandling: 'stop', timeout: 30000 },
  runtimeEnv: {},
  createdAt: new Date(),
  updatedAt: new Date(),
};
```

> The `as any` casts are a scaffold so the example compiles before you supply
> real, schema-valid node `data`. **Remove them** once you import the concrete
> node `data` types from `src/types` — per project policy, untyped casts are
> not production-ready.

Verify the object is well-formed:

```bash
npx tsx -e "import('./scripts/run-headless.ts').catch(e=>{console.error(e);process.exit(1)})" \
  && echo "module loads"
```

### Step 5: Execute and inspect the result

The engine validates the graph, topologically sorts it, runs each node through
its registered executor, and resolves to a result carrying `status`.

```ts
const result = await executor.execute(workflow, { /* initial input */ });

if (result.status === 'completed') {
  logger.success('headless', 'workflow completed');
  process.exit(0);
} else {
  logger.error('headless', `workflow failed: ${result.error ?? 'unknown'}`);
  process.exit(1);
}
```

Run it:

```bash
npx tsx scripts/run-headless.ts
```

Expected output (order of `event:` lines depends on the graph):

```
[info] headless: event: workflow.started
[info] headless: event: node.started
...
[success] headless: workflow completed
```

The process exits `0` on success, `1` on failure.

## Verification

End-to-end check — the script should exit `0` and emit a `completed` log:

```bash
npx tsx scripts/run-headless.ts; echo "exit=$?"
# ...
# [success] headless: workflow completed
# exit=0
```

If `exit=0` and you see `workflow completed`, the engine ran headlessly with no
frontend and no store.

## Troubleshooting

**Symptom: `No executor registered for node type: <type>`** (a `ValidationError`).
The engine has no executor for a `node.type` in your graph. Confirm the type is
one of the registered defaults. List them at runtime:

```ts
import { createDefaultRegistry } from '@/core/executors';
console.log(createDefaultRegistry().getRegisteredNodeTypes());
```

If your type is missing, register a custom executor — see
[how-to-add-a-custom-node-executor.md](./how-to-add-a-custom-node-executor.md).

**Symptom: `Cannot find module '@/core/...'`.** The `@/` path alias is not
resolved by your runner. Either run through the repo's configured tooling
(`npm run` scripts use Vite/tsconfig aliases) or replace `@/` with a relative
path to `src/`.

**Symptom: execution hangs.** A node executor is awaiting I/O (for example a
`delay` or an integration node reaching the gateway). Integration nodes
(`webhook`/`database`/`email`) require `INTEGRATION_GATEWAY_TARGET_URL` and a
running gateway; remove them from this minimal graph or follow
[how-to-run-the-ssh-gateway.md](./how-to-run-the-ssh-gateway.md).

**Symptom: result fields you expected are `undefined`.** Only `status` and
`error` are confirmed from the call site. Read `src/core/workflow-executor.ts`
for the full result type before depending on other fields.

## Next steps

- [how-to-add-a-custom-node-executor.md](./how-to-add-a-custom-node-executor.md) — teach the engine a new node type.
- [how-to-drive-the-cli.md](./how-to-drive-the-cli.md) — the same execution, driven from the terminal.
- ← [Building blocks index](./README.md)
