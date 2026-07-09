"""codebase_mapper.languages.cobol — Tier-1 COBOL / COBOL-copybook support.

There is no maintained PyPI ``tree-sitter-cobol`` wheel in the style this
project consumes (individual ``tree-sitter-<lang>`` packages), so — like the
Dart and Clojure analyzers — this is a self-contained, **column-aware** reader.

Structural mapping onto the cbm symbol model
---------------------------------------------
COBOL has no functions or classes. Its compilation unit is a *program*
(``PROGRAM-ID. FOO.``) whose PROCEDURE DIVISION contains *sections* and
*paragraphs* — named, ``PERFORM``-able blocks of statements. We map:

  * program   -> ``top_level_classes``  (item kind ``"program"``,   chunk ``class``)
  * section   -> a procedure of its program (item kind ``"section"``,
                 chunk ``method``, ``parent`` = program name)
  * paragraph -> a procedure of its program (item kind ``"paragraph"``,
                 chunk ``method``, ``parent`` = program name)

so a COBOL bundle is shape-compatible with a Python/Rust bundle: one chunk per
program (class-like) plus one chunk per PERFORM-able procedure (method-like).
The symbol-xref layer (plugins/symbol_xrefs/cobol_resolver.py) then binds:

  * ``PERFORM para`` / ``PERFORM a THRU b`` -> ``calls`` (intra-program)
  * ``CALL 'PROG'``                          -> ``calls`` (inter-program)
  * ``CALL identifier``                      -> unresolved ``dynamic_dispatch``

Copybook includes surface here as imports:

  * ``COPY book`` / ``COPY "book"`` / ``COPY book OF lib`` -> import kind
    ``"copy"``; resolved against in-repo ``.cpy``/``.cbl``/``.cob`` files by
    basename (see :func:`resolve_cobol_imports`).

Source formats
--------------
Both **fixed-format** (cols 1-6 sequence, 7 indicator, 8-11 Area A,
12-72 Area B, 73+ ignored) and **free-format** are handled. The format is
detected per file from a ``>> SOURCE FORMAT`` directive or, absent one, a
column-usage heuristic (:func:`_detect_format`). Fixed-format paragraph and
section headers are recognised by their Area-A start column — the reliable
COBOL rule that a statement never begins in Area A.

Known limits (documented, not silent)
--------------------------------------
* DATA DIVISION data items (01/05/77 level numbers) are NOT surfaced as
  symbols in this v1 — the symbol surface is the PROCEDURE DIVISION
  (programs + procedures) plus COPY/CALL linkage.
* ``CALL identifier`` (dynamic, data-name target) cannot be resolved
  statically; the xref layer records it as ``dynamic_dispatch`` rather than
  guessing.
* Nested programs are attached to the most recent enclosing ``PROGRAM-ID``;
  the full nesting tree is not modelled.
"""
from __future__ import annotations

import re


COBOL_EXTENSIONS = (".cbl", ".cob", ".cpy", ".cobol")

# Words that may follow PERFORM but are NOT a procedure target (inline PERFORM,
# PERFORM VARYING/UNTIL/n TIMES). Kept upper-case; comparison upper-cases.
PERFORM_NONTARGET = frozenset({
    "UNTIL", "VARYING", "WITH", "TEST", "FOREVER", "TIMES", "THRU", "THROUGH",
})

# Lone reserved words that read like a paragraph header (``NAME.``) but are
# statements. Area-A gating handles fixed format; free format needs this list.
_LONE_STATEMENT_WORDS = frozenset({
    "EXIT", "GOBACK", "CONTINUE", "STOP", "END", "DECLARATIVES",
})


# ---------------------------------------------------------------------------
# Line model
# ---------------------------------------------------------------------------


def _split_lines(content: bytes) -> list[tuple[int, int, int, str]]:
    """Split into ``(lineno, byte_start, byte_end, text)`` records.

    ``byte_end`` is exclusive and includes the trailing newline, so a span
    a..b maps to bytes ``[bounds[a].start, bounds[b].end)`` exactly. A
    trailing newline does NOT create a phantom empty final line.
    """
    out: list[tuple[int, int, int, str]] = []
    start = 0
    line = 1
    n = len(content)
    for i in range(n):
        if content[i] == 0x0A:
            out.append((line, start, i + 1,
                        content[start:i].decode("utf-8", "replace")))
            start = i + 1
            line += 1
    if start < n or not out:
        out.append((line, start, n, content[start:n].decode("utf-8", "replace")))
    return out


_FMT_DIRECTIVE_RE = re.compile(
    r">>\s*SOURCE\s+FORMAT\s+(?:IS\s+)?(FREE|FIXED)", re.IGNORECASE)


