#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a self-contained static HTML site from a codebase-mapper bundle.

The generator reuses the FastAPI backend's bundle loader
(``serving.application.bundle_data.load_bundle``) so that every mechanically
derived fact rendered here comes from the same RDF projection the live backend
serves -- this script never re-parses RDF or re-derives edges. It only
*projects* an already-loaded :class:`Bundle` into static pages.

The output is fully offline: pages cross-link with relative hrefs, all data is
inlined into each page, and no HTTP fetch is performed. The site therefore
renders from a bare ``file://`` open or any static host (GitHub Pages, S3, nginx).

Provenance separation (see ``PURPOSE.md``)
------------------------------------------
Three tiers of data are visually distinguished on every page:

* ``mechanical`` -- facts extracted deterministically from source (files,
  imports, AST chunks, mechanically resolved cross-references).
* ``inferred``   -- generated graph/statistical derivations (concepts,
  co-occurrence, embedding-derived neighbourhoods).
* ``llm``        -- stochastic, LLM-authored enrichment. Rendered behind the
  PALS's-LAW banner and labelled ``advisory / unverified``.

Usage
-----
    python scripts/generate_static_site.py --bundle _tmp/code-base-mapper \\
        --output _site

See ``--help`` for all options.

ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs will always produce some form of
error. Absence of output verification is a design defect, not a runtime bug.
All LLM output rendered by this site is labelled untrusted and unverified.
"""
from __future__ import annotations

__file_meta__ = {
    "role": "tool",
    "status": "active",
    "summary": "Static-site projector over a codebase-mapper bundle.",
    "rules": [
        {
            "id": "reuse-backend-loader",
            "severity": "error",
            "text": "Load bundles via serving.application.bundle_data.load_bundle; "
            "do not re-parse RDF or re-derive edges in this file.",
        },
        {
            "id": "label-llm-output",
            "severity": "error",
            "text": "LLM enrichment must render behind the PALS's-LAW banner and "
            "be labelled advisory/unverified.",
        },
    ],
}

import argparse
import hashlib
import html
import json
import posixpath
import re
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    from markdown_it import MarkdownIt
except ModuleNotFoundError as exc:  # pragma: no cover - actionable failure
    raise SystemExit(
        "markdown-it-py is required to render project prose. Install the site "
        "extra:  pip install -e '.[site]'   (or: pip install markdown-it-py)"
    ) from exc

try:
    from pygments import highlight as _pyg_highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_by_name, get_lexer_for_filename, guess_lexer
    from pygments.util import ClassNotFound
except ModuleNotFoundError as exc:  # pragma: no cover - actionable failure
    raise SystemExit(
        "Pygments is required for syntax highlighting. Install the site extra:"
        "  pip install -e '.[site]'   (or: pip install Pygments)"
    ) from exc

# --------------------------------------------------------------------------- #
# Backend loader import (the single source of mechanically derived facts).
# --------------------------------------------------------------------------- #
_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_ROOT = _REPO_ROOT / "frontend" / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

try:  # pragma: no cover - import wiring
    from serving.application.bundle_data import (  # type: ignore
        Bundle,
        load_bundle,
        xref_row,
    )
except Exception as exc:  # pragma: no cover - actionable failure
    raise SystemExit(
        "Failed to import the backend bundle loader from "
        f"{_BACKEND_ROOT}. Run this script from a checkout of the "
        f"codebase-mapper repository.\nUnderlying error: {exc!r}"
    )

TIER_MECHANICAL = "mechanical"
TIER_INFERRED = "inferred"
TIER_LLM = "llm"

PALS_BANNER = (
    "ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs will always produce some form "
    "of error. The text below is LLM-authored, advisory, and UNVERIFIED. Treat "
    "it as untrusted derived data, not ground truth."
)

# --------------------------------------------------------------------------- #
# Markdown rendering. Project prose is rendered verbatim from the bundle's own
# blobs, so the site's narrative always matches the mapped commit.
# --------------------------------------------------------------------------- #
# Build-time syntax highlighting via Pygments. Highlighted spans are baked
# into the HTML, so the published site needs no client-side highlighter and
# stays fully offline. Code blocks keep a dark scheme in both site themes.
_PYG_STYLE = "monokai"
_PYG_FORMATTER = HtmlFormatter(cssclass="hl", style=_PYG_STYLE, nowrap=False)
_PYG_FORMATTER_LINES = HtmlFormatter(
    cssclass="hl", style=_PYG_STYLE, linenos="inline", nowrap=False
)
# nowrap=True yields bare token spans; we wrap them ourselves in <pre class="hl">
# so the result starts with <pre (markdown-it then uses it verbatim).
_PYG_FORMATTER_INLINE = HtmlFormatter(style=_PYG_STYLE, nowrap=True)


def _lexer_for(code: str, lang: str | None, filename: str | None):
    if lang:
        try:
            return get_lexer_by_name(lang.strip().lower())
        except ClassNotFound:
            pass
    if filename:
        try:
            return get_lexer_for_filename(filename, code)
        except ClassNotFound:
            pass
    try:
        return guess_lexer(code)
    except (ClassNotFound, ValueError):
        return None


def highlight_code(
    code: str, lang: str | None = None, filename: str | None = None, lines: bool = False
) -> str | None:
    """Return Pygments-highlighted HTML, or None if no lexer fits."""
    lexer = _lexer_for(code, lang, filename)
    if lexer is None:
        return None
    fmt = _PYG_FORMATTER_LINES if lines else _PYG_FORMATTER
    return _pyg_highlight(code, lexer, fmt)


def _md_highlight(code: str, lang: str, _attrs: str) -> str:
    """markdown-it highlight hook: returns a <pre class="hl"> block, or '' to
    let markdown-it fall back to its default escaped <pre><code>."""
    lexer = _lexer_for(code, lang or None, None)
    if lexer is None:
        return ""
    inner = _pyg_highlight(code, lexer, _PYG_FORMATTER_INLINE)
    return f'<pre class="hl"><code>{inner}</code></pre>'


_MD = (
    MarkdownIt(
        "commonmark",
        {"html": True, "linkify": True, "highlight": _md_highlight},
    )
    .enable("table")
    .enable("strikethrough")
)
_FRONTMATTER_RE = re.compile(r"^﻿?---\n.*?\n---\n", re.S)
_HREF_RE = re.compile(r'href="([^"]*)"')
_HEADING_RE = re.compile(r"<h([2-4])>(.*?)</h\1>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def add_heading_anchors(html_str: str) -> str:
    """Give h2–h4 stable ids and a hover anchor link (deep-linkable docs)."""
    used: dict[str, int] = {}

    def repl(m: "re.Match[str]") -> str:
        level, inner = m.group(1), m.group(2)
        text = html.unescape(_TAG_RE.sub("", inner)).strip()
        base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"
        n = used.get(base, 0)
        used[base] = n + 1
        slug = base if n == 0 else f"{base}-{n}"
        return (
            f'<h{level} id="{slug}">'
            f'<a class="anchor" href="#{slug}" aria-label="permalink">#</a>'
            f"{inner}</h{level}>"
        )

    return _HEADING_RE.sub(repl, html_str)


def strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def first_heading(text: str) -> str | None:
    """First ATX heading (# or ##) in a markdown document, if any."""
    for line in strip_frontmatter(text).splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    for line in strip_frontmatter(text).splitlines():
        s = line.strip()
        if s.startswith("## ") and "disclaimer" not in s.lower():
            return s[3:].strip()
    return None


def first_paragraph(text: str, min_len: int = 50) -> str | None:
    """First substantive prose paragraph (skips headings, blockquotes, the
    disclaimer block, code fences, and links-only lines)."""
    body = strip_frontmatter(text)
    in_fence = False
    para: list[str] = []
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.strip():
            joined = " ".join(para).strip()
            para = []
            if len(joined) >= min_len and "disclaimer" not in joined.lower():
                return joined
            continue
        s = line.strip()
        if s.startswith(("#", ">", "|", "-", "*", "```")) or "DISCLAIMER.md" in s:
            continue
        para.append(s)
    joined = " ".join(para).strip()
    if len(joined) >= min_len and "disclaimer" not in joined.lower():
        return joined
    return None


def humanize_filename(path: str) -> str:
    name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return name.replace("_", " ").replace("-", " ").strip().title()


@dataclass
class Doc:
    """A project prose document rendered from a bundle blob."""

    repo_path: str
    slug: str
    title: str
    group: str
    order: int
    raw: str
    html: str = ""

    @property
    def rel_path(self) -> str:
        return f"docs/{self.slug}.html"


# --------------------------------------------------------------------------- #
# Small HTML helpers (zero third-party dependencies).
# --------------------------------------------------------------------------- #
def esc(value: Any) -> str:
    """HTML-escape an arbitrary value (None -> empty string)."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def slug_for_path(path: str) -> str:
    """Deterministic, collision-resistant filename slug for a repo path."""
    safe = "".join(c if c.isalnum() else "_" for c in path).strip("_")
    safe = safe[:80] or "file"
    digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
    return f"{safe}__{digest}"


def slug_for_concept(name: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in name).strip("_")
    safe = safe[:80] or "concept"
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    return f"{safe}__{digest}"


def tier_badge(tier: str, label: str | None = None) -> str:
    text = label or tier
    return f'<span class="badge badge-{esc(tier)}" title="provenance: {esc(tier)}">{esc(text)}</span>'


@dataclass
class Page:
    """A rendered page plus its location relative to the site root."""

    rel_path: str  # e.g. "files/foo__ab12cd34.html"
    title: str
    body: str

    @property
    def depth(self) -> int:
        return self.rel_path.count("/")

    @property
    def root_prefix(self) -> str:
        return "../" * self.depth


def page_shell(page: Page, repo_name: str, generated_at: str) -> str:
    """Wrap a page body in the shared HTML chrome."""
    rp = page.root_prefix
    nav = "".join(
        f'<a href="{rp}{href}">{esc(label)}</a>'
        for href, label in (
            ("index.html", "Home"),
            ("architecture.html", "Architecture"),
            ("docs.html", "Docs"),
            ("files.html", "Files"),
            ("concepts.html", "Concepts"),
            ("graph.html", "Graph"),
            *_EXTRA_NAV,
        )
    )
    legend = (
        f'{tier_badge(TIER_MECHANICAL, "mechanical")} extracted from source &nbsp;'
        f'{tier_badge(TIER_INFERRED, "inferred")} generated derivation &nbsp;'
        f'{tier_badge(TIER_LLM, "LLM")} advisory / unverified'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="codebase-mapper static-site generator">
<title>{esc(page.title)} · {esc(repo_name)}</title>
<link rel="stylesheet" href="{rp}assets/site.css">
<script>(function(){{try{{var t=localStorage.getItem('cbm-theme');if(t)document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}}})();</script>
</head>
<body data-root="{rp}">
<header class="site-header">
  <button class="nav-toggle" aria-label="Toggle navigation">☰</button>
  <div class="brand"><a href="{rp}index.html">{esc(repo_name)}</a>
    <span class="subtitle">codebase map</span></div>
  <nav class="site-nav">{nav}</nav>
  <div class="header-actions">
    <form class="search-form" action="{rp}search.html" method="get" role="search">
      <input type="search" name="q" placeholder="Search…" aria-label="Search the site" autocomplete="off">
    </form>
    <button class="theme-toggle" aria-label="Toggle light/dark theme" title="Toggle theme">◑</button>
  </div>
</header>
<div class="provenance-legend">{legend}</div>
<main class="content">
{page.body}
</main>
<footer class="site-footer">
  <span>Generated by the codebase-mapper static-site generator.</span>
  <span>Bundle commit derivation: {esc(generated_at)}</span>
  <span class="pals">{esc(PALS_BANNER)}</span>
</footer>
<script src="{rp}assets/site.js"></script>
</body>
</html>
"""


def table(headers: Iterable[str], rows: Iterable[Iterable[str]], cls: str = "") -> str:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{c}</td>" for c in row)  # cells are pre-rendered HTML
        body_rows.append(f"<tr>{cells}</tr>")
    css = f' class="{cls}"' if cls else ""
    return f"<table{css}><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def section(title: str, tier: str, inner: str, note: str = "") -> str:
    note_html = f'<p class="section-note">{esc(note)}</p>' if note else ""
    return (
        f'<section class="card"><h2>{esc(title)} {tier_badge(tier)}</h2>'
        f"{note_html}{inner}</section>"
    )


def kv_grid(pairs: Iterable[tuple[str, str]]) -> str:
    items = "".join(
        f'<div class="kv"><dt>{esc(k)}</dt><dd>{v}</dd></div>' for k, v in pairs
    )
    return f'<div class="kv-grid">{items}</div>'


# --------------------------------------------------------------------------- #
# Page builders.
# --------------------------------------------------------------------------- #
class SiteBuilder:
    def __init__(self, bundle: Bundle, opts: "Options") -> None:
        self.b = bundle
        self.opts = opts
        self.manifest = bundle.manifest
        self.repo_name = self.manifest.get("repo_name") or bundle.output_dir.name
        self.generated_at = self.manifest.get("generated_at") or "unknown"
        # path -> slug, name -> slug maps for cross-linking
        self.file_slug = {f["path"]: slug_for_path(f["path"]) for f in bundle.files}
        concepts = bundle.concepts.get("concepts", {})
        self.concept_slug = {name: slug_for_concept(name) for name in concepts}
        # Concepts that will have a generated detail page (set by generate()).
        # Links to non-generated concepts render as plain text, never as a
        # dangling href.
        self.linkable_concepts: set[str] = set(concepts)

        # Import degree per file (used to surface "key modules").
        self.degree: Counter[str] = Counter()
        for a, c in bundle.imports:
            self.degree[a] += 1
            self.degree[c] += 1

        # Top-level package grouping (mechanical, by path).
        self.packages: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for f in bundle.files:
            top = f["path"].split("/")[0] if "/" in f["path"] else "(root)"
            self.packages[top].append(f)

        # Prose documents rendered from the bundle's own blobs.
        self.docs: list[Doc] = self._discover_docs()
        self.doc_slug = {d.repo_path: d.slug for d in self.docs}
        # Unique-basename indexes let prose links survive moved/stale paths
        # (e.g. a doc that still points at a pre-refactor file location).
        self._doc_by_basename = self._unique_basenames(self.doc_slug)
        self._file_by_basename = self._unique_basenames(self.file_slug)
        for d in self.docs:
            d.html = self._render_doc_html(d)
        self._docs_sidebar = self._build_docs_sidebar()

    @staticmethod
    def _unique_basenames(path_to_slug: dict[str, str]) -> dict[str, str]:
        counts = Counter(p.rsplit("/", 1)[-1] for p in path_to_slug)
        return {
            p.rsplit("/", 1)[-1]: slug
            for p, slug in path_to_slug.items()
            if counts[p.rsplit("/", 1)[-1]] == 1
        }

    # -- prose document discovery ------------------------------------------- #
    _DOC_GROUPS = (
        ("(root)", "Project"),
        ("docs", "Guides & Reference"),
        ("frontend", "Frontend"),
        ("plugins", "Plugins"),
    )

    def _doc_group(self, repo_path: str) -> str:
        top = repo_path.split("/")[0] if "/" in repo_path else "(root)"
        for key, label in self._DOC_GROUPS:
            if top == key:
                return label
        return "More"

    def _doc_order(self, repo_path: str) -> int:
        name = repo_path.rsplit("/", 1)[-1].lower()
        # README/overview first within a group; disclaimer last.
        if name == "readme.md":
            return 0
        if name == "onboarding.md":
            return 1
        if name == "disclaimer.md":
            return 90
        if name == "claude.md":
            return 80
        return 10

    def _discover_docs(self) -> list[Doc]:
        docs: list[Doc] = []
        seen: set[str] = set()
        for f in self.b.files:
            path = f["path"]
            if not path.lower().endswith(".md"):
                continue
            blob = self.b.output_dir / "blobs" / (f.get("contentSha256") or "")
            if not blob.exists():
                continue
            try:
                raw = blob.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            slug = slug_for_path(path)
            if slug in seen:
                continue
            seen.add(slug)
            title = first_heading(raw) or humanize_filename(path)
            docs.append(
                Doc(
                    repo_path=path,
                    slug=slug,
                    title=title,
                    group=self._doc_group(path),
                    order=self._doc_order(path),
                    raw=raw,
                )
            )
        group_rank = {label: i for i, (_, label) in enumerate(self._DOC_GROUPS)}
        docs.sort(
            key=lambda d: (
                group_rank.get(d.group, len(self._DOC_GROUPS)),
                d.order,
                d.title.lower(),
            )
        )
        return docs

    def _render_doc_html(self, doc: Doc) -> str:
        raw = strip_frontmatter(doc.raw)
        rendered = _MD.render(raw)
        # The page chrome already renders doc.title as an <h1>; drop a leading
        # duplicate H1 from the body so the title isn't shown twice.
        rendered = re.sub(r"^\s*<h1>.*?</h1>\s*", "", rendered, count=1, flags=re.S)
        rendered = self._rewrite_doc_links(rendered, doc.repo_path)
        rendered = add_heading_anchors(rendered)
        return rendered

    def _rewrite_doc_links(self, html_str: str, doc_repo_path: str) -> str:
        """Rewrite intra-repo links so prose cross-links resolve on the site.

        Targets that map to another rendered doc point at its page; targets
        that map to a bundle file point at that file's detail page; everything
        else is left untouched.
        """
        base_dir = posixpath.dirname(doc_repo_path)

        def repl(m: "re.Match[str]") -> str:
            href = m.group(1)
            if not href or href.startswith(("http://", "https://", "mailto:", "#")):
                return m.group(0)
            target, _, anchor = href.partition("#")
            target = target.lstrip("@").strip()
            if not target:
                return m.group(0)
            resolved = posixpath.normpath(posixpath.join(base_dir, target))
            anchor_suffix = f"#{anchor}" if anchor else ""
            if resolved in self.doc_slug:
                return f'href="{self.doc_slug[resolved]}.html{anchor_suffix}"'
            if resolved in self.file_slug:
                return f'href="../files/{self.file_slug[resolved]}.html"'
            # Fall back to a unique basename match (repairs moved/stale paths).
            base = resolved.rsplit("/", 1)[-1]
            if base in self._doc_by_basename:
                return f'href="{self._doc_by_basename[base]}.html{anchor_suffix}"'
            if base in self._file_by_basename:
                return f'href="../files/{self._file_by_basename[base]}.html"'
            # Unresolvable intra-repo target: neutralise so no link 404s.
            return f'data-unresolved="{esc(href)}"'

        return _HREF_RE.sub(repl, html_str)

    def _build_docs_sidebar(self) -> str:
        groups: dict[str, list[Doc]] = defaultdict(list)
        for d in self.docs:
            groups[d.group].append(d)
        ordered = sorted(
            groups.items(),
            key=lambda kv: {label: i for i, (_, label) in enumerate(self._DOC_GROUPS)}.get(
                kv[0], len(self._DOC_GROUPS)
            ),
        )
        parts = ['<nav class="docs-sidebar">']
        for group, items in ordered:
            parts.append(f"<h4>{esc(group)}</h4><ul>")
            for d in items:
                parts.append(
                    f'<li><a href="{d.slug}.html" data-doc="{d.slug}">{esc(d.title)}</a></li>'
                )
            parts.append("</ul>")
        parts.append("</nav>")
        return "".join(parts)

    # -- link helpers (paths are relative to a page at `depth`) -------------- #
    def file_link(self, path: str, depth: int, label: str | None = None) -> str:
        text = esc(label if label is not None else path)
        slug = self.file_slug.get(path)
        if slug is None:
            return text  # external / unmapped target: render plain
        prefix = "../" * depth
        return f'<a href="{prefix}files/{slug}.html">{text}</a>'

    def concept_link(self, name: str, depth: int) -> str:
        slug = self.concept_slug.get(name)
        text = esc(name)
        if slug is None or name not in self.linkable_concepts:
            return text
        prefix = "../" * depth
        return f'<a href="{prefix}concepts/{slug}.html">{text}</a>'

    # -- blob / source reading ---------------------------------------------- #
    def read_blob(self, sha: str | None) -> str | None:
        if not sha:
            return None
        blob = self.b.output_dir / "blobs" / sha
        if not blob.exists():
            return None
        data = blob.read_bytes()
        if len(data) > self.opts.max_source_bytes:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    # -- shared building blocks --------------------------------------------- #
    def doc_by_name(self, *candidates: str) -> Doc | None:
        for cand in candidates:
            for d in self.docs:
                if d.repo_path == cand or d.repo_path.rsplit("/", 1)[-1] == cand:
                    return d
        return None

    def metric_cards(self, depth: int) -> str:
        b, m = self.b, self.manifest
        emb_name = (b.embeddings_meta.get("backend") or {}).get("name")
        cards = [
            ("Files", len(b.files), "source files mapped"),
            ("Import edges", len(b.imports), "resolved module imports"),
            ("Cross-refs", len(b.xrefs), "symbol-level references"),
            ("AST chunks", len(b.chunks), "extracted code units"),
            ("Concepts", len(b.concepts.get("concepts", {})), "inferred concepts"),
            ("Tests", len(b.tests), "test→subject edges"),
        ]
        items = "".join(
            f'<div class="metric"><span class="metric-num">{v:,}</span>'
            f'<span class="metric-label">{esc(label)}</span>'
            f'<span class="metric-sub">{esc(sub)}</span></div>'
            for label, v, sub in cards
        )
        emb = (
            f'<span class="metric-sub">embeddings: {esc(emb_name)} '
            f'({esc(b.embeddings_meta.get("dimension"))}d)</span>'
            if emb_name
            else ""
        )
        return f'<div class="metrics">{items}</div>{emb}'

    def package_table(self, depth: int) -> str:
        rows = []
        for top, files in sorted(self.packages.items(), key=lambda kv: -len(kv[1])):
            langs = Counter(f.get("language") or "—" for f in files)
            lang_str = ", ".join(
                f"{k} {v}" for k, v in langs.most_common(4) if k != "—"
            ) or "—"
            anchor = re.sub(r"[^a-z0-9]+", "-", top.lower()).strip("-") or "root"
            prefix = "../" * depth
            label = (
                f'<a href="{prefix}architecture.html#{anchor}"><code>{esc(top)}</code></a>'
            )
            rows.append(
                (
                    label,
                    f'<span class="num">{len(files)}</span>',
                    esc(lang_str),
                )
            )
        return table(["package", "files", "languages"], rows)

    # -- pages -------------------------------------------------------------- #
    def build_index(self) -> Page:
        m = self.manifest
        overview_doc = self.doc_by_name("README.md")
        tagline = (
            first_paragraph(overview_doc.raw) if overview_doc else None
        ) or "A mapped, queryable knowledge bundle of this repository."

        chips = " ".join(
            f'<span class="chip">{c}</span>'
            for c in (
                f'commit <code>{esc((m.get("commit_sha") or "")[:10])}</code>',
                f'tool v{esc(m.get("tool_version"))}',
                f'vocab {esc(m.get("vocabulary_version"))}',
                f'mapped {esc((self.generated_at or "")[:10])}',
            )
            if c
        )

        hero = (
            '<section class="hero">'
            '<div class="hero-marginalia">Codebase Atlas · Plate 01 — Overview</div>'
            f"<h1>{esc(self.repo_name)}</h1>"
            f'<p class="hero-tagline">{esc(tagline)}</p>'
            f'<div class="chips">{chips}</div>'
            '<div class="hero-actions">'
            '<a class="btn" href="architecture.html">Explore the architecture</a>'
            '<a class="btn btn-ghost" href="docs.html">Read the docs</a>'
            '<a class="btn btn-ghost" href="files.html">Browse files</a>'
            "</div></section>"
        )

        # Featured guides (only those present in the bundle).
        featured_specs = [
            ("docs/ONBOARDING.md", "Onboarding", "Start here."),
            ("docs/analyze.md", "Analyze a repo", "Produce a bundle."),
            ("docs/regenerate.md", "Regenerate", "Rebuild artifacts from a bundle."),
            ("docs/mcp-install.md", "MCP server", "Read-only repo intelligence."),
            ("docs/llm-enrich.md", "LLM enrichment", "Opt-in advisory annotations."),
            ("docs/vocabulary.md", "Vocabulary", "The controlled RDF vocabulary."),
        ]
        cards = []
        for path, title, blurb in featured_specs:
            d = self.doc_by_name(path)
            if not d:
                continue
            cards.append(
                f'<a class="doc-card" href="{d.rel_path}">'
                f"<h3>{esc(title)}</h3><p>{esc(blurb)}</p></a>"
            )
        featured = (
            f'<div class="card-grid">{"".join(cards)}</div>' if cards else ""
        )

        what_doc = overview_doc
        what_html = ""
        if what_doc:
            para = first_paragraph(what_doc.raw, min_len=80)
            if para:
                what_html = (
                    f"<p>{esc(para)}</p>"
                    f'<p><a href="{what_doc.rel_path}">Full overview →</a></p>'
                )

        body = (
            hero
            + section(
                "At a glance",
                TIER_MECHANICAL,
                self.metric_cards(0),
                note="Headline counts straight from the bundle manifest and graph.",
            )
            + (
                section("What this project does", TIER_MECHANICAL, what_html)
                if what_html
                else ""
            )
            + section(
                "Architecture at a glance",
                TIER_MECHANICAL,
                self.package_table(0)
                + '<p><a href="architecture.html">Architecture details →</a></p>',
                note="Top-level packages by file count.",
            )
            + (
                section("Guides", TIER_MECHANICAL, featured) if featured else ""
            )
        )
        return Page("index.html", "Home", body)

    def build_architecture(self) -> Page:
        m = self.manifest
        b = self.b
        intro = (
            "<p>This page is generated from the bundle's mechanically derived "
            "structure. Package groupings are by top-level directory; "
            "&ldquo;key modules&rdquo; are ranked by import degree (in + out). "
            "Authored narrative lives in the "
            '<a href="docs.html">project docs</a>.</p>'
        )

        # Per-package detail cards.
        cards = []
        for top, files in sorted(self.packages.items(), key=lambda kv: -len(kv[1])):
            anchor = re.sub(r"[^a-z0-9]+", "-", top.lower()).strip("-") or "root"
            langs = Counter(f.get("language") or "—" for f in files)
            types = Counter(f.get("type") or "—" for f in files)
            subpkgs = Counter(
                f["path"].split("/")[1]
                for f in files
                if f["path"].count("/") >= 2 and f["path"].startswith(top + "/")
            )
            ranked = sorted(files, key=lambda f: -self.degree.get(f["path"], 0))
            key_mods = [f for f in ranked if self.degree.get(f["path"], 0) > 0][:8]
            key_list = "".join(
                f"<li>{self.file_link(f['path'], 0, label=f['path'])}"
                f' <span class="muted">· deg {self.degree.get(f["path"], 0)}</span></li>'
                for f in key_mods
            ) or '<li class="muted">no internal import edges</li>'
            lang_str = ", ".join(f"{k} ({v})" for k, v in langs.most_common() if k != "—")
            type_str = ", ".join(f"{k} ({v})" for k, v in types.most_common())
            sub_str = (
                " ".join(f'<span class="chip">{esc(s)}</span>' for s, _ in subpkgs.most_common(12))
                if subpkgs
                else '<span class="muted">flat</span>'
            )
            cards.append(
                f'<section class="card" id="{anchor}">'
                f'<h2><code>{esc(top)}</code> {tier_badge(TIER_MECHANICAL)}</h2>'
                + kv_grid(
                    [
                        ("Files", f'<span class="num">{len(files)}</span>'),
                        ("Languages", esc(lang_str) or "—"),
                        ("Types", esc(type_str) or "—"),
                    ]
                )
                + f"<h3>Subpackages</h3><div class='chips'>{sub_str}</div>"
                + f"<h3>Key modules (by import degree)</h3><ul class='link-list'>{key_list}</ul>"
                + "</section>"
            )

        # Entry points (mechanical heuristic: well-known names).
        entry_names = {"__main__.py", "cli.py", "app.py", "main.py"}
        entries = [
            f for f in b.files
            if f["path"].rsplit("/", 1)[-1] in entry_names
            or f["path"].startswith("scripts/")
        ]
        entry_list = "".join(
            f"<li>{self.file_link(f['path'], 0, label=f['path'])}</li>"
            for f in sorted(entries, key=lambda f: f["path"])
        ) or '<li class="muted">none detected</li>'

        lang_rows = [
            (esc(k), f'<span class="num">{esc(v)}</span>')
            for k, v in sorted(m.get("files_by_language", {}).items(), key=lambda kv: -kv[1])
        ]
        type_rows = [
            (esc(k), f'<span class="num">{esc(v)}</span>')
            for k, v in sorted(m.get("files_by_type", {}).items(), key=lambda kv: -kv[1])
        ]

        body = (
            "<h1>Architecture</h1>"
            f'<div class="prose">{intro}</div>'
            + section("Packages", TIER_MECHANICAL, self.package_table(0))
            + "".join(cards)
            + section(
                "Entry points",
                TIER_MECHANICAL,
                f'<ul class="link-list">{entry_list}</ul>',
                note="Heuristic: files named __main__/cli/app/main and scripts/.",
            )
            + section(
                "Language mix",
                TIER_MECHANICAL,
                table(["language", "files"], lang_rows, cls="sortable"),
            )
            + section(
                "File-type mix",
                TIER_MECHANICAL,
                table(["type", "files"], type_rows, cls="sortable"),
            )
            + section(
                "Import graph",
                TIER_MECHANICAL,
                '<p>The full module import graph is available as an '
                '<a href="graph.html">interactive view</a>.</p>',
            )
        )
        return Page("architecture.html", "Architecture", body)

    def build_docs_index(self) -> Page:
        groups: dict[str, list[Doc]] = defaultdict(list)
        for d in self.docs:
            groups[d.group].append(d)
        order = {label: i for i, (_, label) in enumerate(self._DOC_GROUPS)}
        sections = []
        for group, items in sorted(
            groups.items(), key=lambda kv: order.get(kv[0], len(order))
        ):
            cards = "".join(
                f'<a class="doc-card" href="docs/{d.slug}.html">'
                f"<h3>{esc(d.title)}</h3>"
                f'<p class="muted"><code>{esc(d.repo_path)}</code></p></a>'
                for d in items
            )
            sections.append(
                f"<h2>{esc(group)}</h2><div class='card-grid'>{cards}</div>"
            )
        body = (
            "<h1>Documentation</h1>"
            f'<p class="lead">{len(self.docs)} documents rendered verbatim from the '
            "mapped commit. These are the project&rsquo;s own authored docs.</p>"
            + "".join(sections)
        )
        return Page("docs.html", "Docs", body)

    def build_doc_page(self, doc: Doc) -> Page:
        body = (
            '<div class="docs-layout">'
            f"{self._docs_sidebar}"
            f'<article class="prose">'
            f'<p class="doc-source muted">Source: <code>{esc(doc.repo_path)}</code> '
            "· rendered verbatim from the mapped commit.</p>"
            f"<h1>{esc(doc.title)}</h1>"
            f"{doc.html}"
            "</article></div>"
        )
        return Page(doc.rel_path, doc.title, body)

    # -- search ------------------------------------------------------------- #
    def build_search_index(self) -> list[dict[str, str]]:
        """Flat, lightweight index over every navigable entity.

        URLs are stored relative to the site root; the client prepends the
        page's root prefix at query time so search works under file://.
        """
        idx: list[dict[str, str]] = []
        for url, title, hint in (
            ("index.html", "Home", "project overview"),
            ("architecture.html", "Architecture", "packages, modules, graph"),
            ("docs.html", "Documentation", "all project docs"),
            ("files.html", "Files", "file index"),
            ("concepts.html", "Concepts", "concept index"),
            ("graph.html", "Import graph", "interactive graph"),
        ):
            idx.append({"t": title, "u": url, "k": "page", "h": hint})
        for top, files in self.packages.items():
            anchor = re.sub(r"[^a-z0-9]+", "-", top.lower()).strip("-") or "root"
            idx.append(
                {"t": top, "u": f"architecture.html#{anchor}", "k": "package",
                 "h": f"{len(files)} files"}
            )
        for d in self.docs:
            idx.append({"t": d.title, "u": d.rel_path, "k": "doc", "h": d.repo_path})
        for f in self.b.files:
            path = f["path"]
            summary = self.b.enrichment_file_summary.get(path, {}).get("text", "")
            hint = (f.get("language") or f.get("type") or "")
            if summary:
                hint = f"{hint} · {summary[:90]}" if hint else summary[:90]
            idx.append(
                {"t": path, "u": f"files/{self.file_slug[path]}.html", "k": "file",
                 "h": hint}
            )
        concepts = self.b.concepts.get("concepts", {})
        for name in self.linkable_concepts:
            c = concepts.get(name, {})
            label = c.get("label") or ""
            alt = " ".join(c.get("alt_labels", []) or [])
            idx.append(
                {"t": name, "u": f"concepts/{self.concept_slug[name]}.html",
                 "k": "concept", "h": (f"{label} {alt}").strip()}
            )
        return idx

    def build_search_page(self, index: list[dict[str, str]]) -> Page:
        payload = json.dumps(index, separators=(",", ":"))
        body = (
            "<h1>Search</h1>"
            '<input id="search-box" class="filter" type="search" autofocus '
            'placeholder="Search files, concepts, docs, packages…">'
            '<p id="search-stats" class="muted"></p>'
            '<div id="search-results" class="search-results"></div>'
            f'<script type="application/json" id="search-index">{payload}</script>'
        )
        return Page("search.html", "Search", body)

    def build_files_index(self) -> Page:
        rows_data = []
        for f in sorted(self.b.files, key=lambda r: r["path"]):
            path = f["path"]
            rows_data.append(
                {
                    "path": path,
                    "href": f"files/{self.file_slug[path]}.html",
                    "language": f.get("language") or "",
                    "type": f.get("type") or "",
                    "size": f.get("size") or 0,
                    "imports_out": len(self.b.imports_out.get(path, [])),
                    "imports_in": len(self.b.imports_in.get(path, [])),
                    "chunks": len(self.b.chunks_by_file.get(path, [])),
                }
            )
        payload = json.dumps(rows_data)
        body = (
            "<h1>Files</h1>"
            f'<p class="lead">{len(rows_data)} files {tier_badge(TIER_MECHANICAL)}. '
            "Type and import/chunk counts are mechanically extracted.</p>"
            '<input id="filter" class="filter" type="search" '
            'placeholder="Filter files by path, language, or type…">'
            '<table id="files-table" class="sortable data-table">'
            "<thead><tr>"
            '<th data-key="path">Path</th>'
            '<th data-key="language">Lang</th>'
            '<th data-key="type">Type</th>'
            '<th data-key="size" class="num-col">Size</th>'
            '<th data-key="imports_out" class="num-col">Imp→</th>'
            '<th data-key="imports_in" class="num-col">→Imp</th>'
            '<th data-key="chunks" class="num-col">Chunks</th>'
            "</tr></thead><tbody></tbody></table>"
            f'<script type="application/json" id="files-data">{payload}</script>'
        )
        return Page("files.html", "Files", body)

    def build_file_detail(self, f: dict[str, Any]) -> Page:
        path = f["path"]
        depth = 1
        rel = f"files/{self.file_slug[path]}.html"

        meta = kv_grid(
            [
                ("Path", f"<code>{esc(path)}</code>"),
                ("Language", esc(f.get("language")) or "—"),
                ("Type", esc(f.get("type")) or "—"),
                ("Size", f'<span class="num">{esc(f.get("size"))}</span> bytes'),
                ("Content SHA-256", f'<code class="sha">{esc(f.get("contentSha256"))}</code>'),
            ]
        )
        parts = [f"<h1><code>{esc(path)}</code></h1>", section("File", TIER_MECHANICAL, meta)]

        # LLM file summary (advisory).
        summary = self.b.enrichment_file_summary.get(path)
        if summary:
            parts.append(self._enrichment_block("LLM file summary", summary))

        # Imports (mechanical).
        imp_out = sorted(self.b.imports_out.get(path, []))
        imp_in = sorted(self.b.imports_in.get(path, []))
        imp_inner = (
            '<div class="two-col">'
            f"<div><h3>Imports ({len(imp_out)})</h3><ul class='link-list'>"
            + (
                "".join(f"<li>{self.file_link(p, depth)}</li>" for p in imp_out)
                or "<li class='muted'>none</li>"
            )
            + "</ul></div>"
            f"<div><h3>Imported by ({len(imp_in)})</h3><ul class='link-list'>"
            + (
                "".join(f"<li>{self.file_link(p, depth)}</li>" for p in imp_in)
                or "<li class='muted'>none</li>"
            )
            + "</ul></div></div>"
        )
        parts.append(section("Import edges", TIER_MECHANICAL, imp_inner))

        # Concepts (inferred).
        concepts = list((self.b.concepts.get("per_path_concepts") or {}).get(path, []))
        if concepts:
            chips = " ".join(
                f'<span class="chip">{self.concept_link(c, depth)}</span>' for c in concepts
            )
            parts.append(
                section(
                    "Concepts",
                    TIER_INFERRED,
                    f'<div class="chips">{chips}</div>',
                    note="Lexicalised concepts are statistically derived.",
                )
            )

        # Chunks (mechanical, from AST).
        chunk_idxs = self.b.chunks_by_file.get(path, [])
        if chunk_idxs:
            rows = []
            for i in chunk_idxs:
                c = self.b.chunks[i]
                rows.append(
                    (
                        esc(c.get("symbol")) or "—",
                        esc(c.get("kind")) or "—",
                        f'<span class="num">{esc(c.get("beginLine"))}</span>',
                        f'<span class="num">{esc(c.get("endLine"))}</span>',
                    )
                )
            parts.append(
                section(
                    f"AST chunks ({len(chunk_idxs)})",
                    TIER_MECHANICAL,
                    table(["symbol", "kind", "begin", "end"], rows, cls="sortable"),
                )
            )

        # Cross-references (mechanical resolution; resolver disclosed).
        xrefs_out, xrefs_in = self._file_xrefs(path)
        if xrefs_out or xrefs_in:
            parts.append(
                section(
                    "Cross-references",
                    TIER_MECHANICAL,
                    self._xref_tables(xrefs_out, xrefs_in, depth),
                    note="Resolver and resolution method are disclosed per edge.",
                )
            )

        # Source (mechanical).
        source = self.read_blob(f.get("contentSha256"))
        if source is not None:
            parts.append(
                section(
                    "Source",
                    TIER_MECHANICAL,
                    self._source_block(source, filename=path),
                    note="Verbatim source content, addressed by content hash.",
                )
            )
        elif self.opts.inline_source:
            parts.append(
                section(
                    "Source",
                    TIER_MECHANICAL,
                    '<p class="muted">Source omitted (binary, missing blob, or '
                    f"larger than {self.opts.max_source_bytes} bytes).</p>",
                )
            )

        return Page(rel, path, "".join(parts))

    def _file_xrefs(self, path: str) -> tuple[list[dict], list[dict]]:
        b = self.b
        chunk_idxs = b.chunks_by_file.get(path, [])
        out, inc = [], []
        seen_out, seen_in = set(), set()
        for ci in chunk_idxs:
            for e_idx in b.xrefs_by_src_idx.get(ci, []):
                edge = b.xrefs[e_idx]
                if edge["dst_idx"] in seen_out:
                    continue
                seen_out.add(edge["dst_idx"])
                out.append(xref_row(b, edge["dst_idx"], edge))
            for e_idx in b.xrefs_by_dst_idx.get(ci, []):
                edge = b.xrefs[e_idx]
                if edge["src_idx"] in seen_in:
                    continue
                seen_in.add(edge["src_idx"])
                inc.append(xref_row(b, edge["src_idx"], edge))
        key = lambda r: (r["file"] or "", r["beginLine"] or 0, r["symbol"] or "")
        out.sort(key=key)
        inc.sort(key=key)
        return out, inc

    def _xref_tables(self, out: list[dict], inc: list[dict], depth: int) -> str:
        def render(rows: list[dict]) -> str:
            trows = []
            for r in rows:
                trows.append(
                    (
                        esc(r.get("symbol")) or "—",
                        esc(r.get("xref_kind")),
                        self.file_link(
                            r.get("file"), depth, label=(r.get("file") or "—")
                        ),
                        f'<span class="num">{esc(r.get("beginLine"))}</span>',
                        f'<span class="badge badge-mechanical" title="resolver">{esc(r.get("resolver"))}</span> '
                        f'<span class="muted">{esc(r.get("resolution"))}</span>',
                    )
                )
            return table(
                ["symbol", "kind", "file", "line", "resolution"],
                trows,
                cls="sortable",
            )

        return (
            f"<h3>Outgoing ({len(out)})</h3>" + (render(out) if out else "<p class='muted'>none</p>")
            + f"<h3>Incoming ({len(inc)})</h3>" + (render(inc) if inc else "<p class='muted'>none</p>")
        )

    def _source_block(self, source: str, filename: str | None = None) -> str:
        highlighted = highlight_code(source, filename=filename, lines=True)
        if highlighted:
            return f'<div class="source">{highlighted}</div>'
        # Fallback: plain, line-numbered, escaped.
        numbered = "\n".join(
            f'<span class="ln" id="L{i}">{i}</span>{esc(line)}'
            for i, line in enumerate(source.split("\n"), start=1)
        )
        return f'<pre class="source plain"><code>{numbered}</code></pre>'

    def _enrichment_block(self, title: str, row: dict[str, Any]) -> str:
        """Render an LLM enrichment row behind the PALS's-LAW banner.

        ⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — LLM OUTPUT IS UNVERIFIED BY
        DEFAULT. The text rendered here is stochastic, may hallucinate, and is
        labelled advisory/unverified. Any consumer that treats it as ground
        truth is introducing an architectural omission, not a code bug.
        """
        text = esc(row.get("text"))
        meta = " · ".join(
            esc(x)
            for x in (
                f"model: {row.get('model')}" if row.get("model") else "",
                f"generated: {row.get('generated_at')}" if row.get("generated_at") else "",
                f"target_sha: {str(row.get('target_sha'))[:12]}" if row.get("target_sha") else "",
            )
            if x
        )
        return (
            f'<section class="card llm-card"><h2>{esc(title)} {tier_badge(TIER_LLM, "LLM")}</h2>'
            f'<p class="pals-banner">{esc(PALS_BANNER)}</p>'
            f'<blockquote class="llm-text">{text}</blockquote>'
            f'<p class="llm-meta">{meta}</p></section>'
        )

    def build_concepts_index(self) -> Page:
        concepts = self.b.concepts.get("concepts", {})
        rows_data = []
        for name, c in concepts.items():
            href = (
                f"concepts/{self.concept_slug[name]}.html"
                if name in self.linkable_concepts
                else ""
            )
            rows_data.append(
                {
                    "name": name,
                    "href": href,
                    "label": c.get("label") or name,
                    "frequency": c.get("frequency", 0),
                    "file_count": c.get("file_count", 0),
                    "components": len(c.get("components", []) or []),
                }
            )
        rows_data.sort(key=lambda r: -r["frequency"])
        payload = json.dumps(rows_data)
        body = (
            "<h1>Concepts</h1>"
            f'<p class="lead">{len(rows_data)} concepts {tier_badge(TIER_INFERRED)}. '
            "Concepts are statistically derived from lexical and embedding "
            "signals — they are inferences, not source facts.</p>"
            '<input id="filter" class="filter" type="search" '
            'placeholder="Filter concepts by name or label…">'
            '<table id="concepts-table" class="sortable data-table">'
            "<thead><tr>"
            '<th data-key="name">Name</th>'
            '<th data-key="label">Label</th>'
            '<th data-key="frequency" class="num-col">Freq</th>'
            '<th data-key="file_count" class="num-col">Files</th>'
            '<th data-key="components" class="num-col">Comp</th>'
            "</tr></thead><tbody></tbody></table>"
            f'<script type="application/json" id="concepts-data">{payload}</script>'
        )
        return Page("concepts.html", "Concepts", body)

    def build_concept_detail(self, name: str, concept: dict[str, Any]) -> Page:
        depth = 1
        rel = f"concepts/{self.concept_slug[name]}.html"
        b = self.b

        meta = kv_grid(
            [
                ("Name", f"<code>{esc(name)}</code>"),
                ("Label", esc(concept.get("label")) or "—"),
                ("Frequency", f'<span class="num">{esc(concept.get("frequency"))}</span>'),
                ("File count", f'<span class="num">{esc(concept.get("file_count"))}</span>'),
                (
                    "Alt labels",
                    " ".join(
                        f'<span class="chip">{esc(a)}</span>'
                        for a in (concept.get("alt_labels") or [])
                    )
                    or "—",
                ),
            ]
        )
        parts = [
            f"<h1>{esc(name)}</h1>",
            section(
                "Concept",
                TIER_INFERRED,
                meta,
                note="A concept is an inferred grouping, not a ground-truth entity.",
            ),
        ]

        # LLM description (advisory).
        desc = b.enrichment_concept_description.get(name)
        if desc:
            parts.append(self._enrichment_block("LLM concept description", desc))

        # Co-occurring concepts (inferred).
        cooccur = b.cooccur.get(name, [])[: self.opts.cooccur_k]
        if cooccur:
            rows = [
                (self.concept_link(n, depth), f'<span class="num">{esc(w)}</span>')
                for n, w in cooccur
            ]
            parts.append(
                section(
                    "Co-occurring concepts",
                    TIER_INFERRED,
                    table(["concept", "weight"], rows, cls="sortable"),
                )
            )

        # Files lexicalising the concept (inferred mapping).
        files = [
            p
            for p, names in (b.concepts.get("per_path_concepts") or {}).items()
            if name in names
        ]
        if files:
            items = "".join(
                f"<li>{self.file_link(p, depth)}</li>" for p in sorted(files)[: self.opts.file_k]
            )
            more = (
                f'<li class="muted">… {len(files) - self.opts.file_k} more</li>'
                if len(files) > self.opts.file_k
                else ""
            )
            parts.append(
                section(
                    f"Files ({len(files)})",
                    TIER_INFERRED,
                    f'<ul class="link-list">{items}{more}</ul>',
                )
            )

        # Chunks for the concept (mechanical chunks, inferred linkage).
        chunk_idxs = b.concept_chunks.get(name, [])[: self.opts.chunk_k]
        if chunk_idxs:
            rows = []
            for i in chunk_idxs:
                c = b.chunks[i]
                rows.append(
                    (
                        esc(c.get("symbol")) or "—",
                        esc(c.get("kind")) or "—",
                        self.file_link(c.get("file"), depth, label=c.get("file") or "—"),
                        f'<span class="num">{esc(c.get("beginLine"))}</span>',
                    )
                )
            parts.append(
                section(
                    f"Chunks ({len(b.concept_chunks.get(name, []))})",
                    TIER_INFERRED,
                    table(["symbol", "kind", "file", "line"], rows, cls="sortable"),
                    note="Chunk text is mechanical; the chunk↔concept link is inferred.",
                )
            )

        return Page(rel, name, "".join(parts))

    def build_graph(self) -> Page:
        """File-import graph, rendered client-side from embedded data."""
        b = self.b
        deg: Counter[str] = Counter()
        for a, c in b.imports:
            deg[a] += 1
            deg[c] += 1
        limit = self.opts.graph_nodes
        ranked = sorted(b.files, key=lambda r: deg.get(r["path"], 0), reverse=True)
        selected = ranked[:limit]
        sel_paths = {r["path"] for r in selected}
        nodes = [
            {
                "id": r["path"],
                "label": r["path"].rsplit("/", 1)[-1],
                "group": r.get("language") or r.get("type") or "unknown",
                "weight": deg.get(r["path"], 0),
                "href": f"files/{self.file_slug[r['path']]}.html",
            }
            for r in selected
        ]
        edges = [
            {"source": a, "target": c}
            for a, c in b.imports
            if a in sel_paths and c in sel_paths
        ]
        graph_json = json.dumps({"nodes": nodes, "edges": edges})
        truncated = len(b.files) > len(selected)
        note = (
            f"Showing the {len(selected)} highest-degree files of "
            f"{len(b.files)} (by import degree)." if truncated else
            f"All {len(b.files)} files shown."
        )
        body = (
            "<h1>Import graph</h1>"
            f'<p class="lead">{tier_badge(TIER_MECHANICAL)} {esc(note)} '
            "Drag nodes; click a node to open its file page.</p>"
            '<div id="graph-wrap"><canvas id="graph-canvas"></canvas></div>'
            f'<script type="application/json" id="graph-data">{graph_json}</script>'
        )
        return Page("graph.html", "Import graph", body)


# --------------------------------------------------------------------------- #
# Static assets (CSS + JS), written verbatim.
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Self-hosted fonts (OFL). Copied into the site so the intended typography
# renders fully offline; CSS var fallbacks cover the case where they're absent.
# --------------------------------------------------------------------------- #
_FONTS_SRC = Path(__file__).resolve().parent / "site_assets" / "fonts"
_FONT_FILES = (
    "fraunces-variable.woff2",
    "fraunces-variable-italic.woff2",
    "ibm-plex-mono-400.woff2",
    "ibm-plex-mono-500.woff2",
    "ibm-plex-mono-600.woff2",
    "ibm-plex-mono-400-italic.woff2",
)
FONT_FACE_CSS = """\
@font-face{font-family:"Fraunces";font-style:normal;font-weight:100 900;font-display:swap;
  src:url("fonts/fraunces-variable.woff2") format("woff2-variations")}
@font-face{font-family:"Fraunces";font-style:italic;font-weight:100 900;font-display:swap;
  src:url("fonts/fraunces-variable-italic.woff2") format("woff2-variations")}
@font-face{font-family:"IBM Plex Mono";font-style:normal;font-weight:400;font-display:swap;
  src:url("fonts/ibm-plex-mono-400.woff2") format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-style:normal;font-weight:500;font-display:swap;
  src:url("fonts/ibm-plex-mono-500.woff2") format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-style:normal;font-weight:600;font-display:swap;
  src:url("fonts/ibm-plex-mono-600.woff2") format("woff2")}
@font-face{font-family:"IBM Plex Mono";font-style:italic;font-weight:400;font-display:swap;
  src:url("fonts/ibm-plex-mono-400-italic.woff2") format("woff2")}
"""


def _install_fonts(assets_dir: Path) -> str:
    """Copy bundled fonts into the site; return the @font-face CSS (or '' if
    the fonts are unavailable, in which case the fallback stacks take over)."""
    if not _FONTS_SRC.is_dir():
        return ""
    dest = assets_dir / "fonts"
    dest.mkdir(exist_ok=True)
    copied = 0
    for name in _FONT_FILES:
        src = _FONTS_SRC / name
        if src.exists():
            shutil.copyfile(src, dest / name)
            copied += 1
    license_src = _FONTS_SRC / "OFL.md"
    if license_src.exists():
        shutil.copyfile(license_src, dest / "OFL.md")
    return FONT_FACE_CSS if copied else ""


SITE_CSS = """
:root{
  --font-display:"Fraunces","Iowan Old Style","Hoefler Text",Georgia,"Times New Roman",serif;
  --font-mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  /* FIELD ATLAS — graphite ink, parchment foreground, surveyor's brass */
  --bg:#0d1014;--bg2:#08090c;--surface:#12171d;--surface2:#181f27;
  --line:#262e37;--line-2:#39434e;--fg:#ece5d4;--muted:#969ca2;--faint:#6a7178;
  --accent:#d6a05a;--accent-ink:#1a1407;--accent-2:#74b4a6;
  --mech:#74b4a6;--inf:#d8a64a;--llm:#cc6b4e;--num:#cfc1a0;
  --grid:rgba(236,229,212,.035);--code-bg:#080a0d;
  --shadow:0 1px 0 rgba(0,0,0,.35),0 22px 50px -34px rgba(0,0,0,.85);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;font-family:var(--font-mono);font-size:13.5px;line-height:1.6;font-weight:400;
  background:var(--bg);color:var(--fg);border-top:2px solid var(--accent);
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
/* atmosphere: a faint cartographic graticule + paper grain, fixed behind all */
body::before{content:"";position:fixed;inset:0;z-index:-2;pointer-events:none;
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),
    linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:72px 72px;background-position:center;
  -webkit-mask-image:radial-gradient(ellipse 120% 90% at 50% 0,#000 30%,transparent 92%);
  mask-image:radial-gradient(ellipse 120% 90% at 50% 0,#000 30%,transparent 92%)}
body::after{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;opacity:.04;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline;text-underline-offset:3px;text-decoration-thickness:1px}
code{font-family:var(--font-mono);font-size:.92em}
::selection{background:rgba(214,160,90,.28);color:#fff}
::-webkit-scrollbar{width:11px;height:11px}
::-webkit-scrollbar-thumb{background:var(--line-2);border:3px solid var(--bg);border-radius:6px}
::-webkit-scrollbar-track{background:transparent}

/* ---- header: instrument bar ---- */
.site-header{display:flex;align-items:center;gap:1rem;padding:.6rem 1.4rem;
  background:rgba(13,16,20,.86);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
  border-bottom:1px solid var(--line);position:sticky;top:0;z-index:20;flex-wrap:wrap}
.brand{display:flex;align-items:baseline;gap:.55rem}
.brand a{font-family:var(--font-display);font-weight:600;font-size:1.18rem;letter-spacing:.005em;
  color:var(--fg)}
.brand a:hover{text-decoration:none;color:var(--accent)}
.brand .subtitle{color:var(--faint);font-size:.62rem;text-transform:uppercase;letter-spacing:.22em}
.site-nav{display:flex;gap:.2rem}
.site-nav a{margin:0;padding:.28rem .6rem;color:var(--muted);font-size:.7rem;text-transform:uppercase;
  letter-spacing:.13em;border:1px solid transparent;border-radius:2px;transition:color .15s,border-color .15s}
.site-nav a:hover{color:var(--fg);text-decoration:none;border-color:var(--line-2)}
/* ---- provenance legend: a map key ---- */
.provenance-legend{padding:.42rem 1.4rem;background:var(--surface);border-bottom:1px solid var(--line);
  color:var(--muted);font-size:.68rem;text-transform:uppercase;letter-spacing:.1em;
  display:flex;flex-wrap:wrap;gap:.2rem .4rem;align-items:center}
.content{max-width:1120px;margin:2rem auto 0;padding:0 1.4rem}
h1{font-family:var(--font-display);font-weight:600;font-size:clamp(1.7rem,1rem+2vw,2.3rem);
  line-height:1.06;letter-spacing:-.01em;margin:.2rem 0 .8rem}
.lead{color:var(--muted);margin-top:0;max-width:66ch}
/* ---- plates (cards) ---- */
.card{background:var(--surface);border:1px solid var(--line);border-radius:3px;
  padding:1.1rem 1.25rem;margin:1.15rem 0;box-shadow:var(--shadow);position:relative}
.card h2{font-family:var(--font-display);font-weight:600;font-size:1.12rem;letter-spacing:0;
  margin:.05rem 0 .75rem;display:flex;align-items:center;gap:.6rem;
  padding-bottom:.55rem;border-bottom:1px solid var(--line)}
.card h2::before{content:"";width:9px;height:9px;background:var(--accent);
  transform:rotate(45deg);flex:0 0 auto;box-shadow:0 0 0 3px rgba(214,160,90,.12)}
.card h2 .badge{margin-left:auto}
.card h3{font-family:var(--font-mono);font-size:.7rem;text-transform:uppercase;letter-spacing:.13em;
  color:var(--muted);margin:1.1rem 0 .45rem}
.section-note{color:var(--muted);font-size:.82rem;margin:-.2rem 0 .8rem;font-style:italic;
  font-family:var(--font-display)}
/* ---- provenance badges: geological legend swatches ---- */
.badge{display:inline-flex;align-items:center;gap:.34em;font-size:.62rem;font-weight:500;
  text-transform:uppercase;letter-spacing:.11em;padding:.12rem .42rem;border-radius:2px;
  border:1px solid color-mix(in srgb,currentColor 40%,transparent);
  background:color-mix(in srgb,currentColor 10%,transparent)}
.badge::before{content:"";width:7px;height:7px;background:currentColor;border-radius:1px}
.badge-mechanical{color:var(--mech)}.badge-inferred{color:var(--inf)}.badge-llm{color:var(--llm)}
/* ---- ledger tables ---- */
table{border-collapse:collapse;width:100%;font-size:.84rem;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:.42rem .6rem;border-bottom:1px solid var(--line);vertical-align:top}
thead th{position:relative;color:var(--muted);font-weight:500;font-size:.66rem;text-transform:uppercase;
  letter-spacing:.1em;cursor:pointer;user-select:none;white-space:nowrap;border-bottom:1px solid var(--line-2)}
thead th:hover{color:var(--accent)}
tbody tr{transition:background .12s}
tbody tr:hover{background:rgba(214,160,90,.05)}
.num,.num-col{text-align:right;font-variant-numeric:tabular-nums;color:var(--num)}
td.num-col,th.num-col{text-align:right}
.kv-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.7rem 1.1rem}
.kv{border-left:2px solid var(--line-2);padding-left:.7rem}
.kv dt{color:var(--faint);font-size:.62rem;text-transform:uppercase;letter-spacing:.13em}
.kv dd{margin:.18rem 0 0;font-size:.92rem}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:1.4rem}
@media(max-width:720px){.two-col{grid-template-columns:1fr}}
.link-list{list-style:none;margin:0;padding:0;max-height:360px;overflow:auto}
.link-list li{padding:.22rem 0;border-bottom:1px solid var(--line);font-size:.84rem}
.link-list li:last-child{border-bottom:none}
.muted{color:var(--muted)}.ok{color:var(--mech)}.warn{color:var(--inf)}
.chips{display:flex;flex-wrap:wrap;gap:.3rem}
.chip{display:inline-flex;align-items:center;background:var(--surface2);border:1px solid var(--line);
  border-radius:2px;padding:.16rem .5rem;font-size:.72rem;color:var(--muted)}
.chip code{color:var(--accent-2)}
.filter{width:100%;padding:.6rem .8rem;margin:.5rem 0 1rem;background:var(--surface);
  border:1px solid var(--line);border-radius:2px;color:var(--fg);font-family:var(--font-mono);font-size:.9rem}
.filter:focus{outline:none;border-color:var(--accent)}
.sha{word-break:break-all;color:var(--faint);font-size:.85em}
/* ---- LLM enrichment: stamped, iron-oxide, "unverified" ---- */
.llm-card{border-color:color-mix(in srgb,var(--llm) 45%,var(--line));
  background:linear-gradient(180deg,rgba(204,107,78,.05),transparent 60%)}
.llm-card h2::before{background:var(--llm);box-shadow:0 0 0 3px rgba(204,107,78,.14)}
.pals-banner,.pals{color:var(--llm);font-size:.66rem;font-weight:500;text-transform:uppercase;
  letter-spacing:.08em;line-height:1.5}
.pals-banner{border:1px dashed color-mix(in srgb,var(--llm) 50%,transparent);
  padding:.45rem .6rem;border-radius:2px;margin:.2rem 0 .7rem}
.llm-text{border-left:2px solid var(--llm);margin:.6rem 0;padding:.2rem 0 .2rem .9rem;
  color:#f0dcd1;white-space:pre-wrap;font-family:var(--font-display);font-size:1rem;line-height:1.6}
.llm-meta{color:var(--faint);font-size:.7rem;text-transform:uppercase;letter-spacing:.08em}
/* ---- footer marginalia ---- */
.site-footer{max-width:1120px;margin:3rem auto 3rem;padding:1.1rem 1.4rem;border-top:1px solid var(--line);
  color:var(--faint);font-size:.7rem;display:flex;flex-direction:column;gap:.4rem;
  text-transform:uppercase;letter-spacing:.07em}
.site-footer .pals{border:1px dashed color-mix(in srgb,var(--llm) 45%,transparent);
  padding:.5rem .7rem;border-radius:2px;color:var(--llm);text-transform:none;letter-spacing:.02em}
/* ---- graph plate ---- */
#graph-wrap{background:var(--bg2);border:1px solid var(--line);border-radius:3px;overflow:hidden;
  box-shadow:var(--shadow);position:relative}
#graph-wrap::before{content:"FIG · MODULE IMPORT GRAPH";position:absolute;top:.6rem;left:.8rem;z-index:2;
  font-size:.6rem;letter-spacing:.16em;color:var(--faint);text-transform:uppercase;pointer-events:none}
#graph-canvas{width:100%;height:640px;display:block;cursor:grab}

/* ---- hero: survey plate ---- */
.hero{position:relative;border:1px solid var(--line-2);border-radius:3px;padding:3rem 2.4rem 2.4rem;
  margin:.5rem 0 1.6rem;background-color:var(--surface);
  background-image:
    radial-gradient(60% 80% at 86% -10%,rgba(214,160,90,.13),transparent 60%),
    radial-gradient(52% 70% at 0% 112%,rgba(116,180,166,.09),transparent 55%);
  overflow:hidden;box-shadow:var(--shadow)}
/* registration marks: top-left + bottom-right corner ticks (robust, no fills) */
.hero::before,.hero::after{content:"";position:absolute;width:16px;height:16px;
  pointer-events:none;opacity:.7}
.hero::before{top:.8rem;left:.8rem;border-top:1px solid var(--accent);border-left:1px solid var(--accent)}
.hero::after{bottom:.8rem;right:.8rem;border-bottom:1px solid var(--accent);border-right:1px solid var(--accent)}
.hero-marginalia{font-size:.64rem;text-transform:uppercase;letter-spacing:.28em;color:var(--accent);
  margin-bottom:1rem}
.hero h1{font-size:clamp(2.1rem,1.2rem+3.4vw,3.5rem);line-height:1.02;margin:0 0 .7rem;
  background:linear-gradient(180deg,var(--fg),#c8ad84);-webkit-background-clip:text;
  background-clip:text;color:transparent}
@supports not ((-webkit-background-clip:text) or (background-clip:text)){
  .hero h1{background:none;color:var(--fg)}}
.hero-tagline{font-family:var(--font-display);font-style:italic;font-size:clamp(1rem,.8rem+.6vw,1.22rem);
  color:var(--muted);max-width:60ch;margin:.2rem 0 1.3rem;line-height:1.55}
.hero .chips{margin:0 0 1.5rem;gap:0;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.hero .chip{background:none;border:none;border-right:1px solid var(--line);border-radius:0;
  padding:.5rem .9rem .5rem 0;margin-right:.9rem;color:var(--faint);font-size:.68rem;
  text-transform:uppercase;letter-spacing:.08em}
.hero .chip:last-child{border-right:none}
.hero-actions{display:flex;flex-wrap:wrap;gap:.7rem}
.btn{display:inline-block;padding:.6rem 1.1rem;border-radius:2px;font-family:var(--font-mono);
  font-weight:500;font-size:.74rem;text-transform:uppercase;letter-spacing:.1em;
  background:var(--accent);color:var(--accent-ink);border:1px solid var(--accent);transition:all .15s}
.btn:hover{text-decoration:none;background:#e7b06a;border-color:#e7b06a;transform:translateY(-1px)}
.btn-ghost{background:transparent;color:var(--accent)}
.btn-ghost:hover{background:rgba(214,160,90,.1);color:var(--accent);transform:translateY(-1px)}
/* ---- metric readouts ---- */
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:.7rem}
.metric{background:var(--surface2);border:1px solid var(--line);border-top:2px solid var(--accent-2);
  border-radius:2px;padding:.85rem .95rem;display:flex;flex-direction:column;gap:.18rem;transition:border-color .15s}
.metric:hover{border-top-color:var(--accent)}
.metric-num{font-family:var(--font-mono);font-size:1.75rem;font-weight:600;color:var(--fg);
  font-variant-numeric:tabular-nums;line-height:1}
.metric-label{font-size:.66rem;text-transform:uppercase;letter-spacing:.1em;color:var(--accent-2)}
.metric-sub{color:var(--faint);font-size:.7rem}
/* ---- doc / link cards ---- */
.card-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.8rem;margin:.7rem 0}
.doc-card{display:block;background:var(--surface2);border:1px solid var(--line);border-radius:2px;
  padding:1rem 1.1rem;color:var(--fg);position:relative;transition:border-color .15s,transform .15s}
.doc-card::after{content:"→";position:absolute;top:.9rem;right:1rem;color:var(--faint);
  transition:transform .15s,color .15s}
.doc-card:hover{text-decoration:none;border-color:var(--accent);transform:translateY(-2px)}
.doc-card:hover::after{transform:translateX(3px);color:var(--accent)}
.doc-card h3{margin:0 0 .35rem;font-family:var(--font-display);font-size:1.05rem;font-weight:600;
  color:var(--fg)}
.doc-card p{margin:0;color:var(--muted);font-size:.78rem;word-break:break-word}
/* ---- docs layout (map index + plate) ---- */
.docs-layout{display:grid;grid-template-columns:228px 1fr;gap:2rem;align-items:start}
@media(max-width:820px){.docs-layout{grid-template-columns:1fr}}
.docs-sidebar{position:sticky;top:70px;max-height:calc(100vh - 90px);overflow:auto;
  border-right:1px solid var(--line);padding-right:1rem;font-size:.82rem}
.docs-sidebar h4{color:var(--accent);text-transform:uppercase;font-size:.62rem;letter-spacing:.16em;
  margin:1.3rem 0 .4rem;padding-bottom:.3rem;border-bottom:1px solid var(--line)}
.docs-sidebar ul{list-style:none;margin:0;padding:0}
.docs-sidebar li{padding:.16rem 0}
.docs-sidebar a{color:var(--muted)}.docs-sidebar a:hover{color:var(--fg);text-decoration:none}
.doc-source{font-size:.66rem;text-transform:uppercase;letter-spacing:.1em;color:var(--faint);margin:0 0 1.2rem}
/* ---- rendered markdown prose: editorial ---- */
.prose{max-width:70ch;line-height:1.72;font-size:.96rem}
.prose>*:first-child{margin-top:0}
.prose h1{font-family:var(--font-display);font-weight:600;font-size:2rem;line-height:1.1;
  margin:.2rem 0 1.1rem;padding-bottom:.4rem;border-bottom:1px solid var(--line-2)}
.prose h2{font-family:var(--font-display);font-weight:600;font-size:1.45rem;margin:2.2rem 0 .8rem;
  padding-bottom:.3rem;border-bottom:1px solid var(--line)}
.prose h3{font-family:var(--font-display);font-weight:600;font-size:1.18rem;margin:1.6rem 0 .5rem;color:var(--fg)}
.prose h4{font-family:var(--font-mono);font-size:.74rem;text-transform:uppercase;letter-spacing:.12em;
  color:var(--accent);margin:1.4rem 0 .4rem}
.prose p,.prose li{color:#ddd6c6}
.prose strong{color:var(--fg);font-weight:600}
.prose a{color:var(--accent);text-decoration:underline;text-decoration-color:color-mix(in srgb,var(--accent) 40%,transparent);text-underline-offset:3px}
.prose a:hover{text-decoration-color:var(--accent)}
.prose a[data-unresolved]{color:var(--faint);text-decoration:underline dotted;cursor:help}
.prose code{background:var(--code-bg);border:1px solid var(--line);border-radius:2px;padding:.06rem .35rem;
  color:var(--accent-2);font-size:.86em}
.prose pre{background:var(--code-bg);border:1px solid var(--line);border-radius:3px;padding:.9rem 1.1rem;
  overflow:auto;line-height:1.5;font-size:.84rem}
.prose pre code{background:none;border:none;padding:0;color:inherit}
.prose blockquote{border-left:2px solid var(--accent);margin:1rem 0;padding:.4rem 0 .4rem 1.1rem;
  color:var(--muted);font-family:var(--font-display);font-style:italic;background:rgba(214,160,90,.04)}
.prose ul,.prose ol{padding-left:1.3rem}
.prose li{margin:.25rem 0}
.prose table{margin:1rem 0;font-family:var(--font-mono)}
.prose img{max-width:100%}
.prose hr{border:none;border-top:1px solid var(--line);margin:2rem 0}

/* ---- light theme: aged map paper (code stays dark) ---- */
:root[data-theme="light"]{
  --bg:#efe7d4;--bg2:#0a0c0f;--surface:#f7f1e2;--surface2:#ece2cd;
  --line:#d8cdb2;--line-2:#c3b693;--fg:#1f231b;--muted:#6c6757;--faint:#8b8470;
  --accent:#a8632a;--accent-ink:#fdf6e7;--accent-2:#2f7d6e;
  --mech:#2f7d6e;--inf:#9a6f1f;--llm:#a8442b;--num:#534b36;
  --grid:rgba(40,36,24,.05);
}
:root[data-theme="light"] body{-webkit-font-smoothing:auto}
:root[data-theme="light"] .hero h1{background:linear-gradient(180deg,#2a2c20,#7a4d22);
  -webkit-background-clip:text;background-clip:text;color:transparent}
:root[data-theme="light"] .metric-num{color:var(--fg)}
:root[data-theme="light"] .llm-text{color:#3a2a20}
:root[data-theme="light"] ::selection{background:rgba(168,99,42,.25);color:#1f231b}
/* ---- header controls ---- */
.header-actions{display:flex;align-items:center;gap:.5rem;margin-left:auto}
.search-form input{width:210px;max-width:38vw;padding:.42rem .65rem;background:var(--surface2);
  border:1px solid var(--line);border-radius:2px;color:var(--fg);font-family:var(--font-mono);font-size:.82rem}
.search-form input::placeholder{color:var(--faint)}
.search-form input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(214,160,90,.12)}
.theme-toggle,.nav-toggle{background:var(--surface2);border:1px solid var(--line);color:var(--fg);
  border-radius:2px;padding:.34rem .56rem;cursor:pointer;font-size:.95rem;line-height:1}
.theme-toggle:hover,.nav-toggle:hover{border-color:var(--accent);color:var(--accent)}
.nav-toggle{display:none}
@media(max-width:880px){
  .nav-toggle{display:inline-block;order:-1}
  .site-nav{display:none;flex-basis:100%;flex-direction:column;gap:.1rem;margin-top:.4rem}
  .site-nav.open{display:flex}
  .site-nav a{margin-left:0;padding:.45rem .5rem}
  .header-actions{margin-left:0}
  .search-form input{width:150px}
}
/* ---- heading anchor links ---- */
.prose h2,.prose h3,.prose h4{position:relative}
.prose .anchor{position:absolute;left:-1.15em;color:var(--accent);opacity:0;
  text-decoration:none;font-weight:400}
.prose h2:hover .anchor,.prose h3:hover .anchor,.prose h4:hover .anchor{opacity:.7}
.prose .anchor:hover{opacity:1;text-decoration:none}
/* ---- highlighted source (Pygments) ---- */
.source{background:var(--code-bg);border:1px solid var(--line);border-radius:3px;overflow:auto;
  max-height:660px;font-size:.82rem;box-shadow:var(--shadow)}
.source .hl{background:none;margin:0}
.source pre{margin:0;padding:.7rem 1rem;background:none;border:none}
.hl{background:var(--code-bg);border-radius:3px}
.hl pre{margin:0;font-family:var(--font-mono)}
.hl .linenos{display:inline-block;width:2.6em;margin-right:1em;text-align:right;
  color:#566;border-right:1px solid #232a32;padding-right:.5em;user-select:none;background:none}
.source .ln{display:inline-block;width:3.2em;padding-right:1em;margin-right:.6em;text-align:right;
  color:#566;border-right:1px solid var(--line);user-select:none}
.prose pre.plain,.source.plain{color:#cfd6e4}
.prose .hl,.prose div.hl{margin:1rem 0}
/* ---- copy-code button ---- */
.code-wrap{position:relative}
.copy-btn{position:absolute;top:.45rem;right:.45rem;background:var(--surface2);border:1px solid var(--line);
  color:var(--muted);border-radius:2px;padding:.18rem .55rem;font-family:var(--font-mono);font-size:.64rem;
  text-transform:uppercase;letter-spacing:.08em;cursor:pointer;opacity:0;transition:opacity .12s,color .12s}
.code-wrap:hover .copy-btn{opacity:1}
.copy-btn:hover{color:var(--accent);border-color:var(--accent)}
.copy-btn.copied{color:var(--mech);border-color:var(--mech)}
/* ---- search results ---- */
#search-stats{text-transform:uppercase;letter-spacing:.1em;font-size:.7rem}
.search-results{margin-top:1rem;display:flex;flex-direction:column;gap:.5rem}
.search-hit{display:block;background:var(--surface);border:1px solid var(--line);border-left:2px solid var(--line-2);
  border-radius:2px;padding:.6rem .9rem;color:var(--fg);transition:border-color .12s}
.search-hit:hover{text-decoration:none;border-color:var(--accent);border-left-color:var(--accent)}
.search-hit .hit-kind{display:inline-block;font-size:.6rem;text-transform:uppercase;letter-spacing:.1em;
  color:var(--accent-2);border:1px solid color-mix(in srgb,var(--accent-2) 45%,transparent);
  border-radius:2px;padding:.04rem .4rem;margin-right:.6rem}
.search-hit .hit-title{font-weight:500}
.search-hit .hit-hint{color:var(--muted);font-size:.78rem;margin-top:.2rem}
.search-hit mark{background:rgba(214,160,90,.3);color:inherit;border-radius:1px}
/* ---- entrance choreography ---- */
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.content>.card,.content>.hero,.content>.docs-layout{animation:rise .55s cubic-bezier(.2,.7,.2,1) both}
.content>*:nth-child(1){animation-delay:.02s}
.content>*:nth-child(2){animation-delay:.08s}
.content>*:nth-child(3){animation-delay:.14s}
.content>*:nth-child(4){animation-delay:.2s}
.content>*:nth-child(5){animation-delay:.26s}
.content>*:nth-child(6){animation-delay:.32s}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

SITE_JS = r"""
"use strict";
// Client-side table filtering + sorting and a tiny force-directed graph.
// All inputs are produced by the static-site generator; no network access.
(function () {
  function renderDataTable(tableId, dataId) {
    var table = document.getElementById(tableId);
    var dataEl = document.getElementById(dataId);
    if (!table || !dataEl) return;
    var rows = JSON.parse(dataEl.textContent || "[]");
    var tbody = table.querySelector("tbody");
    var headers = [].slice.call(table.querySelectorAll("th"));
    var sortKey = headers.length ? headers[0].getAttribute("data-key") : null;
    var sortDir = 1;
    var filterEl = document.getElementById("filter");

    function draw() {
      var q = (filterEl && filterEl.value || "").toLowerCase();
      var view = rows.filter(function (r) {
        if (!q) return true;
        return Object.keys(r).some(function (k) {
          return k !== "href" && String(r[k]).toLowerCase().indexOf(q) !== -1;
        });
      });
      if (sortKey) {
        view.sort(function (a, b) {
          var x = a[sortKey], y = b[sortKey];
          if (typeof x === "number" && typeof y === "number") return (x - y) * sortDir;
          return String(x).localeCompare(String(y)) * sortDir;
        });
      }
      var html = view.map(function (r) {
        return "<tr>" + headers.map(function (h) {
          var k = h.getAttribute("data-key");
          var v = r[k] == null ? "" : r[k];
          var cls = h.classList.contains("num-col") ? " class='num-col'" : "";
          if (h === headers[0] && r.href) {
            return "<td" + cls + "><a href='" + r.href + "'>" + escapeHtml(v) + "</a></td>";
          }
          return "<td" + cls + ">" + escapeHtml(v) + "</td>";
        }).join("") + "</tr>";
      }).join("");
      tbody.innerHTML = html;
    }
    headers.forEach(function (h) {
      h.addEventListener("click", function () {
        var k = h.getAttribute("data-key");
        if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = 1; }
        draw();
      });
    });
    if (filterEl) filterEl.addEventListener("input", draw);
    draw();
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var ATLAS_PALETTE = ["#d6a05a", "#74b4a6", "#cc6b4e", "#c9b27e", "#8aa6b8",
                       "#b58a6a", "#7f9c7a", "#cdbf9f", "#bf7f8a", "#9a8fb0"];
  function colorFor(group) {
    var h = 0; group = String(group);
    for (var i = 0; i < group.length; i++) h = (h * 31 + group.charCodeAt(i)) >>> 0;
    return ATLAS_PALETTE[h % ATLAS_PALETTE.length];
  }

  function renderGraph() {
    var dataEl = document.getElementById("graph-data");
    var canvas = document.getElementById("graph-canvas");
    if (!dataEl || !canvas) return;
    var g = JSON.parse(dataEl.textContent || "{}");
    var nodes = g.nodes || [], edges = g.edges || [];
    var byId = {};
    nodes.forEach(function (n, i) {
      n.x = Math.cos(i) * 200 + (i % 17) * 9;
      n.y = Math.sin(i) * 200 + (i % 13) * 9;
      n.vx = 0; n.vy = 0; byId[n.id] = n;
    });
    var links = edges.map(function (e) { return { s: byId[e.source], t: byId[e.target] }; })
                     .filter(function (l) { return l.s && l.t; });
    var ctx = canvas.getContext("2d");
    var W, H, dpr = window.devicePixelRatio || 1;
    function resize() {
      W = canvas.clientWidth; H = canvas.clientHeight;
      canvas.width = W * dpr; canvas.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize(); window.addEventListener("resize", resize);

    var cx = function () { return W / 2; }, cy = function () { return H / 2; };
    var maxW = Math.max.apply(null, nodes.map(function (n) { return n.weight || 1; }).concat([1]));
    function radius(n) { return 3 + 7 * Math.sqrt((n.weight || 1) / maxW); }

    var iter = 0;
    function step() {
      var k = 0.0006, rep = 1400;
      for (var i = 0; i < nodes.length; i++) {
        var a = nodes[i];
        a.vx += (cx() - a.x) * k; a.vy += (cy() - a.y) * k;
        for (var j = i + 1; j < nodes.length; j++) {
          var b = nodes[j], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy + 0.01;
          var f = rep / d2, dist = Math.sqrt(d2);
          var fx = (dx / dist) * f, fy = (dy / dist) * f;
          a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
        }
      }
      links.forEach(function (l) {
        var dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, dist = Math.sqrt(dx * dx + dy * dy) || 1;
        var f = (dist - 70) * 0.01;
        var fx = (dx / dist) * f, fy = (dy / dist) * f;
        l.s.vx += fx; l.s.vy += fy; l.t.vx -= fx; l.t.vy -= fy;
      });
      nodes.forEach(function (n) {
        if (n === dragging) return;
        n.x += (n.vx *= 0.85); n.y += (n.vy *= 0.85);
      });
      draw(); if (iter++ < 400) requestAnimationFrame(step);
    }
    function draw() {
      ctx.clearRect(0, 0, W, H);
      ctx.strokeStyle = "rgba(176,168,148,0.14)"; ctx.lineWidth = 1;
      links.forEach(function (l) {
        ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.lineTo(l.t.x, l.t.y); ctx.stroke();
      });
      nodes.forEach(function (n) {
        var c = colorFor(n.group), r = radius(n);
        ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 6.2832);
        ctx.fillStyle = c;
        ctx.shadowColor = c; ctx.shadowBlur = n === hover ? 14 : 6;
        ctx.fill(); ctx.shadowBlur = 0;
        ctx.lineWidth = 1; ctx.strokeStyle = "rgba(8,9,12,0.65)"; ctx.stroke();
      });
      if (hover) {
        ctx.fillStyle = "#ece5d4";
        ctx.font = "12px 'IBM Plex Mono', ui-monospace, monospace";
        ctx.fillText(hover.label + "  (deg " + (hover.weight || 0) + ")", hover.x + 8, hover.y - 8);
      }
    }
    var dragging = null, hover = null, downPos = null;
    function at(ev) {
      var r = canvas.getBoundingClientRect(), mx = ev.clientX - r.left, my = ev.clientY - r.top;
      var best = null, bd = 1e9;
      nodes.forEach(function (n) {
        var dx = n.x - mx, dy = n.y - my, d = dx * dx + dy * dy;
        if (d < bd && d < 400) { bd = d; best = n; }
      });
      return { node: best, mx: mx, my: my };
    }
    canvas.addEventListener("mousedown", function (ev) {
      var h = at(ev); dragging = h.node; downPos = { x: ev.clientX, y: ev.clientY };
      if (dragging) canvas.style.cursor = "grabbing";
    });
    canvas.addEventListener("mousemove", function (ev) {
      var h = at(ev); hover = h.node;
      if (dragging) { dragging.x = h.mx; dragging.y = h.my; dragging.vx = dragging.vy = 0; }
      canvas.style.cursor = hover ? "pointer" : "grab"; draw();
    });
    function release(ev) {
      if (dragging && downPos) {
        var moved = Math.abs(ev.clientX - downPos.x) + Math.abs(ev.clientY - downPos.y);
        if (moved < 4 && dragging.href) window.location.href = dragging.href;
      }
      dragging = null; downPos = null; canvas.style.cursor = "grab";
    }
    canvas.addEventListener("mouseup", release);
    step();
  }

  function highlightActiveDoc() {
    var here = location.pathname.split("/").pop();
    var links = document.querySelectorAll(".docs-sidebar a");
    for (var i = 0; i < links.length; i++) {
      var href = links[i].getAttribute("href");
      if (href === here) {
        links[i].style.color = "var(--accent)";
        links[i].style.fontWeight = "700";
      }
    }
  }

  function initThemeToggle() {
    var btn = document.querySelector(".theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme");
      var next = cur === "light" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("cbm-theme", next); } catch (e) {}
    });
  }

  function initNavToggle() {
    var btn = document.querySelector(".nav-toggle");
    var nav = document.querySelector(".site-nav");
    if (!btn || !nav) return;
    btn.addEventListener("click", function () { nav.classList.toggle("open"); });
  }

  function initCopyButtons() {
    var blocks = document.querySelectorAll(".prose pre, .source");
    blocks.forEach(function (block) {
      if (block.closest(".code-wrap")) return;
      var wrap = document.createElement("div");
      wrap.className = "code-wrap";
      block.parentNode.insertBefore(wrap, block);
      wrap.appendChild(block);
      var btn = document.createElement("button");
      btn.className = "copy-btn"; btn.type = "button"; btn.textContent = "Copy";
      wrap.appendChild(btn);
      btn.addEventListener("click", function () {
        var clone = block.cloneNode(true);
        clone.querySelectorAll(".linenos, .lineno, .ln").forEach(function (n) { n.remove(); });
        var text = clone.textContent;
        var done = function () {
          btn.textContent = "Copied"; btn.classList.add("copied");
          setTimeout(function () { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1400);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, done);
        } else {
          var ta = document.createElement("textarea");
          ta.value = text; document.body.appendChild(ta); ta.select();
          try { document.execCommand("copy"); } catch (e) {}
          document.body.removeChild(ta); done();
        }
      });
    });
  }

  function initSearch() {
    var dataEl = document.getElementById("search-index");
    var box = document.getElementById("search-box");
    var out = document.getElementById("search-results");
    var stats = document.getElementById("search-stats");
    if (!dataEl || !box || !out) return;
    var index = JSON.parse(dataEl.textContent || "[]");
    var root = document.body.getAttribute("data-root") || "";
    var KIND_RANK = { page: 0, doc: 1, package: 2, file: 3, concept: 4 };

    function run(q) {
      q = (q || "").trim().toLowerCase();
      if (!q) { out.innerHTML = ""; stats.textContent = index.length + " entries indexed."; return; }
      var hits = [];
      for (var i = 0; i < index.length; i++) {
        var e = index[i];
        var t = e.t.toLowerCase(), h = (e.h || "").toLowerCase();
        var ti = t.indexOf(q), hi = h.indexOf(q);
        if (ti === -1 && hi === -1) continue;
        var score = ti === 0 ? 0 : ti > 0 ? 1 : 2;
        score = score * 10 + (KIND_RANK[e.k] || 9);
        hits.push({ e: e, score: score, ti: ti });
      }
      hits.sort(function (a, b) { return a.score - b.score || a.e.t.length - b.e.t.length; });
      var shown = hits.slice(0, 100);
      stats.textContent = hits.length + " result" + (hits.length === 1 ? "" : "s")
        + (hits.length > shown.length ? " (showing 100)" : "");
      out.innerHTML = shown.map(function (hit) {
        var e = hit.e, title = escapeHtml(e.t);
        if (hit.ti >= 0) {
          var raw = e.t;
          title = escapeHtml(raw.slice(0, hit.ti)) + "<mark>"
            + escapeHtml(raw.slice(hit.ti, hit.ti + q.length)) + "</mark>"
            + escapeHtml(raw.slice(hit.ti + q.length));
        }
        return '<a class="search-hit" href="' + root + e.u + '">'
          + '<span class="hit-kind">' + escapeHtml(e.k) + '</span>'
          + '<span class="hit-title">' + title + '</span>'
          + (e.h ? '<div class="hit-hint">' + escapeHtml(e.h) + '</div>' : '')
          + '</a>';
      }).join("");
    }

    var params = new URLSearchParams(location.search);
    var initial = params.get("q") || "";
    if (initial) box.value = initial;
    run(initial);
    var timer = null;
    box.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(function () { run(box.value); }, 80);
    });
  }

  renderDataTable("files-table", "files-data");
  renderDataTable("concepts-table", "concepts-data");
  renderGraph();
  highlightActiveDoc();
  initThemeToggle();
  initNavToggle();
  initCopyButtons();
  initSearch();
})();
"""


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
@dataclass
class Options:
    bundle_dir: Path
    output_dir: Path
    inline_source: bool = True
    max_source_bytes: int = 256 * 1024
    graph_nodes: int = 200
    cooccur_k: int = 30
    chunk_k: int = 50
    file_k: int = 100
    max_concept_pages: int | None = None


def write_page(out_root: Path, page: Page, repo_name: str, generated_at: str) -> None:
    dest = out_root / page.rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(page_shell(page, repo_name, generated_at), encoding="utf-8")


def generate(opts: Options) -> dict[str, int]:
    bundle = load_bundle(opts.bundle_dir)
    builder = SiteBuilder(bundle, opts)
    out = opts.output_dir
    out.mkdir(parents=True, exist_ok=True)

    # assets (self-hosted fonts + site CSS + the Pygments stylesheet, scoped to .hl)
    (out / "assets").mkdir(exist_ok=True)
    font_face_css = _install_fonts(out / "assets")
    pyg_css = HtmlFormatter(style=_PYG_STYLE).get_style_defs(".hl")
    (out / "assets" / "site.css").write_text(
        font_face_css + SITE_CSS + "\n/* Pygments (" + _PYG_STYLE + ") */\n" + pyg_css + "\n",
        encoding="utf-8",
    )
    (out / "assets" / "site.js").write_text(SITE_JS, encoding="utf-8")

    repo, gen = builder.repo_name, builder.generated_at
    stats = {"files": 0, "concepts": 0, "docs": 0, "pages": 0}

    # Decide which concept detail pages exist *before* rendering anything, so
    # cross-links never dangle when --max-concept-pages truncates the set.
    concepts = bundle.concepts.get("concepts", {})
    ranked = sorted(
        concepts.items(), key=lambda kv: kv[1].get("frequency", 0), reverse=True
    )
    if opts.max_concept_pages is not None:
        ranked = ranked[: opts.max_concept_pages]
    builder.linkable_concepts = {name for name, _ in ranked}

    search_index = builder.build_search_index()
    for page in (
        builder.build_index(),
        builder.build_architecture(),
        builder.build_docs_index(),
        builder.build_files_index(),
        builder.build_concepts_index(),
        builder.build_graph(),
        builder.build_search_page(search_index),
    ):
        write_page(out, page, repo, gen)
        stats["pages"] += 1

    stats["docs"] = 0
    for doc in builder.docs:
        write_page(out, builder.build_doc_page(doc), repo, gen)
        stats["docs"] += 1
        stats["pages"] += 1

    for f in bundle.files:
        write_page(out, builder.build_file_detail(f), repo, gen)
        stats["files"] += 1
        stats["pages"] += 1

    for name, concept in ranked:
        write_page(out, builder.build_concept_detail(name, concept), repo, gen)
        stats["concepts"] += 1
        stats["pages"] += 1

    return stats


def parse_args(argv: list[str] | None = None) -> Options:
    p = argparse.ArgumentParser(
        description="Generate a self-contained static HTML site from a "
        "codebase-mapper bundle.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/generate_static_site.py -b _tmp/code-base-mapper -o _site\n"
            "  python scripts/generate_static_site.py -b _tmp/code-base-mapper -o _site "
            "--no-inline-source\n"
            "  # then open _site/index.html in a browser (works over file://)\n"
        ),
    )
    p.add_argument(
        "-b", "--bundle", required=True, type=Path,
        help="Path to a bundle output directory (containing run_manifest.json).",
    )
    p.add_argument(
        "-o", "--output", required=True, type=Path,
        help="Directory to write the static site into.",
    )
    p.add_argument(
        "--no-inline-source", dest="inline_source", action="store_false",
        help="Do not embed file source content into file pages.",
    )
    p.add_argument(
        "--max-source-bytes", type=int, default=256 * 1024,
        help="Skip embedding source blobs larger than this many bytes.",
    )
    p.add_argument(
        "--graph-nodes", type=int, default=200,
        help="Max nodes in the import graph (highest-degree files kept).",
    )
    p.add_argument(
        "--max-concept-pages", type=int, default=None,
        help="Limit concept detail pages to the N most frequent (default: all).",
    )
    p.add_argument(
        "--clean", action="store_true",
        help="Remove the output directory before generating.",
    )
    ns = p.parse_args(argv)

    bundle_dir = ns.bundle.resolve()
    if not (bundle_dir / "run_manifest.json").exists():
        p.error(f"{bundle_dir} does not look like a bundle (no run_manifest.json).")
    if ns.clean and ns.output.exists():
        shutil.rmtree(ns.output)

    return Options(
        bundle_dir=bundle_dir,
        output_dir=ns.output.resolve(),
        inline_source=ns.inline_source,
        max_source_bytes=ns.max_source_bytes,
        graph_nodes=ns.graph_nodes,
        max_concept_pages=ns.max_concept_pages,
    )


# Extra nav entries added by optional companion artifacts (today: the
# interactive Cartogram map). Reset per main() run; page_shell reads it.
_EXTRA_NAV: list[tuple[str, str]] = []


def _try_build_cartogram(bundle_dir: Path, output_dir: Path) -> None:
    """Build the interactive Cartogram as ``map.html`` when possible.

    The Cartogram is the explorable companion to the site's static pages —
    same bundle, zoomable regions and import/test flows. It needs Node and
    an L3 bundle; every skip is disclosed on the build log rather than
    leaving a silently absent page.
    """
    import shutil as _shutil

    if _shutil.which("node") is None:
        print("[site] cartogram skipped: node not found (interactive map "
              "needs Node >= 20)")
        return
    if not (bundle_dir / "inventory.jsonld").is_file():
        print("[site] cartogram skipped: bundle has no inventory.jsonld")
        return
    import cbm_cartogram

    output_dir.mkdir(parents=True, exist_ok=True)
    rc = cbm_cartogram.main([str(bundle_dir), "-o",
                             str(output_dir / "map.html")])
    if rc == 0:
        _EXTRA_NAV.append(("map.html", "Map"))
    else:
        print("[site] cartogram skipped: build failed (reason above; an L1 "
              "bundle is refused — produce one with scripts/run_l3.py)")


def main(argv: list[str] | None = None) -> int:
    opts = parse_args(argv)
    print(f"Loading bundle: {opts.bundle_dir}")
    _EXTRA_NAV.clear()
    _try_build_cartogram(opts.bundle_dir, opts.output_dir)
    stats = generate(opts)
    index = opts.output_dir / "index.html"
    print(
        f"Wrote {stats['pages']} pages "
        f"({stats['docs']} docs, {stats['files']} files, "
        f"{stats['concepts']} concepts) to {opts.output_dir}"
    )
    print(f"Open: {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
