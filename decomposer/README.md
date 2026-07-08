---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-08"
---

# Repository Decomposer / Recomposer

[⬆ Back to project root](../README.md)

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

A language-agnostic system that reads a codebase-mapper **bundle** (the RDF/JSON
artifacts under `_tmp/<name>/`) and decomposes the repository into its meaningful
parts — then (second delivery) recomposes an ordered, natural-language plan to
rebuild the system from scratch.

The decomposer consumes **only verifiable, bundle-derived evidence**. Every
conclusion is confidence-tagged; nothing is inferred from memory or convention
unless explicitly labeled a hypothesis (PALS's Law — see `../CLAUDE.md`).

## Delivery status

| Delivery | Component | Status |
|---|---|---|
| First | **Decomposer** | ✅ implemented (this package) |
| Second | **Recomposer** | 🔵 designed; consumes the Decomposer YAML only |

## Usage

```bash
# Emit YAML decomposition + Markdown report for a bundle
python -m decomposer _tmp/cbm-itself \
    --yaml   _tmp/cbm-itself.decomposition.yaml \
    --report _tmp/cbm-itself.decomposition.md

# Quick summary to stdout
python -m decomposer _tmp/cbm-itself
```

Programmatic:

```python
from decomposer import decompose, to_yaml, to_markdown
d = decompose("_tmp/cbm-itself")
open("out.yaml", "w").write(to_yaml(d))
open("out.md", "w").write(to_markdown(d))
```

## What it produces

* **Parts** at two granularities — *modules* (directory subtrees; the primary
  analytical unit) and *cross-cutting* parts (applications/services, external
  dependencies, entry points, semantic domains, data schemas, generated
  artifacts). Structurally significant files are promoted to their own parts;
  ordinary files roll up into their module's evidence ("meaningful parts", not a
  file dump).
* **Classification** per part — role (core / supporting / infrastructure /
  adapter / test / generated), layer, Martin **instability** `I = Ce/(Ca+Ce)`,
  reusability, and risk — each with a confidence label.
* **Relationships** — module→module imports, external imports, tests, and
  aggregated call edges, with strength counts.
* **Detected architecture** — signal-scored style with evidence, candidate
  hypotheses, and violations (cycles, shared-kernel leakage, bidirectional
  coupling).
* **Quality gates** (Part IV) — circular dependencies, god modules, dead-code
  candidates, hidden entry points, duplicated responsibilities, test gaps,
  ambiguous ownership, generated-code-as-dependency, and missing evidence.
* **Build order** — a topological, SCC-condensed ordering of modules that the
  Recomposer turns into reconstruction steps.

## Confidence ladder

`certain` (graph-proven) › `strong` (multiple signals) › `probable`
(naming/location) › `weak` (under-evidenced) › `unknown`.

## Module map

| Module | Responsibility |
|---|---|
| `model.py` | Dataclasses + `Confidence`; serializes to the Part II schema |
| `evidence.py` | Single seam to the bundle (`load_bundle` + phase augmentation) |
| `metrics.py` | Instability, Tarjan SCC/cycles, topological build order |
| `parts.py` | Module graph + part extraction |
| `classify.py` | Role / layer / stability / reusability / risk rules |
| `architecture.py` | Style detection + violations |
| `quality.py` | Part IV quality gates |
| `decompose.py` | Orchestrator → `Decomposition` |
| `serialize.py` / `report.py` | YAML / Markdown emitters |
| `cli.py` | `python -m decomposer` |

Tests: `tests/decomposer/`.

## Design & references

Coupling and instability metrics follow Robert C. Martin, *OO Design Quality
Metrics* (1994) and *Agile Software Development* (2002, ch. 20). SCC detection
follows Tarjan (1972). The full architecture, data model, algorithms, risks, and
acceptance criteria are documented in the design that accompanied this delivery.
