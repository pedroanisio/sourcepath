---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-09"
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

  report    Structural report (HTML / MD / JSON) from a bundle
  dossier   A4 PDF dossier, typeset with the Measured Ink design system
  pdf       Render an authored Markdown report to a themed PDF
  site      Generate the static bundle-browser site
  repair    Apply post-hoc data-quality fixes to an emitted bundle
```

The dispatcher routes to `scripts/cbm_report.py`, `cbm_dossier.py`,
`report_to_pdf.py`, `generate_static_site.py`, and `cbm_repair.py`,
which all remain independently runnable. Commands import lazily, so a
missing optional dependency (reportlab for `dossier`, weasyprint for
`pdf`) fails that command only, with an install hint.

Reports load the inventory through a persistent pyoxigraph store cached
per bundle (built once, re-opened in seconds); the first run on a very
large bundle pays the one-time store build.

## Verification

```
PATH="$PWD/.venv/bin:$PATH" make test    # full offline surface; needs the venv on PATH
python -m pytest tests/ -q               # pytest suite
python tests/verify_drift_p1.py          # doc/code drift checks
```

`make test` invokes bare `python`; without the venv on `PATH` it dies
with `python: not found` — and piping make's output hides the failure
exit code.
