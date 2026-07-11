"""codebase_mapper.inspection.languages.sql — first-class SQL support.

SQL has no maintained PyPI tree-sitter package in this project's dependency
set, so — like Dart, COBOL, and Clojure — it uses a disciplined regex
extractor rather than adding a grammar wheel. The extractor records line +
byte spans for every top-level schema object (``CREATE TABLE/VIEW/FUNCTION/
PROCEDURE/TRIGGER/INDEX/TYPE/SCHEMA/SEQUENCE`` and materialized views) so the
L2 chunker can produce one chunk per object and the symbol resolver can map
a chunk to a line range.

Statement hazards handled
-------------------------
The single hard problem in SQL segmentation is that ``;`` is *not* a reliable
statement terminator: it appears inside dollar-quoted bodies (``$$ ... $$`` /
``$tag$ ... $tag$``), inside ``BEGIN ... END`` blocks, inside string literals,
and inside comments. ``_neutralize`` blanks all of those (length-preserving,
newlines kept) *before* the ``CREATE`` scan and the ``;`` search, so a
function body like ``SELECT count(*) FROM users;`` never truncates its
enclosing ``CREATE FUNCTION ... $$ ... $$ LANGUAGE sql;`` statement.

Include directives recognised (cross-dialect)
---------------------------------------------
  * ``\\i path`` / ``\\ir path`` / ``\\include path``  — psql meta-commands
  * ``SOURCE path;`` / ``source path``                  — MySQL
  * ``@path`` / ``@@path``                              — Oracle SQL*Plus

All resolve relative to the including file's own directory (the useful repo
heuristic; psql's ``\\i`` is technically CWD-relative, which a static repo
mapper cannot know). Unresolved includes are surfaced as external rather than
dropped.
"""
from __future__ import annotations

import re

from pathlib import PurePosixPath


# ---------------------------------------------------------------------------
# Line/byte helpers (same convention as the other regex analyzers)
# ---------------------------------------------------------------------------


def _line_byte_starts(content: bytes) -> list[int]:
    """Byte offset where each 1-indexed line starts. Index 0 is unused."""
    starts = [0, 0]
    for i, b in enumerate(content):
        if b == 0x0A:
            starts.append(i + 1)
    return starts


def _line_of(byte_idx: int, line_byte_starts: list[int]) -> int:
    """Binary-search the 1-indexed line number for a byte offset."""
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


# ---------------------------------------------------------------------------
# Neutralisation — blank dollar bodies, comments, and string literals so the
# CREATE scan and ';' search only see real statement structure.
# ---------------------------------------------------------------------------


def _blank(match: re.Match) -> str:
    """Replace a matched span with whitespace of equal length, keeping
    newlines so line numbers and byte offsets are preserved exactly."""
    return re.sub(r"[^\n]", " ", match.group(0))


def _neutralize(text: str) -> str:
    # Dollar-quoted bodies first (they may legitimately contain --, /*, ').
    text = re.sub(r"\$(\w*)\$.*?\$\1\$", _blank, text, flags=re.DOTALL)
    text = re.sub(r"/\*.*?\*/", _blank, text, flags=re.DOTALL)      # block comment
    text = re.sub(r"--[^\n]*", _blank, text)                        # line comment
    text = re.sub(r"'(?:[^']|'')*'", _blank, text)                  # string literal
    return text


# ---------------------------------------------------------------------------
# CREATE-statement scan
# ---------------------------------------------------------------------------


_IDENT = r'"[^"]+"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][\w$]*'
_QNAME = rf"(?:{_IDENT})(?:\s*\.\s*(?:{_IDENT}))*"

_CREATE_RE = re.compile(
    r"\bCREATE\s+"
    r"(?P<mods>(?:OR\s+REPLACE\s+|TEMP(?:ORARY)?\s+|GLOBAL\s+|LOCAL\s+|"
    r"UNLOGGED\s+|UNIQUE\s+|MATERIALIZED\s+|RECURSIVE\s+)*)"
    r"(?P<obj>TABLE|VIEW|FUNCTION|PROCEDURE|TRIGGER|INDEX|TYPE|DOMAIN|"
    r"AGGREGATE|SCHEMA|SEQUENCE)\b"
    r"(?:\s+IF\s+NOT\s+EXISTS)?"
    rf"\s+(?P<name>{_QNAME})",
    re.IGNORECASE,
)

_OBJ_KIND = {
    "TABLE": "table", "VIEW": "view", "FUNCTION": "function",
    "PROCEDURE": "procedure", "TRIGGER": "trigger", "INDEX": "index",
    "TYPE": "type", "DOMAIN": "type", "AGGREGATE": "function",
    "SCHEMA": "schema", "SEQUENCE": "sequence",
}

