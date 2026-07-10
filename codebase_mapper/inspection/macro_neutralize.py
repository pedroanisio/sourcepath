"""Macro harvest + byte-preserving neutralization (error-free-mapping E1).

Unexpanded macros are the single root cause behind the C-family parse-error
mass (linux-v23: 49% of source files flagged, median 4 error nodes, every
sampled failure one of three shapes):

  1. annotation macros between type and declarator — ``void __iomem *base``
  2. iterator macros — ``for_each_set_bit(bit, mask, 4) { … }``
  3. token-pasted digit-leading identifiers — ``1000baseX_Full``

The repository's own ``#define`` bodies classify its macros — no hardcoded,
project-specific lists:

  * empty body or a body that IS an ``__attribute__(…)``  → *annotation*
  * body whose first token is ``for``                      → *iterator*

Neutralization rewrites a *parse buffer*, never stored content, and every
substitution is byte-length-preserving so all node offsets and line numbers
remain valid against the original blob:

  * annotation token   → same-length spaces
  * iterator name + `(`→ ``while`` + same-length padding (comma expressions
                         make ``while (a, b, 4) { … }`` valid C)
  * ``1000baseX_Full`` → ``_000baseX_Full`` (never a valid C token anyway)

Preprocessor directive lines (including continuations) are copied verbatim:
a ``#define``'s own name/body must survive so macro symbols extract intact.
Strings, char literals, and comments are never touched. Files parsed from a
neutralized buffer carry ``ast_summary.parse_buffer = "macro_neutralized"``
(PALS's Law: an altered parse input is disclosed, never silent).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_DEFINE_RE = re.compile(
    rb"^\s*#\s*define\s+(?P<name>[A-Za-z_]\w*)(?P<args>\([^)]*\))?\s*(?P<body>.*)$"
)
_IDENT_RE = re.compile(rb"[A-Za-z_]\w*")
_DIGIT_IDENT_RE = re.compile(rb"\d[\dA-Za-z_]*[A-Za-z_][\dA-Za-z_]*")

#: An iterator macro name must be at least this long to take the
#: same-length ``while`` substitution.
_MIN_ITER_LEN = len(b"while")


@dataclass
class MacroTable:
    """Repo-harvested macro classification."""
    annotations: set[str] = field(default_factory=set)
    iterators: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.annotations or self.iterators)


def harvest_macros(content: bytes, table: MacroTable) -> None:
    """Scan one file's ``#define`` logical lines into *table*."""
    logical: list[bytes] = []
    pending = b""
    for line in content.splitlines():
        if line.endswith(b"\\"):
            pending += line[:-1] + b" "
            continue
        logical.append(pending + line)
        pending = b""
    if pending:
        logical.append(pending)

    for line in logical:
        m = _DEFINE_RE.match(line)
        if m is None:
            continue
        name = m.group("name").decode("ascii", "replace")
        body = m.group("body").strip()
        if body == b"" or body.startswith(b"__attribute__"):
            table.annotations.add(name)
        elif re.match(rb"for\b|for\s*\(", body):
            if len(name) >= _MIN_ITER_LEN:
                table.iterators.add(name)


def neutralize(content: bytes, table: MacroTable) -> bytes:
    """Byte-length-preserving neutralization of *content* against *table*.

    Returns the original object unchanged when nothing matches, so callers
    can cheaply detect "no retry needed" via identity/equality.
    """
    out = bytearray(content)
    n = len(content)
    i = 0
    changed = False
    line_is_preproc = False
    at_line_start = True

    while i < n:
        c = content[i]

        if at_line_start:
            j = i
            while j < n and content[j] in b" \t":
                j += 1
            line_is_preproc = j < n and content[j : j + 1] == b"#"
            at_line_start = False

        if c == 0x0A:  # \n — a preproc line continues past a backslash-newline
            if not (line_is_preproc and i > 0 and content[i - 1] == 0x5C):
                at_line_start = True
            i += 1
            continue

        if line_is_preproc:
            i += 1
            continue

        # strings / char literals / comments: skip wholesale
        if c in (0x22, 0x27):  # " or '
            quote = c
            i += 1
            while i < n and content[i] != quote:
                i += 2 if content[i] == 0x5C else 1
            i += 1
            continue
        if c == 0x2F and i + 1 < n:  # /
            nxt = content[i + 1]
            if nxt == 0x2F:  # //
                while i < n and content[i] != 0x0A:
                    i += 1
                continue
            if nxt == 0x2A:  # /*
                end = content.find(b"*/", i + 2)
                i = n if end == -1 else end + 2
                continue

        if c == 0x5F or 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A:  # identifier
            m = _IDENT_RE.match(content, i)
            tok = m.group()
            name = tok.decode("ascii", "replace")
            end = m.end()
            if name in table.annotations:
                out[i:end] = b" " * len(tok)
                changed = True
            elif name in table.iterators:
                k = end
                while k < n and content[k] in b" \t":
                    k += 1
                if k < n and content[k : k + 1] == b"(":
                    out[i:end] = b"while".ljust(len(tok))
                    changed = True
            i = end
            continue

        if 0x30 <= c <= 0x39:  # digit — maybe a pasted identifier
            m = _DIGIT_IDENT_RE.match(content, i)
            if m:
                out[i] = 0x5F  # leading digit → '_'
                changed = True
                i = m.end()
                continue
            while i < n and (0x30 <= content[i] <= 0x39
                             or content[i] in b".xXbBuUlLfFeE+-aAcCdD"):
                i += 1
            continue

        i += 1

    return bytes(out) if changed else content
