"""codebase_mapper.inspection.languages.html — first-class HTML support.

A stack-based element parser (no tree-sitter dependency, in the same spirit
as the Dart / SQL regex extractors) that builds the element tree with line +
byte spans. It honours the three things a naive ``<tag>`` regex gets wrong:

  * **void elements** (``<img>``, ``<link>``, ``<meta>`` …) have no close tag;
  * **raw-text elements** (``<script>``, ``<style>``, ``<textarea>``,
    ``<title>``) contain text that must NOT be parsed as markup — a
    ``.foo > .bar`` selector or a ``<div>`` inside a ``<style>`` comment is
    opaque content, not a child element;
  * **comments** (``<!-- … -->``) and the ``<!doctype>`` / ``<?…?>``
    declarations are skipped.

Emitted ``items`` are the landmark/structural elements plus any element that
carries an ``id`` — emitting every ``<span>``/``<div>`` would be noise. Each
item records its tag, id, classes, parent (nearest emitted ancestor's tag),
and spans, so the L2 chunker can produce one chunk per structural element.

Import edges are the cross-file references a page actually depends on:
``<link href>`` (stylesheets), ``<script src>``, ``<a href>`` (in-repo page
links), and media ``src`` (``<img>``/``<iframe>``/…). Absolute URLs,
protocol-relative ``//`` refs, ``data:``/``mailto:``/``tel:`` and bare
fragments are external / skipped.
"""
from __future__ import annotations

import re

from pathlib import PurePosixPath


_VOID = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split())
_RAWTEXT = frozenset("script style textarea title".split())
_STRUCTURAL = frozenset((
    "html head body header nav main section article aside footer form table "
    "figure dialog template svg script style").split())


def _line_byte_starts(content: bytes) -> list[int]:
    starts = [0, 0]
    for i, b in enumerate(content):
        if b == 0x0A:
            starts.append(i + 1)
    return starts


def _line_of(byte_idx: int, line_byte_starts: list[int]) -> int:
    lo, hi = 1, len(line_byte_starts) - 1
    if hi <= 0:
        return 1
    if byte_idx >= line_byte_starts[hi]:
        return hi
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_byte_starts[mid] <= byte_idx:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _collapse(text: str) -> str:
    return " ".join(text.split())


_TOKEN_RE = re.compile(
    r"(?P<comment><!--.*?-->)"
    r"|(?P<decl><![^>]*>|<\?[^>]*>)"
    r"|(?P<close></\s*(?P<ctag>[A-Za-z][\w:-]*)\s*>)"
    r"|(?P<open><\s*(?P<otag>[A-Za-z][\w:-]*)(?P<attrs>(?:\"[^\"]*\"|'[^']*'|[^>])*)>)",
    re.DOTALL,
)


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(
        r"(?<![\w-])" + re.escape(name) + r"\s*=\s*(\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
        attrs, re.IGNORECASE,
    )
    if not m:
        return None
    return m.group(2) or m.group(3) or m.group(4)


def _collect_import(tag: str, attrs: str, lineno: int, out: list[dict]) -> None:
    if tag in ("link", "a"):
        ref = _attr(attrs, "href")
        kind = "link" if tag == "link" else "anchor"
    elif tag in ("script", "img", "iframe", "embed", "audio", "video",
                 "source", "track"):
        ref = _attr(attrs, "src")
        kind = "script" if tag == "script" else ("img" if tag == "img" else tag)
    else:
        return
    if ref:
        out.append({"kind": kind, "source": ref, "lineno": lineno})


def _finalize(el: dict, byte_end: int, lbs: list[int], items: list[dict]) -> None:
    if not (el["tag"] in _STRUCTURAL or el["id"]):
        return
    item = {
        "kind": "element",
        "tag": el["tag"],
        "name": el["id"] or el["tag"],
        "parent": el["parent"],
        "line_start": el["line_start"],
        "line_end": _line_of(max(el["byte_start"], byte_end - 1), lbs),
        "byte_start": el["byte_start"],
        "byte_end": byte_end,
        "signature": el["signature"],
    }
    if el["id"]:
        item["id"] = el["id"]
    if el["classes"]:
        item["classes"] = el["classes"]
    items.append(item)


