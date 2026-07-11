import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const context = vm.createContext({ console, Intl, Math, Map, Set, Uint8Array, Uint32Array, Int32Array, Float64Array });
context.window = context;
context.globalThis = context;
vm.runInContext(fs.readFileSync(path.join(root, "data/atlas-data.js"), "utf8"), context, { filename: "atlas-data.js" });
vm.runInContext(fs.readFileSync(path.join(root, "src/model.js"), "utf8"), context, { filename: "model.js" });

const data = context.ATLAS_DATA;
const model = context.AtlasModel.derive(data);

function flattenFiles(node, result = []) {
  if (node.kind === "file") result.push(node.file);
  for (const child of node.children ?? []) flattenFiles(child, result);
  return result;
}

test("metadata counts equal normalized arrays", () => {
  assert.equal(data.metadata.counts.files, data.files.length);
  assert.equal(data.metadata.counts.chunks, data.chunks.length);
  assert.equal(data.metadata.counts.concepts, data.concepts.length);
  assert.equal(data.metadata.counts.externalPackages, data.externals.length);
  assert.equal(data.metadata.counts.internalImports, data.relations.imports.length);
  assert.equal(data.metadata.counts.externalImports, data.relations.externalImports.length);
  assert.equal(data.metadata.counts.explicitTests, data.relations.tests.length);
});

test("all canonical relation endpoints are valid", () => {
  for (const [consumer, provider] of data.relations.imports) {
    assert.ok(consumer >= 0 && consumer < data.files.length);
    assert.ok(provider >= 0 && provider < data.files.length);
  }
  for (const [consumer, external] of data.relations.externalImports) {
    assert.ok(consumer >= 0 && consumer < data.files.length);
    assert.ok(external >= 0 && external < data.externals.length);
  }
  for (const [testFile, subject] of data.relations.tests) {
    assert.ok(testFile >= 0 && testFile < data.files.length);
    assert.ok(subject >= 0 && subject < data.files.length);
  }
});

test("import projection reverses every canonical import without dropping it", () => {
  assert.equal(model.importEdges.length, data.relations.imports.length);
  model.importEdges.forEach((edge, index) => {
    const [consumer, provider] = data.relations.imports[index];
    assert.equal(edge.canonicalSource, consumer);
    assert.equal(edge.canonicalTarget, provider);
    assert.equal(edge.source, provider);
    assert.equal(edge.target, consumer);
    assert.equal(edge.directionTransform, "reverse");
  });
});

test("one primary provider is selected per non-cut consumer and the forest is acyclic", () => {
  assert.equal(context.AtlasModel.findParentCycles(model.parent).length, 0);
  const primaryByConsumer = new Map();
  for (const edge of model.importEdges.filter((edge) => edge.primary)) {
    primaryByConsumer.set(edge.target, (primaryByConsumer.get(edge.target) ?? 0) + 1);
  }
  for (let consumer = 0; consumer < model.parent.length; consumer += 1) {
    const count = primaryByConsumer.get(consumer) ?? 0;
    assert.ok(count <= 1);
    if (model.parent[consumer] >= 0) assert.equal(count, 1);
  }
  assert.equal(
    model.importEdges.filter((edge) => edge.primary).length + model.importEdges.filter((edge) => !edge.primary).length,
    data.relations.imports.length,
  );
});

test("test projection reverses explicit test mappings", () => {
  assert.equal(model.testEdges.length, data.relations.tests.length);
  model.testEdges.forEach((edge, index) => {
    const [testFile, subject] = data.relations.tests[index];
    assert.equal(edge.canonicalSource, testFile);
    assert.equal(edge.canonicalTarget, subject);
    assert.equal(edge.source, subject);
    assert.equal(edge.target, testFile);
    assert.equal(edge.directionTransform, "reverse");
  });
});

test("external boundary supply reverses file-to-package imports", () => {
  assert.equal(model.externalEdges.length, data.relations.externalImports.length);
  model.externalEdges.forEach((edge, index) => {
    const [consumer, external] = data.relations.externalImports[index];
    assert.equal(edge.canonicalSource, consumer);
    assert.equal(edge.canonicalTarget, external);
    assert.equal(edge.sourceExternal, external);
    assert.equal(edge.target, consumer);
    assert.equal(edge.directionTransform, "reverse");
  });
});

test("directory hierarchy contains every file exactly once", () => {
  const members = flattenFiles(model.directoryTree).sort((a, b) => a - b);
  assert.equal(members.length, data.files.length);
  assert.deepEqual(members, Array.from({ length: data.files.length }, (_, index) => index));
});

test("test-suite aggregates preserve complete test membership without duplicates", () => {
  const suiteMembers = Array.from(model.suites).flatMap((suite) => Array.from(suite.members)).sort((a, b) => a - b);
  const testFiles = Array.from(model.testFiles).sort((a, b) => a - b);
  assert.equal(suiteMembers.length, testFiles.length);
  suiteMembers.forEach((value, index) => assert.equal(value, testFiles[index]));
  assert.equal(new Set(suiteMembers).size, suiteMembers.length);
  for (const fileIndex of testFiles) assert.ok(model.suiteByFile[fileIndex] >= 0);
});

test("all symbol chunks remain traceable to valid files", () => {
  const seen = new Set();
  data.chunks.forEach((chunk, index) => {
    assert.ok(chunk.file >= 0 && chunk.file < data.files.length);
    assert.ok(!seen.has(chunk.id), `duplicate chunk id at ${index}`);
    seen.add(chunk.id);
    assert.ok(data.files[chunk.file].chunks.includes(index));
  });
});

test("artifact identifiers are unique", () => {
  assert.equal(new Set(data.files.map((file) => file.id)).size, data.files.length);
  assert.equal(new Set(data.externals.map((external) => external.id)).size, data.externals.length);
  assert.equal(new Set(data.concepts.map((concept) => concept.id)).size, data.concepts.length);
});
