"""codebase_mapper.inspection.languages.json — first-class JSON support.

A hand-written recursive-descent JSON parser (stdlib only — no new
dependency, and it does not shadow the stdlib ``json`` module, which is
reached by absolute import elsewhere) that builds a value AST with byte/line
spans. One ``item`` is emitted per object member, at the top level and one
nested level, so the L2 chunker produces a chunk per structural key.

The parser is strict enough to report malformed JSON as an error (rather than
crash the pipeline) and lenient about nothing — it is a real JSON grammar.

Import edges are the cross-file references JSON configs actually carry:
JSON-Schema / OpenAPI ``$ref``, and the ``extends`` / tsconfig ``references``
file pointers. Internal pointers (``#/definitions/X``) and http(s) URLs are
external; relative file refs (with any ``#...`` fragment stripped) resolve
in-repo.
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


class _JsonError(Exception):
    pass


_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
            "n": "\n", "r": "\r", "t": "\t"}


def _unescape(raw: str) -> str:
    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "u" and i + 6 <= n:
                try:
                    out.append(chr(int(raw[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _parse(text: str) -> dict:
    """Return the root node, or raise _JsonError. Nodes carry char offsets
    (start/end); object members carry (key, key_start, value)."""
    n = len(text)

    def skip_ws(p: int) -> int:
        while p < n and text[p] in " \t\r\n":
            p += 1
        return p

    def parse_string(p: int) -> tuple[str, int]:
        q = p + 1
        while q < n:
            ch = text[q]
            if ch == "\\":
                q += 2
                continue
            if ch == '"':
                return _unescape(text[p + 1:q]), q + 1
            q += 1
        raise _JsonError("unterminated string")

    def parse_value(p: int) -> tuple[dict, int]:
        p = skip_ws(p)
        if p >= n:
            raise _JsonError("unexpected end of input")
        c = text[p]
        if c == "{":
            return parse_object(p)
        if c == "[":
            return parse_array(p)
        if c == '"':
            val, end = parse_string(p)
            return {"type": "string", "start": p, "end": end, "value": val}, end
        if c == "-" or c.isdigit():
            q = p + 1
            while q < n and text[q] in "-+.eE0123456789":
                q += 1
            return {"type": "number", "start": p, "end": q}, q
        for lit, kind, ln in (("true", "bool", 4), ("false", "bool", 5), ("null", "null", 4)):
            if text.startswith(lit, p):
                return {"type": kind, "start": p, "end": p + ln}, p + ln
        raise _JsonError(f"unexpected character {c!r} at offset {p}")

    def parse_array(p: int) -> tuple[dict, int]:
        start = p
        p = skip_ws(p + 1)
        elements: list[dict] = []
        if p < n and text[p] == "]":
            return {"type": "array", "start": start, "end": p + 1, "elements": elements}, p + 1
        while True:
            node, p = parse_value(p)
            elements.append(node)
            p = skip_ws(p)
            if p >= n:
                raise _JsonError("unterminated array")
            if text[p] == ",":
                p += 1
                continue
            if text[p] == "]":
                return {"type": "array", "start": start, "end": p + 1, "elements": elements}, p + 1
            raise _JsonError(f"expected ',' or ']' at offset {p}")

    def parse_object(p: int) -> tuple[dict, int]:
        start = p
        p = skip_ws(p + 1)
        members: list[dict] = []
        if p < n and text[p] == "}":
            return {"type": "object", "start": start, "end": p + 1, "members": members}, p + 1
        while True:
            p = skip_ws(p)
            if p >= n or text[p] != '"':
                raise _JsonError(f"expected string key at offset {p}")
            key_start = p
            key, kend = parse_string(p)
            p = skip_ws(kend)
            if p >= n or text[p] != ":":
                raise _JsonError(f"expected ':' at offset {p}")
            value, p = parse_value(p + 1)
            members.append({"key": key, "key_start": key_start, "value": value})
            p = skip_ws(p)
            if p >= n:
                raise _JsonError("unterminated object")
            if text[p] == ",":
                p += 1
                continue
            if text[p] == "}":
                return {"type": "object", "start": start, "end": p + 1, "members": members}, p + 1
            raise _JsonError(f"expected ',' or '}}' at offset {p}")

    root, p = parse_value(0)
    p = skip_ws(p)
    if p != n:
        raise _JsonError(f"trailing content at offset {p}")
    return root


_REF_KEYS = {"$ref": "ref", "extends": "extends"}


def extract_json_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    """Return ``(summary, errors)`` for a JSON file."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]
    try:
        root = _parse(text)
    except _JsonError as e:
        return None, [f"json_parse_error: {e}"]

    lbs = _line_byte_starts(content)
    _cache: dict[int, int] = {}

    def cb(char_idx: int) -> int:  # char offset -> byte offset (utf-8)
        v = _cache.get(char_idx)
        if v is None:
            v = len(text[:char_idx].encode("utf-8"))
            _cache[char_idx] = v
        return v

    items: list[dict] = []
    imports: list[dict] = []

    def emit_members(obj: dict, parent: str | None, depth: int) -> None:
        for m in obj["members"]:
            v = m["value"]
            b_start = cb(m["key_start"])
            b_end = cb(v["end"])
            items.append({
                "kind": "member",
                "name": m["key"],
                "parent": parent,
                "value_type": v["type"],
                "line_start": _line_of(b_start, lbs),
                "line_end": _line_of(max(b_start, b_end - 1), lbs),
                "byte_start": b_start,
                "byte_end": b_end,
                "signature": _collapse(text[m["key_start"]:min(v["start"] + 40, v["end"])])[:120],
            })
            if depth < 2 and v["type"] == "object":
                emit_members(v, m["key"], depth + 1)

    def collect_imports(node: dict) -> None:
        if node["type"] == "object":
            for m in node["members"]:
                v = m["value"]
                if m["key"] in _REF_KEYS and v["type"] == "string":
                    imports.append({
                        "kind": _REF_KEYS[m["key"]],
                        "source": v["value"],
                        "lineno": _line_of(cb(m["key_start"]), lbs),
                    })
                elif m["key"] == "references" and v["type"] == "array":
                    for el in v["elements"]:
                        if el["type"] == "object":
                            for mm in el["members"]:
                                if mm["key"] == "path" and mm["value"]["type"] == "string":
                                    imports.append({
                                        "kind": "reference",
                                        "source": mm["value"]["value"],
                                        "lineno": _line_of(cb(mm["key_start"]), lbs),
                                    })
                collect_imports(v)
        elif node["type"] == "array":
            for el in node["elements"]:
                collect_imports(el)

    if root["type"] == "object":
        emit_members(root, None, 1)
    collect_imports(root)

    items.sort(key=lambda x: (x["line_start"], x["byte_start"]))
    imports.sort(key=lambda x: (x["lineno"], x["source"]))

    summary = {
        "language": "json",
        "extraction_method": "recursive-descent",
        "imports": imports,
        "items": items,
        "top_level_keys": [m["key"] for m in root["members"]] if root["type"] == "object" else [],
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


def resolve_json_imports(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    src_dir = PurePosixPath(src_path).parent
    in_repo: set[str] = set()
    external: set[str] = set()

    for imp in summary.get("imports", []):
        spec = str(imp.get("source", "")).strip()
        if not spec or spec.startswith("#"):  # internal JSON-pointer, no file
            continue
        if re.match(r"^[a-z][a-z0-9+.-]*://", spec) or spec.startswith("//"):
            external.add(spec)
            continue
        clean = spec.split("#", 1)[0]
        if not clean:
            continue
        target = _normalize_rel((src_dir / clean).parts)
        if target in paths_set:
            in_repo.add(target)
        else:
            external.add(spec)

    in_repo.discard(src_path)
    return sorted(in_repo), sorted(external)
