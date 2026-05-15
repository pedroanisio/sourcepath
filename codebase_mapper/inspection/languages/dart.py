"""codebase_mapper.languages.dart — Tier-1 Dart support.

Dart has no maintained PyPI tree-sitter package, so this module uses a
disciplined regex extractor. The extractor records line + byte spans
for every top-level declaration AND for every method inside a class,
so the L2 chunker can produce per-symbol chunks (not just whole-file
chunks) and the symbol-xref resolver can find a source chunk for any
call site by line lookup.

Multi-package monorepos are first-class: ``detect_dart_packages`` walks
every ``pubspec.yaml`` in the repo and returns a directory→name map.
``dart_package_for_path`` picks the nearest enclosing package for a
given source path; the resolver uses it to recognise sibling-package
``package:foo/...`` imports as in-repo.

Import forms recognised
-----------------------

  * ``import 'package:foo/bar.dart'``  — in-repo when ``foo`` is a
    workspace package; external otherwise.
  * ``import 'dart:async'``            — always external (Dart SDK).
  * ``import 'relative.dart'``         — in-repo via path resolution.
  * ``export ...``                     — same resolution rules.
  * ``part 'file.dart'``               — emits a "part" edge to the
    included file. Resolved identically to a relative import.
  * ``part of '<lib.dart>' | <id>``    — emits a "part_of" back-edge
    when the parent is in-repo.
  * Conditional imports
    ``import 'x' if (dart.library.html) 'y' if (...) 'z'``
                                       — both branches participate;
    each emits an entry. The primary branch is the unconditional spec;
    the if-branches share the same line number.
  * Deferred imports ``import 'x' deferred as y`` — treated as a plain
    import. The ``deferred``/``as`` clauses do not affect resolution.

Show / hide / ``as`` clauses are intentionally ignored — they don't
change which file is referenced.
"""
from __future__ import annotations

import hashlib
import re

from pathlib import PurePosixPath
from typing import Callable

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


from ..models import FileRecord


# ---------------------------------------------------------------------------
# Import / part regexes
# ---------------------------------------------------------------------------


# Captures: (kind, primary_spec, if-branches blob, trailing — used for `as`/show/hide)
# We don't grab show/hide; we just care about the spec.
_DART_DIRECTIVE_RE = re.compile(
    r"^\s*(?P<kind>import|export|part)\s+(['\"])(?P<spec>[^'\"]+)\2"
    r"(?P<rest>[^;]*);",
    re.MULTILINE,
)

# `if (...) '<spec>'` conditional alternatives. Run against the `rest` group.
_DART_CONDITIONAL_RE = re.compile(r"if\s*\([^)]*\)\s*['\"]([^'\"]+)['\"]")

