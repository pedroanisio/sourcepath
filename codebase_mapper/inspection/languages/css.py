"""codebase_mapper.inspection.languages.css — first-class CSS/SCSS support.

A brace-scanning parser (no tree-sitter dependency, like the Dart / SQL / HTML
extractors) that extracts rules and at-rules with line + byte spans. It also
covers the SCSS dialect: nested rules and ``//`` line comments are handled, so
one analyzer serves both ``.css`` and ``.scss``.

Hazards handled before scanning (``_neutralize``, length-preserving):
  * ``/* … */`` block comments and string literals are blanked so a ``{``,
    ``}`` or ``;`` inside them never affects block matching;
  * SCSS ``//`` line comments are blanked (``.css`` never uses them, and the
    ``(?<!:)`` guard keeps ``http://`` in an unquoted ``url()`` intact).

Emitted ``items`` are rule blocks and at-rule blocks (``@media``,
``@supports``, ``@keyframes``, ``@font-face``, ``@mixin``, ``@function`` …).
Nested rules — SCSS nesting and rules inside ``@media``/``@supports`` — carry a
parent link. Plain declarations (``color: red;``) are not items.

Import edges: ``@import`` (incl. ``url(...)``), and the SCSS module rules
``@use`` / ``@forward``. Relative refs resolve against the file's directory
(with SCSS partial candidates ``_name.scss`` / ``name.scss``); ``sass:*``
built-ins and http(s) URLs are external.
"""
from __future__ import annotations

import re

from pathlib import PurePosixPath


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


def _blank(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def _neutralize(text: str, is_scss: bool) -> str:
    text = re.sub(r'"(?:[^"\\]|\\.)*"', _blank, text)
    text = re.sub(r"'(?:[^'\\]|\\.)*'", _blank, text)
    text = re.sub(r"/\*.*?\*/", _blank, text, flags=re.DOTALL)
    if is_scss:
        text = re.sub(r"(?<!:)//[^\n]*", _blank, text)
    return text


def _match_brace(neu: str, open_idx: int, end: int) -> int:
    depth = 0
    k = open_idx
    while k < end:
        c = neu[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return k
        k += 1
    return end - 1


def _classify_prelude(prelude: str) -> str:
    low = prelude.strip().lower()
    if low.startswith("@media"):
        return "media"
    if "keyframes" in low.split("{")[0] and low.startswith("@"):
        return "keyframes"
    if low.startswith("@font-face"):
        return "font_face"
    if low.startswith("@supports"):
        return "supports"
    if low.startswith("@mixin"):
        return "mixin"
    if low.startswith("@function"):
        return "function"
    if low.startswith("@"):
        return "at_rule"
    return "rule"


def _prelude_name(prelude: str, kind: str) -> str:
    if kind == "keyframes":
        m = re.search(r"@[-\w]*keyframes\s+([-\w]+)", prelude, re.IGNORECASE)
        return m.group(1) if m else _collapse(prelude)
    if kind in ("mixin", "function"):
        m = re.search(r"@\w+\s+([-\w]+)", prelude, re.IGNORECASE)
        return m.group(1) if m else _collapse(prelude)
    return _collapse(prelude)


def _walk(neu: str, raw: str, start: int, end: int, parent: str | None,
          items: list[dict], lbs: list[int]) -> None:
    i = start
    while i < end:
        while i < end and neu[i] in " \t\r\n;":
            i += 1
        if i >= end:
            break
        prelude_start = i
        j = i
        depth = 0
        while j < end:
            c = neu[j]
            if c == "(":
                depth += 1
            elif c == ")":
                if depth > 0:
                    depth -= 1
            elif depth == 0 and c in "{;}":
                break
            j += 1
        if j >= end:
            break
        ch = neu[j]
        if ch == "{":
            block_close = _match_brace(neu, j, end)
            prelude = _collapse(raw[prelude_start:j])
            if prelude:
                kind = _classify_prelude(prelude)
                name = _prelude_name(prelude, kind)
                item = {
                    "kind": kind,
                    "name": name,
                    "parent": parent,
                    "line_start": _line_of(prelude_start, lbs),
                    "line_end": _line_of(block_close, lbs),
                    "byte_start": prelude_start,
                    "byte_end": block_close + 1,
                    "signature": prelude[:160],
                }
                items.append(item)
                if kind in ("rule", "media", "supports"):
                    _walk(neu, raw, j + 1, block_close, name, items, lbs)
            i = block_close + 1
        else:  # ';' (declaration / at-statement) or stray '}'
            i = j + 1


_IMPORT_RE = re.compile(
    r"@(?P<kw>import|use|forward)\b\s+(?:url\(\s*)?[\"']?(?P<path>[^\"')\s;]+)",
    re.IGNORECASE,
)


def _extract_imports(raw: str, lbs: list[int]) -> list[dict]:
    out: list[dict] = []
    for m in _IMPORT_RE.finditer(raw):
        out.append({
            "kind": m.group("kw").lower(),
            "source": m.group("path"),
            "lineno": _line_of(m.start(), lbs),
        })
    out.sort(key=lambda x: (x["lineno"], x["source"]))
    return out


def extract_css_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    """Return ``(summary, errors)`` for a CSS or SCSS file."""
    try:
        raw = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]

    is_scss = path.endswith(".scss") or path.endswith(".sass")
    neu = _neutralize(raw, is_scss)
    lbs = _line_byte_starts(content)
    items: list[dict] = []
    _walk(neu, raw, 0, len(neu), None, items, lbs)
    items.sort(key=lambda x: (x["line_start"], x["byte_start"]))

    summary = {
        "language": "scss" if is_scss else "css",
        "extraction_method": "regex",
        "imports": _extract_imports(raw, lbs),
        "items": items,
        "top_level_rules": sorted({
            it["name"] for it in items if it["kind"] == "rule"}),
    }
    return summary, []


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------


def _normalize_rel(parts: tuple[str, ...]) -> str:
    norm: list[str] = []
    for part in parts:
        if part == "..":
            if norm and norm[-1] != "..":
                norm.pop()
        elif part not in ("", "."):
            norm.append(part)
    return "/".join(norm)


def resolve_css_imports(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    src_dir = PurePosixPath(src_path).parent
    in_repo: set[str] = set()
    external: set[str] = set()

    for imp in summary.get("imports", []):
        spec = str(imp.get("source", "")).strip().strip("\"'")
        if not spec:
            continue
        if "://" in spec or spec.startswith("//") or spec.startswith(("sass:", "data:")):
            external.add(spec)
            continue
        base = _normalize_rel((src_dir / spec).parts)
        candidates = [base]
        p = PurePosixPath(base)
        if p.suffix == "":
            parent_dir = PurePosixPath(base).parent
            for cand in (
                f"{base}.css", f"{base}.scss",
                _normalize_rel((parent_dir / f"_{p.name}.scss").parts),
                _normalize_rel((parent_dir / f"_{p.name}.css").parts),
            ):
                candidates.append(cand)
        hit = next((c for c in candidates if c in paths_set), None)
        if hit:
            in_repo.add(hit)
        else:
            external.add(spec)

    in_repo.discard(src_path)
    return sorted(in_repo), sorted(external)
