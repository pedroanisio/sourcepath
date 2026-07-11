---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-10"
---

# AGENTS.md — programmatic CLI / tooling reference

The tooling reference for agents working on this repository. Read
[CLAUDE.md](./CLAUDE.md) first for process rules; this file only says
what to run. Flags shown here are entry points, not exhaustive lists —
every command answers `--help` from its own argparse, which is the
source of truth.

## Producing bundles (pipeline)

| Command | Layers | Notes |
|---|---|---|
| `codebase-mapper` (console script) | L1 | `codebase_mapper.cli:main`; map + optional roundtrip verify |
| `python scripts/run_l2.py` | L1+L2 | chunks + embeddings |
| `python scripts/run_l3.py` | L1–L3 | + concept graph; `--llm-enrich` shorthand for L4 defaults |
| `python scripts/run_l4.py` | L1–L4 | full pipeline; all L4 knobs surfaced |
| `python scripts/run_xrefs.py` | +xrefs | symbol cross-references |

Kernel-scale cost controls on `run_l4.py`: `--skip-shacl` and
`--no-jsonld` (both disclosed in `run_manifest.json`, never silent).
Concurrency knobs are environment variables; the complete inventory
with semantics lives in [.env.example](./.env.example) (enforced by
`tests/verify_drift_p1.py` — do not document env vars anywhere else).

## Reporting (unified CLI)

```
python scripts/cbm.py <command> [options]

  report     Structural report (HTML / MD / JSON) from a bundle
  report-rs  Rust-rendered PDF report (streams multi-GB inventories)
  dossier    A4 PDF dossier, typeset with the Measured Ink design system
  pdf        Render an authored Markdown report to a themed PDF
  site       Generate the static bundle-browser site
  cartogram    Interactive Cartogram map (regions + import/test flows)
  verify       Re-verify a bundle's hash claims / quality gate
  repair       Apply post-hoc data-quality fixes to an emitted bundle
  terrain      SourcePath 3D code-terrain map (self-contained HTML)
  walkthrough  Narrated five-scene customer walkthrough (HTML)
```

The dispatcher routes to `scripts/cbm_report.py`, `cbm_report_rs.py`,
`cbm_dossier.py`, `report_to_pdf.py`, `generate_static_site.py`,
`cbm_repair.py`, and `cbm_walkthrough.py`, which all remain
independently runnable. Commands
import lazily, so a missing optional dependency (reportlab for
`dossier`, weasyprint for `pdf`) fails that command only, with an
install hint.

Two structural read paths exist by design — pick by bundle size:

- `report` / `dossier` (Python) load the inventory through a persistent
  pyoxigraph store cached per bundle (built once, re-opened in
  seconds); the first run on a very large bundle pays the one-time
  store build. This path does the graph analytics (chokepoints, SHACL,
  test evidence, t-SNE districts).
- `report-rs` (Rust, `tools/cbm-report`) never loads a graph store: it
  streams `inventory.jsonld` in fixed-size blocks and recounts it
  independently of the manifest. Use it when the bundle is multi-GB and
  the question is "render the health/epistemics PDF now". Needs a
  compiled binary — `cargo build --release --manifest-path
  tools/cbm-report/Cargo.toml` — or `CBM_REPORT_BIN=<path>`.

`terrain` (`scripts/cbm_terrain.py`) emits one self-contained WebGL2
HTML map per bundle: seeded t-SNE geography over per-directory mean
chunk embeddings, chunk-density elevation, and the L1 graph as roads,
build-tide layers, impact floods, path tracing, and stress fault
lines. `--max-segments 0` (default) auto-fits the directory roll-up
under `--max-points`; keep `--seed` fixed per repo — stable geography
is the feature. Requires an L2+ bundle (`embeddings.npz` present).

## Analysis (decompose / recompose)

| Command | Consumes | Emits |
|---|---|---|
| `python -m decomposer <bundle_dir> [--yaml OUT] [--report OUT.md] [--symbols OUT.yaml]` | bundle dir | confidence-tagged decomposition YAML + Markdown report + symbol-map sidecar |
| `python -m recomposer <decomposition.yaml> [--plan OUT.md] [--yaml OUT]` | Decomposer YAML only | ordered natural-language build plan |

Both print a short stdout summary when run without output flags. The
recomposer never reads the bundle — the decomposition YAML is its whole
evidence surface.

## Verification

```
PATH="$PWD/.venv/bin:$PATH" make test    # full offline surface; needs the venv on PATH
python -m pytest tests/ -q               # pytest suite
python tests/verify_drift_p1.py          # doc/code drift checks
```

`make test` invokes bare `python`; without the venv on `PATH` it dies
with `python: not found` — and piping make's output hides the failure
exit code.
