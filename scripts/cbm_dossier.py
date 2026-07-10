#!/usr/bin/env python3
"""
cbm_dossier.py — Structural X-Ray Dossier (A4, consulting-grade) via ReportLab.

Consumes the same output set as cbm_report.py (bundle + optional abox /
decomposition / buildplan) and typesets a complete, designed, 100+ page A4
dossier: cover, front matter, TOC, designed chapter openers, analytic chapters
with infographics, and full evidence registers (receipts ledger, parts
register, reconstruction sequence, dimension dossiers).

Design system: "Measured Ink" — cream paper, carbon ink, rationed vermilion.
Type: Bricolage Grotesque (display) · IBM Plex Serif (text) · IBM Plex Mono
(data) · Big Shoulders (numerals) · Arsenal SC (small-cap kickers).
Fonts are loaded from --font-dir (or $CBM_FONT_DIR); when the TTF set is not
available the dossier falls back to the built-in Type 1 faces and says so.

Usage:
    python scripts/cbm_dossier.py --bundle <bundle-dir> [--abox X.ttl]
        [--decomposition X.yaml] [--buildplan X.yaml] [--out dossier.pdf]
        [--font-dir DIR] [--cache-dir DIR] [--validate-shacl]

Requires reportlab (``pip install -e ".[dossier]"``) on top of the core
project dependencies.
"""
import argparse, ast as pyast, json, math, os, sys, time
from collections import Counter, defaultdict

import rdflib
import yaml

from codebase_mapper.shared_kernel.settings import default_report_path, load_env


def default_out(source, when=None):
    """Standardized default output: <reports_dir>/<source>__dossier__<UTC-ts>.pdf."""
    return str(default_report_path(source, "dossier", ext="pdf", when=when))

try:
    from reportlab.lib.pagesizes import A4
except ImportError:  # pragma: no cover - dependency guidance only
    sys.exit("cbm_dossier: reportlab is not installed - "
             "run `pip install -e \".[dossier]\"` (or `pip install reportlab`)")
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph,
                                Spacer, Table, TableStyle, PageBreak, Flowable,
                                NextPageTemplate, KeepTogether, CondPageBreak)
from reportlab.platypus.tableofcontents import TableOfContents, SimpleIndex
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cbm_report as CR

# ----------------------------------------------------------------------------- tokens
PAGE_W, PAGE_H = A4
M_OUT, M_IN, M_TOP, M_BOT = 16 * mm, 20 * mm, 20 * mm, 18 * mm
COL_W = PAGE_W - M_OUT - M_IN

PAPER = HexColor("#f5efe3"); INK = HexColor("#1c1a17"); GREY = HexColor("#6f6a60")
PALE = HexColor("#b7b0a2"); FAINT = HexColor("#ded7c7"); VERM = HexColor("#c8371f")
SLATE = HexColor("#3f5c6b"); CARD = HexColor("#efe8d8")
TONE = {"certain": INK, "strong": GREY, "probable": PALE, "weak": FAINT, "unknown": FAINT,
        "High": INK, "Medium": GREY, "Low": PALE, "Unknown": FAINT}
CONF_R = {"High": 1.0, "Medium": 0.66, "Low": 0.38, "Unknown": 0.16}

FDIR_DEFAULT = os.environ.get("CBM_FONT_DIR",
                              "/mnt/skills/examples/canvas-design/canvas-fonts")
# Built-in Type 1 stand-ins so the dossier renders (with degraded typography)
# on machines that lack the designed TTF set.
FALLBACK_FACES = {"Disp": "Helvetica-Bold", "DispR": "Helvetica",
                  "Body": "Times-Roman", "BodyB": "Times-Bold",
                  "BodyI": "Times-Italic", "BodyBI": "Times-BoldItalic",
                  "Mono": "Courier", "MonoB": "Courier-Bold",
                  "Num": "Helvetica-Bold", "Caps": "Helvetica"}

def register_fonts(font_dir):
    fam = {
        "Disp": "BricolageGrotesque-Bold.ttf", "DispR": "BricolageGrotesque-Regular.ttf",
        "Body": "IBMPlexSerif-Regular.ttf", "BodyB": "IBMPlexSerif-Bold.ttf",
        "BodyI": "IBMPlexSerif-Italic.ttf", "BodyBI": "IBMPlexSerif-BoldItalic.ttf",
        "Mono": "IBMPlexMono-Regular.ttf", "MonoB": "IBMPlexMono-Bold.ttf",
        "Num": "BigShoulders-Bold.ttf", "Caps": "ArsenalSC-Regular.ttf"}
    missing = [f for f in fam.values()
               if not os.path.exists(os.path.join(font_dir, f))]
    if missing:
        print(f"[dossier] {len(missing)}/{len(fam)} designed fonts absent from "
              f"{font_dir!r} — falling back to built-in faces "
              "(pass --font-dir or set CBM_FONT_DIR for the full design system)",
              file=sys.stderr)
        from reportlab.lib.fonts import addMapping
        for k, face in FALLBACK_FACES.items():
            pdfmetrics.registerFont(pdfmetrics.Font(k, face, "WinAnsiEncoding"))
            # TTFont registration adds these ps2tt mappings implicitly; plain
            # Type 1 aliases need them spelled out for Paragraph markup.
            for bold in (0, 1):
                for italic in (0, 1):
                    addMapping(k, bold, italic, k)
    else:
        for k, f in fam.items():
            pdfmetrics.registerFont(TTFont(k, os.path.join(font_dir, f)))
    pdfmetrics.registerFontFamily("Body", normal="Body", bold="BodyB",
                                  italic="BodyI", boldItalic="BodyBI")

S = {}
def make_styles():
    S["kicker"] = ParagraphStyle("kicker", fontName="Caps", fontSize=9.5, leading=12,
                                 textColor=GREY, spaceAfter=2, tracking=0)
    S["h1"] = ParagraphStyle("H1", fontName="Disp", fontSize=23, leading=26,
                             textColor=INK, spaceBefore=0, spaceAfter=6)
    S["h2"] = ParagraphStyle("H2", fontName="Disp", fontSize=13.5, leading=16,
                             textColor=INK, spaceBefore=14, spaceAfter=4)
    S["h3"] = ParagraphStyle("H3", fontName="Caps", fontSize=9.5, leading=12,
                             textColor=GREY, spaceBefore=10, spaceAfter=3)
    S["body"] = ParagraphStyle("body", fontName="Body", fontSize=9.8, leading=14.6,
                               textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6)
    S["lead"] = ParagraphStyle("lead", fontName="Body", fontSize=11.5, leading=17.5,
                               textColor=INK, spaceAfter=8)
    S["deck"] = ParagraphStyle("deck", fontName="BodyI", fontSize=12.5, leading=18.5,
                               textColor=GREY, spaceAfter=10)
    S["mono"] = ParagraphStyle("mono", fontName="Mono", fontSize=8.2, leading=11.6,
                               textColor=INK)
    S["monosm"] = ParagraphStyle("monosm", fontName="Mono", fontSize=7.2, leading=9.8,
                                 textColor=GREY)
    S["cap"] = ParagraphStyle("cap", fontName="BodyI", fontSize=8.4, leading=11.5,
                              textColor=GREY, spaceBefore=3, spaceAfter=10)
    S["cell"] = ParagraphStyle("cell", fontName="Body", fontSize=8.4, leading=11.2,
                               textColor=INK)
    S["cellm"] = ParagraphStyle("cellm", fontName="Mono", fontSize=7.6, leading=10.4,
                                textColor=INK)
    S["cellmW"] = ParagraphStyle("cellmW", fontName="Mono", fontSize=7.2, leading=10,
                                 textColor=INK, wordWrap="CJK")
    S["cellg"] = ParagraphStyle("cellg", fontName="Body", fontSize=8.2, leading=11,
                                textColor=GREY)
    S["toc1"] = ParagraphStyle("toc1", fontName="Disp", fontSize=10.5, leading=17,
                               textColor=INK)
    S["toc2"] = ParagraphStyle("toc2", fontName="Body", fontSize=9, leading=13.5,
                               textColor=GREY, leftIndent=10)
    S["folio"] = ParagraphStyle("folio", fontName="Mono", fontSize=8, textColor=GREY)

def hex_(c): return c.hexval()[2:] if hasattr(c, "hexval") else str(c)
def cspan(t, font="Mono", color=None, size=None):
    a = f'face="{font}"'
    if color is not None: a += f' color="#{hex_(color)}"'
    if size: a += f' size="{size}"'
    return f'<font {a}>{t}</font>'
def esc(t): return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ----------------------------------------------------------------------------- doc
class Dossier(BaseDocTemplate):
    def __init__(self, fn, meta, **kw):
        super().__init__(fn, pagesize=A4, leftMargin=M_IN, rightMargin=M_OUT,
                         topMargin=M_TOP, bottomMargin=M_BOT, **kw)
        self.meta = meta; self.chapter = ""; self.chapter_no = ""
        body = Frame(M_IN, M_BOT, COL_W, PAGE_H - M_TOP - M_BOT, id="body")
        two = [Frame(M_IN, M_BOT, COL_W / 2 - 4 * mm, PAGE_H - M_TOP - M_BOT, id="c1"),
               Frame(M_IN + COL_W / 2 + 4 * mm, M_BOT, COL_W / 2 - 4 * mm,
                     PAGE_H - M_TOP - M_BOT, id="c2")]
        self.addPageTemplates([
            PageTemplate("cover", [body], onPage=self.pg_cover),
            PageTemplate("front", [body], onPage=self.pg_front),
            PageTemplate("opener", [Frame(M_IN, M_BOT, COL_W, PAGE_H - 70 * mm, id="op")],
                         onPage=self.pg_opener),
            PageTemplate("bodyT", [body], onPage=self.pg_body),
            PageTemplate("ledger", two, onPage=self.pg_body)])

    def bg(self, c):
        c.setFillColor(PAPER); c.rect(0, 0, PAGE_W, PAGE_H, 1, 0)

    def pg_cover(self, c, d): self.bg(c)
    def pg_front(self, c, d):
        self.bg(c)
        c.setFont("Mono", 7.6); c.setFillColor(GREY)
        c.drawCentredString(PAGE_W / 2, M_BOT - 7 * mm, f"{d.page:03d}")
    def pg_opener(self, c, d):
        self.bg(c)
        n = self.chapter_no
        c.setFillColor(FAINT); c.setFont("Num", 210)
        c.drawRightString(PAGE_W - M_OUT + 2 * mm, PAGE_H - 96 * mm, n)
        c.setStrokeColor(VERM); c.setLineWidth(2.2)
        c.line(M_IN, PAGE_H - 42 * mm, M_IN + 26 * mm, PAGE_H - 42 * mm)
        c.setFont("Mono", 7.6); c.setFillColor(GREY)
        c.drawCentredString(PAGE_W / 2, M_BOT - 7 * mm, f"{d.page:03d}")
    def pg_body(self, c, d):
        self.bg(c)
        c.setFont("Caps", 8.2); c.setFillColor(GREY)
        c.drawString(M_IN, PAGE_H - M_TOP + 5 * mm, (self.chapter or "").upper()[:60])
        c.drawRightString(PAGE_W - M_OUT, PAGE_H - M_TOP + 5 * mm,
                          f"{self.meta['repo'].upper()} — STRUCTURAL X-RAY DOSSIER")
        c.setStrokeColor(INK); c.setLineWidth(0.6)
        c.line(M_IN, PAGE_H - M_TOP + 3.4 * mm, PAGE_W - M_OUT, PAGE_H - M_TOP + 3.4 * mm)
        c.setStrokeColor(VERM); c.setLineWidth(1.4)
        c.line(PAGE_W / 2 - 3 * mm, M_BOT - 5.4 * mm, PAGE_W / 2 + 3 * mm, M_BOT - 5.4 * mm)
        c.setFont("Mono", 7.6); c.setFillColor(GREY)
        c.drawCentredString(PAGE_W / 2, M_BOT - 9 * mm, f"{d.page:03d}")

    def afterFlowable(self, fl):
        if isinstance(fl, Paragraph):
            if fl.style.name == "H1":
                txt = fl.getPlainText()
                self.notify("TOCEntry", (0, txt, self.page))
                key = f"h1-{self.page}-{txt[:12]}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(txt, key, 0, False)
            elif fl.style.name == "H2":
                txt = fl.getPlainText()
                self.notify("TOCEntry", (1, txt, self.page))

class SetChapter(Flowable):
    def __init__(self, no, title): super().__init__(); self.no, self.t = no, title
    def wrap(self, w, h): return 0, 0
    def draw(self):
        d = self.canv._doctemplate; d.chapter = self.t; d.chapter_no = self.no

# ----------------------------------------------------------------------------- graphic flowables
class Rule(Flowable):
    def __init__(self, w=None, lw=0.8, color=INK, pad=3):
        super().__init__(); self.w, self.lw, self.c, self.pad = w, lw, color, pad
    def wrap(self, aw, ah): self.w = self.w or aw; return self.w, self.lw + 2 * self.pad
    def draw(self):
        self.canv.setStrokeColor(self.c); self.canv.setLineWidth(self.lw)
        self.canv.line(0, self.pad, self.w, self.pad)

class StatStrip(Flowable):
    def __init__(self, vals, w=COL_W, h=17 * mm): super().__init__(); self.vals, self.w, self.h = vals, w, h
    def wrap(self, aw, ah): return self.w, self.h
    def draw(self):
        c = self.canv; n = len(self.vals); cw = self.w / n
        c.setStrokeColor(INK); c.setLineWidth(0.9)
        c.line(0, self.h, self.w, self.h); c.line(0, 0, self.w, 0)
        for i, (num, lab) in enumerate(self.vals):
            x = i * cw
            if i: c.setStrokeColor(FAINT); c.setLineWidth(0.6); c.line(x, 2, x, self.h - 2)
            c.setFillColor(INK); c.setFont("MonoB", 13.5)
            c.drawCentredString(x + cw / 2, self.h - 8.2 * mm,
                                f"{num:,}" if isinstance(num, int) else str(num))
            c.setFillColor(GREY); c.setFont("Caps", 6.6)
            c.drawCentredString(x + cw / 2, 3.2 * mm, lab.upper())

class HBars(Flowable):
    def __init__(self, pairs, w=COL_W, rowh=5.4 * mm, color=GREY, label_w=None):
        super().__init__(); self.pairs, self.w, self.rowh, self.color = pairs, w, rowh, color
        self.label_w = label_w or min(58 * mm, w * 0.42)
    def wrap(self, aw, ah): return self.w, self.rowh * len(self.pairs)
    def draw(self):
        c = self.canv; mx = max((v for _, v in self.pairs), default=1)
        bw = self.w - self.label_w - 18 * mm
        for i, (k, v) in enumerate(self.pairs):
            y = self.rowh * (len(self.pairs) - 1 - i) + 1.2
            c.setFillColor(INK); c.setFont("Mono", 7.4)
            k = str(k); k = k if len(k) <= 34 else k[:33] + "…"
            c.drawString(0, y + 1, k)
            c.setFillColor(self.color)
            c.rect(self.label_w, y, bw * v / mx, self.rowh - 2.6, 0, 1)
            c.setFillColor(GREY); c.setFont("Mono", 7.4)
            c.drawRightString(self.w, y + 1, f"{v:,}")

