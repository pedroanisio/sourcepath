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
  - tool: "vitest"
    version: "^4.0.18"
---

# How to Add a Custom Node Executor to the Workflow Engine

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Overview

The engine resolves each node's behavior through a `NodeExecutorRegistry`
(Open-Closed Principle: new node types are added without editing
`WorkflowExecutor`). This guide shows how to implement a `NodeExecutor`,
register it, and execute a workflow that uses it. End state: a custom
`uppercase` node runs inside a headless workflow.

## Audience

TypeScript developers comfortable with the headless execution flow in
[how-to-run-a-workflow-headless.md](./how-to-run-a-workflow-headless.md).

## Prerequisites

1. You can run a headless workflow (complete Guide 1 first) — verify:
   `npx tsx scripts/run-headless.ts` exits `0`.
2. `vitest` runs — verify: `npx vitest --version` prints `4.x`.
3. You have read `src/core/executors/node-executor.interface.ts` and
   `src/core/executors/base-node-executor.ts`.

## Non-goals

- This guide does not modify `createDefaultRegistry()`. You register your
  executor on a registry instance you control.

## Steps

### Step 1: Write a failing test first (TDD red)

Per project policy, the test comes first. Create
`src/core/executors/uppercase-node-executor.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { UppercaseNodeExecutor } from './uppercase-node-executor';

describe('UppercaseNodeExecutor', () => {
  it('uppercases the primary string input', async () => {
    const exec = new UppercaseNodeExecutor();
    const result = await exec.execute(
      { id: 'n', type: 'uppercase', name: 'U', data: {} } as any,
      { in: 'hello' },
      {} as any, // context is unused by this executor
    );
    expect(result.output).toBe('HELLO');
  });
});
```

Run it and confirm it fails because the file does not exist yet:

```bash
npx vitest run src/core/executors/uppercase-node-executor.test.ts
# FAIL  Cannot find module './uppercase-node-executor'
```

### Step 2: Implement the executor by extending `BaseNodeExecutor`

`BaseNodeExecutor` (an abstract class) supplies `validate()`, logging helpers,
and `createEvaluationContext()`. You implement `nodeType` and `execute()`.
Create `src/core/executors/uppercase-node-executor.ts`:

```ts
import type { Node, NodeType } from '@/types';
import { BaseNodeExecutor } from './base-node-executor';
import type { NodeExecutionContext, NodeExecutionResult } from './node-executor.interface';

export class UppercaseNodeExecutor extends BaseNodeExecutor {
  // NOTE: 'uppercase' must be a valid NodeType in src/types. If NodeType is a
  // closed union, extend that union there first, or this will not type-check.
  readonly nodeType = 'uppercase' as NodeType;

  async execute(
    _node: Node,
    inputs: unknown,
    _context: NodeExecutionContext,
  ): Promise<NodeExecutionResult> {
    const ctx = this.createEvaluationContext(inputs);
    const value = String(ctx.input ?? '');
    return { output: value.toUpperCase() };
  }
}
```

Re-run the test — it should pass (TDD green):

```bash
npx vitest run src/core/executors/uppercase-node-executor.test.ts
# PASS  1 passed
```

### Step 3: Register the executor on a registry instance

`register()` throws a `ValidationError` (`EXECUTOR_ALREADY_REGISTERED`) if the
`nodeType` is already taken, so register your new type onto the default set:

```ts
import { createDefaultRegistry } from '@/core/executors';
import { UppercaseNodeExecutor } from '@/core/executors/uppercase-node-executor';

const registry = createDefaultRegistry();
registry.register(new UppercaseNodeExecutor());
```

Verify the type is now known:

```ts
console.log(registry.has('uppercase' as any)); // true
```

### Step 4: Hand the registry to the engine

> ⚠ **(unverified) — confirm before relying on this step.** The mechanism by
> which `WorkflowExecutor` receives a custom registry is **not confirmed** from
> the source available in the bundle. The constructor observed at the call site
> is `new WorkflowExecutor(eventBus, logger)` (two args). Open
> `src/core/workflow-executor.ts` and check the constructor / any
> `setRegistry`-style method for whether a registry can be injected. If the
> engine always builds its own `createDefaultRegistry()` internally, you must
> add your executor inside that factory (editing
> `node-executor-registry.ts`) rather than injecting it.

Confirm the injection path:

```bash
grep -n "registry\|createDefaultRegistry\|NodeExecutorRegistry" src/core/workflow-executor.ts
```

Use whichever the source supports — constructor argument, setter, or editing
the default factory.

### Step 5: Run a workflow that uses the node

Add an `uppercase` node to the workflow from Guide 1 and execute. The node's
output appears in downstream `nodeOutputs` keyed by node id.

```bash
npx tsx scripts/run-headless.ts; echo "exit=$?"
# [success] headless: workflow completed
# exit=0
```

## Verification

The full executor test plus a type-check pass confirms the new block is wired:

```bash
npx vitest run src/core/executors/uppercase-node-executor.test.ts && \
  npx tsc --noEmit -p tsconfig.json && echo "custom executor verified"
```

## Troubleshooting

**Symptom: `Type '"uppercase"' is not assignable to type 'NodeType'`.**
`NodeType` is a closed union in `src/types`. Add `'uppercase'` to that union
(and any node-data map) before the executor type-checks. The `as NodeType` cast
in Step 2 silences the compiler but will fail at the registry's runtime
`validate()` if the type is genuinely unknown elsewhere.

**Symptom: `Executor for node type 'uppercase' already registered`.** You
registered twice, or the type collides with a default. Pick a unique
`nodeType`, or call `registry.has(type)` before `register()`.

**Symptom: `Invalid inputs: expected object`** (`INVALID_INPUTS`).
`createEvaluationContext()` requires `inputs` to be a non-null object. The
engine passes upstream node outputs as an object keyed by node id; if you call
`execute()` directly in a test, pass an object, not a bare string.

**Symptom: the node never runs.** It is unreachable in the graph (no active
edge into it) or downstream of a failed node when `settings.errorHandling` is
`'stop'`. Check the event stream for `node.skipped`/`node.failed` events.

## Next steps

- [how-to-run-a-workflow-headless.md](./how-to-run-a-workflow-headless.md) — the execution harness this guide extends.
- ← [Building blocks index](./README.md)
