(function attachCartogramThemes(global) {
  "use strict";

  // Canonical semantic token set. Every theme, in every mode, must resolve all
  // of these — the guardrail test asserts it, and register() rejects any theme
  // that does not. Two surfaces consume these: the Canvas renderer (via
  // canvasColors) and the CSS chrome (via cssVars). One value per concept — this
  // is what removes the historical CSS-vs-JS palette drift.
  const REQUIRED_TOKENS = [
    // structural neutrals
    "bg", "bgDeep", "panel", "panelStrong", "border", "borderStrong",
    "text", "muted", "grid", "regionFill", "regionStroke", "directoryStroke",
    "node", "selected", "dim",
    // role colors
    "production", "documentation", "gate", "config", "asset", "unknown", "chunk",
    // status
    "success", "warning", "danger",
    // brand (projection) colors
    "importEdge", "importHot", "importDark", "secondaryImport", "external",
    "testEdge", "testSoft", "quality",
  ];

  const MODES = ["dark", "light"];

  // Structural + role + status neutrals per mode. Themes override brand colors
  // on top of these (extendsNeutral), so a study only declares its signature.
  const NEUTRAL = {
    dark: {
      bg: "#071019", bgDeep: "#04080e",
      panel: "rgba(10, 20, 31, 0.84)", panelStrong: "rgba(10, 20, 31, 0.95)",
      border: "rgba(190, 220, 238, 0.14)", borderStrong: "rgba(190, 220, 238, 0.24)",
      text: "#eff8fb", muted: "#8fa8b8",
      grid: "rgba(151, 190, 207, 0.055)",
      regionFill: "rgba(39, 67, 81, 0.13)", regionStroke: "rgba(141, 188, 209, 0.24)",
      directoryStroke: "rgba(152, 189, 205, 0.12)",
      node: "#d9c8bd", selected: "#ffffff", dim: "rgba(180, 198, 207, 0.12)",
      production: "#e2c7ba", documentation: "#7f93a0", gate: "#738fff",
      config: "#65cda4", asset: "#899aa5", unknown: "#a9a0a0", chunk: "#74e0b6",
      success: "#58d6a7", warning: "#ffbf69", danger: "#ff6482",
    },
    light: {
      bg: "#f4f7fa", bgDeep: "#e7edf3",
      panel: "rgba(255, 255, 255, 0.86)", panelStrong: "rgba(255, 255, 255, 0.96)",
      border: "rgba(22, 45, 66, 0.14)", borderStrong: "rgba(22, 45, 66, 0.26)",
      text: "#0f1c26", muted: "#546675",
      grid: "rgba(30, 60, 80, 0.06)",
      regionFill: "rgba(80, 110, 130, 0.08)", regionStroke: "rgba(60, 100, 120, 0.30)",
      directoryStroke: "rgba(60, 100, 120, 0.16)",
      node: "#6b5e54", selected: "#0f1c26", dim: "rgba(30, 60, 80, 0.12)",
      production: "#8a4a36", documentation: "#5a6b78", gate: "#3a58d0",
      config: "#1f8f66", asset: "#6a7681", unknown: "#7a7070", chunk: "#12855f",
      success: "#1f8f66", warning: "#b9781a", danger: "#d1345a",
    },
  };

  // Study palettes (brand colors only; neutrals inherited). Hues are derived by
  // eye from the reference studies in _process/ — tune freely via register().
  const PRESETS = [
    {
      id: "default", label: "Default",
      dark: { importEdge: "#ff6b62", importHot: "#ffd0bb", importDark: "#8f1f31", secondaryImport: "#f1a05a", external: "#ffcc77", testEdge: "#55c7ff", testSoft: "#9ee8ff", quality: "#6b8cff" },
      light: { importEdge: "#d63a45", importHot: "#ff8f7a", importDark: "#7d1526", secondaryImport: "#c8721f", external: "#b8860b", testEdge: "#1f78c8", testSoft: "#4aa3e6", quality: "#3a58d0" },
    },
    {
      id: "crimson-classic", label: "Crimson Classic",
      dark: { importEdge: "#e23b4e", importHot: "#ffb3b0", importDark: "#6d1020", secondaryImport: "#ef8f6a", external: "#f2c14e", testEdge: "#4a90d9", testSoft: "#9fd0ff", quality: "#6b7fd6" },
      light: { importEdge: "#b81f38", importHot: "#e8564f", importDark: "#5a0d1a", secondaryImport: "#c26a2a", external: "#b07d18", testEdge: "#1f6fc0", testSoft: "#4a9be0", quality: "#3f52c0" },
    },
    {
      id: "cyan-circuit", label: "Cyan Circuit",
      dark: { importEdge: "#22d3ee", importHot: "#baf7ff", importDark: "#0b5560", secondaryImport: "#38bdf8", external: "#7dd3fc", testEdge: "#a78bfa", testSoft: "#d8ccff", quality: "#34d399" },
      light: { importEdge: "#0e8fa8", importHot: "#22c3dd", importDark: "#084652", secondaryImport: "#1f8fd0", external: "#2a7fb8", testEdge: "#7c56e0", testSoft: "#a98fe8", quality: "#10996f" },
    },
    {
      id: "ultramarine-gold", label: "Ultramarine Gold",
      dark: { importEdge: "#3b5bdb", importHot: "#9db4ff", importDark: "#1c2b7a", secondaryImport: "#6d7fe0", external: "#f5c451", testEdge: "#35c6c0", testSoft: "#a7ece8", quality: "#f0b429" },
      light: { importEdge: "#2a3fb0", importHot: "#5b74d8", importDark: "#16205e", secondaryImport: "#4a5bc0", external: "#cf9a1e", testEdge: "#178f89", testSoft: "#4ac2bb", quality: "#c98a10" },
    },
    {
      id: "forest-amber", label: "Forest Amber",
      dark: { importEdge: "#6aa84f", importHot: "#cfe6b0", importDark: "#2f5320", secondaryImport: "#a3c46a", external: "#e0a83a", testEdge: "#40b3a2", testSoft: "#a9e8de", quality: "#d99a2b" },
      light: { importEdge: "#4a7f36", importHot: "#7cae5a", importDark: "#234015", secondaryImport: "#7a9a3f", external: "#c1861f", testEdge: "#1f8f80", testSoft: "#4ec2b4", quality: "#b57d18" },
    },
    {
      id: "graphite-magenta", label: "Graphite Magenta",
      dark: { bg: "#14121a", bgDeep: "#0c0a12", importEdge: "#d6409f", importHot: "#f7b8e0", importDark: "#6d1a52", secondaryImport: "#e06ab0", external: "#f0a6d0", testEdge: "#4dd0e1", testSoft: "#b3edf5", quality: "#9b6bff" },
      light: { bg: "#f3f0f5", bgDeep: "#e7e2ec", importEdge: "#b12a80", importHot: "#d64fa0", importDark: "#591542", secondaryImport: "#c04f95", external: "#c66aad", testEdge: "#1f9aad", testSoft: "#52c2d2", quality: "#7a4fd0" },
    },
  ];

  // The default palette's brand colors, and the complete BASE palette a custom
  // theme inherits from when it opts in via `inheritDefaults` (so it may override
  // any subset of tokens and the rest fall back to the default look).
  const DEFAULT_BRAND = { dark: PRESETS[0].dark, light: PRESETS[0].light };
  const BASE = {
    dark: Object.assign({}, NEUTRAL.dark, DEFAULT_BRAND.dark),
    light: Object.assign({}, NEUTRAL.light, DEFAULT_BRAND.light),
  };

  // canonical token -> Canvas COLORS key
  const CANVAS_MAP = {
    background: "bg", grid: "grid", regionFill: "regionFill", regionStroke: "regionStroke",
    directoryStroke: "directoryStroke", node: "node", production: "production",
    documentation: "documentation", test: "testEdge", gate: "gate", config: "config",
    asset: "asset", unknown: "unknown", import: "importEdge", importHot: "importHot",
    importDark: "importDark", secondaryImport: "secondaryImport", external: "external",
    testSoft: "testSoft", collection: "quality", selected: "selected", chunk: "chunk",
    warning: "warning", dim: "dim", text: "text", muted: "muted",
  };

  // canonical token -> CSS custom property
  const CSS_MAP = {
    "--bg": "bg", "--bg-deep": "bgDeep", "--panel": "panel", "--panel-strong": "panelStrong",
    "--border": "border", "--border-strong": "borderStrong", "--text": "text", "--muted": "muted",
    "--import": "importEdge", "--import-hot": "importHot", "--import-dark": "importDark",
    "--secondary-import": "secondaryImport", "--external": "external", "--test": "testEdge",
    "--test-soft": "testSoft", "--quality": "quality", "--node": "node",
    "--success": "success", "--warning": "warning", "--danger": "danger",
  };

  const isColor = (v) => typeof v === "string" && v.trim().length > 0;
  const registry = new Map();

  function assertMode(mode) {
    if (!MODES.includes(mode)) throw new Error(`CartogramThemes: unknown mode "${mode}" (expected dark|light)`);
  }

  function mergedForMode(theme, mode) {
    const base = theme.inheritDefaults ? BASE[mode] : {};
    const supplied = (theme.modes && theme.modes[mode]) || theme[mode] || {};
    return Object.assign({}, base, supplied);
  }

  function register(theme) {
    if (!theme || typeof theme.id !== "string" || !theme.id) {
      throw new Error("CartogramThemes.register: theme needs a string id");
    }
    for (const mode of MODES) {
      const resolved = mergedForMode(theme, mode);
      const missing = REQUIRED_TOKENS.filter((k) => !isColor(resolved[k]));
      if (missing.length) {
        throw new Error(`CartogramThemes.register: theme "${theme.id}" mode "${mode}" is incomplete — missing token(s): ${missing.join(", ")}`);
      }
    }
    registry.set(theme.id, {
      id: theme.id,
      label: typeof theme.label === "string" && theme.label ? theme.label : theme.id,
      inheritDefaults: !!theme.inheritDefaults,
      modes: { dark: mergedForMode(theme, "dark"), light: mergedForMode(theme, "light") },
    });
    return theme.id;
  }

  function has(id) { return registry.has(id); }
  function get(id) { return registry.get(id); }
  function list() { return [...registry.values()].map((t) => ({ id: t.id, label: t.label })); }

  function resolve(id, mode) {
    assertMode(mode);
    const theme = registry.get(id);
    if (!theme) throw new Error(`CartogramThemes.resolve: unknown theme "${id}"`);
    return Object.assign({}, theme.modes[mode]);
  }

  function project(map, id, mode) {
    const tokens = resolve(id, mode);
    const out = {};
    for (const key of Object.keys(map)) out[key] = tokens[map[key]];
    return out;
  }

  function canvasColors(id, mode) { return project(CANVAS_MAP, id, mode); }
  function cssVars(id, mode) { return project(CSS_MAP, id, mode); }

  // register the built-in studies (validates them at load — a typo fails loudly)
  for (const p of PRESETS) {
    register({ id: p.id, label: p.label, inheritDefaults: true, modes: { dark: p.dark, light: p.light } });
  }

  global.CartogramThemes = Object.freeze({
    REQUIRED_TOKENS: Object.freeze(REQUIRED_TOKENS.slice()),
    MODES: Object.freeze(MODES.slice()),
    DEFAULT_ID: "default",
    list, get, has, resolve, canvasColors, cssVars, register,
  });
})(typeof window !== "undefined" ? window : globalThis);
