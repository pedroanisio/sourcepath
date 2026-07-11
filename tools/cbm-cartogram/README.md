---
disclaimer:
  notice: >-
    No information within this document should be taken for granted. Any statement
    or premise not backed by a real logical definition or verifiable reference may
    be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-10"
---

# Cartogram

A self-contained, framework-free D3 renderer that maps **any** repository as an
interactive cartogram: top-level directories become **regions**, and one canonical
software graph is shown through two explicit, reversible projections.

- **Imports** — canonical `consumer imports provider` relations projected as
  `provider → consumer` (capability flow).
- **Tests** — canonical `test tests subject` relations projected as
  `subject → test` (validation flow).

The renderer uses HTML Canvas for scale and D3.js for hierarchy, circle-packing,
paths, zoom, quadtrees, interpolation, and interaction. No application framework
and no runtime network request is required.

> The visualizer was originally prototyped with a human-body-systems metaphor
> (vascular / lymphatic). That was inspiration only; the shipped tool represents
> repositories with neutral cartographic vocabulary — regions, imports, tests.

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Input: a code-base-mapper bundle

Cartogram consumes a `code-base-mapper` bundle's `inventory.jsonld` — it reads the
native `cbm:` / `cbml2:` / `cbml3:` / `skos:` CURIEs directly, with no predicate
remapping.

**It requires an L3/L4 bundle.** A bare `codebase-mapper` run emits only L1
(`cbm:File`/import/package triples) with zero chunks and zero concepts; the
normalizer **refuses** such a bundle rather than render an empty cartogram.
Produce a valid bundle with:

```bash
python scripts/run_l3.py --repo <repo> --out <dir>   # or run_l4.py for LLM summaries
```

## Build

From the repository root (Node ≥ 20 required):

```bash
make build-cartogram INVENTORY=<dir>/inventory.jsonld
```

This runs the normalizer (`inventory.jsonld → data/atlas-data.js`) and the
standalone bundler (`→ cbm-cartogram-standalone.html`, everything inlined).
Set `SOURCE_DATE_EPOCH` for a byte-reproducible build. Equivalent direct calls:

```bash
node tools/normalize-inventory.mjs <dir>/inventory.jsonld data/atlas-data.js
node tools/build-standalone.mjs
```

The generated `data/atlas-data.*` and `cbm-cartogram-standalone.html` are large and
rebuildable, so they are git-ignored — treat them as build outputs, not source.

## Open

Open `cbm-cartogram-standalone.html` in a modern browser (it inlines the data, D3,
styles, model, and renderer). For the source build, serve the directory and open
`index.html` (e.g. `python3 -m http.server 8000`).

## Controls

| Control | Action |
|---|---|
| `1`, `2`, `3` | Combined, Imports, or Tests projection |
| Mouse wheel | Zoom |
| Drag | Pan |
| Double-click | Focus selected structure |
| `/` | Search files, chunks, concepts, packages, regions, and suites |
| `F` | Fit the whole map |
| `Esc` | Clear selection |

Low zoom uses region bundles as a level-of-detail representation. Zooming restores
individual mapped relations and per-file symbols; aggregation never removes
semantic facts.

## Themes

Cartogram ships six palettes — **Default**, **Crimson Classic**, **Cyan Circuit**,
**Ultramarine Gold**, **Forest Amber**, **Graphite Magenta** — each with a **dark**
and **light** mode. Pick one from the top-bar selector; the light/dark toggle sits
beside it, and your choice persists in `localStorage`. Every color resolves from a
single source (`src/themes.js`) that drives both the Canvas renderer and the CSS
chrome, so the two never drift.

### Custom palettes (API)

Themes are **presentation-only** — they never change the data or the projection
semantics. Register your own before the renderer initializes (e.g. an inline
`<script>` before `atlas.js`):

```js
CartogramThemes.register({
  id: "my-brand",
  label: "My Brand",
  inheritDefaults: true,            // unspecified tokens fall back to the default palette
  modes: {
    dark:  { importEdge: "#e5484d", testEdge: "#3b82f6" },
    light: { importEdge: "#b91c1c", testEdge: "#1d4ed8" },
  },
});
```

Omit `inheritDefaults` to supply a complete palette — every token in
`CartogramThemes.REQUIRED_TOKENS`, in both modes; an incomplete theme without it is
rejected (a guardrail test enforces this). Full API: `list()`, `get(id)`, `has(id)`,
`resolve(id, mode)`, `canvasColors(id, mode)`, `cssVars(id, mode)`, `register(theme)`.

## Projection invariants

1. Canonical relation direction is preserved in the normalized data.
2. Every direction reversal is explicit in the projection model (`directionTransform: "reverse"`).
3. Each importing consumer receives at most one primary provider.
4. All remaining imports stay visible as secondary imports.
5. Parent cycles are cut only in the visual forest and remain preserved as secondary relations.
6. Synthetic routing junctions and suite/gate collectors map to no software artifact.
7. Every test artifact remains present even without an explicit `cbm:tests` target.
8. Every chunk remains traceable to its containing file and line range.

## Tests

```bash
make test-cartogram      # Node model tests (node --test)
make lint-cartogram      # parse-check every JS/MJS file
```

The suite verifies endpoint validity, complete relation preservation, the imports
and tests reversal semantics, primary-parent uniqueness, acyclic primary layout,
complete aggregate membership, chunk traceability, and identifier uniqueness.

## Layout

```text
tools/cbm-cartogram/
├── index.html                  # source-build entry (scripts loaded in order)
├── src/
│   ├── model.js                # pure, tested projection model
│   ├── themes.js               # palette token module + customization API
│   ├── atlas.js                # Canvas + D3 renderer
│   └── atlas.css               # styles / design tokens
├── tools/
│   ├── normalize-inventory.mjs # inventory.jsonld → data/atlas-data.js
│   └── build-standalone.mjs    # inline everything into one HTML
├── tests/model.test.mjs        # Node model tests
└── vendor/
    ├── d3.v7.min.js            # vendored D3 (v7.9.0)
    └── D3-LICENSE.txt
```

---

Part of [code-base-mapper](../../README.md); wired via the root `Makefile`
(`build-cartogram`, `test-cartogram`, `lint-cartogram`).
