#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report_to_pdf.py — render an authored Markdown report to a polished PDF.

This is the *standard* pipeline for codebase-mapper analysis reports. It turns a
Markdown file (with a small set of report-specific authoring primitives) into a
print-quality PDF using the shared house theme in
``scripts/site_assets/report_theme.css`` (Fraunces + IBM Plex Mono, brass accent,
provenance-coded callouts). Using one tool + one theme is what gives every report
a stable, repeatable level of polish.

Pipeline:  Markdown  ->  HTML (markdown-it-py, CommonMark + GFM tables, raw HTML)
           ->  PDF (WeasyPrint, theme loaded from file so bundled fonts resolve).

Authoring primitives (the ``disclaimer`` frontmatter block is mandatory —
rendering refuses without it; everything else is optional):

  YAML frontmatter
  ----------------
    title:     "Architectural Analysis — <repo>"
    subtitle:  "one-line positioning"
    masthead:                       # rendered as a dotted masthead line
      - Bundle: stone-sat
      - Commit: ed4c092
    verdict:                        # rendered as the score "hero" card
      score: "6.3"
      max:   "10"
      grade: "C+"
      summary: "one-paragraph verdict (inline Markdown allowed)"
    disclaimer:                     # rendered as the top banner
      notice: "..."
      generated_by: "..."
      date: "YYYY-MM-DD"
    footer:    "left-side footer text"   # page footer; page numbers appended

  Body
  ----
    ::: risk  Migration debt (primary risk)
    Body Markdown of a *risk* callout. Types: info | note | caution | risk.
    :::

    ## 4. Coupling & dependency posture {confidence: low}
        -> the {confidence: low|medium|high} suffix becomes a colored tag.

