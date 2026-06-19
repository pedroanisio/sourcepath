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
  - tool: "tsx"
    version: "^4.21.0"
---

# How to Drive Workflows from the Terminal CLI

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Overview

`usl-builder-canvas` ships a terminal CLI (`src/cli/entry.ts`, kernel version
`1.0.0`) that manages flows, executions, recipes, plugins, slots, and
configuration — no browser. This guide shows how to launch it, discover its
command grammar from its own `help`, and run an interactive REPL session. End
state: a working CLI invocation and a REPL prompt.

## Audience

Developers and operators who want to script or interactively manage workflows
without the React canvas.

## Prerequisites

1. Repo installed at commit `355a43d` — verify: `npm ls tsx` shows a `4.x`
   version under `devDependencies`.
2. Node ≥ 18 — verify: `node --version`.

## Non-goals

- This guide does **not** enumerate every subcommand flag. The CLI parses its
  own arguments; the authoritative grammar is its `help` output (Step 2). Exact
  flag spellings are **(unverified)** in this document and must be read from
  `help`.

## Verified command surface

These top-level command groups are confirmed from handler functions in
`src/cli/cli.ts`:

| Group | Backed by | Confirmed sub-operations (from registry methods) |
|---|---|---|
| `flow` | `FlowRegistry` | create, list, get, remove, add-node, add-edge, replace, set-recipe |
| `execution` | `ExecutionRegistry` | start, complete, fail, list, latest |
| `recipes` | `recipe-registry.ts` | (read from `help`) |
| `plugin` | `plugin-manager.ts` | (read from `help`) |
| `slot` | plugin slots | (read from `help`) |
| `config` | `config-manager.ts` | (read from `help`) |
| `help` | — | prints usage |

The exact CLI token for each operation (e.g. `add-node` vs `addnode`) is parsed
by `tokenize`/`parseArgs` in `cli.ts` — confirm spelling via `help`.

## Steps

### Step 1: Launch the CLI with no arguments

Per `entry.ts`, running with **no arguments** (or `--repl`) starts the REPL;
any other arguments are executed as a single command. First, confirm it runs:

```bash
npm run cli -- help
```

(The `--` passes everything after it to `src/cli/entry.ts`.) Expected: a usage
listing naming the command groups above. If you see the groups, the CLI is
wired.

### Step 2: Read the authoritative command grammar

`help` is the source of truth for exact syntax. Capture it:

```bash
npm run cli -- help | tee /tmp/usl-cli-help.txt
```

Verify it names `flow` and `execution`:

```bash
grep -E "flow|execution" /tmp/usl-cli-help.txt
```

Use this output — not this guide — for exact flag names in the steps below.

### Step 3: Create a flow and inspect it

Using the `flow` group (backed by `FlowRegistry.create`/`list`/`get`). The
precise flags come from Step 2; the shape is:

```bash
npm run cli -- flow create --name demo     # create (confirm flag names via help)
npm run cli -- flow list                   # FlowRegistry.list()
```

Expected: `flow list` prints at least the `demo` flow you just created.

### Step 4: Add nodes and edges to the flow

`FlowRegistry` exposes `addNode` and `addEdge`. The CLI validates the flow
shape (`validateFlowShape` in `cli.ts`) on mutation:

```bash
npm run cli -- flow add-node --flow demo --type trigger --id n1   # confirm via help
npm run cli -- flow add-node --flow demo --type transform --id n2
npm run cli -- flow add-edge --flow demo --from n1 --to n2
npm run cli -- flow get --flow demo                                # inspect result
```

Verify: `flow get` shows two nodes and one edge.

### Step 5: Start an execution and read its status

The `execution` group is backed by `ExecutionRegistry`
(`start`/`complete`/`fail`/`list`/`latest`):

```bash
npm run cli -- execution start --flow demo
npm run cli -- execution latest             # ExecutionRegistry.latest()
```

Verify: `execution latest` prints a record for the `demo` flow.

### Step 6: Use the interactive REPL

Start the REPL (no args, or explicit `--repl`):

```bash
npm run repl
# or: npm run cli
```

Expected: a prompt where the same commands work without the `npm run cli --`
prefix:

```
> flow list
> execution latest
```

Exit the REPL with the documented quit command shown at the prompt (read from
its banner).

## Verification

End-to-end: create → mutate → execute → read, all from the terminal:

```bash
npm run cli -- flow create --name vtest && \
npm run cli -- flow add-node --flow vtest --type trigger --id n1 && \
npm run cli -- execution start --flow vtest && \
npm run cli -- execution latest
```

Exit code `0` from the chain and a printed execution record confirm the CLI
drives the workflow lifecycle headlessly.

## Troubleshooting

**Symptom: `help` shows different flag names than this guide.** Trust `help`.
This guide marks exact flag spellings as unverified; `cli.ts`'s parser defines
the real grammar.

**Symptom: a command exits with code `1` and no output.** `entry.ts` sets
`process.exitCode = 1` when a command's result is `!ok`. Re-run with `help` for
that group to confirm argument shape; the parser (`parseArgs`) rejects unknown
options.

**Symptom: `flow add-edge` fails validation.** `validateFlowShape` rejects
edges referencing missing nodes. Add both endpoint nodes (Step 4) before the
edge.

**Symptom: `Cannot find module 'tsx'`.** Dev dependencies are not installed.
Run `npm install` at the repo root.

## Next steps

- [how-to-run-a-workflow-headless.md](./how-to-run-a-workflow-headless.md) — drive the engine from code instead of the CLI.
- ← [Building blocks index](./README.md)