# `part of <lib>;` where <lib> is a string spec OR a library identifier.
_DART_PART_OF_STRING_RE = re.compile(
    r"^\s*part\s+of\s+(['\"])([^'\"]+)\1\s*;", re.MULTILINE,
)
_DART_PART_OF_LIB_RE = re.compile(
    r"^\s*part\s+of\s+([A-Za-z_][\w.]*)\s*;", re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Declaration regexes — class-like declarations and free functions
# ---------------------------------------------------------------------------


# Class / mixin / enum / extension / typedef at column 0.
_DART_CLASS_DECL_RE = re.compile(
    r"^(?P<mods>(?:abstract\s+|sealed\s+|base\s+|final\s+|interface\s+|mixin\s+)*)"
    r"(?P<keyword>class|mixin|enum|extension(?:\s+type)?|typedef)\s+"
    r"(?P<name>[A-Z][A-Za-z0-9_]*)"
    r"(?P<generics><[^{};]*>)?"
    r"(?P<rest>[^{;]*)"
    r"(?P<delim>[{;])",
    re.MULTILINE,
)

# Top-level free function at column 0. Conservative — we accept either
# explicit return type or none.
_DART_TOP_FUNC_RE = re.compile(
    r"^(?P<ret>[A-Za-z_][\w<>?,\s]*?\s+)?"
    r"(?P<name>[a-z_][A-Za-z0-9_]*)"
    r"(?P<generics><[^>{};]+>)?"
    r"\s*\(",
    re.MULTILINE,
)

_DART_FUNC_BLACKLIST = {
    "if", "for", "while", "switch", "return", "throw",
    "assert", "rethrow", "yield", "break", "continue",
    "set", "get", "operator", "new", "do", "catch", "try",
    "import", "export", "part", "library", "show", "hide",
    "as", "is", "in", "of", "with", "extends", "implements",
    "abstract", "sealed", "base", "final", "interface", "mixin",
    "void",
}

# Methods (and getters/setters/operators) inside a class body.
#
# Two variants matched separately to avoid the "return type consumes get/set"
# trap when a single permissive regex is used:
#
#   1. Getters / setters / operators (no parameter list before name).
#   2. Regular methods + constructors (return type optional, name +
#      optional ``.named`` for named constructors, then parens).
_DART_GETSET_RE = re.compile(
    r"^(?P<indent>[ \t]+)"
    r"(?P<mods>(?:static\s+|external\s+|abstract\s+|"
    r"covariant\s+|const\s+|final\s+)*)"
    r"(?:(?P<ret>[A-Za-z_][\w<>?,\s]*?)\s+)?"
    r"(?P<kw>get|set|operator)\s+"
    r"(?P<name>[A-Za-z_$][\w$=<>!+\-*/%~|&^]*)"
    r"\s*(?:\(|=>|;|=)",
    re.MULTILINE,
)
_DART_METHOD_RE = re.compile(
    r"^(?P<indent>[ \t]+)"
    r"(?P<mods>(?:static\s+|external\s+|abstract\s+|"
    r"covariant\s+|factory\s+|const\s+|final\s+)*)"
    r"(?:(?P<ret>[A-Za-z_][\w<>?,\s]*?)\s+)?"
    r"(?P<name>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)"
    r"(?P<generics><[^>{};]+>)?"
    r"\s*\(",
    re.MULTILINE,
)

# Constructor: `ClassName(...)` or `ClassName.named(...)` or `factory ClassName(...)`
# Captured during class-body walk by checking method name against class name.


# ---------------------------------------------------------------------------
# AST extractor
# ---------------------------------------------------------------------------


def _line_byte_starts(content: bytes) -> list[int]:
    """Byte offset where each 1-indexed line starts. Index 0 is unused.

    Duplicated from chunker for use during span computation here.
    Cheap; called once per file.
    """
    starts = [0, 0]
    for i, b in enumerate(content):
        if b == 0x0A:
            starts.append(i + 1)
    return starts


def _strip_strings_and_comments(text: str) -> str:
    """Replace string and comment contents with whitespace of equal length.

    Length-preserving so byte offsets and line numbers are unaffected.
    This stops the declaration regexes from matching inside strings
    or comments (e.g. a comment that reads ``class Foo`` won't trip
    the class-decl regex).
    """
    # /* block comment */
    def _block(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    text = re.sub(r"/\*.*?\*/", _block, text, flags=re.DOTALL)
    # // line comment
    text = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), text)
    # String literals — single/double/triple. Dart strings can be raw (r'...')
    # but raw is a prefix and doesn't change the structure.
    # Triple first to avoid pre-splitting them.
    def _triple(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    text = re.sub(r"'''.*?'''", _triple, text, flags=re.DOTALL)
    text = re.sub(r'""".*?"""', _triple, text, flags=re.DOTALL)
    # Single-line strings: do NOT eat across newlines.
    text = re.sub(r"'(?:[^'\\\n]|\\.)*'", lambda m: " " * len(m.group(0)), text)
    text = re.sub(r'"(?:[^"\\\n]|\\.)*"', lambda m: " " * len(m.group(0)), text)
    return text


def _find_matching_brace(text: str, open_idx: int) -> int:
    """Index of the matching ``}`` for the ``{`` at ``open_idx``, or len(text)
    if not balanced. Operates on already-stripped text (so strings can't
    contain braces).
    """
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _scan_member_end(body: str, start_idx: int) -> int:
    """Given an index after a member's parameter list, return the index
    one past the end of its body (or trailing ``;`` for an abstract member,
    or the ``;`` ending an ``=>``-expression body).
    """
    n = len(body)
    k = start_idx
    while k < n and body[k] not in "{;":
        if body[k] == "=" and k + 1 < n and body[k + 1] == ">":
            end = body.find(";", k)
            return end + 1 if end != -1 else n
        k += 1
    if k >= n:
        return n
    if body[k] == "{":
        close = _find_matching_brace(body, k)
        return close + 1
    return k + 1


def _line_of(byte_idx: int, line_byte_starts: list[int]) -> int:
    """Binary-search the 1-indexed line number for a byte offset."""
    # line_byte_starts[1] is 0; line_byte_starts[L] is the first byte of line L.
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


def _emit_imports(text: str, content: bytes) -> tuple[list[dict], list[dict]]:
    """Return ``(imports, parts)`` where:

    * ``imports``  — list of {kind, source, lineno, deferred?, conditional?}
                     for each ``import``/``export`` (incl. conditional alternatives).
    * ``parts``    — list of {kind: 'part'|'part_of', source, lineno} for
                     ``part 'x.dart'`` and ``part of`` directives.
    """
    imports: list[dict] = []
    parts: list[dict] = []
    line_byte_starts = _line_byte_starts(content)
    text_bytes = content  # alias for clarity in byte->line math

    for m in _DART_DIRECTIVE_RE.finditer(text):
        kind = m.group("kind")
        spec = m.group("spec")
        rest = m.group("rest") or ""
        line = _line_of(m.start(), line_byte_starts)
        entry: dict = {
            "kind": kind,
            "source": spec,
            "lineno": line,
        }
        if "deferred" in rest:
            entry["deferred"] = True
        if kind in ("import", "export"):
            imports.append(entry)
        else:  # part
            parts.append(entry)
        # Conditional alternates: shared lineno, kind tagged "conditional"
        if "if" in rest:
            for alt in _DART_CONDITIONAL_RE.findall(rest):
                imports.append({
                    "kind": kind,
                    "source": alt,
                    "lineno": line,
                    "conditional": True,
                })

    for m in _DART_PART_OF_STRING_RE.finditer(text):
        parts.append({
            "kind": "part_of",
            "source": m.group(2),
            "lineno": _line_of(m.start(), line_byte_starts),
        })
    for m in _DART_PART_OF_LIB_RE.finditer(text):
        parts.append({
            "kind": "part_of_library",
            "source": m.group(1),
            "lineno": _line_of(m.start(), line_byte_starts),
        })

    # Deterministic ordering.
    imports.sort(key=lambda x: (x["lineno"], x["source"], x.get("conditional", False)))
    parts.sort(key=lambda x: (x["lineno"], x["source"]))
    return imports, parts


def _emit_items(text: str, content: bytes) -> list[dict]:
    """Walk class-like declarations and emit per-item descriptors.

    Each item is a dict with:

      * ``kind``        : ``"class"`` | ``"mixin"`` | ``"enum"`` |
                          ``"extension"`` | ``"typedef"`` | ``"function"`` |
                          ``"method"`` | ``"getter"`` | ``"setter"`` |
                          ``"constructor"`` | ``"operator"``
      * ``name``        : declaration name (or ``ClassName.namedCtor`` for
                          named constructors)
      * ``parent``      : enclosing class name for methods, else ``None``
      * ``line_start``  : 1-indexed
      * ``line_end``    : 1-indexed inclusive
      * ``byte_start``  : inclusive
      * ``byte_end``    : exclusive
    """
    items: list[dict] = []
    line_byte_starts = _line_byte_starts(content)

    # Class-like declarations. We find every match; for those that open
    # a body (`{`), we'll later walk the body for methods.
    class_spans: list[tuple[str, int, int]] = []  # (class_name, body_open, body_close)

    for m in _DART_CLASS_DECL_RE.finditer(text):
        keyword = m.group("keyword").strip()
        name = m.group("name")
        delim = m.group("delim")
        decl_byte_start = m.start()
        decl_line_start = _line_of(decl_byte_start, line_byte_starts)

        if delim == ";":
            # Forward typedef or extension-on-form without body — span ends at `;`.
            decl_byte_end = m.end()
            decl_line_end = _line_of(decl_byte_end - 1, line_byte_starts)
            kind = "typedef" if "typedef" in keyword else "class"
            items.append({
                "kind": kind,
                "name": name,
                "parent": None,
                "line_start": decl_line_start,
                "line_end": decl_line_end,
                "byte_start": decl_byte_start,
                "byte_end": decl_byte_end,
            })
            continue

        # `{` — has a body.
        body_open = m.end() - 1  # index of `{` itself
        body_close = _find_matching_brace(text, body_open)
        decl_byte_end = body_close + 1
        decl_line_end = _line_of(decl_byte_end - 1, line_byte_starts)
        if keyword.startswith("extension"):
            kind = "extension"
        elif keyword == "mixin":
            kind = "mixin"
        elif keyword == "enum":
            kind = "enum"
        else:
            kind = "class"
        items.append({
            "kind": kind,
            "name": name,
            "parent": None,
            "line_start": decl_line_start,
            "line_end": decl_line_end,
            "byte_start": decl_byte_start,
            "byte_end": decl_byte_end,
        })
        if kind in ("class", "mixin", "extension"):
            class_spans.append((name, body_open, body_close))

    # Methods inside class bodies.
    for class_name, body_open, body_close in class_spans:
        body = text[body_open + 1: body_close]
        body_offset = body_open + 1
        # Track byte ranges already claimed by an emitted item so we don't
        # mistake a body-internal `name(...)` call (e.g. `print(value);`)
        # for a fresh method declaration on a later iteration.
        claimed_ranges: list[tuple[int, int]] = []

        def _claimed(idx: int) -> bool:
            for a, b in claimed_ranges:
                if a <= idx < b:
                    return True
            return False

        # First pass: getters/setters/operators. These are unambiguous
        # because the `get`/`set`/`operator` keyword anchors the match.
        for m in _DART_GETSET_RE.finditer(body):
            decl_start = m.start()
            if _claimed(body_offset + decl_start):
                continue
            kw = m.group("kw")
            name = m.group("name")
            byte_start = body_offset + decl_start
            line_start = _line_of(byte_start, line_byte_starts)
            # Walk to body / `;`.
            end_in_body = _scan_member_end(body, m.end() - 1)
            byte_end = body_offset + end_in_body
            line_end = _line_of(byte_end - 1, line_byte_starts)
            if kw == "get":
                kind = "getter"
                item_name = name
            elif kw == "set":
                kind = "setter"
                item_name = name
            else:  # operator
                kind = "operator"
                item_name = f"operator {name}"
            items.append({
                "kind": kind,
                "name": item_name,
                "parent": class_name,
                "line_start": line_start,
                "line_end": line_end,
                "byte_start": byte_start,
                "byte_end": byte_end,
            })
            claimed_ranges.append((decl_start, end_in_body))

        # Second pass: ordinary methods + constructors.
        for m in _DART_METHOD_RE.finditer(body):
            decl_start = m.start()
            if _claimed(decl_start):
                continue
            name = m.group("name")
            # Drop control-flow keywords matched as identifiers.
            simple_name = name.split(".", 1)[0]
            if simple_name in _DART_FUNC_BLACKLIST or simple_name == "":
                continue
            mods = (m.group("mods") or "")
            ret = (m.group("ret") or "").strip()
            byte_start = body_offset + decl_start
            line_start = _line_of(byte_start, line_byte_starts)
            # Walk past parens then to body delimiter.
            paren_open = m.end() - 1  # index of `(` within body
            j = paren_open
            depth = 0
            n_body = len(body)
            while j < n_body:
                ch = body[j]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            end_in_body = _scan_member_end(body, j)
            byte_end = body_offset + end_in_body
            line_end = _line_of(byte_end - 1, line_byte_starts)

            if "factory" in mods:
                kind = "constructor"
                # Named factories: `factory ClassName.named()` — regex
                # captured `ClassName.named`. Default factory: just the
                # bare class name.
                if name == class_name:
                    item_name = class_name
                elif name.startswith(class_name + "."):
                    item_name = name  # already "ClassName.named"
                else:
                    item_name = f"{class_name}.{name}"
            elif "." in name and name.split(".", 1)[0] == class_name:
                # Named constructor `ClassName.foo(...)`.
                kind = "constructor"
                item_name = name
            elif name == class_name and not ret:
                kind = "constructor"
                item_name = class_name
            else:
                kind = "method"
                item_name = name

            items.append({
                "kind": kind,
                "name": item_name,
                "parent": class_name,
                "line_start": line_start,
                "line_end": line_end,
                "byte_start": byte_start,
                "byte_end": byte_end,
            })
            claimed_ranges.append((decl_start, end_in_body))

    # Top-level free functions: scan only the regions OUTSIDE class bodies.
    # We build an "exclusion mask" of class-body ranges to skip.
    excluded: list[tuple[int, int]] = [
        (b_open, b_close + 1) for _name, b_open, b_close in class_spans
    ]
    excluded.sort()

    def _in_excluded(idx: int) -> bool:
        for a, b in excluded:
            if a <= idx < b:
                return True
            if idx < a:
                return False
        return False

    for m in _DART_TOP_FUNC_RE.finditer(text):
        name = m.group("name")
        if name in _DART_FUNC_BLACKLIST:
            continue
        decl_start = m.start("name")
        if _in_excluded(decl_start):
            continue
        # Require the match to begin at column 0 (top-level).
        # _DART_TOP_FUNC_RE is anchored at `^` so this is guaranteed,
        # but defensive: ensure no leading whitespace.
        line_start = _line_of(decl_start, line_byte_starts)
        # Walk parens then body.
        paren_open = m.end() - 1
        j = paren_open
        depth = 0
        n = len(text)
        while j < n:
            ch = text[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        # Find body open: `{`, `;`, or `=>`.
        k = j
        while k < n and text[k] not in "{;":
            if text[k] == "=" and k + 1 < n and text[k + 1] == ">":
                break
            k += 1
        if k >= n:
            continue
        if text[k] == "{":
            close = _find_matching_brace(text, k)
            byte_end = close + 1
        elif text[k] == ";":
            byte_end = k + 1
        else:
            end = text.find(";", k)
            byte_end = end + 1 if end != -1 else n
        byte_start = m.start()  # include any return-type prefix on this line
        # Trim leading whitespace from the start so spans are clean.
        while byte_start < n and text[byte_start] in " \t":
            byte_start += 1
        line_end = _line_of(byte_end - 1, line_byte_starts)

        items.append({
            "kind": "function",
            "name": name,
            "parent": None,
            "line_start": line_start,
            "line_end": line_end,
            "byte_start": byte_start,
            "byte_end": byte_end,
        })

    items.sort(key=lambda x: (x["line_start"], x["kind"], x.get("parent") or "", x["name"]))
    return items


def extract_dart_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    """Return ``(summary, errors)`` for a Dart source file.

    The summary's contract matches the SPEC's first-class shape plus an
    ``items`` array carrying per-symbol spans (consumed by the L2 chunker
    and symbol-xref resolver).
    """
    try:
        text_raw = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]
    # Strip strings/comments before declaration scanning so `class Foo`
    # mentioned inside a doc-comment doesn't trigger a false match. But
    # *imports* are themselves quoted strings, so they have to be parsed
    # on the raw text — otherwise the stripping erases the very content
    # we need to read.
    text = _strip_strings_and_comments(text_raw)
    imports, parts = _emit_imports(text_raw, content)
    items = _emit_items(text, content)

    top_level_functions = sorted({
        it["name"] for it in items if it["kind"] == "function"
    })
    top_level_classes = sorted({
        it["name"] for it in items
        if it["kind"] in ("class", "mixin", "enum", "extension", "typedef")
    })

    summary = {
        "language": "dart",
        "extraction_method": "regex",
        "imports": imports,
        "parts": parts,
        "top_level_functions": top_level_functions,
        "top_level_classes": top_level_classes,
        "items": items,
    }
    return summary, []


# ---------------------------------------------------------------------------
# Multi-package detection
# ---------------------------------------------------------------------------


def detect_dart_packages(
    records: list[FileRecord], read: Callable[[str], bytes],
) -> dict[str, str]:
    """Return ``{package_dir: package_name}`` for every ``pubspec.yaml``
    found in the repo. ``package_dir`` is the POSIX directory containing
    the pubspec; ``package_name`` is the ``name:`` field's value.

    A repo with one pubspec at the root yields ``{"": "<name>"}``.
    A monorepo with workspaces at ``packages/foo/`` and ``packages/bar/``
    yields ``{"packages/foo": "foo", "packages/bar": "bar"}``.

    The map is the canonical Dart workspace index; the resolver uses
    ``dart_package_for_path`` to pick the nearest enclosing entry.
    """
    if not YAML_AVAILABLE:
        return {}
    out: dict[str, str] = {}
    for r in records:
        if PurePosixPath(r.path).name != "pubspec.yaml":
            continue
        try:
            data = yaml.safe_load(read(r.path))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        if not isinstance(name, str) or not name:
            continue
        pkg_dir = str(PurePosixPath(r.path).parent)
        if pkg_dir == ".":
            pkg_dir = ""
        out[pkg_dir] = name
    return out


def detect_dart_package_name(
    records: list[FileRecord], read: Callable[[str], bytes],
) -> str | None:
    """Back-compat shim: return the package name of the shallowest
    pubspec.yaml. Preserved so any external caller using the old
    scalar ``host:dart_pkg_name`` continues to work.
    """
    pkgs = detect_dart_packages(records, read)
    if not pkgs:
        return None
    # Shallowest = fewest path separators in the package dir; "" beats all.
    shallowest = min(pkgs.keys(), key=lambda d: (d.count("/"), d))
    return pkgs[shallowest]


def dart_package_for_path(
    src_path: str, packages: dict[str, str],
) -> tuple[str, str] | None:
    """Pick the *nearest enclosing* package for ``src_path``.

    Returns ``(package_dir, package_name)`` or ``None`` if no package
    contains the file. Used by the resolver to:

      1. Decide whether ``package:foo/...`` is in-repo (foo is some
         workspace package) and which directory to anchor it at.
      2. Resolve relative imports inside ``lib/`` correctly when
         multiple packages coexist.
    """
    if not packages:
        return None
    best: tuple[str, str] | None = None
    best_depth = -1
    for pkg_dir in packages:
        if pkg_dir == "":
            depth = 0
            prefix = ""
        else:
            prefix = pkg_dir + "/"
            depth = pkg_dir.count("/") + 1
        if pkg_dir == "" or src_path.startswith(prefix):
            if depth > best_depth:
                best = (pkg_dir, packages[pkg_dir])
                best_depth = depth
    return best


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


def resolve_dart_imports(
    src_path: str,
    summary: dict,
    packages: dict[str, str] | str | None,
    paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """Resolve imports + part directives to ``(in_repo, external)``.

    ``packages`` may be:

      * ``dict[str, str]``  — the new multi-package index
        (``host:dart_packages``).
      * ``str``             — the legacy single package name
        (``host:dart_pkg_name``); treated as ``{"": name}``.
      * ``None``            — no pubspec detected.

    The dual signature keeps back-compat with the pre-Tier-1 host
    index while letting the new pipeline pass the rich map.
    """
    if isinstance(packages, str):
        packages_map: dict[str, str] = {"": packages}
    elif isinstance(packages, dict):
        packages_map = packages
    else:
        packages_map = {}

    enclosing = dart_package_for_path(src_path, packages_map)
    package_name_by_name: dict[str, str] = {v: k for k, v in packages_map.items()}

    dst: set[str] = set()
    unresolved: set[str] = set()
    src_dir = PurePosixPath(src_path).parent

    # Both `imports` (import/export) and `parts` (part directives) flow
    # through the same resolution path — they all reference a file.
    for imp in list(summary.get("imports", [])) + list(summary.get("parts", [])):
        spec = imp["source"]
        kind = imp.get("kind", "import")

        if spec.startswith("dart:"):
            unresolved.add(spec)
            continue

        if spec.startswith("package:"):
            body = spec[len("package:"):]
            pkg, _, rest = body.partition("/")
            if pkg in package_name_by_name:
                pkg_dir = package_name_by_name[pkg]
                base = f"{pkg_dir}/lib/" if pkg_dir else "lib/"
                target = base + rest
                if target in paths_set:
                    dst.add(target)
                    continue
                # `package:foo/foo.dart` is the canonical re-export entry.
                # If the precise path doesn't exist we still treat the
                # package as in-repo (no external pin); fall through to
                # adding the package as a known-but-unresolved internal.
                unresolved.add(pkg)
            else:
                unresolved.add(pkg)
            continue

        if kind == "part_of_library":
            # `part of library_id;` — library name, not a path. No edge.
            continue

        # Relative path (or `part of '...'` with a path).
        raw = src_dir / spec
        target = _normalize_rel(raw.parts)
        if target in paths_set:
            dst.add(target)
            continue
        # No package-anchored fallback today — relative imports that don't
        # resolve are silently dropped. (Matches the C / Kotlin behaviour
        # of "if it's not where the spec says, don't guess".)

    # Drop self-reference.
    dst.discard(src_path)
    return sorted(dst), sorted(unresolved)