Usage:
    uv run python scripts/report_to_pdf.py docs/reports/<name>.md
    uv run python scripts/report_to_pdf.py <in.md> -o <out.pdf> [--theme <css>] [--html]

ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs will always produce some form of
error. Absence of output verification is a design defect, not a runtime bug.
Reports authored with LLM assistance must keep their provenance/disclaimer
frontmatter; this tool renders it, it does not vouch for the content.
"""
from __future__ import annotations

__file_meta__ = {
    "role": "tool",
    "status": "active",
    "summary": "Standard Markdown->PDF report renderer (house theme).",
    "rules": [
        {
            "id": "shared-theme",
            "severity": "warning",
            "text": "Render with scripts/site_assets/report_theme.css unless a "
            "caller deliberately overrides --theme; do not fork the theme per "
            "report or polish drifts between documents.",
        },
        {
            "id": "keep-disclaimer",
            "severity": "error",
            "text": "Do not strip the disclaimer/provenance frontmatter from a "
            "report to render it; the banner is required output (PALS's LAW).",
        },
    ],
}

import argparse
import html as _html
import json
import math
import re
import sys
from pathlib import Path

try:
    import yaml
    from markdown_it import MarkdownIt
    from weasyprint import CSS, HTML
except ModuleNotFoundError as exc:  # graceful: this is an application, not a lib
    sys.exit(
        f"missing dependency: {exc.name}. Run via `uv run python "
        f"scripts/report_to_pdf.py ...` so the project env (markdown-it-py, "
        f"weasyprint, pyyaml) is on the path."
    )

DEFAULT_THEME = Path(__file__).resolve().parent / "site_assets" / "report_theme.css"

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_OPEN = re.compile(r"^:::\s+(\w+)\s*(.*?)\s*$")   # ::: <type> [title...]
_CLOSE = re.compile(r"^:::\s*$")
_CHART_OPEN = re.compile(r"^```chart\s*$")
_FENCE = re.compile(r"^```\s*$")
_CONF = re.compile(r"\{conf(?:idence)?:?\s*([A-Za-z]+)\}\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_CALLOUT_TYPES = {"info", "note", "caution", "risk"}

# ---- SVG chart engine (vector charts that render crisply in the PDF) --------
# Palette is the house theme: brass, teal, amber, terracotta, then neutrals.
_PALETTE = ["#a9772a", "#2f7d6e", "#9a6b15", "#b1442a", "#5b7fa6",
            "#7a6aa0", "#c08a3e", "#4f9d8a", "#b5563f", "#8a9097"]
_INK, _MUTED, _FAINT, _LINE = "#1b1f24", "#5d646b", "#8a9097", "#d9d3c4"
_ACCENT = "#a9772a"
_FMONO = "'IBM Plex Mono','DejaVu Sans Mono',monospace"
_FDISP = "'Fraunces','Iowan Old Style',Georgia,serif"


def _svg_esc(s) -> str:
    return _html.escape(str(s), quote=True)


def _poly(cx, cy, r, n, k):
    ang = -math.pi / 2 + k * 2 * math.pi / n
    return cx + r * math.cos(ang), cy + r * math.sin(ang)


def chart_hbar(spec) -> str:
    data = spec["data"]
    unit = spec.get("unit", "")
    maxv = spec.get("max") or max((float(r[1]) for r in data), default=1) or 1
    W, label_w, val_w, row_h, pad = 720, spec.get("label_width", 168), 70, 27, 8
    bar_x = label_w + 8
    bar_w = W - bar_x - val_w
    H = pad * 2 + row_h * len(data)
    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="{_FMONO}">']
    for i, row in enumerate(data):
        label, val = row[0], float(row[1])
        color = row[2] if len(row) > 2 else _PALETTE[i % len(_PALETTE)]
        y = pad + i * row_h
        cy = y + row_h / 2
        w = max(1.0, (val / maxv) * bar_w)
        disp = row[3] if len(row) > 3 else f"{row[1]}{unit}"
        p.append(f'<text x="{label_w}" y="{cy + 3:.1f}" text-anchor="end" font-size="11" fill="{_INK}">{_svg_esc(label)}</text>')
        p.append(f'<rect x="{bar_x}" y="{y + 5}" width="{w:.1f}" height="{row_h - 10}" rx="2.5" fill="{color}"/>')
        p.append(f'<text x="{bar_x + w + 6:.1f}" y="{cy + 3:.1f}" font-size="10" fill="{_MUTED}">{_svg_esc(disp)}</text>')
    p.append("</svg>")
    return _chart_wrap(spec, "".join(p))


def chart_donut(spec) -> str:
    data = spec["data"]
    total = sum(float(r[1]) for r in data) or 1
    W, H = 720, max(210, 40 + 24 * len(data))
    cx, cy, R, r = 120, H / 2, 92, 54
    a = -math.pi / 2
    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="{_FMONO}">']
    for i, row in enumerate(data):
        frac = float(row[1]) / total
        a2 = a + frac * 2 * math.pi
        large = 1 if (a2 - a) > math.pi else 0
        color = _PALETTE[i % len(_PALETTE)]
        x0, y0 = cx + R * math.cos(a), cy + R * math.sin(a)
        x1, y1 = cx + R * math.cos(a2), cy + R * math.sin(a2)
        xi0, yi0 = cx + r * math.cos(a2), cy + r * math.sin(a2)
        xi1, yi1 = cx + r * math.cos(a), cy + r * math.sin(a)
        d = (f"M{x0:.1f},{y0:.1f} A{R},{R} 0 {large} 1 {x1:.1f},{y1:.1f} "
             f"L{xi0:.1f},{yi0:.1f} A{r},{r} 0 {large} 0 {xi1:.1f},{yi1:.1f} Z")
        p.append(f'<path d="{d}" fill="{color}"/>')
        a = a2
    p.append(f'<text x="{cx}" y="{cy - 1}" text-anchor="middle" font-size="20" font-family="{_FDISP}" font-weight="600" fill="{_INK}">{int(total)}</text>')
    p.append(f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="8.5" fill="{_MUTED}">{_svg_esc(spec.get("center_label", "total"))}</text>')
    lx, ly = 268, (H - 22 * len(data)) / 2 + 8
    for i, row in enumerate(data):
        yy = ly + i * 22
        pct = float(row[1]) / total * 100
        p.append(f'<rect x="{lx}" y="{yy}" width="11" height="11" rx="2" fill="{_PALETTE[i % len(_PALETTE)]}"/>')
        p.append(f'<text x="{lx + 18}" y="{yy + 10}" font-size="11" fill="{_INK}">{_svg_esc(row[0])}</text>')
        p.append(f'<text x="{W - 12}" y="{yy + 10}" text-anchor="end" font-size="10" fill="{_MUTED}">{int(float(row[1]))} · {pct:.0f}%</text>')
    p.append("</svg>")
    return _chart_wrap(spec, "".join(p))


def chart_radar(spec) -> str:
    axes, vals = spec["axes"], spec["values"]
    maxv = spec.get("max", 10)
    rings = spec.get("rings", 5)
    n = len(axes)
    W, H = 720, 400
    cx, cy, R = W / 2, H / 2 + 4, 130
    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="{_FMONO}">']
    for ring in range(1, rings + 1):
        rr = R * ring / rings
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (_poly(cx, cy, rr, n, k) for k in range(n)))
        p.append(f'<polygon points="{pts}" fill="none" stroke="{_LINE}" stroke-width="0.7"/>')
    for k in range(n):
        ex, ey = _poly(cx, cy, R, n, k)
        p.append(f'<line x1="{cx}" y1="{cy}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="{_LINE}" stroke-width="0.7"/>')
        lx, ly = _poly(cx, cy, R + 20, n, k)
        c = math.cos(-math.pi / 2 + k * 2 * math.pi / n)
        anchor = "middle" if abs(c) < 0.3 else ("start" if c > 0 else "end")
        p.append(f'<text x="{lx:.1f}" y="{ly + 4:.1f}" text-anchor="{anchor}" font-size="10" fill="{_INK}">{_svg_esc(axes[k])}</text>')
    vpts = [_poly(cx, cy, R * float(vals[k]) / maxv, n, k) for k in range(n)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in vpts)
    p.append(f'<polygon points="{poly}" fill="{_ACCENT}" fill-opacity="0.18" stroke="{_ACCENT}" stroke-width="1.8"/>')
    for x, y in vpts:
        p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{_ACCENT}"/>')
    p.append("</svg>")
    return _chart_wrap(spec, "".join(p))


def chart_flow(spec) -> str:
    """Vertical layered architecture/integration diagram: stacked boxes + arrows.
    spec.layers = [{label, sub?, tag?}]; tag word colors the box (rust/ts/gpu/wasm)."""
    layers = spec["layers"]
    W, box_w, box_h, gap, pad = 720, 580, 52, 24, 6
    box_x = (W - box_w) / 2.0
    cx = W / 2.0
    n = len(layers)
    H = pad * 2 + n * box_h + (n - 1) * gap

    def tagcolor(tag):
        t = (tag or "").lower()
        if "svelte" in t or "typescript" in t or t.startswith("ts"):
            return "#2f7d6e"
        if "gpu" in t or "wgsl" in t:
            return "#b1442a"
        if "wasm" in t or "boundary" in t or "bridge" in t:
            return "#5b7fa6"
        if "rust" in t:
            return "#a9772a"
        return "#5d646b"

    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="{_FMONO}">']
    for i, L in enumerate(layers):
        y = pad + i * (box_h + gap)
        color = tagcolor(L.get("tag"))
        p.append(f'<rect x="{box_x:.0f}" y="{y}" width="{box_w}" height="{box_h}" rx="6" fill="#faf8f2" stroke="{color}" stroke-width="1.4"/>')
        p.append(f'<text x="{box_x + 16:.0f}" y="{y + 22}" font-family="{_FDISP}" font-weight="600" font-size="11.5" fill="{_INK}">{_svg_esc(L["label"])}</text>')
        if L.get("sub"):
            p.append(f'<text x="{box_x + 16:.0f}" y="{y + 39}" font-size="8.3" fill="{_MUTED}">{_svg_esc(L["sub"])}</text>')
        if L.get("tag"):
            tw = 10 + len(str(L["tag"])) * 5.7
            tx = box_x + box_w - tw - 12
            p.append(f'<rect x="{tx:.0f}" y="{y + 15}" width="{tw:.0f}" height="18" rx="9" fill="{color}"/>')
            p.append(f'<text x="{tx + tw / 2:.0f}" y="{y + 27}" text-anchor="middle" font-size="7.5" fill="#ffffff">{_svg_esc(L["tag"])}</text>')
        if i < n - 1:
            y1, y2 = y + box_h, y + box_h + gap
            p.append(f'<line x1="{cx}" y1="{y1}" x2="{cx}" y2="{y2 - 4:.0f}" stroke="#c9c1ae" stroke-width="1.5"/>')
            p.append(f'<polygon points="{cx - 4},{y2 - 7:.0f} {cx + 4},{y2 - 7:.0f} {cx},{y2:.0f}" fill="#c9c1ae"/>')
    p.append("</svg>")
    return _chart_wrap(spec, "".join(p))


def _chart_wrap(spec, svg) -> str:
    title = f'<div class="chart-title">{_svg_esc(spec["title"])}</div>' if spec.get("title") else ""
    cap = f'<div class="chart-cap">{_svg_esc(spec["caption"])}</div>' if spec.get("caption") else ""
    return f'<figure class="chart">{title}{svg}{cap}</figure>'


_CHARTS = {"hbar": chart_hbar, "donut": chart_donut, "radar": chart_radar, "flow": chart_flow}


def render_chart_block(text) -> str:
    try:
        spec = json.loads(text)
        fn = _CHARTS.get(spec.get("type"))
        if not fn:
            return (f'<div class="callout caution"><p class="callout-title">Unknown chart type</p>'
                    f'<p>{_svg_esc(spec.get("type"))} (expected: {", ".join(_CHARTS)})</p></div>')
        return fn(spec)
    except Exception as exc:  # graceful: never let a bad chart spec kill the report
        return (f'<div class="callout risk"><p class="callout-title">Chart render error</p>'
                f'<p>{_svg_esc(exc)}</p></div>')


def _md() -> MarkdownIt:
    # CommonMark + GFM tables/strikethrough; html=True lets our injected
    # confidence <span>s and any deliberate raw HTML pass through.
    md = MarkdownIt("commonmark", {"html": True})
    md.enable(["table", "strikethrough"])
    return md


def split_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, m.group(2)


def _tag_confidence(line: str) -> str:
    """Rewrite a heading's trailing {confidence: X} into a colored span."""
    hm = _HEADING.match(line)
    if not hm:
        return line
    hashes, body = hm.group(1), hm.group(2)
    cm = _CONF.search(body)
    if not cm:
        return line
    level = cm.group(1).lower()
    level = level if level in {"low", "medium", "high"} else "medium"
    body = body[: cm.start()].rstrip()
    span = (
        f'<span class="confidence conf-{level}">confidence: '
        f"{_html.escape(cm.group(1).upper())}</span>"
    )
    return f"{hashes} {body} {span}"