def _detect_format(lines: list[str]) -> str:
    """Return ``"fixed"`` or ``"free"``.

    An explicit ``>> SOURCE FORMAT`` directive wins. Otherwise a per-line
    vote: a line shaped like fixed format (blank/numeric cols 1-6, an
    indicator char in col 7) votes fixed; anything else with content votes
    free, and an inline ``*>`` comment marker is a strong free signal.
    """
    for ln in lines:
        m = _FMT_DIRECTIVE_RE.search(ln)
        if m:
            return m.group(1).lower()

    fixed_votes = 0
    free_votes = 0
    for raw in lines:
        if not raw.strip():
            continue
        if "*>" in raw:
            free_votes += 1
        if len(raw) >= 7:
            seq = raw[:6]
            ind = raw[6]
            fixed_shaped = ind in " *-/dD" and (not seq.strip() or seq.strip().isdigit())
            if fixed_shaped:
                fixed_votes += 1
            else:
                free_votes += 1
        else:
            free_votes += 1
    return "free" if free_votes > fixed_votes else "fixed"


def _line_code(raw: str, fmt: str) -> tuple[str, bool, bool]:
    """Return ``(code, starts_in_area_a, is_comment)`` for one physical line.

    ``code`` is the significant COBOL text with the sequence area, indicator
    column, and comment material removed. ``starts_in_area_a`` marks
    header-eligibility (in fixed format a paragraph/section/division header
    MUST begin in Area A, cols 8-11; free-format lines are always eligible).
    ``is_comment`` marks comment/blank lines that carry no code.
    """
    if fmt == "fixed":
        ind = raw[6] if len(raw) >= 7 else " "
        if ind in "*/":
            return "", False, True
        if ind in "dD":  # debugging line — active only WITH DEBUGGING MODE
            return "", False, True
        body = raw[7:72] if len(raw) > 7 else ""
        if not body.strip():
            return "", False, True
        first = len(body) - len(body.lstrip())
        starts_in_area_a = first <= 3 and ind != "-"  # cols 8-11, not a continuation
        return body, starts_in_area_a, False

    # free format
    code = raw
    cpos = code.find("*>")
    if cpos != -1:
        code = code[:cpos]
    stripped = code.strip()
    if not stripped:
        return "", False, True
    if stripped.startswith("*"):  # legacy full-line comment in a free file
        return "", False, True
    return code, True, False


# ---------------------------------------------------------------------------
# Structural regexes (applied to a line's ``code``, already area-A gated)
# ---------------------------------------------------------------------------


_DIVISION_RE = re.compile(
    r"^\s*(IDENTIFICATION|ID|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION\b",
    re.IGNORECASE)
_PROGRAM_ID_RE = re.compile(
    r"\bPROGRAM-ID\s*\.\s*([A-Za-z0-9][A-Za-z0-9-]*)", re.IGNORECASE)
_END_PROGRAM_RE = re.compile(
    r"\bEND\s+PROGRAM\s+([A-Za-z0-9][A-Za-z0-9-]*)", re.IGNORECASE)
_SECTION_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9-]*)\s+SECTION\b", re.IGNORECASE)
_PARAGRAPH_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9-]*)\s*\.\s*$", re.IGNORECASE)
# COPY <book> | COPY "book" | COPY 'book'  (library qualifier / REPLACING ignored)
_COPY_RE = re.compile(
    r"\bCOPY\s+(?:\"([^\"]+)\"|'([^']+)'|([A-Za-z0-9][A-Za-z0-9_-]*))",
    re.IGNORECASE)


