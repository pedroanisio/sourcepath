#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");
const safeScript = (source) => source.replaceAll("</script", "<\\/script");

let html = read("index.html");
html = html.replace('<link rel="stylesheet" href="src/atlas.css">', () => `<style>\n${read("src/atlas.css")}\n</style>`);
html = html.replace(/\n\s*<script src="vendor\/d3\.v7\.min\.js"><\/script>/, () => `\n<script>\n${safeScript(read("vendor/d3.v7.min.js"))}\n</script>`);
html = html.replace(/\n\s*<script src="data\/atlas-data\.js"><\/script>/, () => `\n<script>\n${safeScript(read("data/atlas-data.js"))}\n</script>`);
html = html.replace(/\n\s*<script src="src\/model\.js"><\/script>/, () => `\n<script>\n${safeScript(read("src/model.js"))}\n</script>`);
html = html.replace(/\n\s*<script src="src\/atlas\.js"><\/script>/, () => `\n<script>\n${safeScript(read("src/atlas.js"))}\n</script>`);

const output = process.argv[2] ?? path.join(root, "cbm-cartogram-standalone.html");
fs.writeFileSync(output, html, "utf8");
console.log(`${output} (${fs.statSync(output).size} bytes)`);