class Wheel(Flowable):
    def __init__(self, abox, size=118 * mm): super().__init__(); self.a, self.size = abox, size
    def wrap(self, aw, ah): return self.size, self.size
    def draw(self):
        c = self.canv; s = self.size; cx, cy, R = s / 2, s / 2, s / 2 - 15 * mm
        dims = self.a["dims"]; N = max(1, len(dims))
        for ring in (0.16, 0.38, 0.66, 1.0):
            c.setStrokeColor(FAINT if ring < 1 else PALE); c.setLineWidth(0.7)
            c.circle(cx, cy, R * ring)
        for i, d in enumerate(dims):
            th = math.pi / 2 - 2 * math.pi * i / N
            x1, y1 = math.cos(th), math.sin(th)
            r = CONF_R.get(d.get("conf", "Unknown"), 0.16)
            risky = "risk" in d
            col = VERM if risky else TONE.get(d.get("conf"), PALE)
            c.setStrokeColor(FAINT); c.setLineWidth(0.6)
            c.line(cx + R * .16 * x1, cy + R * .16 * y1, cx + R * x1, cy + R * y1)
            c.setStrokeColor(col); c.setLineWidth(1.4)
            c.line(cx + R * .16 * x1, cy + R * .16 * y1, cx + R * r * x1, cy + R * r * y1)
            c.setFillColor(col); c.circle(cx + R * r * x1, cy + R * r * y1, 2.6, 0, 1)
            if risky:
                c.setStrokeColor(VERM); c.setLineWidth(0.9)
                c.circle(cx + R * r * x1, cy + R * r * y1, 4.6)
            rl = R * (1.10 + 0.12 * (i % 2))
            lx, ly = cx + rl * x1, cy + rl * y1
            val = d.get("dominant") or "/".join(d.get("values", [])) or "—"
            val = val if len(val) <= 15 else val[:14] + "…"
            c.saveState(); c.translate(lx, ly)
            ang = math.degrees(th); anch = 1
            if x1 < 0: ang += 180; anch = -1
            c.rotate(ang)
            c.setFont("Mono", 5.3); c.setFillColor(VERM if risky else GREY)
            t = f"{d.get('dim','')[:3]} {val}"
            (c.drawString if anch > 0 else c.drawRightString)(0, -1.8, t)
            c.restoreState()
        c.setFillColor(INK); c.setFont("Num", 24)
        c.drawCentredString(cx, cy - 3, str(N))
        c.setFillColor(GREY); c.setFont("Caps", 5.6)
        c.drawCentredString(cx, cy - 11, "DIMENSIONS")

class MetroRL(Flowable):
    def __init__(self, metro, w=COL_W, h=96 * mm): super().__init__(); self.m, self.w, self.h = metro, w, h
    def wrap(self, aw, ah): return self.w, self.h
    def draw(self):
        c = self.canv; m = self.m
        if not m["lines"] or not m["lines"][0]["stations"]: return
        core = m["lines"][0]
        order = sorted(core["stations"],
                       key=lambda s: (s["file"].endswith("__init__.py"), -s["out"], s["in"]))
        X0 = 12 * mm; DX = (self.w - 34 * mm) / max(1, len(order) - 1); YC = self.h * 0.52
        pos = {s["file"]: (X0 + i * DX, YC) for i, s in enumerate(order)}
        xs = [pos[s["file"]][0] for s in order]
        inter = set(m["interchanges"])
        c.setStrokeColor(VERM); c.setLineWidth(4); c.setLineCap(1)
        c.line(xs[0] - 7 * mm, YC, xs[-1] + 7 * mm, YC)
        def station(x, y, col, f):
            ic = f in inter
            c.setFillColor(HexColor("#ffffff") if ic else col)
            c.setStrokeColor(INK if ic else col); c.setLineWidth(1.6 if ic else 1.2)
            c.circle(x, y, 2.9 * mm / 2 if ic else 1.9 * mm / 2, 1, 1)
        def lab(x, y, t, up):
            c.saveState(); c.translate(x - 1.2 * mm, y + (2.6 * mm if up else -2.6 * mm))
            c.rotate(38); c.setFont("Mono", 5.6); c.setFillColor(INK)
            c.drawString(0, 0, t); c.restoreState()
        for s in order:
            x, y = pos[s["file"]]; station(x, y, VERM, s["file"]); lab(x, y, s["file"].split("/")[-1], True)
        tones = [INK, GREY, HexColor("#8d8779"), HexColor("#a49d8d"), PALE]
        lanes = [-19 * mm, -33 * mm, 19 * mm, 33 * mm, -47 * mm]
        for j, L in enumerate(m["lines"][1:6]):
            dy = lanes[j]; col = tones[j % 5]
            anchor = L["anchor"] or order[-1]["file"]
            ax, _ = pos.get(anchor, (xs[-1], YC)); yb = YC + dy
            hdir = -1 if ax > self.w * 0.66 else 1
            ex = ax + hdir * abs(dy)
            seq = [s for s in L["stations"] if not s["file"].endswith("__init__.py")][:3] + \
                  [s for s in L["stations"] if s["file"].endswith("__init__.py")]
            if not seq: continue
            stx = [ex + hdir * (5 * mm + i * 19 * mm) for i in range(len(seq))]
            lim = self.w - 5 * mm
            if hdir > 0 and stx[-1] + 6 * mm > lim:
                sh = stx[-1] + 6 * mm - lim; ex -= sh; stx = [x - sh for x in stx]
            if hdir < 0 and stx[-1] - 6 * mm < 3 * mm:
                sh = 3 * mm - (stx[-1] - 6 * mm); ex += sh; stx = [x + sh for x in stx]
            c.setStrokeColor(col); c.setLineWidth(2.4); c.setLineJoin(1)
            if L["anchor"] is None: c.setDash(1.2, 4)
            p = c.beginPath(); p.moveTo(ax, YC); p.lineTo(ex, yb); p.lineTo(stx[-1] + hdir * 6 * mm, yb)
            c.drawPath(p); c.setDash()
            c.setFont("Caps", 6.6); c.setFillColor(col)
            nm = L["name"] + (" · re-export only" if L["anchor"] is None else "")
            c.drawRightString(min(ex + 2 * mm, self.w - 3 * mm) if hdir < 0 else ex - 1.5 * mm,
                              yb + (2.8 * mm if dy > 0 else -3.6 * mm), nm)
            for x, s in zip(stx, seq):
                station(x, yb, col, s["file"]); lab(x, yb, s["file"].split("/")[-1], dy > 0)

class Scatter(Flowable):
    def __init__(self, dist, Y, w=COL_W, h=110 * mm): super().__init__(); self.d, self.Y, self.w, self.h = dist, Y, w, h
    def wrap(self, aw, ah): return self.w, self.h
    def draw(self):
        if self.Y is None: return
        c = self.canv; files = self.d["files"]; endl = self.d["endl"]; ft = self.d["ftype"]
        for i, f in enumerate(files):
            x = 4 * mm + self.Y[i][0] * (self.w - 8 * mm)
            y = 6 * mm + (1 - self.Y[i][1]) * (self.h - 12 * mm)
            if ft.get(f) == "test_code":
                c.setStrokeColor(PALE); c.setLineWidth(0.5); c.circle(x, y, 0.75 * mm)
            else:
                r = 0.8 * mm + min(2.2 * mm, math.sqrt(max(endl.get(f, 10), 1)) / 16)
                c.setFillColor(GREY); c.circle(x, y, r, 0, 1)

class BarcodeRL(Flowable):
    def __init__(self, build, w=COL_W, h=52 * mm): super().__init__(); self.b, self.w, self.h = build, w, h
    def wrap(self, aw, ah): return self.w, self.h
    def draw(self):
        c = self.canv; b = self.b; seq = b["seq"]; phases = b["phases"]
        if not seq or not phases: return
        n = len(seq); maxc = max((s["creates"] for s in seq), default=1) or 1
        gap = 2.2 * mm; usable = self.w - gap * (len(phases) - 1)
        widths = {p["phase"]: max(1.6 * mm, p["n"] / n * usable) for p in phases}
        xs, x = {}, 0
        for p in phases: xs[p["phase"]] = x; x += widths[p["phase"]] + gap
        base = 11 * mm; idx = Counter(); pts = []
        per_phase = {p["phase"]: p["n"] for p in phases}
        for s in seq:
            ph = s["phase"]; i = idx[ph]; idx[ph] += 1
            w = widths[ph] / per_phase[ph]
            h = 2 * mm + (self.h - 22 * mm) * math.log1p(s["creates"]) / math.log1p(maxc)
            c.setFillColor(TONE.get(s["conf"], FAINT))
            c.rect(xs[ph] + i * w, base, max(0.5, w * 0.7), h, 0, 1)
            pts.append((xs[ph] + i * w + w * 0.35,
                        base + 1.5 * mm + (self.h - 24 * mm) * s["cum"] / max(1, b["total_creates"])))
        c.setStrokeColor(VERM); c.setLineWidth(1.1)
        p = c.beginPath(); p.moveTo(*pts[0])
        for q in pts[1:]: p.lineTo(*q)
        c.drawPath(p)
        c.setFont("MonoB", 7); c.setFillColor(VERM)
        c.drawRightString(pts[-1][0], pts[-1][1] + 2 * mm, f"{b['total_creates']:,} files")
        for p_ in phases:
            cx = xs[p_["phase"]] + widths[p_["phase"]] / 2
            if widths[p_["phase"]] > 5 * mm:
                c.setFont("Caps", 6.4); c.setFillColor(INK)
                c.drawCentredString(cx, 5.6 * mm, f"{p_['phase']:02d}")
            if widths[p_["phase"]] > 24 * mm:
                c.setFont("Mono", 5.4); c.setFillColor(GREY)
                c.drawCentredString(cx, 2.2 * mm, f"{p_['n']} steps · {p_['creates']} files")

class WaffleRL(Flowable):
    def __init__(self, decomp, w=82 * mm, h=52 * mm): super().__init__(); self.d, self.w, self.h = decomp, w, h
    def wrap(self, aw, ah): return self.w, self.h
    def draw(self):
        c = self.canv; d = self.d
        order = {"certain": 0, "strong": 1, "probable": 2, "weak": 3, "unknown": 4}
        kr = {k: i for i, (k, _) in enumerate(d["kinds"])}
        cells = sorted(d["part_conf"], key=lambda kc: (kr.get(kc[0], 99), order.get(kc[1], 9)))
        if not cells: return
        cols = 24; cw = self.w / cols; rows = math.ceil(len(cells) / cols)
        ch = min(cw * 1.25, self.h / rows)
        for i, (k, cf) in enumerate(cells):
            r, cc = divmod(i, cols)
            c.setFillColor(TONE.get(cf, FAINT))
            c.rect(cc * cw, self.h - (r + 1) * ch, cw * 0.78, ch * 0.78, 0, 1)

class UMLPackagesRL(Flowable):
    def __init__(self, nodes, edges, w=COL_W, h=88 * mm):
        super().__init__(); self.nodes, self.edges, self.w, self.h = nodes, edges, w, h
    def wrap(self, aw, ah): return self.w, self.h
    def draw(self):
        c = self.canv
        core = self.nodes[0]; others = self.nodes[1:]
        bw, bh = 46 * mm, 15 * mm
        pos = {core[0]: (6 * mm, self.h / 2 - bh / 2)}
        colx = [self.w - bw - 4 * mm, self.w - 2 * bw - 22 * mm]
        rows = max(1, math.ceil(len(others) / 2))
        gap = (self.h - rows * bh) / (rows + 1)
        for i, nd in enumerate(others):
            r, col = divmod(i, 2)
            pos[nd[0]] = (colx[col], self.h - (gap + bh) * (r + 1))
        def pkg(x, y_bot, name, sub, hot=False):
            c.setLineWidth(1.1); c.setStrokeColor(INK)
            c.setFillColor(PAPER if hot else CARD)
            c.rect(x, y_bot + bh - 3.2 * mm, 11 * mm, 3.2 * mm, 1, 1)
            c.rect(x, y_bot, bw, bh - 3.2 * mm, 1, 1)
            c.setFillColor(VERM if hot else INK); c.setFont("Caps", 7.6)
            nm = name if len(name) <= 30 else name[:29] + "\u2026"
            c.drawString(x + 3 * mm, y_bot + bh - 8.2 * mm, nm.upper())
            c.setFillColor(GREY); c.setFont("Mono", 6)
            c.drawString(x + 3 * mm, y_bot + 2.4 * mm, sub)
        def edge(a, b, label):
            (x1, y1), (x2, y2) = pos[a], pos[b]
            ax = x1 + (bw if x2 > x1 else 0); ay = y1 + bh / 2
            bx = x2 + (0 if x2 > x1 else bw); by = y2 + bh / 2
            c.setStrokeColor(GREY); c.setLineWidth(0.9); c.setDash(2.4, 2.4)
            c.line(ax, ay, bx, by); c.setDash()
            ang = math.atan2(by - ay, bx - ax)
            for da in (0.5, -0.5):
                c.line(bx, by, bx - 3.2 * mm * math.cos(ang + da),
                       by - 3.2 * mm * math.sin(ang + da))
            c.setFillColor(GREY); c.setFont("Mono", 5.6)
            c.drawCentredString((ax + bx) / 2, (ay + by) / 2 + 1.4 * mm,
                                f"\u00abimport\u00bb {label}")
        for a, b, n in self.edges:
            if a in pos and b in pos: edge(a, b, f"\u00d7{n}")
        for i, (nm, sub) in enumerate(self.nodes):
            x, y0 = pos[nm]; pkg(x, y0, nm, sub, hot=(i == 0))

class UMLClassTreeRL(Flowable):
    def __init__(self, tree, w=COL_W, box_w=40 * mm):
        super().__init__(); self.t, self.w, self.bw = tree, w, box_w
        self.levels = tree["levels"]
        self.bh = {}
        for nm, nd in tree["nodes"].items():
            m = len(nd.get("methods", []))
            self.bh[nm] = (8.0 + 3.2 * min(3, m) + (3.0 if m > 3 else 0)) * mm
        self.rowh = [max([self.bh[n] for n in lv]) + 9 * mm for lv in self.levels]
        self.h = sum(self.rowh) + 2 * mm
    def wrap(self, aw, ah): return self.w, self.h
    def draw(self):
        c = self.canv; T = self.t; pos = {}
        y = self.h
        for li, lv in enumerate(self.levels):
            y -= self.rowh[li]
            span = self.w / len(lv)
            for i, nm in enumerate(sorted(lv)):
                pos[nm] = (span * i + (span - self.bw) / 2, y)
        for ch, pa in T["edges"]:
            if ch not in pos or pa not in pos: continue
            (x1, y1), (x2, y2) = pos[ch], pos[pa]
            sx, sy = x1 + self.bw / 2, y1 + self.bh[ch]
            tx, ty = x2 + self.bw / 2, y2
            c.setStrokeColor(GREY); c.setLineWidth(0.9)
            c.line(sx, sy, tx, ty)
            ang = math.atan2(ty - sy, tx - sx); L = 3.4 * mm
            p1 = (tx - L * math.cos(ang - 0.42), ty - L * math.sin(ang - 0.42))
            p2 = (tx - L * math.cos(ang + 0.42), ty - L * math.sin(ang + 0.42))
            c.setFillColor(PAPER)
            pth = c.beginPath(); pth.moveTo(tx, ty)
            pth.lineTo(*p1); pth.lineTo(*p2); pth.close()
            c.drawPath(pth, 1, 1)
        for nm, (x, y) in pos.items():
            nd = T["nodes"][nm]; h = self.bh[nm]; ext = nd.get("external")
            c.setLineWidth(1); c.setStrokeColor(PALE if ext else INK)
            if ext: c.setDash(2, 2)
            c.setFillColor(PAPER if ext else CARD)
            c.rect(x, y, self.bw, h, 1, 1); c.setDash()
            c.setFillColor(GREY if ext else INK); c.setFont("MonoB", 6.8)
            c.drawCentredString(x + self.bw / 2, y + h - 3.6 * mm,
                                nm if len(nm) <= 26 else nm[:25] + "\u2026")
            if ext:
                c.setFont("Mono", 5); c.setFillColor(PALE)
                c.drawCentredString(x + self.bw / 2, y + h - 6.6 * mm,
                                    "\u00abexternal\u00bb")
                continue
            c.setStrokeColor(FAINT); c.setLineWidth(0.6)
            c.line(x, y + h - 5.2 * mm, x + self.bw, y + h - 5.2 * mm)
            c.setFont("Mono", 5.4); c.setFillColor(GREY)
            yy = y + h - 8.4 * mm
            for m in nd["methods"][:3]:
                c.drawString(x + 2 * mm, yy, ("+ " + m)[:30]); yy -= 3.1 * mm
            if len(nd["methods"]) > 3:
                c.drawString(x + 2 * mm, yy, f"\u2026 +{len(nd['methods'])-3} more")