def parse_blocks(body: str) -> list[dict]:
    """Segment body into plain-markdown blocks and callout blocks.

    Callouts are rendered with their inner Markdown processed separately so that
    Markdown inside a wrapping <div> still works (CommonMark would otherwise
    treat the div as a raw HTML block and skip it).
    """
    blocks: list[dict] = []
    buf: list[str] = []
    cur: dict | None = None
    chart: list[str] | None = None

    def flush_md() -> None:
        if buf:
            blocks.append({"kind": "md", "text": "\n".join(buf)})
            buf.clear()

    for line in body.splitlines():
        if chart is not None:
            if _FENCE.match(line):
                blocks.append({"kind": "chart", "text": "\n".join(chart)})
                chart = None
            else:
                chart.append(line)
            continue
        if cur is None:
            if _CHART_OPEN.match(line):
                flush_md()
                chart = []
                continue
            om = _OPEN.match(line)
            if om and om.group(1).lower() in _CALLOUT_TYPES:
                flush_md()
                cur = {
                    "kind": "callout",
                    "type": om.group(1).lower(),
                    "title": om.group(2).strip(),
                    "lines": [],
                }
                continue
            buf.append(_tag_confidence(line))
        else:
            if _CLOSE.match(line):
                cur["text"] = "\n".join(cur.pop("lines"))
                blocks.append(cur)
                cur = None
            else:
                cur["lines"].append(line)
    if chart is not None:  # unterminated fence: don't lose content
        buf.append("```chart")
        buf.extend(chart)
    if cur is not None:  # unterminated callout: treat as plain md, don't lose it
        buf.append(f"::: {cur['type']} {cur['title']}".rstrip())
        buf.extend(cur["lines"])
    flush_md()
    return blocks