def extract_html_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    """Return ``(summary, errors)`` for an HTML file."""
    try:
        raw = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]

    lbs = _line_byte_starts(content)
    items: list[dict] = []
    imports: list[dict] = []
    stack: list[dict] = []
    pos, n = 0, len(raw)

    def _parent_tag() -> str | None:
        for anc in reversed(stack):
            if anc["tag"] in _STRUCTURAL or anc["id"]:
                return anc["tag"]
        return None

    while pos < n:
        m = _TOKEN_RE.search(raw, pos)
        if not m:
            break
        if m.group("comment") or m.group("decl"):
            pos = m.end()
            continue

        if m.group("close"):
            ctag = m.group("ctag").lower()
            for i in range(len(stack) - 1, -1, -1):
                if stack[i]["tag"] == ctag:
                    closed = stack[i]
                    # implicitly close any unclosed descendants above it
                    for orphan in stack[i + 1:]:
                        _finalize(orphan, m.start(), lbs, items)
                    del stack[i:]
                    _finalize(closed, m.end(), lbs, items)
                    break
            pos = m.end()
            continue

        # open tag
        otag = m.group("otag").lower()
        attrs = m.group("attrs") or ""
        self_close = attrs.rstrip().endswith("/")
        classes = _attr(attrs, "class")
        el = {
            "tag": otag,
            "byte_start": m.start(),
            "line_start": _line_of(m.start(), lbs),
            "id": _attr(attrs, "id"),
            "classes": classes.split() if classes else None,
            "parent": _parent_tag(),
            "signature": _collapse(raw[m.start():m.end()])[:160],
        }
        _collect_import(otag, attrs, el["line_start"], imports)

        if otag in _VOID or self_close:
            _finalize(el, m.end(), lbs, items)
        elif otag in _RAWTEXT:
            close_re = re.compile(r"</\s*" + re.escape(otag) + r"\s*>", re.IGNORECASE)
            cm = close_re.search(raw, m.end())
            end = cm.end() if cm else n
            _finalize(el, end, lbs, items)
            pos = end
            continue
        else:
            stack.append(el)
        pos = m.end()

    # Close any elements left open at EOF.
    for el in stack:
        _finalize(el, n, lbs, items)

    items.sort(key=lambda x: (x["line_start"], x["byte_start"]))
    imports.sort(key=lambda x: (x["lineno"], x["source"]))

    summary = {
        "language": "html",
        "extraction_method": "regex",
        "imports": imports,
        "items": items,
        "top_level_elements": sorted({it["name"] for it in items}),
    }
    return summary, []


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------


_EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:", "mailto:",
                      "tel:", "javascript:", "blob:", "about:")


def _normalize_rel(parts: tuple[str, ...]) -> str:
    norm: list[str] = []
    for part in parts:
        if part == "..":
            if norm and norm[-1] != "..":
                norm.pop()
        elif part not in ("", "."):
            norm.append(part)
    return "/".join(norm)


def resolve_html_imports(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    src_dir = PurePosixPath(src_path).parent
    in_repo: set[str] = set()
    external: set[str] = set()

    for imp in summary.get("imports", []):
        spec = str(imp.get("source", "")).strip()
        if not spec or spec.startswith("#"):
            continue
        if spec.lower().startswith(_EXTERNAL_PREFIXES):
            external.add(spec)
            continue
        clean = spec.split("#", 1)[0].split("?", 1)[0]
        if not clean:
            continue
        target = _normalize_rel((src_dir / clean).parts)
        if target in paths_set:
            in_repo.add(target)
        else:
            external.add(spec)

    in_repo.discard(src_path)
    return sorted(in_repo), sorted(external)