class UMLObjectRL(Flowable):
    def __init__(self, data, w=COL_W, h=64 * mm):
        super().__init__(); self.d, self.w, self.h = data, w, h
    def wrap(self, aw, ah): return self.w, self.h
    def _ubox(self, c, x, y, bw, name, cls, slots=None):
        bh = (8.6 + 3.2 * len(slots or [])) * mm
        c.setLineWidth(1); c.setStrokeColor(INK); c.setFillColor(CARD)
        c.rect(x, y - bh, bw, bh, 1, 1)
        c.setFillColor(INK); c.setFont("MonoB", 6.8)
        t = f"{name} : {cls}"
        c.drawCentredString(x + bw / 2, y - 3.8 * mm, t)
        tw = c.stringWidth(t, "MonoB", 6.8)
        c.setLineWidth(0.6)
        c.line(x + bw / 2 - tw / 2, y - 4.6 * mm, x + bw / 2 + tw / 2, y - 4.6 * mm)
        if slots:
            c.setStrokeColor(FAINT)
            c.line(x, y - 6 * mm, x + bw, y - 6 * mm)
            c.setFont("Mono", 5.6); c.setFillColor(GREY)
            yy = y - 9 * mm
            for k, v in slots:
                c.drawString(x + 2 * mm, yy, f"{k} = {v}"[:44]); yy -= 3.2 * mm
        return bh
    def draw(self):
        c = self.canv; d = self.d
        mw = 62 * mm
        mx, my = 2 * mm, self.h - 4 * mm
        mh = self._ubox(c, mx, my, mw, d["main"]["name"], d["main"]["cls"],
                        d["main"]["slots"])
        lx = self.w - 58 * mm
        n = len(d["links"]); gap = (self.h - 6 * mm) / max(1, n)
        for i, (lab, nd) in enumerate(d["links"]):
            ly = self.h - 2 * mm - i * gap
            self._ubox(c, lx, ly, 56 * mm, nd["name"], nd["cls"])
            c.setStrokeColor(GREY); c.setLineWidth(0.8)
            ax, ay = mx + mw, my - mh / 2
            bx, by = lx, ly - 4.3 * mm
            c.line(ax, ay, bx, by)
            c.setFont("Mono", 5.4); c.setFillColor(GREY)
            c.drawString((ax + bx) / 2 - 10 * mm, (ay + by) / 2 + 1.2 * mm, lab)

class UMLComponentRL(Flowable):
    def __init__(self, nodes, edges, w=COL_W, h=92 * mm):
        super().__init__(); self.nodes, self.edges, self.w, self.h = nodes, edges, w, h
    def wrap(self, aw, ah): return self.w, self.h
    def draw(self):
        c = self.canv; bw, bh = 44 * mm, 12 * mm
        pos = {}
        center = self.nodes[0]
        pos[center[0]] = (self.w / 2 - bw / 2, self.h / 2 + bh / 2)
        left = [n for n in self.nodes[1:] if not n[2]]
        right = [n for n in self.nodes[1:] if n[2]]
        for arr, x in ((left, 3 * mm), (right, self.w - bw - 3 * mm)):
            gap = (self.h - 4 * mm) / max(1, len(arr))
            for i, n in enumerate(arr):
                pos[n[0]] = (x, self.h - 2 * mm - i * gap)
        def comp(x, y_top, label, ext, hot=False):
            c.setLineWidth(1.1)
            c.setStrokeColor(PALE if ext else INK)
            if ext: c.setDash(2, 2)
            c.setFillColor(PAPER if hot else CARD)
            c.rect(x, y_top - bh, bw, bh, 1, 1); c.setDash()
            ix, iy = x + bw - 6.4 * mm, y_top - 3 * mm
            c.setLineWidth(0.7); c.setFillColor(PAPER)
            c.rect(ix, iy - 3.2 * mm, 4.6 * mm, 3.2 * mm, 1, 1)
            for dy in (0.8 * mm, 1.9 * mm):
                c.rect(ix - 1.1 * mm, iy - 3.2 * mm + dy, 2.2 * mm, 0.8 * mm, 1, 1)
            c.setFillColor(VERM if hot else (GREY if ext else INK))
            c.setFont("Caps", 6.6)
            c.drawString(x + 2 * mm, y_top - 5 * mm, label[:26].upper())
            if ext:
                c.setFont("Mono", 5); c.setFillColor(PALE)
                c.drawString(x + 2 * mm, y_top - 8.6 * mm, "\u00abexternal\u00bb")
        for ei, (a, b, lab, tone) in enumerate(self.edges):
            if a not in pos or b not in pos: continue
            (x1, y1), (x2, y2) = pos[a], pos[b]
            ax = x1 + (bw if x2 > x1 else 0); ay = y1 - bh / 2
            bx = x2 + (0 if x2 > x1 else bw); by = y2 - bh / 2
            c.setStrokeColor(tone); c.setLineWidth(0.9); c.setDash(2.2, 2.2)
            c.line(ax, ay, bx, by); c.setDash()
            ang = math.atan2(by - ay, bx - ax)
            for da in (0.5, -0.5):
                c.line(bx, by, bx - 3 * mm * math.cos(ang + da),
                       by - 3 * mm * math.sin(ang + da))
            c.setFillColor(tone); c.setFont("Mono", 5)
            c.drawCentredString((ax + bx) / 2,
                                (ay + by) / 2 + (1 + 2.1 * (ei % 3)) * mm, lab)
        for i, n in enumerate(self.nodes):
            x, yt = pos[n[0]]; comp(x, yt, n[1], n[2], hot=(i == 0))

class UMLCompositeRL(Flowable):
    def __init__(self, name, parts, connectors, ports, w=COL_W, h=96 * mm):
        super().__init__()
        self.name, self.parts, self.conn, self.ports = name, parts, connectors, ports
        self.w, self.h = w, h
    def wrap(self, aw, ah): return self.w, self.h
    def draw(self):
        c = self.canv
        ox, oy, ow, oh = 2 * mm, 3 * mm, self.w - 4 * mm, self.h - 10 * mm
        c.setLineWidth(1.3); c.setStrokeColor(INK); c.setFillColor(PAPER)
        c.rect(ox, oy, ow, oh, 1, 1)
        c.setFillColor(INK); c.setFont("Caps", 8)
        c.drawString(ox + 3 * mm, oy + oh + 2 * mm, self.name.upper())
        bw, bh = 38 * mm, 10.5 * mm
        pos = {}
        core = self.parts[0]
        pos[core] = (ox + ow / 2 - bw / 2, oy + oh / 2 + bh / 2)
        others = self.parts[1:]
        cols = [ox + 5 * mm, ox + ow - bw - 5 * mm]
        for i, pnm in enumerate(others):
            r, col = divmod(i, 2)
            pos[pnm] = (cols[col], oy + oh - 6 * mm - r * (bh + 8 * mm))
        for a, b, n in self.conn:
            if a not in pos or b not in pos: continue
            (x1, y1), (x2, y2) = pos[a], pos[b]
            c.setStrokeColor(PALE); c.setLineWidth(0.8)
            c.line(x1 + (bw if x2 > x1 else 0), y1 - bh / 2,
                   x2 + (0 if x2 > x1 else bw), y2 - bh / 2)
            c.setFillColor(GREY); c.setFont("Mono", 4.8)
            c.drawCentredString((x1 + x2 + bw) / 2, (y1 + y2 - bh) / 2 + 1 * mm,
                                f"\u00d7{n}")
        for i, pnm in enumerate(self.parts):
            x, yt = pos[pnm]
            c.setLineWidth(1); c.setStrokeColor(INK)
            c.setFillColor(CARD)
            c.rect(x, yt - bh, bw, bh, 1, 1)
            c.setFillColor(VERM if i == 0 else INK); c.setFont("Caps", 6.4)
            c.drawString(x + 2 * mm, yt - 4.6 * mm, pnm[:26].upper())
        psz = 4.6 * mm
        anchors = [(ox + ow * 0.32, oy + oh), (ox + ow, oy + oh * 0.62)]
        for (pf, cnt), (px, py) in zip(self.ports, anchors):
            c.setLineWidth(1.1); c.setStrokeColor(INK); c.setFillColor(PAPER)
            c.rect(px - psz / 2, py - psz / 2, psz, psz, 1, 1)
            inward = px > self.w * 0.7
            c.setFillColor(INK); c.setFont("MonoB", 6)
            (c.drawRightString if inward else c.drawString)(
                px + (-3.4 if inward else 3.4) * mm, py + 1.4 * mm, pf)
            c.setFillColor(GREY); c.setFont("Mono", 5.4)
            (c.drawRightString if inward else c.drawString)(
                px + (-3.4 if inward else 3.4) * mm, py - 2.2 * mm,
                f"\u00abport\u00bb {cnt} imports from outside")
            tx, ty = pos[self.parts[0]]
            c.setStrokeColor(PALE); c.setLineWidth(0.8)
            c.line(px, py - psz / 2 if py > ty else py + psz / 2,
                   tx + bw / 2, ty - 1 * mm)

class UMLActivityRL(Flowable):
    def __init__(self, groups, w=COL_W):
        super().__init__(); self.g, self.w = groups, w
        self.h = (14 + len(groups) * 30) * mm
    def wrap(self, aw, ah): return self.w, self.h
    def draw(self):
        c = self.canv; cx = self.w / 2
        y = self.h - 3 * mm
        c.setFillColor(INK); c.circle(cx, y, 2.2 * mm, 0, 1)
        y -= 2.2 * mm
        def arrow(y1, y2):
            c.setStrokeColor(INK); c.setLineWidth(1)
            c.line(cx, y1, cx, y2)
            c.setFillColor(INK)
            pth = c.beginPath(); pth.moveTo(cx, y2)
            pth.lineTo(cx - 1.4 * mm, y2 + 2.4 * mm)
            pth.lineTo(cx + 1.4 * mm, y2 + 2.4 * mm); pth.close()
            c.drawPath(pth, 0, 1)
        for gi, grp in enumerate(self.g):
            arrow(y, y - 4 * mm); y -= 4 * mm
            barw = self.w * 0.72
            c.setFillColor(INK)
            c.rect(cx - barw / 2, y - 1.2 * mm, barw, 1.2 * mm, 0, 1)
            y -= 1.2 * mm
            acts = grp["samples"][:3] + [f"\u2026 +{grp['n']-3} more \u2014 order-free"]
            aw_ = barw / len(acts) - 3 * mm
            ah_ = 8.6 * mm
            ay_top = y - 3.6 * mm
            for i, a in enumerate(acts):
                axc = cx - barw / 2 + (aw_ + 3 * mm) * i + aw_ / 2 + 1.5 * mm
                c.setStrokeColor(INK); c.setLineWidth(0.9)
                c.setFillColor(CARD)
                c.roundRect(axc - aw_ / 2, ay_top - ah_, aw_, ah_, 2.4 * mm, 1, 1)
                c.setStrokeColor(PALE); c.setLineWidth(0.7)
                c.line(axc, y, axc, ay_top)
                c.line(axc, ay_top - ah_, axc, ay_top - ah_ - 3.6 * mm)
                c.setFillColor(INK); c.setFont("Mono", 5.2)
                c.drawCentredString(axc, ay_top - ah_ / 2 - 0.8 * mm, a[:26])
            y = ay_top - ah_ - 3.6 * mm
            c.setFillColor(INK)
            c.rect(cx - barw / 2, y - 1.2 * mm, barw, 1.2 * mm, 0, 1)
            y -= 1.2 * mm
            c.setFillColor(GREY); c.setFont("Caps", 6)
            c.drawString(cx - barw / 2, y - 3.4 * mm,
                         f"GROUP {gi+1} \u2014 {grp['n']} PARTS")
            y -= 5 * mm
        arrow(y, y - 4 * mm); y -= 4 * mm
        c.setStrokeColor(INK); c.setLineWidth(1.1)
        c.circle(cx, y - 2.2 * mm, 2.6 * mm)
        c.setFillColor(INK); c.circle(cx, y - 2.2 * mm, 1.5 * mm, 0, 1)

class UMLProfileRL(Flowable):
    def __init__(self, w=COL_W, h=52 * mm):
        super().__init__(); self.w, self.h = w, h
        self.rows = [("\u00abimport\u00bb", "Dependency"),
                     ("\u00abimports_external\u00bb", "Dependency"),
                     ("\u00abtests\u00bb", "Dependency"),
                     ("\u00abexternal\u00bb", "Component"),
                     ("\u00abport\u00bb", "Port"),
                     ("\u00aborder-free\u00bb", "ActivityGroup")]
    def wrap(self, aw, ah): return self.w, self.h
    def draw(self):
        c = self.canv
        c.setLineWidth(1.1); c.setStrokeColor(INK); c.setFillColor(PAPER)
        c.rect(1 * mm, 2 * mm, self.w - 2 * mm, self.h - 8 * mm, 1, 1)
        c.setFillColor(INK); c.setFont("Caps", 7.4)
        c.drawString(4 * mm, self.h - 4.6 * mm, "\u00abPROFILE\u00bb CBM DOSSIER UML")
        bw, bh = 42 * mm, 9 * mm
        cols = 3
        for i, (st_, meta) in enumerate(self.rows):
            r, col = divmod(i, cols)
            x = 5 * mm + col * (bw + 6 * mm)
            yt = self.h - 12 * mm - r * (bh + 6 * mm)
            c.setLineWidth(0.9); c.setStrokeColor(INK); c.setFillColor(CARD)
            c.rect(x, yt - bh, bw, bh, 1, 1)
            c.setFillColor(INK); c.setFont("Mono", 6)
            c.drawString(x + 2 * mm, yt - 3.6 * mm, "\u00abstereotype\u00bb " + st_)
            c.setFillColor(GREY); c.setFont("Mono", 5.4)
            c.drawString(x + 2 * mm, yt - 7 * mm, "extends " + meta)

