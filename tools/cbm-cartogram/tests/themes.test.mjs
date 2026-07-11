import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// themes.js is a browser-global IIFE (like model.js): load it into a vm context
// with a window global and read the attached CartogramThemes registry. It is
// DOM-free and pure, so no jsdom is required.
const context = vm.createContext({ console, Intl, Math, Map, Set, Object, Array, JSON, String, Number });
context.window = context;
context.globalThis = context;
vm.runInContext(fs.readFileSync(path.join(root, "src/themes.js"), "utf8"), context, { filename: "themes.js" });

const Themes = context.CartogramThemes;
const MODES = ["dark", "light"];
const PRESETS = ["default", "crimson-classic", "cyan-circuit", "ultramarine-gold", "forest-amber", "graphite-magenta"];

const isColor = (v) => typeof v === "string" && v.trim().length > 0;

test("registry exposes its API and the required-token contract", () => {
  assert.equal(typeof Themes, "object");
  for (const fn of ["list", "get", "has", "resolve", "canvasColors", "cssVars", "register"]) {
    assert.equal(typeof Themes[fn], "function", `missing API: ${fn}`);
  }
  assert.ok(Array.isArray(Themes.REQUIRED_TOKENS));
  assert.ok(Themes.REQUIRED_TOKENS.length >= 30, "expected a substantial canonical token set");
  assert.ok(Themes.REQUIRED_TOKENS.includes("bg") && Themes.REQUIRED_TOKENS.includes("importEdge") && Themes.REQUIRED_TOKENS.includes("testEdge"));
});

test("all five studies plus the default are registered", () => {
  const ids = Themes.list().map((t) => t.id);
  for (const id of PRESETS) assert.ok(ids.includes(id), `missing preset: ${id}`);
  for (const t of Themes.list()) {
    assert.ok(isColor(t.label) || typeof t.label === "string", `theme ${t.id} needs a label`);
  }
});

test("every preset resolves a complete token set in both modes", () => {
  for (const id of PRESETS) {
    for (const mode of MODES) {
      const tokens = Themes.resolve(id, mode);
      for (const key of Themes.REQUIRED_TOKENS) {
        assert.ok(isColor(tokens[key]), `theme ${id}/${mode} missing token ${key}`);
      }
    }
  }
});

test("canvasColors projects every renderer color key the atlas consumes", () => {
  const CANVAS_KEYS = [
    "background", "grid", "regionFill", "regionStroke", "directoryStroke", "node",
    "production", "documentation", "test", "gate", "config", "asset", "unknown",
    "import", "importHot", "importDark", "secondaryImport", "external",
    "testSoft", "collection", "selected", "chunk", "warning", "dim", "text", "muted",
  ];
  for (const id of PRESETS) {
    for (const mode of MODES) {
      const c = Themes.canvasColors(id, mode);
      for (const key of CANVAS_KEYS) assert.ok(isColor(c[key]), `canvas ${id}/${mode} missing ${key}`);
    }
  }
});

test("cssVars projects the :root custom properties used by the chrome", () => {
  const CSS_VARS = [
    "--bg", "--bg-deep", "--panel", "--panel-strong", "--border", "--border-strong",
    "--text", "--muted", "--import", "--import-hot", "--import-dark", "--secondary-import",
    "--external", "--test", "--test-soft", "--quality", "--node", "--success", "--warning", "--danger",
  ];
  const vars = Themes.cssVars("default", "dark");
  for (const key of Object.keys(vars)) assert.ok(key.startsWith("--"), `css var must start with --: ${key}`);
  for (const key of CSS_VARS) assert.ok(isColor(vars[key]), `cssVars missing ${key}`);
});

test("the CSS/canvas drift is unified — same concept resolves to one value", () => {
  for (const mode of MODES) {
    const c = Themes.canvasColors("default", mode);
    const v = Themes.cssVars("default", mode);
    assert.equal(c.background, v["--bg"], "background and --bg must be the same token");
    assert.equal(c.import, v["--import"]);
    assert.equal(c.testSoft, v["--test-soft"]);
    assert.equal(c.collection, v["--quality"]);
  }
});

test("register accepts a complete custom theme and rejects an incomplete one (guardrail)", () => {
  const complete = { id: "custom-x", label: "Custom X", modes: { dark: {}, light: {} } };
  for (const mode of MODES) for (const key of Themes.REQUIRED_TOKENS) complete.modes[mode][key] = "#123456";
  Themes.register(complete);
  assert.ok(Themes.has("custom-x"));
  assert.equal(Themes.resolve("custom-x", "dark").importEdge, "#123456");

  const broken = { id: "custom-bad", label: "Bad", modes: { dark: { bg: "#000" }, light: {} } };
  assert.throws(() => Themes.register(broken), /token|missing|incomplete/i, "must reject an incomplete theme");
  assert.ok(!Themes.has("custom-bad"), "a rejected theme must not be registered");
});

test("brand overrides layer over the neutral base without dropping neutral tokens", () => {
  // a theme supplying only brand colors still resolves neutrals (grid, borders, roles)
  const brandOnly = { id: "brand-only", label: "Brand Only", modes: { dark: { importEdge: "#abcdef" }, light: { importEdge: "#abcdef" } }, inheritDefaults: true };
  Themes.register(brandOnly);
  const t = Themes.resolve("brand-only", "dark");
  assert.equal(t.importEdge, "#abcdef");
  assert.ok(isColor(t.grid) && isColor(t.border) && isColor(t.production), "neutral tokens must still be present");
});

test("unknown theme or mode fails loudly", () => {
  assert.throws(() => Themes.resolve("nope", "dark"), /unknown|not found/i);
  assert.throws(() => Themes.resolve("default", "bogus"), /mode/i);
});

test("swatch() returns a preview strip drawn from the resolved palette", () => {
  assert.equal(typeof Themes.swatch, "function");
  for (const id of PRESETS) {
    for (const mode of MODES) {
      const strip = Themes.swatch(id, mode);
      assert.ok(Array.isArray(strip), `swatch ${id}/${mode} must be an array`);
      assert.ok(strip.length >= 5 && strip.length <= 8, `swatch ${id}/${mode} length out of range: ${strip.length}`);
      const palette = new Set(Object.values(Themes.resolve(id, mode)));
      for (const c of strip) {
        assert.ok(isColor(c), `swatch ${id}/${mode} has a non-color entry`);
        assert.ok(palette.has(c), `swatch ${id}/${mode} color ${c} is not part of the resolved palette`);
      }
    }
  }
});

test("swatch() is mode-sensitive (dark and light previews differ)", () => {
  for (const id of PRESETS) {
    const dark = Themes.swatch(id, "dark").join("|");
    const light = Themes.swatch(id, "light").join("|");
    assert.notEqual(dark, light, `swatch for ${id} should differ between dark and light`);
  }
});

test("swatch() carries the projection identity (import + test hues)", () => {
  for (const id of PRESETS) {
    for (const mode of MODES) {
      const strip = Themes.swatch(id, mode);
      const tokens = Themes.resolve(id, mode);
      assert.ok(strip.includes(tokens.importEdge), `swatch ${id}/${mode} must include the import hue`);
      assert.ok(strip.includes(tokens.testEdge), `swatch ${id}/${mode} must include the test hue`);
    }
  }
});
