import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { computeStats, formatStats } from "../check-backlog-governance.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..");
const SCRIPT = join(HERE, "..", "check-backlog-governance.mjs");

// A backlog shaped like another project's — no id_prefix/decision_prefix, no
// enum-vocabulary lists — the way a sibling repo's own (differently-governed)
// backlog.yml can legitimately look. --stats must work on this; the strict
// validate mode is not expected to.
const FOREIGN_SCHEMA_BACKLOG = `metadata:
  schema_version: "1.0.0"
  generated_by: "test fixture"
  generated_on: "2026-01-01"
  system_of_record: "docs/backlog.yml"
  total_items: 2

items:

  - id:          "BL-001"
    title:        "Foreign item one"
    summary:      "Summary one"
    category:     "extraction"
    type:         "bug"
    status:       "done"
    complexity:   "S"
    priority:     "high"
    rationale:    "rationale one"
    owner:        "unassigned"
    source:       "test"

  - id:          "BL-002"
    title:        "Foreign item two"
    summary:      "Summary two"
    category:     "ingestion"
    type:         "feature"
    status:       "ready"
    complexity:   "M"
    priority:     "medium"
    rationale:    "rationale two"
    owner:        "unassigned"
    source:       "test"
`;

function writeForeignFixture() {
  const dir = mkdtempSync(join(tmpdir(), "cbm-backlog-fixture-"));
  const path = join(dir, "backlog.yml");
  writeFileSync(path, FOREIGN_SCHEMA_BACKLOG, "utf8");
  return path;
}

function sampleBacklog() {
  return {
    metadata: { total_items: 4 },
    items: [
      { id: "BL-001", status: "done", priority: "high", complexity: "S", category: "docs", type: "infra", owner: "shared" },
      { id: "BL-002", status: "ready", priority: "critical", complexity: "M", category: "feature", type: "bug", owner: "unassigned" },
      { id: "BL-003", status: "ready", priority: "low", complexity: "XL", category: "feature", type: "research", owner: "unassigned" },
      { id: "BL-004", status: "parked", priority: "low", complexity: "XS", category: "feature", type: "research", owner: "unassigned" },
    ],
  };
}

test("computeStats: counts each dimension correctly", () => {
  const stats = computeStats(sampleBacklog());
  assert.equal(stats.total, 4);
  assert.deepEqual(stats.status, { done: 1, ready: 2, parked: 1 });
  assert.deepEqual(stats.priority, { high: 1, critical: 1, low: 2 });
  assert.deepEqual(stats.complexity, { S: 1, M: 1, XL: 1, XS: 1 });
  assert.deepEqual(stats.category, { docs: 1, feature: 3 });
  assert.deepEqual(stats.type, { infra: 1, bug: 1, research: 2 });
  assert.deepEqual(stats.owner, { shared: 1, unassigned: 3 });
});

test("computeStats: cross-tab counts status x priority", () => {
  const stats = computeStats(sampleBacklog());
  assert.equal(stats.crossTab.ready.critical, 1);
  assert.equal(stats.crossTab.ready.low, 1);
  assert.equal(stats.crossTab.done.high, 1);
  assert.equal(stats.crossTab.parked.low, 1);
  assert.equal(stats.crossTab.ready.high ?? 0, 0);
});

test("computeStats: complexity-weighted size counts only open statuses as remaining", () => {
  const stats = computeStats(sampleBacklog());
  // weights: XS=1 S=2 M=3 L=5 XL=8
  // total = S(2) + M(3) + XL(8) + XS(1) = 14
  // open (ready/in-progress/blocked) = M(3) + XL(8) = 11 ; done+parked excluded
  assert.equal(stats.weight.total, 14);
  assert.equal(stats.weight.open, 11);
});

test("computeStats: flags ready/in-progress/blocked + critical items", () => {
  const stats = computeStats(sampleBacklog());
  assert.deepEqual(stats.criticalOpen.map((it) => it.id), ["BL-002"]);
});

test("computeStats: category-by-status cross-tab", () => {
  const stats = computeStats(sampleBacklog());
  assert.deepEqual(stats.categoryByStatus.docs, { done: 1 });
  assert.deepEqual(stats.categoryByStatus.feature, { ready: 2, parked: 1 });
});

test("computeStats: type-by-status cross-tab", () => {
  const stats = computeStats(sampleBacklog());
  assert.deepEqual(stats.typeByStatus.infra, { done: 1 });
  assert.deepEqual(stats.typeByStatus.bug, { ready: 1 });
  assert.deepEqual(stats.typeByStatus.research, { ready: 1, parked: 1 });
});

test("formatStats: renders a human-readable report with the key sections", () => {
  const text = formatStats(computeStats(sampleBacklog()));
  assert.match(text, /TOTAL ITEMS: 4/);
  assert.match(text, /STATUS/);
  assert.match(text, /PRIORITY/);
  assert.match(text, /COMPLEXITY/);
  assert.match(text, /CROSS-TAB: status x priority/);
  assert.match(text, /CATEGORY x STATUS/);
  assert.match(text, /TYPE x STATUS/);
  assert.match(text, /COMPLEXITY-WEIGHTED/);
  assert.match(text, /BL-002/);
});

test("CLI: --stats prints a report for the real backlog and exits 0", () => {
  const output = execFileSync("node", [SCRIPT, "--stats"], { cwd: REPO_ROOT, encoding: "utf8" });
  assert.match(output, /TOTAL ITEMS: \d+/);
  assert.match(output, /STATUS/);
});

test("CLI: default invocation (no --stats) still only validates, no stats report", () => {
  const output = execFileSync("node", [SCRIPT], { cwd: REPO_ROOT, encoding: "utf8" });
  assert.match(output, /backlog-governance: \d+ item\(s\) verified/);
  assert.doesNotMatch(output, /TOTAL ITEMS/);
});

test("CLI: --stats accepts an absolute path to a backlog outside this repo", () => {
  const fixturePath = writeForeignFixture();
  const output = execFileSync("node", [SCRIPT, fixturePath, "--stats"], { cwd: REPO_ROOT, encoding: "utf8" });
  assert.match(output, /TOTAL ITEMS: 2/);
});

test("CLI: --stats works on a backlog whose schema doesn't match this repo's governance rules", () => {
  const fixturePath = writeForeignFixture();
  // This backlog has no id_prefix/decision_prefix/enum-vocabulary lists at all,
  // so strict validate() would reject it outright — --stats must not require that.
  const output = execFileSync("node", [SCRIPT, fixturePath, "--stats"], { cwd: REPO_ROOT, encoding: "utf8" });
  assert.match(output, /extraction/); // the foreign category value, not in this repo's own vocab
  assert.match(output, /ingestion/);
});

test("CLI: default (validating) mode still rejects a schema-incompatible backlog rather than silently passing", () => {
  const fixturePath = writeForeignFixture();
  assert.throws(() => execFileSync("node", [SCRIPT, fixturePath], { cwd: REPO_ROOT, encoding: "utf8" }));
});