class ToneScale(Flowable):
    def __init__(self, w=COL_W): super().__init__(); self.w = w
    def wrap(self, aw, ah): return self.w, 7 * mm
    def draw(self):
        c = self.canv; items = [("certain / High", INK), ("strong / Medium", GREY),
                                ("probable / Low", PALE), ("unverified", FAINT),
                                ("risk · living line", VERM)]
        x = 0
        for lab, col in items:
            c.setFillColor(col); c.rect(x, 2.2 * mm, 3.2 * mm, 3.2 * mm, 0, 1)
            c.setFillColor(GREY); c.setFont("Mono", 6.6)
            c.drawString(x + 4.4 * mm, 3 * mm, lab)
            x += 4.4 * mm + c.stringWidth(lab, "Mono", 6.6) + 6 * mm

# ----------------------------------------------------------------------------- content helpers
FIG = {"n": 0}
def figcap(txt, tag):
    FIG["n"] += 1
    label = f"FIG. {FIG['n']:02d}"
    return Paragraph(f'{cspan(label, "MonoB", VERM, 7)} '
                     f'{esc(txt)} {cspan("· " + tag, "Mono", GREY, 7)}', S["cap"])

def data_table(header, rows, widths, aligns=None, w=COL_W):
    hd = [Paragraph(cspan(h.upper(), "Caps", GREY, 7), S["cellg"]) for h in header]
    def sty(i):
        a = (aligns or {}).get(i)
        return S["cellmW"] if a == "mw" else (S["cellm"] if a == "m" else S["cell"])
    body = [[Paragraph(x, sty(i)) for i, x in enumerate(r)] for r in rows]
    t = Table([hd] + body, colWidths=[w * x for x in widths], repeatRows=1)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
          ("LINEBELOW", (0, 1), (-1, -1), 0.35, FAINT),
          ("TOPPADDING", (0, 0), (-1, -1), 2.4),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4)]
    t.setStyle(TableStyle(st))
    return t

def callout(title, text, color=VERM):
    inner = [Paragraph(cspan(title.upper(), "Caps", INK, 8.5), S["cell"]),
             Spacer(0, 2), Paragraph(text, S["cell"])]
    t = Table([[inner]], colWidths=[COL_W - 6])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CARD),
                           ("LINEBEFORE", (0, 0), (0, -1), 2.4, color),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 7),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return t

def receipt_card(r, w=COL_W):
    body = [Paragraph(f'“{esc(r["summary"][:420])}”', S["cell"]),
            Spacer(0, 3),
            Paragraph(ixn(r["file"]) + cspan(f'{esc(r["file"])} · {esc(r["model"])} · prompt '
                            f'{esc(r["prompt_sha"][:16])}… · {esc(r["generated_at"])}',
                            "Mono", GREY, 6.6), S["cellg"])]
    t = Table([[body]], colWidths=[w - 6])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CARD),
                           ("LINEBEFORE", (0, 0), (0, -1), 2.2, VERM),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                           ("TOPPADDING", (0, 0), (-1, -1), 6),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t

def _ixc(t): return str(t).replace(",", " ").replace('"', "'").strip()
# Blank anchors emit no tag at all: ReportLab's SimpleIndex raises
# IndexError on an empty entry during multiBuild.
def ixs(t):
    c = _ixc(t)
    return f'<index name="subjects" item="{c}"/>' if c.strip(", ") else ""
def ixn(t):
    c = _ixc(t)
    return f'<index name="names" item="{c}"/>' if c.strip(", ") else ""


def pin_name(release: str) -> str:
    """Package name from a pinned-release string. Version is the part
    after the LAST '@' — scoped npm packages ('@types/node@18.2.3')
    legitimately start with '@'."""
    name = release.rsplit("@", 1)[0]
    return name or release

CH = {"n": 0}
def chapter(story, title, deck, kicker="CHAPTER", sections=None):
    CH["n"] += 1; no = f"{CH['n']:02d}"
    story += [SetChapter(no, title), NextPageTemplate("opener"), PageBreak(),
              Spacer(0, 26 * mm),
              Paragraph(f"{kicker} {no}", S["kicker"]),
              Paragraph(title + ixs(title), S["h1"]),
              Spacer(0, 2 * mm),
              Paragraph(deck, S["deck"])]
    if sections:
        story += [Spacer(0, 8 * mm), Rule(w=30 * mm, lw=1.2, color=INK),
                  Spacer(0, 2 * mm), Paragraph("IN THIS CHAPTER", S["kicker"]),
                  Spacer(0, 1.5 * mm)]
        for i, sct in enumerate(sections, 1):
            story.append(Paragraph(
                f'{cspan(f"{no}.{i}", "MonoB", VERM, 8)}  {esc(sct)}',
                ParagraphStyle("opsec", fontName="Body", fontSize=10.5, leading=17,
                               textColor=INK)))
    story += [NextPageTemplate("bodyT"), PageBreak()]

def h2(story, t): story.append(Paragraph(t + ixs(t), S["h2"]))
def h3(story, t): story.append(Paragraph(t.upper() + ixs(t), S["h3"]))
def p(story, t, st="body"): story.append(Paragraph(t, S[st]))

