---
title: "Architectural Analysis — <repo>"
subtitle: "one-line positioning of what this report answers"
masthead:
  - Bundle: <bundle-name>
  - Commit: <short-sha>
  - Layers: L1+L2+L3
verdict:
  score: "0.0"
  max: "10"
  grade: "—"
  summary: >-
    One paragraph. State the finding, not the process. Inline *Markdown*
    is allowed here.
disclaimer:
  label: "Evidence basis & confidence"
  notice: >-
    Structural findings in this report are mechanically extracted from the
    bundle's RDF graph and are evidence-backed. Architectural, behavioral,
    and security interpretations are LLM-synthesized, confidence-tagged per
    section, and must be validated before high-stakes decisions.
  generated_by: "<model/tool identifier>"
  date: "<YYYY-MM-DD>"
footer: "<repo> · architectural analysis"
---

# Architectural Analysis — &lt;repo&gt;

Starting point for a new authored report. Copy this file, replace every
`<placeholder>`, and render it. Back to [docs/reports/](./) and the root
[README.md](../../README.md).

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

---

## How to render

```bash
python scripts/cbm.py pdf docs/reports/<name>.md          # -> $CBM_REPORTS_DIR/<name>__authored__<ts>.pdf
python scripts/cbm.py pdf docs/reports/<name>.md -o my.pdf --html
```

The PDF lands under `CBM_REPORTS_DIR` (default `reports/`, gitignored);
`docs/reports/` holds the authored Markdown, which is version-controlled
input, not build output. `--html` also writes the intermediate HTML beside
the PDF.

## The disclaimer block is mandatory

`scripts/report_to_pdf.py` **refuses to render** a file whose frontmatter has
no `disclaimer.notice` — the banner is required output, and a silent render
without it was the defect that rule exists to prevent (`__file_meta__` rule
`keep-disclaimer`, severity error; PALS's LAW).

The `label` above is the operator-approved override for the report pipeline
(CLAUDE.md §5): it *splits* the disclosure rather than weakening it —
mechanically derived graph facts are not hallucinations and are not labelled
as such, while every interpretive claim stays explicitly marked unverified.
Do not replace it with a blanket "nothing here is reliable" notice, and do
not delete it.

## Authoring primitives

### Confidence-tagged headings

Append `{confidence: low|medium|high}` to any heading; it renders as a
colored tag beside the heading text.

## 1. Coupling posture {confidence: medium}

Body text. Cite the mechanical source of every structural number — a manifest
count, a graph query, a verifier — so a reader can re-derive it.

### Callouts

Types: `info`, `note`, `caution`, `risk`.

::: risk  Primary risk (one-line title)
Body Markdown of the callout. Reserve `risk` for findings that change a
build-or-buy, ship-or-hold, or remediation-priority decision.
:::

::: note  Evidence
Mechanical: 412 files, 1,308 import edges, 87 % resolved (`run_manifest.json`).
Interpretive: the layering read below is LLM-synthesized from that graph.
:::

### Vector charts

Charts render as inline SVG, so they stay crisp at print resolution. A bad
spec degrades to an error callout instead of killing the render.

```chart
{"type": "bar",
 "title": "Files by language",
 "data": [["Python", 214], ["TypeScript", 118], ["Rust", 46], ["Other", 34]]}
```

### Tables

| Finding | Basis | Confidence |
|---|---|---|
| 3 import cycles in `core/` | mechanical — graph query | certain |
| Cycles reflect a missing ports layer | interpretive — LLM | medium |

---

## Checklist before rendering

- [ ] Every `<placeholder>` replaced, including `generated_by` and `date`.
- [ ] Every structural number cites the artifact it came from.
- [ ] Every interpretive claim carries a confidence tag or sits in a callout
      that names it as interpretation.
- [ ] No claim, citation, or API signature that was not verified against the
      bundle or the source (CLAUDE.md §2 — fabricating a reference is a
      critical failure; "I cannot verify this" is always acceptable).