def render_body(md: MarkdownIt, blocks: list[dict]) -> str:
    out: list[str] = []
    for b in blocks:
        if b["kind"] == "md":
            out.append(md.render(b["text"]))
        elif b["kind"] == "chart":
            out.append(render_chart_block(b["text"]))
        else:
            title = (
                f'<p class="callout-title">{md.renderInline(b["title"])}</p>'
                if b["title"]
                else ""
            )
            inner = md.render(b["text"])
            out.append(f'<div class="callout {b["type"]}">{title}{inner}</div>')
    return "\n".join(out)


def _mh_pairs(masthead) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in masthead or []:
        if isinstance(item, dict):
            for k, v in item.items():
                pairs.append((str(k), str(v)))
        elif isinstance(item, str) and ":" in item:
            k, v = item.split(":", 1)
            pairs.append((k.strip(), v.strip()))
    return pairs


def build_header(md: MarkdownIt, meta: dict) -> str:
    parts: list[str] = ['<div class="doc-head">']
    if meta.get("title"):
        parts.append(f'<h1 class="doc-title">{md.renderInline(str(meta["title"]))}</h1>')
    if meta.get("subtitle"):
        parts.append(
            f'<div class="doc-subtitle">{md.renderInline(str(meta["subtitle"]))}</div>'
        )
    parts.append('<hr class="accent-rule">')
    pairs = _mh_pairs(meta.get("masthead"))
    if pairs:
        cells = '<span class="mh-sep">·</span>'.join(
            f'<span class="mh-label">{_html.escape(k)}:</span> '
            f'<span class="mh-val">{md.renderInline(v)}</span>'
            for k, v in pairs
        )
        parts.append(f'<div class="masthead">{cells}</div>')
    parts.append("</div>")
    return "\n".join(parts)


