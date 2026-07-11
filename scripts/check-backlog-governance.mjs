import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_BACKLOG = "docs/backlog.yml";
const BACKLOG_PATH = process.argv[2] ?? DEFAULT_BACKLOG;
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

  const mdPath = join(rootDir, dirname(backlogPath), "BACKLOG.md");
  if (existsSync(mdPath) && idPrefix) {
    const yamlIds = new Set(items.map((item) => item.id));
    const mdIds = idsInMarkdown(readFileSync(mdPath, "utf8"), idPrefix);
    const onlyYaml = [...yamlIds].filter((id) => !mdIds.has(id));
    const onlyMd = [...mdIds].filter((id) => !yamlIds.has(id));
    if (onlyYaml.length || onlyMd.length) {
      errors.push(`BACKLOG.md ids differ from backlog.yml (missing in md: ${onlyYaml.join(", ") || "none"}; extra in md: ${onlyMd.join(", ") || "none"})`);
    }
  }

  const schemaPath = join(rootDir, dirname(backlogPath), "schema/backlog.schema.json");
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
  const fullPath = join(rootDir, backlogPath);
  const backlog = parseBacklog(readFileSync(fullPath, "utf8"));
  const errors = validate(backlog, rootDir, backlogPath);
  if (errors.length > 0) {
    throw new Error(errors.map((error) => `backlog-governance: ${error}`).join("\n"));
  }
  return backlog.items.length;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const count = run(BACKLOG_PATH);
    console.log(`backlog-governance: ${count} item(s) verified`);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}