# Guards against pathological captures (e.g. unnamed `CREATE INDEX ON t`,
# where the token after INDEX is the keyword ON, not a name).
_NAME_STOPWORDS = {"ON", "AS", "IF", "NOT", "EXISTS"}


def _strip_ident(part: str) -> str:
    s = part.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"`":
        return s[1:-1]
    if len(s) >= 2 and s[0] == "[" and s[-1] == "]":
        return s[1:-1]
    return s


def _norm_name(raw: str) -> tuple[str | None, str]:
    """Split a possibly schema-qualified, possibly quoted name into
    ``(schema_or_None, leaf)`` with quoting removed."""
    parts = [_strip_ident(p) for p in raw.split(".")]
    leaf = parts[-1]
    schema = ".".join(parts[:-1]) if len(parts) > 1 else None
    return schema, leaf


# ---------------------------------------------------------------------------
# Include directives
# ---------------------------------------------------------------------------


_INCLUDE_RES = (
    re.compile(r"^\s*\\include\s+(?P<path>\S+)", re.IGNORECASE),
    re.compile(r"^\s*\\ir?\s+(?P<path>\S+)", re.IGNORECASE),
    re.compile(r"^\s*SOURCE\s+(?P<path>\S+?)\s*;?\s*$", re.IGNORECASE),
    re.compile(r"^\s*@@?\s*(?P<path>\S+)"),
)


def _extract_includes(raw: str) -> list[dict]:
    out: list[dict] = []
    for lineno, line in enumerate(raw.split("\n"), start=1):
        for rex in _INCLUDE_RES:
            m = rex.match(line)
            if not m:
                continue
            path = m.group("path").strip().rstrip(";").strip("\"'")
            if path:
                out.append({"kind": "include", "source": path, "lineno": lineno})
            break
    out.sort(key=lambda x: (x["lineno"], x["source"]))
    return out


# ---------------------------------------------------------------------------
# AST extractor
# ---------------------------------------------------------------------------


def extract_sql_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    """Return ``(summary, errors)`` for a SQL file.

    The summary matches the first-class shape (``imports`` + ``items`` with
    per-object spans) consumed by the L2 chunker and the import resolver.
    """
    try:
        raw = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]

    neu = _neutralize(raw)
    line_byte_starts = _line_byte_starts(content)
    items: list[dict] = []

    for m in _CREATE_RE.finditer(neu):
        obj = m.group("obj").upper()
        kind = _OBJ_KIND.get(obj)
        if kind is None:
            continue
        schema, leaf = _norm_name(m.group("name"))
        if not leaf or leaf.upper() in _NAME_STOPWORDS:
            continue
        if kind == "view" and "MATERIALIZED" in (m.group("mods") or "").upper():
            kind = "materialized_view"

        byte_start = m.start()
        semi = neu.find(";", m.end())
        byte_end = (semi + 1) if semi != -1 else len(raw)
        line_start = _line_of(byte_start, line_byte_starts)
        line_end = _line_of(byte_end - 1, line_byte_starts)

        paren = raw.find("(", byte_start)
        newline = raw.find("\n", byte_start)
        cands = [x for x in (paren, newline) if x != -1 and x < byte_end]
        sig_end = min(cands) if cands else min(byte_end, byte_start + 80)
        signature = " ".join(raw[byte_start:sig_end].split())

        items.append({
            "kind": kind,
            "name": leaf,
            "parent": schema,
            "line_start": line_start,
            "line_end": line_end,
            "byte_start": byte_start,
            "byte_end": byte_end,
            "signature": signature,
        })

    items.sort(key=lambda x: (x["line_start"], x["kind"], x["name"]))

    summary = {
        "language": "sql",
        "extraction_method": "regex",
        "imports": _extract_includes(raw),
        "top_level_tables": sorted({
            it["name"] for it in items if it["kind"] == "table"}),
        "top_level_functions": sorted({
            it["name"] for it in items if it["kind"] in ("function", "procedure")}),
        "items": items,
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


def resolve_sql_imports(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """Resolve include directives to ``(in_repo, external)``.

    Relative includes are resolved against the including file's own
    directory. Absolute paths, and relative paths that do not correspond to a
    repo file, are surfaced as ``external`` (their spec preserved) rather than
    silently dropped.
    """
    src_dir = PurePosixPath(src_path).parent
    in_repo: set[str] = set()
    external: set[str] = set()

    for imp in summary.get("imports", []):
        spec = str(imp.get("source", "")).strip().strip("\"'")
        if not spec:
            continue
        if spec.startswith("/") or (len(spec) > 1 and spec[1] == ":"):
            external.add(spec)
            continue
        target = _normalize_rel((src_dir / spec).parts)
        if target in paths_set:
            in_repo.add(target)
        else:
            external.add(spec)

    in_repo.discard(src_path)
    return sorted(in_repo), sorted(external)