def build_disclaimer(meta: dict) -> str:
    d = meta.get("disclaimer") or {}
    notice = str(d.get("notice", "")).strip()
    if not notice:
        return ""
    prov = " · ".join(p for p in [d.get("generated_by", ""), str(d.get("date", ""))] if p)
    prov_html = f'<div class="prov">Generated by: {_html.escape(prov)}</div>' if prov else ""
    label = _html.escape(str(d.get("label", "Evidence basis & confidence")))
    return (
        f'<div class="disclaimer"><span class="lbl">{label} —</span> '
        f"{_html.escape(notice)}{prov_html}</div>"
    )


def build_verdict(md: MarkdownIt, meta: dict) -> str:
    v = meta.get("verdict") or {}
    if not v.get("score"):
        return ""
    grade = f'<span class="vs-grade">Grade {_html.escape(str(v["grade"]))}</span>' if v.get("grade") else ""
    summary = md.render(str(v.get("summary", ""))) if v.get("summary") else ""
    return (
        '<div class="verdict"><div class="verdict-score">'
        f'<span class="vs-num">{_html.escape(str(v["score"]))}</span>'
        f'<span class="vs-max">/ {_html.escape(str(v.get("max", "10")))}</span>'
        f"{grade}</div>"
        f'<div class="verdict-body">{summary}</div></div>'
    )


def page_css(meta: dict) -> str:
    footer = str(meta.get("footer", "")).strip()
    left = f'"{_escape_css(footer)}  ·  "' if footer else '""'
    return (
        "@page{@bottom-center{"
        f'content:{left} "page " counter(page) " / " counter(pages);'
        'font-family:"IBM Plex Mono",monospace;font-size:7pt;color:#8a9097}}'
    )


def _escape_css(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def render(src: Path, out: Path, theme: Path, dump_html: bool) -> None:
    raw = src.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    md = _md()

    disclaimer = build_disclaimer(meta)
    if not disclaimer:
        # keep-disclaimer (__file_meta__, severity error): the banner is
        # required output — a silent render without it was the defect.
        sys.exit(
            f"refusing to render {src}: no disclaimer/provenance frontmatter. "
            "The 'Evidence basis & confidence' banner is required output "
            "(PALS's LAW). Add a `disclaimer:` block with notice / "
            "generated_by / date, or start from docs/reports/_report_template.md."
        )

    content = "\n".join(
        x
        for x in [
            disclaimer,
            build_header(md, meta),
            build_verdict(md, meta),
            render_body(md, parse_blocks(body)),
        ]
        if x
    )
    doc = f'<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>{content}</body></html>'

    if dump_html:
        html_out = out.with_suffix(".html")
        html_out.write_text(doc, encoding="utf-8")
        print(f"wrote {html_out}")

    stylesheets = [CSS(filename=str(theme)), CSS(string=page_css(meta))]
    HTML(string=doc, base_url=str(src.resolve().parent)).write_pdf(
        str(out), stylesheets=stylesheets
    )
    print(f"wrote {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="source Markdown report")
    ap.add_argument("-o", "--output", type=Path, help="output PDF (default: input with .pdf)")
    ap.add_argument("--theme", type=Path, default=DEFAULT_THEME, help="theme CSS (default: house theme)")
    ap.add_argument("--html", action="store_true", help="also write the intermediate HTML")
    args = ap.parse_args(argv)

    if not args.input.is_file():
        sys.exit(f"input not found: {args.input}")
    if not args.theme.is_file():
        sys.exit(f"theme not found: {args.theme}")
    out = args.output or args.input.with_suffix(".pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    render(args.input, out, args.theme, args.html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
