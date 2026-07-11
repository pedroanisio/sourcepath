"""codebase_mapper.inspection.languages.shell — first-class Shell support.

Shell has no bundled tree-sitter grammar here, so — like Dart / SQL / HTML /
CSS / JSON — this is a disciplined pure-Python extractor. What makes shell
tractable without a full grammar is doing the lexing honestly first:
``_neutralize`` is a **single-pass state machine** that blanks (length-
preserving, newlines kept) the three constructs that otherwise wreck any
structural scan:

  * ``#`` comments — but only where ``#`` actually starts a word, so ``$#``
    and ``${#arr}`` survive;
  * ``'single'`` and ``"double"`` quoted strings — so a ``#`` inside a string
    is not a comment;
  * **heredoc bodies** (``<<EOF`` / ``<<-'EOF'`` … terminator) — so a
    ``fake() { … }`` inside a heredoc yields no phantom function and its
    braces do not corrupt brace matching for the enclosing function.

Because it is one left-to-right pass, ordering hazards resolve correctly: an
apostrophe inside a comment cannot open a string, and a ``#`` inside a string
cannot open a comment.

Symbols are functions, in both shell forms (``name() { }`` and
``function name { }``), with line/byte spans from brace matching.

Import edges are ``source <file>`` and ``. <file>`` (dot-source). Paths with
unexpanded variables (``"$DIR/x.sh"``) and absolute paths cannot be resolved
statically — they are surfaced as external rather than dropped.
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


_HEREDOC_RE = re.compile(r"<<-?\s*(?P<q>[\"']?)(?P<tag>[A-Za-z_][\w]*)(?P=q)")


def _neutralize(text: str, blank_strings: bool = True) -> str:
    """Blank comments, heredoc bodies and (optionally) quoted strings, in one
    left-to-right pass. Length- and newline-preserving, so every offset stays
    valid in the original text.

    ``blank_strings=True``  — for the structural (brace/function) scan: braces
    inside a string must not count.
    ``blank_strings=False`` — for the ``source``/``.`` import scan: the scanner
    still *skips over* strings (so a ``#`` inside one is not a comment) but
    leaves them intact, because a sourced path is usually quoted.
    """
    out = list(text)
    i, n = 0, len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = text[i]

        # comment: '#' that begins a word (start of file/line or after space)
        if c == "#" and (i == 0 or text[i - 1] in " \t\n"):
            j = text.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
            continue

        if c == "'":
            j = text.find("'", i + 1)
            j = n if j == -1 else j + 1
            if blank_strings:
                blank(i, j)
            i = j
            continue

        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            if blank_strings:
                blank(i, j)
            i = j
            continue

        if c == "<" and i + 1 < n and text[i + 1] == "<":
            m = _HEREDOC_RE.match(text, i)
            if m:
                nl = text.find("\n", m.end())
                if nl == -1:
                    blank(i, n)
                    i = n
                    continue
                term = re.compile(r"^[ \t]*" + re.escape(m.group("tag")) + r"[ \t]*$",
                                  re.MULTILINE)
                tm = term.search(text, nl + 1)
                end = tm.end() if tm else n
                blank(nl + 1, end)   # body only; the `<<TAG` operator stays
                i = end
                continue

        i += 1

    return "".join(out)


# `name() {`  or  `function name [()] {`  — the `{` may sit on the next line.
_FUNC_RE = re.compile(
    r"(?m)^[ \t]*"
    r"(?:function[ \t]+(?P<n1>[A-Za-z_][\w:.-]*)[ \t]*(?:\(\))?"
    r"|(?P<n2>[A-Za-z_][\w:.-]*)[ \t]*\(\))"
    r"[ \t]*(?:\r?\n[ \t]*)?\{"
)

# `source <file>` / `. <file>` at a command position (line start, or after ; && ||)
_SOURCE_RE = re.compile(
    r"(?:^|[;&|]\s*)[ \t]*(?:source|\.)[ \t]+"
    r"(?P<path>\"[^\"]*\"|'[^']*'|[^\s;&|<>()]+)",
    re.MULTILINE,
)

_SHEBANG_RE = re.compile(r"^#!\s*(?P<path>\S+)(?:[ \t]+(?P<arg>\S+))?")


def _match_brace(neu: str, open_idx: int) -> int:
    depth = 0
    k, n = open_idx, len(neu)
    while k < n:
        c = neu[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return k
        k += 1
    return n - 1


def _interpreter(raw: str) -> str | None:
    m = _SHEBANG_RE.match(raw)
    if not m:
        return None
    path = m.group("path")
    base = path.rsplit("/", 1)[-1]
    if base == "env" and m.group("arg"):
        return m.group("arg").rsplit("/", 1)[-1]
    return base


def extract_shell_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    """Return ``(summary, errors)`` for a shell script."""
    try:
        raw = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]

    neu = _neutralize(raw)
    lbs = _line_byte_starts(content)

    def cb(char_idx: int) -> int:
        return len(raw[:char_idx].encode("utf-8"))

    items: list[dict] = []
    for m in _FUNC_RE.finditer(neu):
        name = m.group("n1") or m.group("n2")
        if not name:
            continue
        open_idx = neu.index("{", m.end() - 1)
        close_idx = _match_brace(neu, open_idx)
        b_start = cb(m.start())
        b_end = cb(close_idx + 1)
        items.append({
            "kind": "function",
            "name": name,
            "parent": None,
            "line_start": _line_of(b_start, lbs),
            "line_end": _line_of(max(b_start, b_end - 1), lbs),
            "byte_start": b_start,
            "byte_end": b_end,
            "signature": _collapse(raw[m.start():open_idx + 1])[:120],
        })

    # Import scan runs on a variant that keeps strings (sourced paths are
    # usually quoted) but still blanks comments/heredocs, so a commented-out
    # `source` line is correctly ignored.
    cmd = _neutralize(raw, blank_strings=False)
    imports: list[dict] = []
    for m in _SOURCE_RE.finditer(cmd):
        spec = m.group("path").strip().strip("\"'")
        if not spec:
            continue
        imports.append({
            "kind": "source",
            "source": spec,
            "lineno": _line_of(cb(m.start("path")), lbs),
        })

    items.sort(key=lambda x: (x["line_start"], x["byte_start"]))
    imports.sort(key=lambda x: (x["lineno"], x["source"]))

    summary = {
        "language": "shell",
        "extraction_method": "regex",
        "interpreter": _interpreter(raw),
        "imports": imports,
        "items": items,
        "top_level_functions": sorted({it["name"] for it in items}),
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


def resolve_shell_imports(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """Resolve ``source``/``.`` targets to ``(in_repo, external)``.

    A path containing an unexpanded variable (``$VAR`` / ``${VAR}``) or a
    command substitution cannot be resolved by static analysis; it is surfaced
    as external (spec preserved) rather than silently dropped. Absolute paths
    are external too — they point outside the repo.
    """
    src_dir = PurePosixPath(src_path).parent
    in_repo: set[str] = set()
    external: set[str] = set()

    for imp in summary.get("imports", []):
        spec = str(imp.get("source", "")).strip().strip("\"'")
        if not spec:
            continue
        if "$" in spec or "`" in spec or spec.startswith("/"):
            external.add(spec)
            continue
        target = _normalize_rel((src_dir / spec).parts)
        if target in paths_set:
            in_repo.add(target)
        else:
            external.add(spec)

    in_repo.discard(src_path)
    return sorted(in_repo), sorted(external)