def extract_cobol_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    """Return ``(summary, errors)`` for a COBOL source file or copybook.

    The summary matches the SPEC's first-class contract
    (``language``/``imports``/``top_level_functions``/``top_level_classes``)
    plus ``items`` with per-symbol line/byte spans consumed by the L2 chunker
    and the symbol-xref resolver, and ``extraction_method: "regex"``.
    """
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]

    lines = _split_lines(content)
    fmt = _detect_format([t for _, _, _, t in lines])
    total_bytes = len(content)

    programs: list[dict] = []
    procedures: list[dict] = []
    copies: list[tuple[str, int]] = []

    division: str | None = None
    open_program: dict | None = None
    open_proc: dict | None = None
    pending_ident_line: tuple[int, int] | None = None  # (lineno, byte_start)

    def close_proc(end_lineno: int, end_byte: int) -> None:
        nonlocal open_proc
        if open_proc is not None:
            open_proc["line_end"] = max(open_proc["line_start"], end_lineno)
            open_proc["byte_end"] = end_byte
            procedures.append(open_proc)
            open_proc = None

    def close_program(end_lineno: int, end_byte: int) -> None:
        nonlocal open_program
        if open_program is not None:
            open_program["line_end"] = max(open_program["line_start"], end_lineno)
            open_program["byte_end"] = end_byte
            programs.append(open_program)
            open_program = None

    for idx, (lineno, b_start, b_end, raw) in enumerate(lines):
        code, area_a, is_comment = _line_code(raw, fmt)
        if is_comment:
            continue
        stripped = code.strip()

        # COPY may appear on any code line (incl. inside statements).
        for m in _COPY_RE.finditer(code):
            book = m.group(1) or m.group(2) or m.group(3)
            if book:
                copies.append((book, lineno))

        # Division header (must start in Area A in fixed format).
        if area_a:
            dm = _DIVISION_RE.match(code)
            if dm:
                div = dm.group(1).upper()
                division = "identification" if div in ("IDENTIFICATION", "ID") \
                    else div.lower()
                if division == "identification":
                    pending_ident_line = (lineno, b_start)
                # Entering a new division ends the current procedure.
                close_proc(lineno - 1, b_start)
                continue

        # PROGRAM-ID begins a new program.
        pm = _PROGRAM_ID_RE.search(code)
        if pm:
            close_proc(lineno - 1, b_start)
            close_program(lineno - 1, b_start)
            start_line, start_byte = pending_ident_line or (lineno, b_start)
            open_program = {
                "kind": "program",
                "name": pm.group(1),
                "parent": None,
                "line_start": start_line,
                "byte_start": start_byte,
                "signature": f"PROGRAM-ID. {pm.group(1)}",
            }
            pending_ident_line = None
            continue

        # END PROGRAM closes the current program (and its last procedure).
        em = _END_PROGRAM_RE.search(code)
        if em:
            close_proc(lineno, b_end)
            close_program(lineno, b_end)
            division = None
            continue

        # Sections / paragraphs only exist in the PROCEDURE DIVISION.
        if division == "procedure" and area_a and open_program is not None:
            sm = _SECTION_RE.match(code)
            if sm and sm.group(1).upper() not in _LONE_STATEMENT_WORDS:
                close_proc(lineno - 1, b_start)
                open_proc = {
                    "kind": "section",
                    "name": sm.group(1),
                    "parent": open_program["name"],
                    "line_start": lineno,
                    "byte_start": b_start,
                }
                continue
            pmatch = _PARAGRAPH_RE.match(stripped)
            if pmatch and pmatch.group(1).upper() not in _LONE_STATEMENT_WORDS:
                close_proc(lineno - 1, b_start)
                open_proc = {
                    "kind": "paragraph",
                    "name": pmatch.group(1),
                    "parent": open_program["name"],
                    "line_start": lineno,
                    "byte_start": b_start,
                }
                continue

    last_line = lines[-1][0] if lines else 1
    close_proc(last_line, total_bytes)
    close_program(last_line, total_bytes)

    items = sorted(
        programs + procedures,
        key=lambda it: (it["line_start"], it["kind"], it["name"]),
    )

    # Dedupe COPY on the copybook name; keep first line; deterministic order.
    seen: set[str] = set()
    imports: list[dict] = []
    for book, lineno in sorted(copies, key=lambda c: (c[1], c[0])):
        if book in seen:
            continue
        seen.add(book)
        imports.append({"kind": "copy", "source": book, "lineno": lineno})

    top_level_classes = sorted({p["name"] for p in programs})
    top_level_functions = sorted({p["name"] for p in procedures})

    return {
        "language": "cobol",
        "extraction_method": "regex",
        "source_format": fmt,
        "imports": imports,
        "top_level_functions": top_level_functions,
        "top_level_classes": top_level_classes,
        "items": items,
    }, []


# ---------------------------------------------------------------------------
# Import (copybook) resolution
# ---------------------------------------------------------------------------


def _find_copybook(name: str, paths_set: set[str]) -> str | None:
    """Nearest in-repo copybook file whose stem matches ``name`` (case- and
    extension-insensitive). Copybooks are conventionally ``.cpy`` but may be
    plain COBOL source; any COBOL extension is accepted. Deterministic: the
    lexicographically-first match wins."""
    lname = name.lower()
    matches: list[str] = []
    for p in paths_set:
        base = p.rsplit("/", 1)[-1]
        stem, dot, ext = base.rpartition(".")
        if not dot:  # extensionless
            stem, ext = base, ""
        if stem.lower() == lname and ("." + ext.lower() in COBOL_EXTENSIONS or ext == ""):
            matches.append(p)
    return sorted(matches)[0] if matches else None


def resolve_cobol_imports(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """Resolve ``COPY`` copybook references to ``(in_repo, external)``.

    A copybook that maps to an in-repo file is in-repo; one that does not
    resolve (a copybook shipped by a compiler/vendor, or simply absent from
    the repo) surfaces as ``external`` under its copybook name — never
    silently dropped (SPEC C2.5.5)."""
    in_repo: set[str] = set()
    external: set[str] = set()
    for imp in summary.get("imports", []):
        book = imp.get("source")
        if not book:
            continue
        target = _find_copybook(book, paths_set)
        if target is not None and target != src_path:
            in_repo.add(target)
        else:
            external.add(book)
    return sorted(in_repo), sorted(external)
