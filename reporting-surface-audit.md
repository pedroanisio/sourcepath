---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Code (authoring model unrecorded); disclosure block added by Claude Fable 5 via Claude Code"
  date: "2026-07-26"
---

# Audit — Reporting Surface

*2026-07-26*

**Verdict:** the "multiple front doors" premise is wrong. `scripts/cbm.py` is a single dispatcher over 10 renderers. Each backing script stays independently runnable by design — the dispatcher only routes, so each tool's `argparse` remains the source of truth. Documented in `AGENTS.md` → *Reporting (unified CLI)*.

## Precondition: everything is downstream of a bundle

Every report command consumes a bundle directory (one containing `run_manifest.json`). Build it first:

```bash
codebase-mapper           --repo <path> --out _tmp/out   # L1 only
python scripts/run_l2.py  --repo <path> --out _tmp/out   # + chunks/embeddings
python scripts/run_l3.py  --repo <path> --out _tmp/out   # + concepts + xrefs
python scripts/run_l4.py  --repo <path> --out _tmp/out   # + LLM enrichment
```

## Command surface (`scripts/cbm.py`)

| Command | Output | Backing script | Requires |
|---|---|---|---|
| `report --bundle DIR [--formats html,md,json]` | Structural X-ray | `cbm_report.py` | pyoxigraph store build (first run slow on large bundles) |
| `report-rs BUNDLE [-o out.pdf]` | 8-page PDF, streaming recount | `cbm_report_rs.py` → Rust crate | `make build-report-rs` or `CBM_REPORT_BIN` |
| `dossier --bundle DIR` | 100+ page A4 PDF | `cbm_dossier.py` | `pip install -e ".[dossier]"` |
| `pdf INPUT.md [-o out.pdf] [--theme CSS]` | Authored Markdown → themed PDF | `report_to_pdf.py` | weasyprint; takes a `.md`, **not** a bundle |
| `site -b DIR -o _site` | Offline static site | `generate_static_site.py` | `[site]` extra |
| `cartogram BUNDLE [-o out.html]` | Interactive D3 region/flow map | `cbm_cartogram.py` → `tools/cbm-cartogram` | Node ≥20, L3 bundle (rejects L1) |
| `terrain --bundle DIR [--style terrain\|tolkien]` | Self-contained WebGL2 HTML map | `cbm_terrain.py` | L2+ bundle (`embeddings.npz`) |
| `walkthrough --bundle DIR` | Narrated five-scene HTML demo | `cbm_walkthrough.py` | — |
| `verify --bundle DIR` | Failing quality gate (hashes, budgets, degradations) | `cbm_verify.py` | — |
| `repair --bundle DIR` | Post-hoc data-quality fixes | `cbm_repair.py` | — |

Imports are lazy: a missing optional dependency breaks only that one command, with an install hint.

Omitting `-o/--out` sends output to `$CBM_REPORTS_DIR` (default `reports/`) as `<bundle>__<kind>__<UTC-timestamp>.<ext>`, with a `-2`/`-3` bump so runs never overwrite each other (`settings.py:144`).

**Full set:**

```bash
B=_tmp/out
python scripts/cbm.py report      --bundle $B --formats html,md,json
python scripts/cbm.py dossier     --bundle $B
python scripts/cbm.py site        --bundle $B --output _site
python scripts/cbm.py terrain     --bundle $B
python scripts/cbm.py cartogram   $B
python scripts/cbm.py walkthrough --bundle $B
make build-report-rs && python scripts/cbm.py report-rs $B
```

## Findings

**F1 — `make build-cartogram` duplicates `cbm.py cartogram`. Low priority.**
Genuine duplication: a second, lower-level path to the same HTML. Fix is known — reduce the make target to a thin delegation to `cbm.py cartogram`, or mark it dev-only in the Makefile header.

**F2 — Argument conventions are inconsistent across the dispatcher. Low priority.**
`--bundle DIR` vs. positional `BUNDLE` (`report-rs`, `cartogram`); `--out` vs. `--output` vs. `-o`. Fix is known — a shared parent parser accepting both a positional bundle and `--bundle`, plus `--out/-o` aliased everywhere. Deliberately deferred if breaking existing invocations matters.

**F3 — `pdf` is the odd command out. Low priority.**
It takes an authored `.md`, not a bundle — correct behaviour, but it violates the surface's one invariant. Fix: group it under a separate `--help` section labelled "authoring", not "bundle reports".

**F4 — Renderer size. Medium priority — needs your decision.**
`cbm_report.py` 79 KB, `cbm_dossier.py` 141 KB, `generate_static_site.py` 98 KB, plus two Rust/Node crates in `tools/`. This is real, but it is a size/architecture concern, not a CLI-discovery one; splitting has no obvious seam and touching three renderers at once carries regression risk. Options: (a) leave as-is and accept the maintenance cost; (b) extract shared bundle-loading and section-assembly into a common module and leave layout code in place; (c) full renderer decomposition. (b) is the low-risk middle — your call on whether it's worth the churn now.

## Out of scope (not counter-evidence)

- `python -m decomposer <bundle> --report OUT.md` and `python -m recomposer <yaml> --plan OUT.md` — Markdown analysis outputs, not routed through the dispatcher. Reporting-adjacent, distinct pipeline.
- `frontend/` (FastAPI + React + MCP server) is a bundle *viewer*, not a report generator — separate surface.

`python scripts/cbm.py --help` enumerates the whole reporting surface.
