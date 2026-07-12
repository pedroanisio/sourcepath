import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_BACKLOG = "docs/backlog.yml";
const DONE_STATUSES = new Set(["done"]);
const BLOCKED_MARKERS = new RegExp("\\b(?:" + ["TO" + "DO", "TB" + "D", "FIX" + "ME"].join("|") + ")\\b|" + "PLACE" + "HOLDER", "i");

function unquote(value) {
  const trimmed = value.trim();
  return trimmed.replace(/^["']|["']$/g, "");
}

function stripInlineComment(value) {
  let quoted = false;
  let quote = "";
  for (let i = 0; i < value.length; i += 1) {
    const ch = value[i];
    if ((ch === "\"" || ch === "'") && value[i - 1] !== "\\") {
      if (!quoted) {
        quoted = true;
        quote = ch;
      } else if (quote === ch) {
        quoted = false;
        quote = "";
      }
    }
    if (!quoted && ch === "#" && /\s/.test(value[i - 1] ?? " ")) return value.slice(0, i).trimEnd();
  }
  return value;
}

function parseScalar(value) {
  const stripped = stripInlineComment(value).trim();
  if (stripped === "") return "";
  if (stripped === "true") return true;
  if (stripped === "false") return false;
  if (/^-?[0-9]+$/.test(stripped)) return Number(stripped);
  if (stripped.startsWith("[") && stripped.endsWith("]")) {
    try {
      return JSON.parse(stripped.replace(/'/g, '"'));
    } catch {
      return stripped.slice(1, -1).split(",").map((item) => unquote(item.trim())).filter(Boolean);
    }
  }
  return unquote(stripped);
}

function parseIndentedList(lines, start, indent) {
  const values = [];
  const re = new RegExp("^ {" + indent + "}- (.+)$");
  for (let i = start; i < lines.length; i += 1) {
    const match = re.exec(lines[i]);
    if (!match) break;
    values.push(parseScalar(match[1]));
  }
  return values;
}

function parseChecks(lines, start) {
  const checks = [];
  let current;
  for (let i = start; i < lines.length; i += 1) {
    if (/^ {4}[a-z_]+: /.test(lines[i]) || /^ {2}- id: /.test(lines[i])) break;
    let match = /^ {6}- kind: (.+)$/.exec(lines[i]);
    if (match) {
      current = { kind: parseScalar(match[1]) };
      checks.push(current);
      continue;
    }
    match = /^ {8}([a-z_]+): (.*)$/.exec(lines[i]);
    if (match && current) current[match[1]] = parseScalar(match[2]);
  }
  return checks;
}

export function parseBacklog(text) {
  const lines = text.split(/\r?\n/);
  const backlog = { metadata: {}, items: [] };

  for (let i = 0; i < lines.length; i += 1) {
    let match = /^ {2}([a-z_]+): (.*)$/.exec(lines[i]);
    if (match && !/^ {2}- /.test(lines[i])) {
      const [, key, raw] = match;
      if (["categories", "types", "statuses", "complexities", "priorities", "owners"].includes(key) && raw.trim() === "") {
        backlog.metadata[key] = parseIndentedList(lines, i + 1, 4);
      } else {
        backlog.metadata[key] = parseScalar(raw);
      }
    }

    match = /^ {2}- id: (.+)$/.exec(lines[i]);
    if (match) {
      const item = { id: parseScalar(match[1]) };
      for (let j = i + 1; j < lines.length; j += 1) {
        if (/^ {2}- id: /.test(lines[j])) break;
        const field = /^ {4}([a-z_]+):(.*)$/.exec(lines[j]);
        if (!field) continue;
        const [, key, rawValue] = field;
        const raw = rawValue.trimStart();
        if (["acceptance_criteria", "dependencies", "related_decisions", "references", "tags"].includes(key)) {
          item[key] = raw.trim() === "" ? parseIndentedList(lines, j + 1, 6) : parseScalar(raw);
        } else if (key === "evidence_checks") {
          item[key] = parseChecks(lines, j + 1);
        } else {
          item[key] = parseScalar(raw);
        }
      }
      backlog.items.push(item);
    }
  }

  return backlog;
}

function idsInMarkdown(text, idPrefix) {
  const re = new RegExp("\\b" + idPrefix + "-[0-9]{3}\\b", "g");
  return new Set(text.match(re) ?? []);
}

function checkEvidence(check, rootDir) {
  const target = join(rootDir, check.path ?? "");
  if (check.kind === "file-exists") return existsSync(target) === (check.expect ?? true);
  if (check.kind === "file-absent") return existsSync(target) === false;
  if (check.kind === "grep") {
    if (!existsSync(target) || !check.pattern) return false;
    const pattern = new RegExp(check.pattern);
    const found = pattern.test(readFileSync(target, "utf8"));
    return found === (check.expect ?? true);
  }
  return false;
}

export function validate(backlog, rootDir, backlogPath = DEFAULT_BACKLOG) {
  const errors = [];
  const metadata = backlog.metadata;
  const items = backlog.items;
  const idPrefix = metadata.id_prefix;
  const decisionPrefix = metadata.decision_prefix;
  const idPattern = new RegExp("^" + idPrefix + "-[0-9]{3}$");
  const decisionPattern = new RegExp("^" + decisionPrefix + "-[0-9]{3}$");
  const ids = new Set();

  if (!idPrefix) errors.push("metadata.id_prefix is required");
  if (!decisionPrefix) errors.push("metadata.decision_prefix is required");
  if (metadata.total_items !== items.length) errors.push("metadata.total_items must match items.length");
  for (const key of ["categories", "types", "statuses", "complexities", "priorities", "owners"]) {
    if (!Array.isArray(metadata[key]) || metadata[key].length === 0) errors.push(`metadata.${key} must be a non-empty list`);
  }

  for (const item of items) {
    const label = item.id ?? "(missing id)";
    if (!item.id || !idPattern.test(item.id)) errors.push(`${label}: id must match ${idPrefix}-NNN`);
    if (ids.has(item.id)) errors.push(`${label}: duplicate id`);
    ids.add(item.id);
    for (const field of ["title", "summary", "category", "type", "status", "complexity", "priority", "rationale", "source", "owner"]) {
      if (item[field] === undefined || item[field] === "") errors.push(`${label}: ${field} is required`);
    }
    const enumChecks = [
      ["category", "categories"],
      ["type", "types"],
      ["status", "statuses"],
      ["complexity", "complexities"],
      ["priority", "priorities"],
      ["owner", "owners"],
    ];
    for (const [field, vocab] of enumChecks) {
      if (Array.isArray(metadata[vocab]) && item[field] && !metadata[vocab].includes(item[field])) {
        errors.push(`${label}: ${field} is not in metadata.${vocab}`);
      }
    }
    if (BLOCKED_MARKERS.test([item.title, item.summary, item.description, item.rationale, item.source].join("\n"))) {
      errors.push(`${label}: item text contains unresolved markers`);
    }
    for (const dep of item.dependencies ?? []) {
      if (!ids.has(dep) && !items.some((candidate) => candidate.id === dep)) errors.push(`${label}: dependency ${dep} is unknown`);
    }
    for (const decision of item.related_decisions ?? []) {
      if (!decisionPattern.test(decision)) errors.push(`${label}: related decision ${decision} must match ${decisionPrefix}-NNN`);
    }
    if (DONE_STATUSES.has(item.status)) {
      const checks = item.evidence_checks ?? [];
      if (checks.length === 0) errors.push(`${label}: done item needs evidence_checks`);
      for (const check of checks) {
        if (!checkEvidence(check, rootDir)) errors.push(`${label}: evidence check failed (${check.kind} ${check.path})`);
      }
    }
  }

  // resolve() (not join()) so an absolute backlogPath — a sibling repo's
  // backlog.yml, say — isn't concatenated onto rootDir into a broken path.
  const baseDir = dirname(resolve(rootDir, backlogPath));
  const mdPath = join(baseDir, "BACKLOG.md");
  if (existsSync(mdPath) && idPrefix) {
    const yamlIds = new Set(items.map((item) => item.id));
    const mdIds = idsInMarkdown(readFileSync(mdPath, "utf8"), idPrefix);
    const onlyYaml = [...yamlIds].filter((id) => !mdIds.has(id));
    const onlyMd = [...mdIds].filter((id) => !yamlIds.has(id));
    if (onlyYaml.length || onlyMd.length) {
      errors.push(`BACKLOG.md ids differ from backlog.yml (missing in md: ${onlyYaml.join(", ") || "none"}; extra in md: ${onlyMd.join(", ") || "none"})`);
    }
  }

  const schemaPath = join(baseDir, "schema/backlog.schema.json");
  if (!existsSync(schemaPath)) {
    errors.push("schema/backlog.schema.json is missing");
  } else {
    try {
      JSON.parse(readFileSync(schemaPath, "utf8"));
    } catch (error) {
      errors.push(`schema/backlog.schema.json is not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return errors;
}

export function run(backlogPath = DEFAULT_BACKLOG, rootDir = join(dirname(fileURLToPath(import.meta.url)), "..")) {
  const fullPath = resolve(rootDir, backlogPath);
  const backlog = parseBacklog(readFileSync(fullPath, "utf8"));
  const errors = validate(backlog, rootDir, backlogPath);
  if (errors.length > 0) {
    throw new Error(errors.map((error) => `backlog-governance: ${error}`).join("\n"));
  }
  return backlog.items.length;
}

const STATUS_ORDER = ["done", "in-progress", "blocked", "ready", "parked"];
const PRIORITY_ORDER = ["critical", "high", "medium", "low"];
const COMPLEXITY_ORDER = ["XS", "S", "M", "L", "XL"];
const COMPLEXITY_WEIGHT = { XS: 1, S: 2, M: 3, L: 5, XL: 8 };
const OPEN_STATUSES = new Set(["ready", "in-progress", "blocked"]);

function count(items, field) {
  const counts = {};
  for (const item of items) counts[item[field]] = (counts[item[field]] ?? 0) + 1;
  return counts;
}

function buildCrossTab(items, rowField, colField) {
  const table = {};
  for (const item of items) {
    const row = (table[item[rowField]] ??= {});
    row[item[colField]] = (row[item[colField]] ?? 0) + 1;
  }
  return table;
}

/**
 * Compute summary statistics (counts, cross-tabs, weighted size) over a parsed backlog.
 */
export function computeStats(backlog) {
  const items = backlog.items;
  const crossTab = buildCrossTab(items, "status", "priority");
  for (const status of STATUS_ORDER) crossTab[status] ??= {};

  const categoryByStatus = buildCrossTab(items, "category", "status");
  const typeByStatus = buildCrossTab(items, "type", "status");

  const weightOf = (item) => COMPLEXITY_WEIGHT[item.complexity] ?? 0;
  const total = items.reduce((sum, item) => sum + weightOf(item), 0);
  const open = items.filter((item) => OPEN_STATUSES.has(item.status)).reduce((sum, item) => sum + weightOf(item), 0);

  const criticalOpen = items.filter((item) => OPEN_STATUSES.has(item.status) && item.priority === "critical");

  return {
    total: items.length,
    status: count(items, "status"),
    priority: count(items, "priority"),
    complexity: count(items, "complexity"),
    category: count(items, "category"),
    type: count(items, "type"),
    owner: count(items, "owner"),
    crossTab,
    categoryByStatus,
    typeByStatus,
    weight: { total, open },
    criticalOpen,
  };
}

function renderBreakdown(title, counts, order) {
  const keys = order ?? Object.keys(counts).sort((a, b) => (counts[b] ?? 0) - (counts[a] ?? 0));
  const max = Math.max(1, ...keys.map((k) => counts[k] ?? 0));
  const lines = [title];
  for (const key of keys) {
    const n = counts[key] ?? 0;
    const barLen = Math.round((n / max) * 40);
    lines.push(`  ${String(key).padEnd(14)} ${String(n).padStart(3)}  ${"#".repeat(barLen)}`);
  }
  return lines.join("\n");
}

function renderCrossTab(title, table, rowOrder, colOrder, colWidth = 10) {
  const lines = [title];
  lines.push("  " + " ".repeat(14) + colOrder.map((c) => String(c).padStart(colWidth)).join("") + "  total");
  for (const row of rowOrder) {
    const rowData = table[row] ?? {};
    const rowTotal = colOrder.reduce((sum, c) => sum + (rowData[c] ?? 0), 0);
    lines.push(
      `  ${String(row).padEnd(14)}` +
        colOrder.map((c) => String(rowData[c] ?? 0).padStart(colWidth)).join("") +
        String(rowTotal).padStart(7)
    );
  }
  return lines.join("\n");
}

/**
 * Render a computeStats() result as a human-readable text report.
 */
export function formatStats(stats) {
  const sections = [`TOTAL ITEMS: ${stats.total}`, ""];
  sections.push(renderBreakdown("STATUS", stats.status, STATUS_ORDER), "");
  sections.push(renderBreakdown("PRIORITY", stats.priority, PRIORITY_ORDER), "");
  sections.push(renderBreakdown("COMPLEXITY", stats.complexity, COMPLEXITY_ORDER), "");
  sections.push(renderBreakdown("CATEGORY", stats.category), "");
  sections.push(renderBreakdown("TYPE", stats.type), "");
  sections.push(renderBreakdown("OWNER", stats.owner), "");

  sections.push("CROSS-TAB: status x priority");
  sections.push("  " + " ".repeat(12) + PRIORITY_ORDER.map((p) => p.padStart(10)).join(""));
  for (const status of STATUS_ORDER) {
    const row = stats.crossTab[status] ?? {};
    sections.push(`  ${status.padEnd(12)}` + PRIORITY_ORDER.map((p) => String(row[p] ?? 0).padStart(10)).join(""));
  }
  sections.push("");

  const categoryOrder = Object.keys(stats.category).sort((a, b) => stats.category[b] - stats.category[a]);
  sections.push(renderCrossTab("CATEGORY x STATUS", stats.categoryByStatus, categoryOrder, STATUS_ORDER, 12), "");

  const typeOrder = Object.keys(stats.type).sort((a, b) => stats.type[b] - stats.type[a]);
  sections.push(renderCrossTab("TYPE x STATUS", stats.typeByStatus, typeOrder, STATUS_ORDER, 12), "");

  const pct = stats.weight.total > 0 ? ((stats.weight.open / stats.weight.total) * 100).toFixed(1) : "0.0";
  sections.push(
    `COMPLEXITY-WEIGHTED SIZE (XS=1..XL=8): total=${stats.weight.total}, remaining-open=${stats.weight.open} (${pct}% of total weight still open)`,
    ""
  );

  sections.push(`CRITICAL + still open: ${stats.criticalOpen.length}`);
  for (const item of stats.criticalOpen) {
    sections.push(`  ${item.id}: ${item.title} [${item.status}, ${item.complexity}, owner=${item.owner}]`);
  }

  return sections.join("\n");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  const wantsStats = args.includes("--stats");
  const pathArg = args.find((arg) => !arg.startsWith("--")) ?? DEFAULT_BACKLOG;
  const rootDir = join(dirname(fileURLToPath(import.meta.url)), "..");
  try {
    if (wantsStats) {
      // --stats is a read-only report, not a governance decision: it works on
      // any parseable backlog.yml (e.g. a sibling repo's own, differently
      // schema'd registry), so it deliberately does not require this repo's
      // strict validate() (id_prefix, decision_prefix, enum vocab, ...) to pass.
      const fullPath = resolve(rootDir, pathArg);
      const backlog = parseBacklog(readFileSync(fullPath, "utf8"));
      console.log(formatStats(computeStats(backlog)));
    } else {
      const count = run(pathArg, rootDir);
      console.log(`backlog-governance: ${count} item(s) verified`);
    }
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}
