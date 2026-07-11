#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const sourcePath = process.argv[2] ?? "/mnt/data/d3_atlas_work/inventory.jsonld";
const outputPath = process.argv[3] ?? new URL("../data/atlas-data.js", import.meta.url).pathname;

function values(value) {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}

function scalar(value) {
  if (value == null) return null;
  if (Array.isArray(value)) return value.map(scalar);
  if (typeof value === "object") {
    if ("@id" in value) return value["@id"];
    if ("@value" in value) return value["@value"];
  }
  return value;
}

function typesOf(node) {
  return values(node["@type"]).map(scalar);
}

function stripPrefix(value, prefix) {
  if (typeof value !== "string") return value;
  return value.startsWith(prefix) ? value.slice(prefix.length) : value;
}

function normalizePath(value) {
  return String(value ?? "").replaceAll("\\", "/").replace(/^\.\//, "");
}

function topRegion(filePath) {
  const normalized = normalizePath(filePath);
  const parts = normalized.split("/").filter(Boolean);
  return parts.length > 1 ? parts[0] : "(root)";
}

function roleFor(type, phases, filePath) {
  const roles = new Set();
  if (type === "test_code" || phases.includes("test")) roles.add("test");
  if (type === "source_code") roles.add("production");
  if (type === "documentation") roles.add("documentation");
  if (type === "ci_cd" || phases.includes("ci")) roles.add("quality_gate");
  if (type === "configuration") roles.add("configuration");
  if (type === "asset") roles.add("asset");
  if (type === "dependency_manifest" || type === "lockfile") roles.add("dependency");
  if (/^(tests?|specs?)(\/|$)/i.test(filePath) || /(^|\/)(test_|.*(?:\.test|_test|\.spec|_spec)\.)/i.test(filePath)) roles.add("test");
  if (/^\.github\/workflows\//.test(filePath)) roles.add("quality_gate");
  if (roles.size === 0) roles.add("unknown");
  return [...roles].sort();
}

function stableHash(text) {
  return crypto.createHash("sha256").update(text).digest("hex").slice(0, 16);
}

const source = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
const graph = source["@graph"] ?? [];
const byId = new Map(graph.map((node) => [node["@id"], node]));

const repoNode = graph.find((node) => typesOf(node).includes("cbm:Repository"));
const commitNode = graph.find((node) => typesOf(node).includes("cbm:Commit"));

const fileNodes = graph
  .filter((node) => typesOf(node).includes("cbm:File") && node["cbm:path"] != null)
  .sort((a, b) => normalizePath(scalar(a["cbm:path"])).localeCompare(normalizePath(scalar(b["cbm:path"])), "en"));

const conceptNodes = graph
  .filter((node) => typesOf(node).includes("skos:Concept"))
  .sort((a, b) => String(scalar(a["skos:prefLabel"]) ?? a["@id"]).localeCompare(String(scalar(b["skos:prefLabel"]) ?? b["@id"]), "en"));

const concepts = conceptNodes.map((node) => ({
  id: node["@id"],
  label: String(scalar(node["skos:prefLabel"]) ?? node["@id"]),
  fileCount: Number(scalar(node["cbml3:fileCount"]) ?? 0),
  occurrenceCount: Number(scalar(node["cbml3:occurrenceCount"]) ?? 0),
}));
const conceptIndex = new Map(concepts.map((concept, index) => [concept.id, index]));

const files = fileNodes.map((node) => {
  const filePath = normalizePath(scalar(node["cbm:path"]));
  const type = stripPrefix(scalar(node["cbm:type"]), "cbmt:") ?? "unknown";
  const phases = values(node["cbm:hasPhase"]).map(scalar).map((value) => stripPrefix(value, "cbmp:")).filter(Boolean).sort();
  const language = values(node["cbm:language"]).map(scalar).filter(Boolean);
  const conceptRefs = values(node["cbml3:lexicalizes"])
    .map(scalar)
    .map((id) => conceptIndex.get(id))
    .filter((index) => Number.isInteger(index));
  return {
    id: node["@id"],
    path: filePath,
    name: path.posix.basename(filePath),
    directory: path.posix.dirname(filePath) === "." ? "(root)" : path.posix.dirname(filePath),
    region: topRegion(filePath),
    type,
    phases,
    roles: roleFor(type, phases, filePath),
    language: language.length === 0 ? null : language.length === 1 ? language[0] : language,
    size: Number(scalar(node["cbm:sizeBytes"]) ?? 0),
    mtime: scalar(node["cbm:mtime"]),
    concepts: conceptRefs,
    contentHash: scalar(node["cbm:contentSha256"]),
  };
});
const fileIndex = new Map(files.map((file, index) => [file.id, index]));

const externalNodes = graph
  .filter((node) => typesOf(node).includes("cbm:ExternalPackage"))
  .sort((a, b) => String(scalar(a["cbm:packageName"]) ?? a["@id"]).localeCompare(String(scalar(b["cbm:packageName"]) ?? b["@id"]), "en"));

const releasesByPackage = new Map();
for (const node of graph) {
  if (!typesOf(node).includes("cbm:PackageRelease")) continue;
  const packageId = scalar(node["cbm:releaseOf"]);
  if (!packageId) continue;
  releasesByPackage.set(packageId, String(scalar(node["cbm:packageVersion"]) ?? ""));
}

const externals = externalNodes.map((node) => ({
  id: node["@id"],
  name: String(scalar(node["cbm:packageName"]) ?? node["@id"]),
  version: releasesByPackage.get(node["@id"]) ?? null,
}));
const externalIndex = new Map(externals.map((item, index) => [item.id, index]));

const imports = [];
const externalImports = [];
const tests = [];
for (const node of fileNodes) {
  const consumer = fileIndex.get(node["@id"]);
  for (const rawTarget of values(node["cbm:imports"])) {
    const provider = fileIndex.get(scalar(rawTarget));
    if (Number.isInteger(provider)) imports.push([consumer, provider]);
  }
  for (const rawTarget of values(node["cbm:importsExternal"])) {
    const provider = externalIndex.get(scalar(rawTarget));
    if (Number.isInteger(provider)) externalImports.push([consumer, provider]);
  }
  for (const rawTarget of values(node["cbm:tests"])) {
    const subject = fileIndex.get(scalar(rawTarget));
    if (Number.isInteger(subject)) tests.push([consumer, subject]);
  }
}

imports.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
externalImports.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
tests.sort((a, b) => a[0] - b[0] || a[1] - b[1]);

const chunkNodes = graph
  .filter((node) => typesOf(node).includes("cbml2:Chunk"))
  .filter((node) => fileIndex.has(scalar(node["cbml2:inFile"])))
  .sort((a, b) => {
    const fa = fileIndex.get(scalar(a["cbml2:inFile"]));
    const fb = fileIndex.get(scalar(b["cbml2:inFile"]));
    return fa - fb || Number(scalar(a["cbml2:beginLine"]) ?? 0) - Number(scalar(b["cbml2:beginLine"]) ?? 0) || String(a["@id"]).localeCompare(String(b["@id"]));
  });

const chunks = chunkNodes.map((node) => ({
  id: node["@id"],
  file: fileIndex.get(scalar(node["cbml2:inFile"])),
  symbol: String(scalar(node["cbml2:symbol"]) ?? "<chunk>"),
  kind: String(scalar(node["cbml2:kind"]) ?? "chunk"),
  begin: Number(scalar(node["cbml2:beginLine"]) ?? 0),
  end: Number(scalar(node["cbml2:endLine"]) ?? 0),
  signature: scalar(node["cbml2:signature"]),
  concepts: values(node["cbml3:lexicalizes"])
    .map(scalar)
    .map((id) => conceptIndex.get(id))
    .filter((index) => Number.isInteger(index)),
}));

const chunksByFile = Array.from({ length: files.length }, () => []);
chunks.forEach((chunk, index) => chunksByFile[chunk.file].push(index));
for (let index = 0; index < files.length; index += 1) {
  files[index].chunks = chunksByFile[index];
}

// Loud guard: an L1-only bundle (files present, but no concepts and no chunks)
// would render an empty Cartogram. Fail rather than emit a misleading artifact.
if (files.length > 0 && concepts.length === 0 && chunks.length === 0) {
  console.error(
    `\n[cbm-cartogram] Refusing to build: the inventory has ${files.length} file(s) ` +
      `but 0 concepts and 0 chunks.\n` +
      `This looks like an L1-only bundle. The Cartogram needs an L3/L4 bundle.\n` +
      `Produce one with:  python scripts/run_l3.py --repo <repo> --out <dir>   (or run_l4.py)\n` +
      `then point this normalizer at <dir>/inventory.jsonld.\n`,
  );
  process.exit(1);
}

const regionCounts = {};
for (const file of files) regionCounts[file.region] = (regionCounts[file.region] ?? 0) + 1;

const metadata = {
  title: "Cartogram",
  repositoryId: repoNode?.["@id"] ?? null,
  repositoryName: stripPrefix(repoNode?.["@id"] ?? "software", "cbmi:repo/"),
  commit: scalar(commitNode?.["cbm:commitSha"]) ?? stripPrefix(commitNode?.["@id"] ?? "", "cbmi:commit/"),
  sourceFile: path.basename(sourcePath),
  sourceDigest: stableHash(fs.readFileSync(sourcePath)),
  generatedAt: (process.env.SOURCE_DATE_EPOCH
    ? new Date(Number(process.env.SOURCE_DATE_EPOCH) * 1000)
    : new Date()
  ).toISOString(),
  d3Version: "7.9.0",
  counts: {
    files: files.length,
    chunks: chunks.length,
    concepts: concepts.length,
    externalPackages: externals.length,
    internalImports: imports.length,
    externalImports: externalImports.length,
    explicitTests: tests.length,
    regions: Object.keys(regionCounts).length,
  },
  regionCounts,
};

const atlas = {
  metadata,
  files,
  chunks,
  concepts,
  externals,
  relations: { imports, externalImports, tests },
};

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
const serialized = JSON.stringify(atlas);
fs.writeFileSync(outputPath, `window.ATLAS_DATA=${serialized};\n`, "utf8");
fs.writeFileSync(outputPath.replace(/\.js$/i, ".json"), `${JSON.stringify(atlas, null, 2)}\n`, "utf8");
console.log(JSON.stringify({ outputPath, bytes: Buffer.byteLength(serialized), ...metadata.counts }, null, 2));