# ----------------------------------------------------------------------------- build
def build(args):
    register_fonts(args.font_dir); make_styles()
    found = CR.discover(args.bundle, args)
    with open(found["run_manifest.json"]) as fh:
        man = json.load(fh)
    meta = {"repo": man.get("repo_name") or os.path.basename(args.bundle.rstrip("/")),
            "commit": man.get("commit_sha", ""), "tool": man.get("tool_version", "?"),
            "generated": str(man.get("generated_at", ""))[:19]}
    hash_rows = CR.verify_hashes(args.bundle, man, found)
    cache_dir = CR.resolve_cache_dir(args.bundle, args.cache_dir)
    g = CR.load_graph(found, cache_dir)
    G = CR.graph_analytics(g, man)
    Y = CR.district_xy(G["_district"], args.bundle, cache_dir, False)
    enrich = CR.load_enrichments(found["enrichments.jsonl"]) if found.get("enrichments.jsonl") else None
    ast = CR.load_ast_coverage(found["ast_coverage.json"]) if found.get("ast_coverage.json") else None
    abox = CR.load_abox(found.get("abox"))
    decomp = CR.load_decomp(found.get("decomposition"))
    bplan = CR.load_buildplan(found.get("buildplan"))
    # full registers (raw)
    receipts_all = []
    if found.get("enrichments.jsonl"):
        with open(found["enrichments.jsonl"]) as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    if r.get("kind") == "file_summary":
                        receipts_all.append(r)
        receipts_all.sort(key=lambda r: r.get("target", ""))
    parts_all, steps_all = [], []
    rels_all, build_groups_raw = [], []
    if found.get("decomposition"):
        with open(found["decomposition"]) as fh:
            D_dec = yaml.safe_load(fh)
        parts_all = D_dec.get("parts", [])
        rels_all = D_dec.get("relationships", [])
        build_groups_raw = D_dec.get("build_order", [])
    if found.get("buildplan"):
        with open(found["buildplan"]) as fh:
            steps_all = yaml.safe_load(fh).get("steps", [])
    pins_pairs = sorted({str(o).split("#release/")[-1]
                         for _, o in g.subject_objects(
                             rdflib.URIRef(CR.CBM + "pinsDependency"))})
    conc_top, n_concepts = [], 0
    if found.get("concepts.json"):
        with open(found["concepts.json"]) as fh:
            cj = json.load(fh)
        n_concepts = len(cj.get("concepts") or cj.get("concept_embedding_ids") or [])
        freq = Counter()
        for _, cl in (cj.get("per_path_concepts") or {}).items():
            for k in (cl or []): freq[k] += 1
        conc_top = freq.most_common(120)

    ok = sum(1 for r in hash_rows if r["ok"]); tot = sum(1 for r in hash_rows if r["ok"] is not None)
    repo = meta["repo"]
    sym_tot = sum(l["symbols_extracted"] for l in ast["langs"]) if ast else None
    te, tev = G["tests_edges"], G["test_evidence"]
    recall_x = round(tev["typed_import_edges"] / te["n"]) if te["n"] else None

    # Optional live SHACL re-validation. The manifest's own self-check is
    # always reported; a "two engines, one verdict" claim is only printed
    # when this run actually executed the second engine.
    shacl_manifest = man.get("shacl_self_check", {}).get("conforms")
    shacl_live = None
    if args.validate_shacl and found.get("shapes.shacl.ttl"):
        from pyshacl import validate as shacl_validate
        sh = rdflib.Graph(); sh.parse(found["shapes.shacl.ttl"], format="turtle")
        shacl_live, _, _ = shacl_validate(g, shacl_graph=sh, inference="none")
        print(f"[dossier] pyshacl re-validation: "
              f"{'conforms' if shacl_live else 'DOES NOT CONFORM'}", file=sys.stderr)

    # Chapter numbers are assigned at chapter() call time; mirror that order
    # here (skipping absent layers) so the reading guide and the executive
    # reading map never drift from the actual pagination.
    glance = [
        ("verify", True, "Verification",
         "Every hash recomputed; the manifest's self-check re-read"
         + (", re-validated with a second engine this run" if shacl_live is not None else "") + "."),
        ("inventory", True, "Inventory census",
         "Files by language and type; the parser's own coverage confession."),
        ("graph", True, "Mechanical graph",
         "Vocabulary, chokepoints, interchanges, external surface, pinned releases."),
        ("tests", True, "Test evidence",
         "Two measurements of test linkage; a precision finding and its fix."),
        ("metro", True, "The metro",
         "Transit map with measured topology; one dossier per line."),
        ("uml", True, "UML views",
         "All 14 UML 2.5 types accounted for: drawn from data or refused with the missing artifact named."),
        ("districts", True, "The districts",
         "The semantic city; directory demoted to paint."),
        ("concepts", True, "Concept stratum",
         "The vocabulary the codebase speaks, ranked by breadth."),
        ("ai", True, "AI layer",
         "Receipts: provenance completeness measured across every record."),
        ("abox", bool(abox), f"{len((abox or {}).get('dims', []))} dimensions",
         "Architecture classification, confidence-tagged, unverified by design."),
        ("decomp", bool(decomp), "Decomposition",
         f"{(decomp or {}).get('n_parts', 0)} parts and the complete register."),
        ("bplan", bool(bplan), "Reconstruction",
         f"{(bplan or {}).get('n_steps', 0)} ordered steps; skips explained, violations flagged."),
        ("findings", True, "Findings", "Actions, evidence-ranked."),
    ]
    chap_no, cn, dm = {}, 2, []
    for key, present, title, what in glance:
        if not present: continue
        chap_no[key] = f"{cn:02d}"
        dm.append((chap_no[key], title, what)); cn += 1
    dm.append((f"{cn:02d}+", "Registers", "The audit trail, complete and typeset."))

    S["ixe"] = ParagraphStyle("ixe", fontName="Body", fontSize=8.4, leading=11.6,
                              textColor=INK, leftIndent=8, firstLineIndent=-8)
    S["ixm"] = ParagraphStyle("ixm", fontName="Mono", fontSize=6.8, leading=9.8,
                              textColor=INK, leftIndent=8, firstLineIndent=-8)
    sub_ix = SimpleIndex(name="subjects", headers=True, dot=" . ")
    nam_ix = SimpleIndex(name="names", headers=True, dot=" . ")
    for _ix, _sty in ((sub_ix, S["ixe"]), (nam_ix, S["ixm"])):
        for attr in ("textStyle", "style"):
            try: setattr(_ix, attr, _sty)
            except Exception: pass

    doc = Dossier(args.out, meta)
    st = []

    # ---- cover
    st += [NextPageTemplate("cover"), Spacer(0, 30 * mm),
           Paragraph("STRUCTURAL X-RAY DOSSIER", S["kicker"]),
           Paragraph(repo, ParagraphStyle("cv", fontName="Disp", fontSize=56, leading=58,
                                          textColor=INK)),
           Spacer(0, 4 * mm),
           Paragraph("A verified account of one repository — every measured fact, every "
                     "derived view, every AI-written sentence with its receipt.", S["deck"]),
           Spacer(0, 10 * mm),
           StatStrip([(man.get("counts", {}).get("files", 0), "files"),
                      (G["triples"], "triples"), (G["edges"], "import edges"),
                      ((decomp or {}).get("n_parts", 0), "parts"),
                      ((bplan or {}).get("n_steps", 0), "rebuild steps"),
                      ((enrich or {}).get("n", 0), "receipts")]),
           Spacer(0, 9 * mm)]
    hero = Wheel(abox, size=104 * mm) if abox else (BarcodeRL(bplan, h=60 * mm) if bplan else Rule())
    hero.hAlign = "CENTER"
    st += [hero,
           Spacer(0, 9 * mm),
           Paragraph(cspan(f"commit {meta['commit'][:12]} · generated {meta['generated']} · "
                           f"codebase-mapper v{meta['tool']}", "Mono", GREY, 8), S["mono"]),
           Paragraph(cspan(f"input hashes independently recomputed at typesetting time: "
                           f"{ok}/{tot} match", "MonoB", INK, 8), S["mono"]),
           Paragraph(cspan("MEASURED INK · PLATE SERIES", "Caps", PALE, 8), S["mono"])]

    # ---- provenance / disclaimer (shared banner label: CR pins the phrase)
    st += [NextPageTemplate("front"), PageBreak(), Spacer(0, 8 * mm)]
    h3(st, "Provenance & candor")
    p(st, f"<b>{CR.EVIDENCE_BANNER_LABEL}.</b> "
          "No statement in this dossier should be taken for granted. Every figure is "
          "labeled by its epistemic class: <b>FACT</b> — measured mechanically from the "
          "artifacts and reproducible by query; <b>DERIVED</b> — computed by a disclosed, "
          "seeded procedure (projections, clusterings, layouts); <b>UNVERIFIED</b> — "
          "authored by a language model and pending validation, shipped only with a "
          "per-sentence receipt (model, prompt hash, timestamp, target hash). Where a "
          "layer is absent from the run, the dossier says so instead of hiding it.")
    p(st, f"Inputs verified at typesetting time: {ok}/{tot} sha-256 recomputations match "
          f"the manifest's claims. SHACL self-check: "
          f"{'conforms' if shacl_manifest else 'see chapter 02'}."
          + (f" Independent pyshacl re-validation this run: "
             f"{'conforms' if shacl_live else 'DOES NOT CONFORM'}."
             if shacl_live is not None else ""))
    st.append(Spacer(0, 4 * mm)); st.append(ToneScale())
    st.append(figcap("The dossier's tone scale — one ink family, one accent, rationed.", "design system"))

    # ---- TOC
    st += [PageBreak(), Paragraph("CONTENTS", S["kicker"]),
           Paragraph("Table of contents", S["h1"]), Spacer(0, 4 * mm)]
    toc = TableOfContents(); toc.levelStyles = [S["toc1"], S["toc2"]]
    st.append(toc)

    # ---- reading guide + design system
    st += [PageBreak(), Spacer(0, 6 * mm)]
    h3(st, "How to read this dossier")
    interp_last = f"{int(chap_no['findings']) - 1:02d}"
    interp_span = (f"Chapters {chap_no['ai']}\u2013{interp_last} quote the "
                   "interpretive layers with their confidence intact"
                   if interp_last != chap_no["ai"] else
                   f"Chapter {chap_no['ai']} quotes the interpretive layer "
                   "with its confidence intact")
    p(st, f"Chapters 01\u2013{chap_no['tests']} establish trust: verification, "
          f"inventory, and the mechanical graph. Chapters {chap_no['metro']}\u2013"
          f"{chap_no['concepts']} are the cartography \u2014 four designed views of "
          f"one measured territory, one of them in UML. {interp_span}; Chapter "
          f"{chap_no['findings']} states findings and recommendations. The registers "
          "that follow are the audit trail \u2014 complete, typeset, and dull on "
          "purpose \u2014 and a remissive index closes the volume with every subject "
          "and every name.")
    h3(st, "Design system (reusable)")
    p(st, "Paper <b>#f5efe3</b>, carbon ink <b>#1c1a17</b>, warm greys for the derived, "
          "and one vermilion <b>#c8371f</b> reserved for risk and living lines; slate "
          "<b>#3f5c6b</b> is held in reserve for comparative series. Type: Bricolage "
          "Grotesque for display, IBM Plex Serif for text, IBM Plex Mono for data, "
          "Big Shoulders for numerals, Arsenal small caps for bearings. A5-derived grid, "
          "16/20 mm margins, 03-digit folios, hairline rules. Every component on these "
          "pages — stat strips, tone scales, receipt cards, registers — is a reusable "
          "primitive of the reporting template.")
    st.append(KeepTogether([Spacer(0, 3 * mm),
        StatStrip([("A4", "format"), ("5", "typefaces"), ("6", "colors"),
                   ("2", "column grids"), ("03", "folio digits"), ("1", "accent")]),
        figcap("Design tokens at a glance.", "design system")]))

    # ================= CH 1 executive summary
    chapter(st, "Executive summary", sections=["What the evidence supports","The repository can be rebuilt on paper","Three risks worth an executive\u2019s minute"],
            deck=f"One repository, {man.get('counts',{}).get('files',0):,} files, read four ways: "
            "verified, mapped, decomposed, and rebuilt on paper — with receipts.")
    p(st, f"<b>{repo}</b> at commit {cspan(meta['commit'][:12],'Mono')} was mapped by "
          f"codebase-mapper v{meta['tool']}. The mechanical layer extracted "
          f"{G['triples']:,} triples including {G['edges']:,} internal import edges; the "
          f"AI layer wrote {(enrich or {}).get('n',0):,} summaries, every one carrying a "
          "verifiable receipt. This dossier typesets the complete output set and its "
          "audit trail.", "lead")
    h2(st, "What the evidence supports")
    bullets = [
        f"<b>Integrity holds.</b> {ok}/{tot} artifact hashes recomputed and matched at "
        f"typesetting time; the manifest's SHACL self-check reports "
        f"{'conforms' if shacl_manifest else 'non-conformance (see chapter 02)'}"
        + (" and an independent pyshacl re-validation this run "
           + ("agrees" if shacl_live else "DISAGREES") if shacl_live is not None else "")
        + ". FACT."]
    if G["chokepoints"] and G["interchanges"]:
        bullets.append(
            f"<b>The structure has a spine.</b> Import mass concentrates in a small core: "
            f"the top chokepoint is imported {G['chokepoints'][0]['in']} times; "
            f"{len(G['interchanges'])} core files serve two or more subsystems, and "
            f"<b>{G['interchanges'][0]['file'].split('/')[-1]}</b> joins "
            f"{len(G['interchanges'][0]['lines'])}. FACT.")
    if te["n"] and tev["typed_import_edges"] > te["n"]:
        bullets.append(
            f"<b>Test linkage is under-measured by the shipped heuristic.</b> "
            f"{te['n']} heuristic edges versus "
            f"{tev['typed_import_edges']} typed test→source import edges — "
            "the graph both convicts its heuristic and supplies the replacement. FACT.")
    if decomp or bplan or abox:
        bullets.append(
            "<b>The interpretive layers disclose themselves.</b> The decomposition and "
            "the reconstruction tag every judgment with confidence; the architecture "
            "classification declares itself unverified until validated. That honesty is "
            "a feature of the artifact, not of this dossier.")
    for t in bullets:
        p(st, t)
    if bplan:
        h2(st, "The repository can be rebuilt on paper")
        p(st, f"The recomposer orders {bplan['n_steps']} steps across "
              f"{len(bplan['phases'])} phases, creating {bplan['total_creates']:,} files, "
              f"with {len(bplan['skipped'])} phases skipped for stated reasons and "
              f"{sum(v for _, v in bplan['violations'])} known violations flagged "
              "“do not replicate blindly.”")
        st.append(BarcodeRL(bplan, h=44 * mm))
        st.append(figcap("The reconstruction at a glance — one bar per step, tone = "
                         "confidence, vermilion = cumulative files created.", "FACT + tagged"))
    h2(st, "The dossier at a glance")
    st.append(data_table(["ch", "chapter", "what it establishes"],
                         [[a, b, c] for a, b, c in dm], [0.08, 0.26, 0.66],
                         aligns={0: "m"}))
    st.append(figcap("Reading map for a 30-minute executive pass: 01, "
                     f"{chap_no['verify']}, {chap_no['tests']}, {chap_no['findings']}.",
                     "guide"))
    if abox:
        risks = [r for r in abox["risks"] if not r["id"].startswith("Overlay")]
        if risks:
            h2(st, f"{len(risks)} risks worth an executive's minute")
            for r in risks:
                p(st, f"<b>{esc(r['id'].replace('Risk_','').replace('_',' '))}.</b> "
                      f"{esc(r['label'][:280])}")
            p(st, "Classification is LLM-authored and remains UNVERIFIED until schema "
                  "validation — stated here exactly as the artifact states it.", "cap")

    # ================= CH 2 verification
    chapter(st, "Verification", sections=["Hash audit — every claim recomputed","Blob store & byte-exact reconstruction","Schema conformance, two engines"],
            deck="Before a single chart: every hash recomputed, every self-check re-run. "
            "The dossier audits its own inputs.")
    p(st, "The output set makes cryptographic claims about itself. At typesetting time "
          "this dossier recomputed each one.")
    rows = [[r["artifact"], r["claimed"] + "…",
             "match" if r["ok"] else ("MISMATCH" if r["ok"] is False else "n/a")]
            for r in hash_rows]
    st.append(data_table(["artifact", "claimed sha-256", "recomputed"], rows,
                         [0.44, 0.34, 0.22], aligns={0: "mw", 1: "m", 2: "m"}))
    st.append(figcap(f"Hash audit — {ok}/{tot} match.", "FACT"))
    if found.get("blobs_dir"):
        nb = len(os.listdir(found["blobs_dir"]))
        cb = man.get("counts", {}).get("unique_blobs_written", 0)
        p(st, f"Blob store: {nb:,} content-addressed objects on disk against {cb:,} "
              f"claimed by the manifest — {'match' if nb == cb else 'MISMATCH'}. "
              "Byte-exact reconstruction of the working tree is therefore possible "
              "from the bundle alone.")
    h2(st, "Schema conformance")
    if shacl_live is not None:
        p(st, f"Manifest self-check: SHACL conforms = {shacl_manifest}. At typesetting "
              "time the graph was re-validated with an independent engine (pyshacl, no "
              f"inference): {'conforms' if shacl_live else 'DOES NOT CONFORM'}. "
              + ("Two engines, one verdict. FACT." if shacl_live and shacl_manifest
                 else "The engines disagree — treat downstream figures with caution. FACT.")
              + ixs("SHACL") + ixs("pyshacl"))
    else:
        p(st, f"Manifest self-check: SHACL conforms = {shacl_manifest}. This run did "
              "not re-validate independently; pass --validate-shacl to execute pyshacl "
              "at typesetting time and print the second verdict here." + ixs("SHACL"))
    st.append(callout("Method note",
                      "Verification is not a chapter that trusts the others; it is the "
                      "chapter the others must survive. Every subsequent figure cites "
                      "artifacts that passed this audit." + ixs("sha-256") + ixs("hash audit")))
    h2(st, "The complete surface")
    p(st, "Everything the mapper can emit, and what this run contained. Absence is "
          "reported, never papered over.", "cap")
    ROLE = {"run_manifest.json": "run identity, counts, hash claims, self-checks",
            "inventory.ttl": "the graph — mechanical facts as RDF (Turtle)",
            "inventory.jsonld": "the same graph, JSON-LD serialization",
            "ontology-mapping.ttl": "vocabulary alignment to external ontologies",
            "shapes.shacl.ttl": "SHACL shapes the graph must satisfy",
            "enrichments.jsonl": "L4 receipts — one provenanced record per AI sentence",
            "embeddings.npz": "L2 chunk vectors",
            "embeddings_meta.json": "embedding model, dimension, artifact hash claim",
            "concepts.json": "L3 concepts, per-path links, co-occurrence",
            "concepts_embeddings.npz": "concept vectors",
            "ast_coverage.json": "parser coverage confession per language",
            "rust_items.jsonl": "Rust item stream (language-specific)",
            "blobs_dir": "content-addressed store of every file version",
            "abox": "arc4d3 dimension classification (companion)",
            "decomposition": "part decomposition (companion)",
            "buildplan": "reconstruction sequence (companion)"}
    rows = []
    for k, role in ROLE.items():
        pth = found.get(k)
        if pth and os.path.isdir(pth):
            sz = f"{len(os.listdir(pth)):,} objects"
        elif pth:
            b = os.path.getsize(pth)
            sz = (f"{b} B" if b < 1024 else
                  f"{b/1024:.0f} KB" if b < 1024**2 else f"{b/1024**2:.1f} MB")
        else:
            sz = "\u2014"
        rows.append([k, "present" if pth else "absent", sz, role])
    st.append(data_table(["artifact", "status", "size", "role in the evidence chain"],
                         rows, [0.24, 0.10, 0.12, 0.54], aligns={0: "mw"}))
    st.append(figcap("The output surface of this run, discovered and sized at "
                     "typesetting time.", "FACT"))

    # ================= CH 3 inventory
    chapter(st, "Inventory census", sections=["Files by language","Files by classified type","AST coverage — the parser\u2019s confession"],
            deck="What the mapper found on disk: files by language and type, and what the "
            "parser honestly could and could not read.")
    c1 = sorted(man.get("files_by_language", {}).items(), key=lambda x: -x[1])[:9]
    c2 = sorted(man.get("files_by_type", {}).items(), key=lambda x: -x[1])
    h2(st, "Files by language")
    st.append(HBars(c1)); st.append(figcap("File count per detected language; “(none)” "
                                           "covers prose and assets.", "FACT"))
    h2(st, "Files by classified type")
    st.append(HBars(c2, color=SLATE))
    st.append(figcap("Type classification drives every later split of source vs. test "
                     "vs. documentation.", "FACT"))
    if ast:
        h2(st, "AST coverage — the parser's own confession")
        rows = [[l["lang"], str(l["files"]), str(l["files_with_ast"]),
                 str(l["files_zero_ast"]), str(l["files_with_parse_errors"]),
                 str(l["symbols_extracted"]), str(l["imports_extracted"])]
                for l in ast["langs"]]
        st.append(data_table(["language", "files", "with AST", "zero-AST",
                              "parse err", "symbols", "imports"],
                             rows, [0.22, 0.13, 0.13, 0.13, 0.13, 0.13, 0.13],
                             aligns={0: "m"}))
        st.append(figcap("Per-language extraction record, straight from "
                         "ast_coverage.json.", "FACT"))
        if sym_tot == 0:
            st.append(callout("Disclosed anomaly",
                              "symbols_extracted totals zero in this run — no first-class "
                              "symbol entities were emitted (chunk-level symbol labels "
                              "exist). Configuration or regression; flagged, not hidden."))

    # ================= CH 4 graph layer
    chapter(st, "The mechanical graph", sections=["Vocabulary & population","Import chokepoints","Interchanges & dossiers","External surface","Register D — pinned releases"],
            deck=f"{G['triples']:,} triples of extracted fact on a W3C substrate — the layer "
            "everything else must answer to.")
    h2(st, "Vocabulary in use")
    st.append(HBars(G["ns"][:8]))
    st.append(figcap("Triples per namespace: mapper vocabularies beside SKOS and "
                     "NIF-core.", "FACT"))
    h2(st, "Population by class")
    st.append(HBars(G["classes"][:9], color=SLATE))
    st.append(figcap("Instances per class across the graph.", "FACT"))
    h2(st, "Import chokepoints")
    rows = [[str(r["in"]), r["file"] + ixn(r["file"]), str(r["out"])] for r in G["chokepoints"][:12]]
    st.append(data_table(["imported by", "file", "imports"], rows, [0.16, 0.68, 0.16],
                         aligns={1: "m"}))
    st.append(figcap("In-degree ranking. Re-export surfaces (__init__) and test "
                     "infrastructure inflate honestly and are annotated in analysis.",
                     "FACT"))
    h2(st, "Degree distribution")
    p(st, "How import attention is spread across the source population: most files are "
          "imported rarely or never; a thin head absorbs the graph's mass. The out-degree "
          "panel includes tests, whose imports are the evidence of Chapter 05.")
    st.append(KeepTogether([
        Paragraph("IN-DEGREE (TIMES IMPORTED) — SOURCE FILES"
                  + ixs("In-degree (times imported) — source files"), S["h3"]),
        HBars(G["deg_hist"]["in"], rowh=5.0 * mm)]))
    st.append(KeepTogether([
        Paragraph("OUT-DEGREE (IMPORTS MADE) — SOURCE + TEST FILES"
                  + ixs("Out-degree (imports made) — source + test files"), S["h3"]),
        HBars(G["deg_hist"]["out"], rowh=5.0 * mm, color=SLATE)]))
    st.append(figcap("Degree histograms over the import graph.", "FACT"))
    h2(st, "Interchanges — files serving several subsystems")
    rows = [[i["file"], ", ".join(i["lines"]), str(len(i["lines"]))]
            for i in G["interchanges"][:10]]
    st.append(data_table(["core file", "importing subsystems", "n"],
                         rows, [0.34, 0.54, 0.12], aligns={0: "m"}))
    st.append(figcap("The connective tissue of the package.", "FACT"))
    h2(st, "Interchange dossiers")
    for i in G["interchanges"][:6]:
        st.append(KeepTogether([Paragraph(
            f'{cspan(esc(i["file"]), "MonoB", INK, 8)}{ixn(i["file"])}  '
            f'{cspan("serves " + str(len(i["lines"])) + " subsystems", "Mono", VERM, 7)}<br/>'
            f'{cspan(esc(", ".join(i["lines"])), "Mono", GREY, 6.8)}', S["cellg"]),
            Spacer(0, 2.2 * mm)]))
    st.append(CondPageBreak(70 * mm))
    h2(st, "External surface")
    st.append(HBars([(str(k).replace("pkg/", ""), v) for k, v in G["external"][:12]]))
    st.append(figcap(f"Most-imported external packages; {G['pins_n']} version pins "
                     "recorded in the graph.", "FACT"))
    h2(st, "Register D — pinned releases, complete")
    p(st, "Every dependency release the lockfile pins, as recorded in the graph "
          "(cbm:pinsDependency → PackageRelease). The supply-chain surface, in full.", "cap")
    if pins_pairs:
        cols = 4; rows_n = math.ceil(len(pins_pairs) / cols)
        tbl = []
        for r_ in range(rows_n):
            row = []
            for c_ in range(cols):
                k = c_ * rows_n + r_
                row.append(Paragraph(ixn(pin_name(pins_pairs[k]))
                                     + cspan(esc(pins_pairs[k]), "Mono", INK, 6.6), S["cellm"])
                           if k < len(pins_pairs) else "")
            tbl.append(row)
        t = Table(tbl, colWidths=[COL_W / cols] * cols)
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.25, FAINT),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.3),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 1.3)]))
        st.append(t)
        st.append(figcap(f"{len(pins_pairs)} pinned releases.", "FACT"))

    # ================= CH 5 test evidence
    chapter(st, "Test evidence, measured twice", sections=["Two measurements of one question","The precision finding","Where typed evidence lands","Recommendation"],
            deck=f"The shipped heuristic said {te['n']:,}. The typed graph said "
            f"{tev['typed_import_edges']:,}. Both numbers are in this chapter "
            "because both are true.")
    st.append(StatStrip([(te["n"], "heuristic edges"),
                         (tev["typed_import_edges"], "typed import edges"),
                         (recall_x if recall_x is not None else "—", "× recall"),
                         (G["n_tst"], "test files")]))
    st.append(figcap("Two measurements of the same question: which tests exercise which "
                     "source?", "FACT"))
    if te["top_objects"] and te["n"] and te["top_objects"][0][1] / te["n"] > 0.5:
        f0, c0 = te["top_objects"][0]
        st.append(callout("Precision finding",
                          f"{c0} of {te['n']} heuristic cbm:tests edges point at a single "
                          f"file ({esc(f0)}) — a basename-collision signature. The map "
                          "was queryable enough to convict its own heuristic. FACT."))
    st.append(CondPageBreak(64 * mm))
    h2(st, "Where typed test evidence lands")
    st.append(HBars(tev["top_targets"]))
    st.append(figcap("Top targets of test-typed → source-typed imports. Test "
                     "infrastructure (client, utilities) is identifiable and excludable.",
                     "FACT"))
    p(st, "<b>Recommendation.</b> Derive test evidence from typed import edges minus "
          "test-infrastructure targets; retire the stem heuristic. This is a small "
          "enricher change with a "
          + (f"{recall_x}×" if recall_x else "measurable")
          + " evidence gain. PROPOSAL.")

    # ================= CH 6 metro
    chapter(st, "The metro", sections=["The map","Line dossiers, one per subsystem"],
            deck="A transit map of the package: stations earned by import degree, anchors "
            "measured from the graph, geometry after Beck.")
    st.append(MetroRL(G["_metro"]))
    st.append(figcap("Core line in vermilion; each branch joins at the core file it "
                     "imports most (measured). Dashed = re-export coupling only. "
                     "Interchange rings = imported by ≥2 subsystems.",
                     "topology FACT · geometry DERIVED"))
    p(st, "The lineage is honest about what is aesthetic: since Beck (1933) transit maps "
          "trade geography for legibility, and the algorithmic literature — Nöllenburg & "
          "Wolff's exact formulation (2011), the EuroVis grid solvers (2020) — optimizes "
          "within that grammar. Here the grammar is borrowed but every topological "
          "choice is a measurement.")
    h2(st, "Reading the map")
    st.append(data_table(["mark", "meaning"],
                         [["solid disc", "station — a file, placed on its subsystem's line"],
                          ["double ring", "interchange — imported by two or more subsystems"],
                          ["dashed line", "subsystem coupled to the core by re-export only"],
                          ["line join", "the measured anchor: the core file that subsystem imports most"],
                          ["vermilion", "the core line — reserved, as everywhere, for what carries risk or life"]],
                         [0.22, 0.78], aligns={0: "m"}))
    st.append(figcap("Legend. Topology is measured; only geometry is aesthetic.", "guide"))
    for L in G["_metro"]["lines"]:
        h2(st, f"Line — {L['name']}")
        anch = L["anchor"] or "— (no measured imports into core; re-export coupling only)"
        p(st, (f"Joins the core at {cspan(esc(anch), 'Mono')}." if L["anchor"] else
               f"Anchor: {esc(anch)}.") + ixn(L["name"]), "cell")
        rows = [[s["file"] + ixn(s["file"]), str(s["in"]), str(s["out"]),
                 (s.get("summary") or "")[:150] + ("…" if len(s.get("summary") or "") > 150 else "")]
                for s in L["stations"]]
        st.append(data_table(["station (file)", "in", "out", "AI summary (UNVERIFIED, receipt in ledger)"],
                             rows, [0.30, 0.07, 0.07, 0.56], aligns={0: "m"}))
        term = next((x for x in L["stations"] if x.get("summary")), None)
        if term:
            st.append(Spacer(0, 1.5 * mm))
            st.append(receipt_card({"summary": term["summary"], "file": term["file"],
                                    "model": term.get("model", ""),
                                    "prompt_sha": term.get("sha", ""),
                                    "generated_at": "receipt in Register A"}))
        st.append(Spacer(0, 3 * mm))

    # ================= CH UML views
    _U = rdflib.URIRef
    path_sha, path_subj = {}, {}
    for s_, o_ in g.subject_objects(_U(CR.CBM + "contentSha256")):
        p_ = next(g.objects(s_, _U(CR.CBM + "path")), None)
        if p_ is not None:
            path_sha[str(p_)] = str(o_); path_subj[str(p_)] = s_
    mp = G["_metro"]["main_pkg"]
    classes, parse_fail, files_parsed = {}, 0, 0
    for p_, sha_ in sorted(path_sha.items()):
        if not (p_.endswith(".py") and p_.startswith(mp + "/")):
            continue
        bp = os.path.join(args.bundle, "blobs", sha_)
        if not os.path.exists(bp): continue
        try:
            tree_ = pyast.parse(open(bp, encoding="utf-8", errors="replace").read())
            files_parsed += 1
        except SyntaxError:
            parse_fail += 1; continue
        for nd in pyast.walk(tree_):
            if isinstance(nd, pyast.ClassDef):
                meths = [b.name for b in nd.body
                         if isinstance(b, (pyast.FunctionDef, pyast.AsyncFunctionDef))
                         and not b.name.startswith("_")]
                bases = []
                for b in nd.bases:
                    while isinstance(b, pyast.Subscript): b = b.value
                    if isinstance(b, pyast.Name): bases.append(b.id)
                    elif isinstance(b, pyast.Attribute): bases.append(b.attr)
                classes[nd.name] = {"methods": meths, "file": p_, "bases": bases}
    gen_edges = [(nm_, b_) for nm_, nd_ in classes.items() for b_ in nd_["bases"]]
    adj = defaultdict(set)
    for a_, b_ in gen_edges:
        if a_ in classes and b_ in classes:
            adj[a_].add(b_); adj[b_].add(a_)
    seen_, comps = set(), []
    for n_ in classes:
        if n_ in seen_ or n_ not in adj: continue
        stack, comp = [n_], set()
        while stack:
            x_ = stack.pop()
            if x_ in comp: continue
            comp.add(x_); seen_.add(x_); stack += list(adj[x_] - comp)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    def mk_tree(comp):
        nodes = {n_: {"methods": classes[n_]["methods"], "file": classes[n_]["file"]}
                 for n_ in comp}
        edges, ext_used = [], set()
        for n_ in comp:
            for b_ in classes[n_]["bases"]:
                if b_ in comp: edges.append((n_, b_))
                elif b_ not in ("object", "Generic", "Protocol", "ABC", "Enum",
                                "str", "type", "BaseModel"):
                    ext_used.add(b_); edges.append((n_, b_))
        for e_ in ext_used:
            nodes[e_] = {"methods": [], "external": True}
        depth = {}
        def dep(n_, guard=0):
            if n_ in depth or guard > 12: return depth.get(n_, 0)
            pars = [b for a, b in edges if a == n_ and b in nodes]
            depth[n_] = 0 if not pars else 1 + max(dep(pp, guard + 1) for pp in pars)
            return depth[n_]
        for n_ in nodes: dep(n_)
        maxd = max(depth.values() or [0])
        levels = [[] for _ in range(maxd + 1)]
        for n_, d_ in depth.items(): levels[d_].append(n_)
        levels = [lv for lv in levels if lv]
        omitted = 0
        for i, lv in enumerate(levels):
            if len(lv) > 6:
                keep = sorted(lv, key=lambda n: -(len(nodes[n].get("methods", []))
                                                  + sum(1 for a, b in edges if b == n)))[:6]
                omitted += len(lv) - 6
                drop = set(lv) - set(keep)
                levels[i] = keep
                edges[:] = [(a, b) for a, b in edges if a not in drop and b not in drop]
                for d_ in drop: nodes.pop(d_, None)
        return {"nodes": nodes, "edges": edges, "levels": levels, "omitted": omitted}
    trees = [mk_tree(c) for c in comps[:2] if len(c) >= 4]
    line_names = [L["name"] for L in G["_metro"]["lines"]]
    lineset = set(line_names)
    subsys_f = G["_district"]["subsystem"]
    pair = Counter()
    for s_, o_ in g.subject_objects(_U(CR.CBM + "imports")):
        a_, b_ = subsys_f(CR.name_of(s_)), subsys_f(CR.name_of(o_))
        if a_ != b_ and a_ in lineset and b_ in lineset: pair[(a_, b_)] += 1
    src_by_sub = Counter(subsys_f(CR.name_of(f)) for f in G["_district"]["files"]
                         if G["_district"]["ftype"].get(f) != "test_code")
    uml_nodes = [(nm, f"{src_by_sub.get(nm, 0)} source files") for nm in line_names]
    uml_edges = sorted(((a, b, n) for (a, b), n in pair.items()),
                       key=lambda x: -x[2])[:9]

    # Object-diagram exemplar: the most-imported main-package file present in
    # the graph (falls back to the first main-package path).
    main_paths = {p_ for p_ in path_subj if p_.startswith(mp + "/")}
    ex_path = next((c["file"] for c in G["chokepoints"] if c["file"] in main_paths),
                   min(main_paths, default=None))
    ex_subj = path_subj.get(ex_path)
    obj_data = None
    if ex_subj is not None:
        def one(pred): return next(g.objects(ex_subj, _U(CR.CBM + pred)), None)
        def lit(pred, fmt=str):
            v = one(pred); return "\u2014" if v is None else fmt(v)
        slots = [("language", lit("language")),
                 ("sizeBytes", lit("sizeBytes", lambda v: f"{int(v):,}")),
                 ("contentSha256", lit("contentSha256", lambda v: str(v)[:12] + "\u2026")),
                 ("gitBlobSha", lit("gitBlobSha", lambda v: str(v)[:10] + "\u2026"))]
        links = []
        for t in list(g.objects(ex_subj, _U(CR.CBM + "imports")))[:2]:
            links.append(("imports", {"name": CR.name_of(t).split("/")[-1],
                                      "cls": "SourceFile"}))
        extp = next(g.objects(ex_subj, _U(CR.CBM + "importsExternal")), None)
        if extp is not None:
            links.append(("importsExternal",
                          {"name": str(extp).split("#pkg/")[-1], "cls": "Package"}))
        conc = next(g.objects(ex_subj, _U(CR.CBM + "lexicalizes")), None)
        if conc is not None:
            links.append(("lexicalizes",
                          {"name": str(conc).split("#concept/")[-1][:18],
                           "cls": "Concept"}))
        ph = next(g.objects(ex_subj, _U(CR.CBM + "hasPhase")), None)
        if ph is not None:
            links.append(("hasPhase", {"name": str(ph).split("#")[-1].split("/")[-1],
                                       "cls": "Phase"}))
        obj_data = {"main": {"name": ex_path.split("/")[-1], "cls": "SourceFile",
                             "slots": slots}, "links": links[:5]}

    comp_nodes, comp_edges = [], []
    if rels_all:
        want = ([f"module:{mp}"]
                + [f"module:{nm}" for nm in line_names if nm != f"{mp} core"]
                + ["module:tests", "ext:pydantic", "ext:starlette"])
        have = {r.get("from") for r in rels_all} | {r.get("to") for r in rels_all}
        want = [w_ for w_ in want if w_ in have][:9]
        comp_nodes = [(w_, w_.split(":", 1)[1], w_.startswith("ext:"))
                      for w_ in want]
        tonemap = {"certain": INK, "strong": GREY, "probable": PALE}
        agg = {}
        for r in rels_all:
            a_, b_ = r.get("from"), r.get("to")
            if a_ in want and b_ in want and a_ != b_:
                k = (a_, b_, r.get("type"))
                cur = agg.get(k)
                if cur is None or r.get("strength", 0) > cur[0]:
                    agg[k] = (r.get("strength", 0), r.get("confidence", "probable"))
        comp_edges = sorted(
            [(a_, b_, "\u00ab" + str(t_) + "\u00bb \u00d7" + str(v[0]),
              tonemap.get(v[1], PALE)) for (a_, b_, t_), v in agg.items()],
            key=lambda x: -len(x[2]))[:10]

    outside_in = Counter()
    for s3_, o3_ in g.subject_objects(_U(CR.CBM + "imports")):
        tn, sn = CR.name_of(o3_), CR.name_of(s3_)
        if tn.startswith(mp + "/") and not sn.startswith(mp + "/"):
            outside_in[tn[len(mp) + 1:]] += 1
    ports = outside_in.most_common(2)
    inner_parts = [f"{mp} core"] + [nm.split("/", 1)[1] for nm in line_names
                                    if nm != f"{mp} core"][:5]
    def _short(x): return x.split("/", 1)[1] if x.startswith(mp + "/") else x
    conn3 = [(_short(a), _short(b), n) for (a, b), n in pair.items()]

    act_groups = []
    for grp in (build_groups_raw or []):
        members = grp if isinstance(grp, list) else [grp]
        act_groups.append({"n": len(members),
                           "samples": [str(m).split(":", 1)[-1][:24]
                                       for m in members[:3]]})

    chapter(st, "UML views",
            deck="All fourteen UML 2.5 diagram types, accounted for: seven drawn "
                 "from data, seven refused in print with the missing artifact named. "
                 "Full coverage of the taxonomy \u2014 never at the price of fiction.",
            sections=["Capability across the full UML 2.5 taxonomy",
                      "Package diagram", "Object diagram \u2014 the substrate, instanced",
                      "Component diagram", "Composite structure \u2014 measured ports",
                      "Class diagrams \u2014 the measured hierarchies",
                      "Activity diagram \u2014 the build's concurrency",
                      "Profile \u2014 the stereotypes declared",
                      "What is deliberately not drawn"])
    h2(st, "Capability across the full UML 2.5 taxonomy")
    st.append(data_table(["family", "uml 2.5 diagram", "supported", "grounding in this bundle"],
        [["Structure", "Class", "yes \u2014 FACT*",
          "classes + generalization parsed from the verified blob store (contentSha256)"
          + ("; compensates for symbols_extracted = 0 (see findings)" if sym_tot == 0 else "")],
         ["Structure", "Package", "yes \u2014 FACT",
          "cbm:imports aggregated by subsystem; counts measured"],
         ["Structure", "Object", "yes \u2014 FACT",
          "one file entity instanced from the graph with its literal slots and typed links"],
         ["Structure", "Component", "yes \u2014 tagged",
          "decomposition relationships (type, strength, confidence); interpretive layer quoted with its own confidence"],
         ["Structure", "Composite structure", "yes \u2014 FACT",
          "internal parts + measured connectors; ports = boundary files ranked by imports arriving from outside the package"],
         ["Structure", "Profile", "yes \u2014 definition",
          "the stereotypes this dossier applies, declared formally; a definition, not a measurement"],
         ["Structure", "Deployment", "no",
          "no runtime or infrastructure topology exists in the output set"],
         ["Behavior", "Activity", "yes \u2014 tagged",
          "decomposition build_order: groups are order-free within, sequential between \u2014 fork/join by construction"],
         ["Behavior", "Use case", "no",
          "no actor or requirement artifacts are recorded"],
         ["Behavior", "State machine", "no",
          "no behavioral state model is extracted"],
         ["Behavior", "Sequence", "no",
          "imports are not calls; no interactions or traces recorded"],
         ["Behavior", "Communication", "no",
          "same missing artifact as sequence: interactions"],
         ["Behavior", "Interaction overview", "no",
          "composes interactions, which do not exist here"],
         ["Behavior", "Timing", "no",
          "no lifelines or timing data in the bundle"]],
        [0.11, 0.19, 0.13, 0.57]))
    st.append(figcap("The honesty table: notation is only offered where data exists. "
                     "*Associations and attribute types are omitted \u2014 resolving "
                     "them needs type analysis the pipeline does not yet perform.",
                     "method"))
    h2(st, "Package diagram")
    st.append(UMLPackagesRL(uml_nodes, uml_edges))
    st.append(figcap("Subsystems of the main package as UML packages; dashed open "
                     "arrows are \u00abimport\u00bb dependencies with measured edge "
                     "counts. Same topology as the metro, different grammar.", "FACT"))
    if obj_data:
        h2(st, "Object diagram \u2014 the substrate, instanced")
        st.append(UMLObjectRL(obj_data))
        st.append(figcap("One entity from the graph, UML-instanced: underlined "
                         "instance names, literal slots, typed links exactly as the "
                         "triples state them.", "FACT"))
    if comp_nodes:
        h2(st, "Component diagram")
        st.append(UMLComponentRL(comp_nodes, comp_edges))
        st.append(figcap("Components and typed dependencies from the decomposition's "
                         "relationship register; edge tone = the decomposer's stated "
                         "confidence, strength = file count.",
                         "interpretive \u00b7 confidence tagged"))
    if ports:
        h2(st, "Composite structure \u2014 measured ports")
        st.append(UMLCompositeRL(mp, inner_parts, conn3, ports))
        st.append(figcap("Internal parts with measured connectors; the two ports are "
                         "the boundary files receiving the most imports from outside "
                         "the package \u2014 where the world actually plugs in.",
                         "FACT"))
    h2(st, "Class diagrams \u2014 the measured hierarchies")
    p(st, f"At typesetting time the dossier parsed {files_parsed} main-package source "
          f"blobs ({parse_fail} syntax failures) and found {len(classes)} classes with "
          f"{sum(1 for a, b in gen_edges if b in classes)} internal generalization "
          "edges. The two largest hierarchies follow; every blob is addressed by its "
          "sha-256, so this extraction inherits the bundle's verification.")
    for ti, T in enumerate(trees, 1):
        st.append(UMLClassTreeRL(T))
        roots = ", ".join(sorted(T["levels"][0])[:3])
        st.append(figcap(f"Hierarchy {ti}: rooted at {roots}. Hollow triangles are "
                         f"generalization; dashed pale boxes are external bases"
                         + (f"; {T['omitted']} narrow leaves omitted for legibility"
                            if T["omitted"] else "") + ".", "FACT"))
        st.append(Paragraph("".join(ixn(n) for n in T["nodes"]), S["monosm"]))
        st.append(Spacer(0, 2 * mm))
    if act_groups:
        h2(st, "Activity diagram \u2014 the build's concurrency")
        st.append(UMLActivityRL(act_groups))
        st.append(figcap("The decomposition's build order as an activity: within each "
                         "fork/join pair the parts are buildable in any order; groups "
                         "are sequential. Three exemplars shown per group.",
                         "interpretive \u00b7 order semantics by construction"))
    h2(st, "Profile \u2014 the stereotypes declared")
    st.append(UMLProfileRL())
    st.append(figcap("The UML profile this dossier applies: every stereotype used in "
                     "this chapter, with the metaclass it extends. A definition, "
                     "stated so the notation is auditable too.", "definition"))
    h2(st, "What is deliberately not drawn")
    p(st, "Seven of the fourteen types are refused: use case (no actor or "
          "requirement artifacts), sequence, communication, interaction overview "
          "and timing (imports are not calls \u2014 no interactions or traces are "
          "recorded), state machine (no behavioral model), and deployment (no "
          "runtime topology). The house rule stands: a diagram without data is "
          "fiction. When the enricher gains call-graph extraction, four of the "
          "seven become measurable at once; until then the refusal is part of the "
          "deliverable.")

    # ================= CH 7 districts
    chapter(st, "The districts", sections=["The semantic city","Method, lineage & limits"],
            deck="The semantic city: positions from meaning, the directory tree demoted to "
            "paint — answering the software-city literature's own critique.")
    st.append(Scatter(G["_district"], Y))
    st.append(figcap("Each dot a file placed by its embedding centroid (t-SNE, seed 42); "
                     "solid = source sized by length, hollow = tests. Tests settle "
                     "beside what they test — the fog is the coverage.",
                     "positions DERIVED · sizes FACT"))
    if Y is not None:
        try:
            from sklearn.cluster import KMeans
            import numpy as np
            km = KMeans(n_clusters=12, random_state=42, n_init=10).fit(Y)
            files = G["_district"]["files"]; ftype = G["_district"]["ftype"]
            nb = defaultdict(lambda: {"n": 0, "t": 0, "dirs": Counter()})
            for i, f in enumerate(files):
                c_ = int(km.labels_[i]); nb[c_]["n"] += 1
                nb[c_]["t"] += 1 if ftype.get(f) == "test_code" else 0
                nb[c_]["dirs"][CR.name_of(f).split("/")[0]] += 1
            rows = []
            for c_, v in sorted(nb.items(), key=lambda kv: -kv[1]["n"]):
                dom, domn = v["dirs"].most_common(1)[0]
                rows.append([f"N{c_+1:02d}", dom, str(v["n"]),
                             f"{100*v['t']/max(1,v['n']):.0f}%",
                             f"{100*domn/max(1,v['n']):.0f}%"])
            h2(st, "Neighborhood census")
            st.append(data_table(["id", "dominant directory", "files", "tests", "purity"],
                                 rows, [0.10, 0.42, 0.14, 0.16, 0.18], aligns={0: "m", 1: "m"}))
            st.append(figcap("Twelve display-space neighborhoods (k-means, seed 42). "
                             "Purity = share of members from the dominant directory; "
                             "impure neighborhoods are where meaning and folder "
                             "structure disagree.", "DERIVED"))
            st.append(Paragraph(ixs("k-means") + ixs("neighborhoods"), S["monosm"]))
        except ImportError:
            pass
    p(st, "CodeCity drew classes as buildings in package districts and its literature "
          "concedes the layout beyond containment is largely arbitrary, suggesting "
          "coupling-aware placement. This view supplies exactly that: proximity is "
          "learned from content, so when a file's neighborhood disagrees with its "
          "directory, the disagreement is a finding, not noise.")
    h2(st, "Method, lineage & limits")
    p(st, "Per-file vectors are the mean of chunk embeddings (MiniLM-L6-v2, d=384); "
          "PCA-50 precedes t-SNE (perplexity 30, seed 42). t-SNE preserves local "
          "neighborhoods, not global distances — separation between far regions is not "
          "a metric." + ixs("t-SNE") + " Lineage: Wettel &amp; Lanza (CodeCity, ICSE\u201908); CodeCharta; "
          "Steinbr\u00fcckner &amp; Lewerentz (Evo-Streets); Kuhn et al. (Software "
          "Cartography); Bruls et al. (Squarified Treemaps). Stated limits are part of "
          "the deliverable.")

    # ================= CH 8 concepts
    chapter(st, "The concept stratum", sections=["Atlas — top 120 by breadth"],
            deck=(f"{n_concepts:,} concepts lexicalized across the corpus — the "
                  "vocabulary the codebase actually speaks." if n_concepts else
                  "No L3 concept layer was provided for this run — the absence is "
                  "reported, not papered over."))
    if conc_top:
        p(st, f"The L3 layer links {n_concepts:,} SKOS concepts to chunks and "
              "paths. The atlas below ranks concepts by breadth: the number of paths in "
              "which each appears. Breadth is a FACT of the corpus; the concept "
              "inventory itself is DERIVED (lexicalization).")
        third = math.ceil(len(conc_top[:120]) / 3)
        colsets = [conc_top[:third], conc_top[third:2 * third], conc_top[2 * third:120]]
        tbl = []
        for i in range(third):
            row = []
            for cs in colsets:
                if i < len(cs):
                    k, v = cs[i]; row += [Paragraph(cspan(esc(k), "Mono", INK, 7), S["cellm"]),
                                          Paragraph(cspan(f"{v:,}", "Mono", GREY, 7), S["cellm"])]
                else: row += ["", ""]
            tbl.append(row)
        t = Table(tbl, colWidths=[COL_W * x for x in (0.22, 0.11, 0.22, 0.11, 0.22, 0.11)])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, FAINT),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.6),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6)]))
        st.append(t)
        st.append(figcap("Concept atlas — top 120 by path breadth.", "FACT (breadth)"))

    # ================= CH 9 L4 receipts method
    chapter(st, "The AI layer and its receipts", sections=["Provenance completeness, measured","Two exemplars","Known gap: the 4,000-character cap"],
            deck="Every model-written sentence ships with model, prompt hash, timestamp, and "
            "target hash. Here is the proof, then the ledger.")
    if enrich:
        pc = 100 * enrich["provenance_complete"] / max(1, enrich["n"])
        st.append(StatStrip([(enrich["n"], "records"),
                             (f"{pc:.1f}%", "provenance complete"),
                             (len(enrich["models"]), "models"),
                             (enrich["near_4000_cap"], "at 4,000-char cap")]))
        st.append(figcap("Provenance completeness measured across every record.", "FACT"))
        p(st, "Content is UNVERIFIED by definition — it is model prose — but its "
              "provenance is FACT: recall any sentence by prompt hash, re-run it, diff "
              "it. Two exemplars follow; the complete ledger is Register A.")
        for r in G["receipts"][:2]:
            st.append(receipt_card(r)); st.append(Spacer(0, 2 * mm))
        if enrich["near_4000_cap"]:
            st.append(callout("Known gap",
                              f"{enrich['near_4000_cap']} records sit at the 4,000-character "
                              "truncation cap — a disclosed limitation of the current "
                              "enricher, scheduled for repair." + ixs("provenance") + ixs("4 000-character cap")))

    # ================= CH 10 arc4d3
    if abox:
        chapter(st, f"{len(abox['dims'])} dimensions", sections=["The wheel","Dimension dossiers","Risk register"],
                deck="An orthogonal architecture classification — LLM-applied, "
                "schema-constrained, and by its own declaration unverified until "
                "validation passes.")
        st.append(Wheel(abox))
        st.append(figcap("Radius = classifier confidence; vermilion = a dimension that "
                         "revealed a risk.", "UNVERIFIED · confidence tagged"))
        cf = Counter(d.get("conf") for d in abox["dims"])
        p(st, "Confidence distribution: " + " · ".join(f"{k} {v}" for k, v in cf.most_common())
              + f". Classified by {esc(abox['creator'][:90])}. The ABox header itself "
                "carries the rule: LLM output is untrusted until the validation step "
                "exits clean.")
        h2(st, "Dimension dossiers")
        for d in abox["dims"]:
            risky = "risk" in d
            head = (ixs(d.get("dim", ""))
                    + f'{cspan(esc(d.get("dim","")), "MonoB", VERM if risky else INK, 8.5)}'
                    f'   {cspan(esc(d.get("dominant") or "/".join(d.get("values",[])) or "—"), "Mono", INK, 8)}'
                    f'   {cspan("confidence " + esc(d.get("conf","?")) + ("  ·  RISK" if risky else ""), "Mono", GREY, 7)}')
            block = [Paragraph(head, S["cell"])]
            vals = d.get("values") or []
            if len(vals) > 1:
                block.append(Paragraph(cspan("values: " + esc(" · ".join(vals[:6])),
                                             "Mono", GREY, 6.6), S["cellg"]))
            if d.get("evidence"):
                block.append(Paragraph(f'{esc(d["evidence"][:420])}', S["cellg"]))
            st.append(KeepTogether(block + [Spacer(0, 2.2 * mm), Rule(lw=0.3, color=FAINT, pad=1), Spacer(0, 2.2 * mm)]))
        h2(st, "Risk register")
        for r in abox["risks"]:
            if r["id"].startswith("Overlay"):
                p(st, f"<i>Overlay reading:</i> {esc(r['label'][:220])}", "cellg"); continue
            st.append(callout(r["id"].replace("Risk_", "").replace("_", " "),
                              ixs(r["id"]) + esc(r["label"][:420])))
            st.append(Spacer(0, 2 * mm))

    # ================= CH 11 decomposition
    if decomp:
        chapter(st, "The decomposition", sections=["Composition & confidence","Register B — parts, complete"],
                deck=f"{decomp['n_parts']} parts, every interpretive field confidence-tagged; "
                f"{decomp['gates']:,} quality gates await whoever rebuilds.")
        st.append(WaffleRL(decomp, w=COL_W * 0.62, h=46 * mm))
        st.append(figcap("One cell per part, tone = confidence.", "confidence tagged"))
        h2(st, "Composition")
        st.append(HBars(decomp["kinds"]))
        st.append(figcap("Parts by kind.", "FACT (partition) · roles tagged"))
        p(st, f"Confidence: " + " · ".join(f"{k} {v}" for k, v in decomp["conf"]) +
              f". Relationships: {decomp['relationships']}. Measured cycles: module "
              f"{decomp['cycles']['module']}, file {decomp['cycles']['file']}. Ordered "
              f"build groups of sizes {decomp['build_groups']}.")
        p(st, f"<i>Stated purpose ({esc(str(decomp['purpose_conf']))}-confidence):</i> "
              f"“{esc(str(decomp['purpose'])[:260])}”", "cellg")
        h2(st, "Register B — parts (complete)")
        p(st, "Complete register of all parts with kind, layer, confidence, and stated "
              "responsibility. Evidence pointers live in the machine artifact.", "cap")
        st += [SetChapter(f"{CH['n']:02d}", "Register B — parts"), NextPageTemplate("ledger"), PageBreak()]
        for pt in parts_all:
            nm = str(pt.get("name", pt.get("id", "")))
            row = Paragraph(
                f'{ixn(nm)}{cspan(esc(nm[:44]), "MonoB", INK, 7)}<br/>'
                f'{cspan(esc(str(pt.get("kind",""))) + " · layer " + esc(str(pt.get("layer")) if pt.get("layer") is not None else "—") + " · " + esc(str(pt.get("overall_confidence") or pt.get("responsibility_confidence") or "")), "Mono", GREY, 6.2)}<br/>'
                f'{esc(str(pt.get("responsibility",""))[:170])}<br/>'
                f'{cspan(esc(((pt.get("evidence") or {}).get("files") or [""])[0])[:64], "Mono", GREY, 6)}<br/>'
                f'{cspan("files " + str(len((pt.get("evidence") or {}).get("files") or [])) + " · deps " + str(len(pt.get("dependencies") or [])) + " · gates via decomposer", "Mono", PALE, 6)}',
                S["cellg"])
            st.append(KeepTogether([row, Spacer(0, 3.2 * mm)]))
        st += [NextPageTemplate("bodyT"), PageBreak(), SetChapter(f"{CH['n']:02d}", "The decomposition")]

    # ================= CH 12 reconstruction
    if bplan:
        chapter(st, "The reconstruction", sections=["The sequence at a glance","Phases, skips, violations","Register C — the complete sequence"],
                deck=f"{bplan['n_steps']} ordered steps that would rebuild the repository — "
                "with skipped phases explained and known violations flagged, not copied.")
        st.append(BarcodeRL(bplan))
        st.append(figcap("The build sequence: bar height = files created (log), tone = "
                         "step confidence, vermilion = cumulative files.", "order FACT"))
        h2(st, "Phases")
        rows = [[f"{p_['phase']:02d}", p_["title"][:46], str(p_["n"]), f"{p_['creates']:,}",
                 " ".join(f"{k}:{v}" for k, v in sorted(p_["conf"].items()))]
                for p_ in bplan["phases"]]
        st.append(data_table(["ph", "title", "steps", "creates", "confidence"],
                             rows, [0.07, 0.41, 0.10, 0.12, 0.30], aligns={0: "m", 4: "m"}))
        if bplan["skipped"]:
            h2(st, "Phases skipped, with reasons")
            for s_ in bplan["skipped"]:
                p(st, f"<b>{esc(s_['phase'])}</b> — {esc(s_['reason'])}", "cell")
        if bplan["violations"]:
            st.append(callout("Do not replicate blindly",
                              " · ".join(f"{esc(str(k))} ×{v}" for k, v in bplan["violations"])
                              + ". The plan reproduces the system, not its accidents."))
        if bplan["assumptions"]:
            h2(st, "Open assumptions")
            for a_ in bplan["assumptions"]:
                p(st, f"— {esc(a_)}", "cellg")
        h2(st, "Register C — the complete sequence")
        st += [SetChapter(f"{CH['n']:02d}", "Register C — reconstruction sequence"),
               NextPageTemplate("ledger"), PageBreak()]
        for s_ in steps_all:
            rat = str(s_.get("rationale") or "").strip()
            crt = "; ".join((s_.get("creates") or [])[:2])[:120]
            txt = (cspan("STEP %03d" % int(s_.get("step", 0)), "MonoB", VERM, 6.6)
                   + " " + cspan("\u00b7 phase " + str(s_.get("phase")) + " \u00b7 "
                                 + esc(str(s_.get("confidence", ""))),
                                 "Mono", GREY, 6.2) + "<br/>"
                   + "<b>" + esc(str(s_.get("goal", ""))[:150]) + "</b><br/>"
                   + ((esc(rat[:300]) + "<br/>") if rat else "")
                   + ((cspan(esc(crt), "Mono", GREY, 6) + "<br/>") if crt else "")
                   + cspan("creates " + str(len(s_.get("creates") or []))
                           + " \u00b7 tests " + str(len(s_.get("tests_required") or []))
                           + " \u00b7 requires "
                           + (",".join(map(str, (s_.get("requires_steps") or [])[:6])) or "\u2014"),
                           "Mono", PALE, 6))
            st.append(KeepTogether([Paragraph(txt, S["cellg"]), Spacer(0, 3.2 * mm)]))
        st += [NextPageTemplate("bodyT"), PageBreak(), SetChapter(f"{CH['n']:02d}", "The reconstruction")]

    # ================= CH 13 findings
    # Each recommendation is emitted only when this run measured the defect it
    # cites — a finding without its evidence would itself violate the dossier's
    # epistemics.
    recs = []
    if te["n"] and tev["typed_import_edges"] > te["n"]:
        recs.append(("Adopt typed test evidence",
                     "Replace the stem heuristic with test-typed → source-typed import "
                     f"derivation minus infrastructure targets; {recall_x}× evidence "
                     "gain measured in this run.", "FACT-backed"))
    if sym_tot == 0:
        recs.append(("Repair symbol extraction",
                     "symbols_extracted = 0 in this run; restore first-class symbol "
                     "entities or document the profile that disables them.",
                     "disclosed anomaly"))
    if (enrich or {}).get("near_4000_cap"):
        recs.append(("Lift the 4,000-character cap",
                     f"{enrich['near_4000_cap']} summaries truncate at the cap; "
                     "long-file receipts lose content.", "known gap"))
    if abox:
        recs.append(("Validate the ABox",
                     f"Run the schema validation step so the {len(abox['dims'])} "
                     "dimensions graduate from UNVERIFIED; the wheel then earns a "
                     "PASS stamp.", "process"))
    if decomp and (decomp["cycles"]["module"] or decomp["cycles"]["file"]):
        recs.append(("Break the flagged cycles",
                     f"{decomp['cycles']['module']} module cycle(s) and "
                     f"{decomp['cycles']['file']} file cycle(s) are enumerated by the "
                     "decomposition as violations not to replicate; treat them as the "
                     "refactoring queue.", "risk register"))
    chapter(st, "Findings & recommendations",
            sections=[f"{len(recs)} actions, evidence-ranked"],
            deck="What the measurements demand, in the order a maintainer should act.")
    for i, (t, b, tag) in enumerate(recs, 1):
        st.append(KeepTogether([
            Paragraph(f'{ixs("R" + str(i) + " — " + t)}{cspan(f"R{i}", "MonoB", VERM, 9)}  <b>{esc(t)}</b> '
                      f'{cspan("· " + tag, "Mono", GREY, 7)}', S["cell"]),
            Paragraph(esc(b), S["cellg"]), Spacer(0, 2.6 * mm)]))

    h2(st, "Re-verification protocol")
    p(st, "This dossier's claims are reproducible without trusting the dossier. From "
          "the bundle directory:", "cap")
    for cmd in ["sha256sum inventory.ttl   # compare with run_manifest.json artifact claims",
                "python -m pyshacl -s shapes.shacl.ttl inventory.ttl   # expect: conforms",
                "python scripts/cbm_report.py --bundle <bundle>   # full audit: HTML / MD / JSON",
                "python scripts/cbm_dossier.py --bundle <bundle> --validate-shacl   # regenerate this document"]:
        p(st, cspan(esc(cmd), "Mono", INK, 7.6), "cellm")
    p(st, "Every regeneration re-verifies every input hash; a tampered bundle cannot "
          "typeset quietly.", "cap")

    # ================= Register A — receipts ledger
    chapter(st, "Register A — the receipts ledger",
            f"All {len(receipts_all):,} AI-written file summaries, each with model, "
            "prompt hash, timestamp, and target hash. The audit trail, complete.",
            kicker="REGISTER")
    st += [SetChapter(f"{CH['n']:02d}", "Register A — receipts ledger"),
           NextPageTemplate("ledger"), PageBreak()]
    for r in receipts_all:
        row = Paragraph(
            f'{ixn(str(r.get("target","")))}{cspan(esc(str(r.get("target",""))[:48]), "MonoB", INK, 7.2)}<br/>'
            f'{esc(str(r.get("text",""))[:460])}<br/>'
            f'{cspan(esc(str(r.get("model",""))) + " · prompt " + esc(str(r.get("prompt_sha",""))[:14]) + "… · target " + esc(str(r.get("target_sha",""))[:12]) + "… · " + esc(str(r.get("generated_at",""))), "Mono", PALE, 6.0)}',
            S["cellg"])
        st.append(KeepTogether([row, Spacer(0, 4.8 * mm)]))
    st += [NextPageTemplate("bodyT"), PageBreak(), SetChapter(f"{CH['n']:02d}", "Back matter")]

    # ================= Appendix E: parser disclosure
    if ast:
        chapter(st, "Register E — parser disclosure",
                deck="Files the extractor could not structure, listed rather than "
                     "hidden: zero-AST inputs and silent zero-symbol sources.",
                kicker="REGISTER",
                sections=["Zero-AST files by language", "Silent zero-symbol list", "Extractor notes"])
        h2(st, "Zero-AST population")
        rows = [[l["lang"], str(l["files"]), str(l["files_zero_ast"])]
                for l in ast["langs"] if l["files_zero_ast"]]
        if rows:
            st.append(data_table(["language", "files", "zero-AST"], rows,
                                 [0.4, 0.3, 0.3], aligns={0: "m"}))
            st.append(figcap("Languages with unstructured inputs.", "FACT"))
        if ast["silent_list"]:
            h2(st, "Silent zero-symbol files")
            for f_ in ast["silent_list"]:
                p(st, ixn(str(f_)) + cspan(esc(str(f_)), "Mono", GREY, 7.4), "cellm")
            if ast.get("silent_truncated"):
                p(st, "List truncated by the extractor; complete set in ast_coverage.json.", "cap")
        notes = ast.get("notes")
        if notes:
            h2(st, "Extractor notes, verbatim")
            items = (list(notes.items()) if isinstance(notes, dict)
                     else [(None, n) for n in notes])
            for k_, n_ in items[:8]:
                pre = f'{cspan(esc(str(k_)), "MonoB", INK, 7)} — ' if k_ else "— "
                p(st, pre + esc(str(n_)[:240]), "cellg")

    # ================= back matter
    st += [SetChapter(f"{CH['n']:02d}", "Back matter"), NextPageTemplate("bodyT"), PageBreak()]
    h2(st, "Methodology & lineage")
    p(st, "Measured layers: tree-sitter extraction to RDF (FACT); SHACL conformance "
          "(FACT); import analytics by SPARQL-equivalent traversal (FACT). Derived "
          "views: t-SNE (seed 42) over per-file embedding centroids; metro geometry in "
          "Beck's octilinear grammar with measured topology; display-space clustering "
          "for neighborhoods. Interpretive layers are quoted with their own confidence "
          "vocabularies.")
    h2(st, "References")
    for ref in ["Beck, H. (1933). London Underground diagram — the octilinear grammar.",
                "Nöllenburg, M. & Wolff, A. (2011). Drawing and Labeling High-Quality "
                "Metro Maps by Mixed-Integer Programming. IEEE TVCG 17(5):626–641. "
                + "<a href='https://doi.org/10.1109/TVCG.2010.81' color='#3f5c6b'><u>doi:10.1109/TVCG.2010.81</u></a>",
                "Bast, H., Brosi, P. & Storandt, S. (2020). Metro Maps on Octilinear "
                "Grid Graphs. EuroVis / CGF 39(3):357–367. "
                + "<a href='https://doi.org/10.1111/cgf.13986' color='#3f5c6b'><u>doi:10.1111/cgf.13986</u></a>",
                "Wu, H.-Y., Niedermann, B., Takahashi, S., Roberts, M. J. & "
                "Nöllenburg, M. (2020). A Survey on Transit Map Layout. "
                "CGF 39(3):619–646. "
                + "<a href='https://doi.org/10.1111/cgf.14030' color='#3f5c6b'><u>doi:10.1111/cgf.14030</u></a>",
                "Wettel, R. & Lanza, M. (2008). CodeCity: 3D Visualization of "
                "Large-Scale Software. ICSE'08 companion, pp. 921–922. "
                + "<a href='https://doi.org/10.1145/1370175.1370188' color='#3f5c6b'><u>doi:10.1145/1370175.1370188</u></a>",
                "CodeCharta (open-source software-city toolkit).",
                "Steinbrückner, F. & Lewerentz, C. Evo-Streets: stable city layouts "
                "for evolving software.",
                "Kuhn, A. et al. Software Cartography: thematic maps from vocabulary.",
                "Bruls, M., Huizing, K. & van Wijk, J. Squarified Treemaps."]:
        st.append(Paragraph("\u2014 " + ref, S["cellg"]))
    h2(st, "Candor — what this artifact is not")
    p(st, "This dossier is typeset by a programmatic pipeline (ReportLab) under a fixed "
          "design system. It does not include hand-tuned kerning, bespoke illustration, "
          "photography, or per-page art direction; the metro and district geometries "
          "are algorithmic renderings of measured topology, stated as such on their "
          "captions. Register pages are deliberately austere. Where the pipeline cannot "
          "yet match a human studio — cover art, editorial illustration, optical "
          "margin alignment — the gap is named here rather than "
          "painted over.")
    h2(st, "Colophon")
    p(st, f"Typeset {time.strftime('%Y-%m-%d %H:%M', time.gmtime())} UTC by cbm_dossier.py on the "
          f"verified output set of {repo} (commit {meta['commit'][:12]}). Type: "
          "Bricolage Grotesque, IBM Plex Serif, IBM Plex Mono, Big Shoulders, Arsenal "
          "SC — all OFL. Palette: Measured Ink. Format A4, margins 16/20 mm. "
          "This document re-verifies its inputs every time it is generated.", "cellg")

    chapter(st, "Remissive index",
            deck="Every subject and every name — files, subsystems, packages — with "
                 "the pages where its evidence lives. Built from anchors placed "
                 "throughout the dossier and regenerated, like everything here, on "
                 "every run.",
            kicker="INDEX",
            sections=["Subjects", "Names — files, subsystems, packages"])
    h2(st, "Subjects")
    st.append(sub_ix)
    st += [SetChapter(f"{CH['n']:02d}", "Remissive index — names"),
           NextPageTemplate("ledger"), PageBreak()]
    st.append(Paragraph("Names — files, subsystems, packages" +
                        ixs("Names — files  subsystems  packages"), S["h2"]))
    st.append(nam_ix)

    doc.multiBuild(st, canvasmaker=sub_ix.getCanvasMaker(nam_ix.getCanvasMaker()))
    return doc.page

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--bundle", required=True, help="bundle directory (run_manifest.json et al.)")
    ap.add_argument("--abox", help="arc4d3 ABox .ttl (auto-discovered when omitted)")
    ap.add_argument("--decomposition", help="decomposition .yaml (auto-discovered when omitted)")
    ap.add_argument("--buildplan", help="buildplan .yaml (auto-discovered when omitted)")
    ap.add_argument("--out", default=None,
                    help="output PDF (default: $CBM_REPORTS_DIR/<bundle>__dossier__<timestamp>.pdf)")
    ap.add_argument("--cache-dir", help="parse/layout cache (defaults to a temp dir keyed on the bundle)")
    ap.add_argument("--font-dir", default=FDIR_DEFAULT,
                    help="directory holding the designed TTF set ($CBM_FONT_DIR)")
    ap.add_argument("--validate-shacl", action="store_true",
                    help="re-validate the graph with pyshacl at typesetting time")
    a = ap.parse_args(argv)
    load_env()  # .env (repo-scoped) fills gaps; real environment always wins
    if a.out is None:
        a.out = default_out(os.path.basename(a.bundle.rstrip("/")) or "bundle")
        print(f"[dossier] out: {a.out}", file=sys.stderr)
    n = build(a)
    print(f"[dossier] wrote {a.out} · {n} pages")
    return 0

if __name__ == "__main__":
    sys.exit(main())
