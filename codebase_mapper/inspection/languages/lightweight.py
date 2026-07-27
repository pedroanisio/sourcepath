"""Line-oriented extractors for the E2 families (error-free-mapping plan).

asm / Kconfig / DeviceTree / Make are simple, line-structured languages —
regex extraction delivers correct symbols and imports without a grammar
dependency. Each extractor returns the same ``(summary, errors)`` contract
as the tree-sitter analyzers: ``items`` carry kind/name/byte+line spans (so
L2 chunking and the symbol surface work unchanged), ``imports`` carry
file-reference edges. A tree-sitter grammar upgrade (verified available on
PyPI for devicetree/kconfig/make) is a drop-in swap behind the same
functions.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

_ASM_LABEL_RE = re.compile(rb"^\s*([A-Za-z_.$][\w.$]*):")
_ASM_ENTRY_RE = re.compile(
    rb"^\s*(?:ENTRY|SYM_FUNC_START(?:_LOCAL|_WEAK)?|SYM_CODE_START(?:_LOCAL)?)"
    rb"\s*\(\s*([\w.$]+)\s*\)")
_ASM_GLOBL_RE = re.compile(rb"^\s*\.glob(?:a)?l\s+([\w.$]+)")
_ASM_INCLUDE_RE = re.compile(rb"^\s*\.include\s+\"([^\"]+)\"")

_KCONFIG_CONFIG_RE = re.compile(rb"^(?:menu)?config\s+([A-Za-z0-9_]+)\s*$")
_KCONFIG_SOURCE_RE = re.compile(rb"^\s*(?:o?source)\s+\"([^\"]+)\"")

_DTS_NODE_RE = re.compile(
    rb"^\s*((?:[\w-]+:\s*)?[/\w,.+-]+(?:@[\w,.+-]+)?)\s*\{")
_DTS_INCLUDE_RE = re.compile(
    rb"^\s*(?:/include/|#include)\s+\"([^\"]+)\"")

_MAKE_TARGET_RE = re.compile(rb"^([A-Za-z0-9][\w./%-]*)\s*:(?!=)")
_MAKE_INCLUDE_RE = re.compile(rb"^-?include\s+(.+)$")


def _iter_lines(content: bytes):
    """(lineno, byte_start, byte_end, line) for each line."""
    offset = 0
    for i, line in enumerate(content.splitlines(keepends=True), 1):
        stripped = line.rstrip(b"\r\n")
        yield i, offset, offset + len(stripped), stripped
        offset += len(line)


def _item(kind: str, name: str, lineno: int, start: int, end: int) -> dict:
    return {"kind": kind, "name": name,
            "line_start": lineno, "line_end": lineno,
            "byte_start": start, "byte_end": end}


def _summary(language: str, items: list[dict], imports: list[dict]) -> dict:
    items.sort(key=lambda x: (x["line_start"], x["kind"], x["name"]))
    imports.sort(key=lambda x: (x["lineno"], x["source"]))
    summary = {"language": language, "imports": imports, "items": items}
    if not items and not imports:
        summary["zero_symbol_reason"] = "no_declarations_found"
    return summary


def extract_asm_summary(content: bytes, path: str) -> tuple[dict, list[str]]:
    items: list[dict] = []
    imports: list[dict] = []
    for lineno, start, end, line in _iter_lines(content):
        m = _ASM_ENTRY_RE.match(line)
        if m:
            items.append(_item("function", m.group(1).decode("ascii", "replace"),
                               lineno, start, end))
            continue
        m = _ASM_LABEL_RE.match(line)
        if m:
            name = m.group(1).decode("ascii", "replace")
            if not name.startswith("."):  # local numeric/dot labels are noise
                items.append(_item("label", name, lineno, start, end))
            continue
        m = _ASM_GLOBL_RE.match(line)
        if m:
            items.append(_item("global", m.group(1).decode("ascii", "replace"),
                               lineno, start, end))
            continue
        m = _ASM_INCLUDE_RE.match(line)
        if m:
            imports.append({"kind": "asm_include",
                            "source": m.group(1).decode("utf-8", "replace"),
                            "lineno": lineno})
    return _summary("asm", items, imports), []


def extract_kconfig_summary(content: bytes, path: str) -> tuple[dict, list[str]]:
    items: list[dict] = []
    imports: list[dict] = []
    for lineno, start, end, line in _iter_lines(content):
        m = _KCONFIG_CONFIG_RE.match(line)
        if m:
            items.append(_item("config", m.group(1).decode("ascii", "replace"),
                               lineno, start, end))
            continue
        m = _KCONFIG_SOURCE_RE.match(line)
        if m:
            imports.append({"kind": "kconfig_source",
                            "source": m.group(1).decode("utf-8", "replace"),
                            "lineno": lineno})
    return _summary("kconfig", items, imports), []


def extract_devicetree_summary(content: bytes, path: str) -> tuple[dict, list[str]]:
    items: list[dict] = []
    imports: list[dict] = []
    for lineno, start, end, line in _iter_lines(content):
        m = _DTS_NODE_RE.match(line)
        if m:
            name = m.group(1).decode("utf-8", "replace")
            name = re.sub(r":\s+", ": ", name)
            if name != "/":  # the root node is structure, not a symbol
                items.append(_item("node", name, lineno, start, end))
            continue
        m = _DTS_INCLUDE_RE.match(line)
        if m:
            imports.append({"kind": "dts_include",
                            "source": m.group(1).decode("utf-8", "replace"),
                            "lineno": lineno})
    return _summary("devicetree", items, imports), []


def extract_make_summary(content: bytes, path: str) -> tuple[dict, list[str]]:
    items: list[dict] = []
    imports: list[dict] = []
    for lineno, start, end, line in _iter_lines(content):
        m = _MAKE_INCLUDE_RE.match(line)
        if m:
            for token in m.group(1).split():
                imports.append({"kind": "make_include",
                                "source": token.decode("utf-8", "replace"),
                                "lineno": lineno})
            continue
        m = _MAKE_TARGET_RE.match(line)
        if m:
            items.append(_item("target", m.group(1).decode("utf-8", "replace"),
                               lineno, start, end))
    return _summary("make", items, imports), []


def _normalize_rel(parts: tuple[str, ...]) -> str:
    norm: list[str] = []
    for part in parts:
        if part == "..":
            if norm and norm[-1] != "..":
                norm.pop()
        elif part not in ("", "."):
            norm.append(part)
    return "/".join(norm)


def resolve_lightweight_imports(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """Resolve include directives to ``(in_repo, external)``.

    The four lightweight languages (asm ``.include``, Kconfig ``source``,
    devicetree ``/include/`` + ``#include``, make ``include``) all emit
    path-like specs under the same ``{kind, source, lineno}`` shape, and all
    four resolve the same way: relative to the including file's directory
    first, then repo-root-relative.

    They previously had analyzers but no ``ImportResolver``. The specs landed
    in ``ast_summary`` and stopped there — never becoming ``cbm:imports``
    edges and never disclosed as a gap. On a kernel-scale repository, where
    these four formats carry a large share of the build graph, that is a
    silent under-capture of exactly the kind the coverage machinery exists to
    surface.

    Unresolvable specs are surfaced as external rather than dropped: an
    unexpanded variable (``$(SRCTREE)``, ``$VAR``) or an absolute path cannot
    be resolved statically, and preserving the spec keeps the fact that a
    dependency exists.
    """
    src_dir = PurePosixPath(src_path).parent
    in_repo: set[str] = set()
    external: set[str] = set()

    for imp in summary.get("imports", []):
        spec = str(imp.get("source", "")).strip().strip("\"'<>")
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
