"""Dart symbol-xref resolver (Tier-1 Stage 2).

Edge kinds covered: ``calls``, ``subclassOf``, ``overrides``.

Resolver names on emitted edges:

  - ``dart_intra_file`` — target resolves to a top-level item in the
    same source file.
  - ``dart_inter_file`` — target resolves via a Dart ``import``/``part``
    whose module path lands on an in-repo file.

Call shapes in scope:

  - Bare identifier call: ``foo()``                        [most common]
  - Constructor call:    ``MyClass()`` / ``MyClass.named()``
  - Static method call:  ``MyClass.method()``  (resolved as a call on
                         the class chunk; the method itself isn't bound
                         here — same posture as the Rust resolver)

Call shapes deliberately out of scope (Stage 2 narrowing):

  - Receiver-method calls: ``obj.method()``      [requires type inference]
  - Cascade chains:        ``..foo()..bar()``
  - Function literals:     ``(() => x)()``
  - Generic invocations:   ``foo<T>()`` — strip the type args; the
                           regex skims any ``<...>`` between the name
                           and ``(``.

Unresolved is data: populated only for names that are import-bound but
whose target file/symbol couldn't be located. Bare names that aren't
imported or defined locally (Dart core like ``print``, ``identical``,
captured locals) are silently ignored.

Out of scope (deferred):

  - Mixin application via ``with M``: emits no edge.
  - ``implements I``: emits a subclassOf edge with ``resolution="heuristic"``.
  - Conditional imports: the unconditional branch wins for lookup; if-branches
    are still recorded by the analyzer's import list but the resolver picks
    the first matching binding only (matches the Python resolver's posture
    on multiple imports of the same name).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from codebase_mapper.emission.models import SymbolXrefEdge, UnresolvedSymbolRef
from codebase_mapper.inspection.models import FileRecord
from codebase_mapper.inspection.languages.dart import (
    _normalize_rel, dart_package_for_path,
)


RESOLVER_INTRA = "dart_intra_file"
RESOLVER_INTER = "dart_inter_file"

_GLOBAL_TARGETS_CACHE_KEY = "_xrefs_dart_global_targets"
_GLOBAL_METHODS_CACHE_KEY = "_xrefs_dart_global_methods"


# A call-site or base-name candidate: an identifier followed by `(` or part
# of a class-extends clause. The regex purposefully tolerates generics:
# `Foo<int>(` and `Foo<int, String>(`.
_CALL_RE = re.compile(
    r"(?<![A-Za-z0-9_$.])(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:<[^>;]+>)?\s*\(",
)

# `extends Base` / `implements Base1, Base2` / `with Mixin1, Mixin2` at the
# class header. Captures everything until the body opens.
_EXTENDS_RE = re.compile(r"\bextends\s+([A-Z][A-Za-z0-9_$]*)")
_IMPLEMENTS_RE = re.compile(r"\bimplements\s+([A-Z][A-Za-z0-9_$<>,\s]*?)(?=\s*(?:with|extends|\{))")


@dataclass(frozen=True)
class _DartImport:
    """A resolved Dart import binding.

    ``target_path``  — the in-repo file imported (or ``None`` for SDK /
                        external).
    ``alias``        — the ``as`` clause name (binds the namespace), or
                        ``None`` if no alias.
    """
    target_path: str | None
    alias: str | None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def resolve_dart_calls(
    record: FileRecord, ctx: PipelineCtx,
) -> tuple[list[SymbolXrefEdge], list[UnresolvedSymbolRef]]:
    if record.language != "dart":
        return [], []
    summary = record.ast_summary or {}
    items = summary.get("items") or []
    if not items:
        return [], []

    chunks_in_file = [
        c for c in cast(list, ctx.indices.get("l2_10_chunks", []))
        if c.get("path") == record.path
    ]
    if not chunks_in_file:
        return [], []

    paths_set = ctx.paths_set
    packages = ctx.indices.get("host:dart_packages") or {}
    if not packages:
        legacy = ctx.indices.get("host:dart_pkg_name")
        if isinstance(legacy, str):
            packages = {"": legacy}

    # Resolve imports → list of _DartImport entries.
    imports = _resolve_imports_for_file(
        record.path, summary.get("imports") or [], packages, paths_set,
    )

    # Same-file maps.
    src_lookup = _source_chunk_lookup(chunks_in_file)
    intra_targets = _top_level_targets(chunks_in_file)

    # Global maps (cached across resolver invocations).
    global_targets = _global_targets_cache(ctx)
    global_methods = _global_methods_cache(ctx)

    edges: list[SymbolXrefEdge] = []
    unresolved: list[UnresolvedSymbolRef] = []

    # ------- subclassOf + overrides --------------------------------------
    classes_by_name = {it["name"]: it for it in items if it["kind"] == "class"}
    file_text_cache: str | None = None
    for cls_item in classes_by_name.values():
        src_id = src_lookup.get((cls_item["name"], None))
        if src_id is None:
            continue
        if file_text_cache is None:
            try:
                file_text_cache = ctx.read_path(record.path).decode("utf-8", "replace")
            except Exception:
                file_text_cache = ""
        cls_header = file_text_cache[cls_item["byte_start"]: cls_item["byte_start"] + 512]
        resolved_bases = []
        for m in _EXTENDS_RE.finditer(cls_header):
            base = m.group(1)
            resolved = _resolve_class_target(
                base, record.path, intra_targets, imports, global_targets,
            )
            if resolved is None:
                continue
            dst_id, kind = resolved
            edges.append(SymbolXrefEdge(
                src_chunk_id=src_id, dst_chunk_id=dst_id,
                kind="subclassOf", resolution="exact",
                resolver=RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER,
            ))
            base_path = record.path if kind == "intra" else _path_for_target(
                base, imports, global_targets,
            )
            if base_path:
                resolved_bases.append((base_path, base, kind))
        # implements: heuristic (Dart implements is structural, not nominal).
        for m in _IMPLEMENTS_RE.finditer(cls_header):
            for raw in m.group(1).split(","):
                base = raw.strip()
                base = re.sub(r"<.*$", "", base).strip()
                if not base or not base[0].isupper():
                    continue
                resolved = _resolve_class_target(
                    base, record.path, intra_targets, imports, global_targets,
                )
                if resolved is None:
                    continue
                dst_id, kind = resolved
                edges.append(SymbolXrefEdge(
                    src_chunk_id=src_id, dst_chunk_id=dst_id,
                    kind="subclassOf", resolution="heuristic",
                    resolver=RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER,
                ))
                base_path = record.path if kind == "intra" else _path_for_target(
                    base, imports, global_targets,
                )
                if base_path:
                    resolved_bases.append((base_path, base, kind))

        # Overrides: any method on this class whose name matches a method
        # on a resolved base emits one edge per base.
        if resolved_bases:
            class_methods = [
                it for it in items
                if it.get("parent") == cls_item["name"]
                and it["kind"] in ("method", "getter", "setter")
            ]
            for meth in class_methods:
                # Use the chunk's actual symbol form (e.g. "get balance")
                # for getters/setters.
                if meth["kind"] == "getter":
                    sym = f"get {meth['name']}"
                elif meth["kind"] == "setter":
                    sym = f"set {meth['name']}"
                else:
                    sym = meth["name"]
                method_chunk_id = src_lookup.get((sym, cls_item["name"]))
                if method_chunk_id is None:
                    continue
                for base_path, base_class, kind in resolved_bases:
                    base_method = global_methods.get((base_path, base_class, sym))
                    if base_method is None:
                        continue
                    edges.append(SymbolXrefEdge(
                        src_chunk_id=method_chunk_id,
                        dst_chunk_id=base_method,
                        kind="overrides", resolution="exact",
                        resolver=RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER,
                    ))

    # ------- calls --------------------------------------------------------
    # For each callable item, scan its body text for bare-name call sites.
    if file_text_cache is None:
        try:
            file_text_cache = ctx.read_path(record.path).decode("utf-8", "replace")
        except Exception:
            file_text_cache = ""

    callable_items = [
        it for it in items
        if it["kind"] in ("function", "method", "constructor",
                          "getter", "setter")
    ]
    for it in callable_items:
        body_text = file_text_cache[it["byte_start"]: it["byte_end"]]
        # Skip the header by jumping past the first `{` or `=>`.
        body_open = _find_body_open(body_text)
        scan_text = body_text[body_open:] if body_open >= 0 else body_text
        if it["kind"] == "getter":
            sym = f"get {it['name']}"
        elif it["kind"] == "setter":
            sym = f"set {it['name']}"
        else:
            sym = it["name"]
        src_id = src_lookup.get((sym, it.get("parent")))
        if src_id is None:
            continue
        seen: set[str] = set()
        for cm in _CALL_RE.finditer(scan_text):
            name = cm.group("name")
            if name == sym or name in _DART_NONCALL_KEYWORDS:
                continue
            if name in seen:
                continue
            seen.add(name)
            resolved = _resolve_call_target(
                name, record.path, intra_targets, imports, global_targets,
            )
            if resolved is None:
                # Only surface as unresolved if the name *is* an imported
                # binding whose target couldn't be located. Otherwise it's
                # probably a local / builtin / dart-core call — silent.
                imp = _find_import_for_name(name, imports)
                if imp is None:
                    continue
                unresolved.append(UnresolvedSymbolRef(
                    src_chunk_id=src_id,
                    raw_target=name,
                    kind="calls",
                    reason="symbol_not_exported",
                    resolver=RESOLVER_INTER,
                ))
                continue
            dst_id, kind = resolved
            edges.append(SymbolXrefEdge(
                src_chunk_id=src_id, dst_chunk_id=dst_id,
                kind="calls", resolution="exact",
                resolver=RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER,
            ))

    return edges, unresolved


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Identifiers that the call-regex will match but that are control-flow,
# not callable bindings.
_DART_NONCALL_KEYWORDS = {
    "if", "for", "while", "switch", "return", "throw", "assert", "rethrow",
    "yield", "await", "async", "case", "catch", "do", "try", "in", "is",
    "as", "of", "with", "extends", "implements", "super", "this", "new",
    "and", "or", "not", "true", "false", "null", "void",
    "Function", "List", "Map", "Set", "Iterable", "Future", "Stream",
    "String", "int", "double", "bool", "num", "dynamic", "Object",
    # Common SDK calls that we don't try to bind.
    "print", "identical", "identityHashCode",
}


def _find_body_open(text: str) -> int:
    """Index of the first ``{`` or first ``=>`` after the parameter list.

    Returns ``-1`` if no body delimiter is found (abstract / declaration-only).
    """
    # Skip past initial signature: first `{`, `;`, or `=>`.
    depth = 0
    n = len(text)
    i = 0
    # Walk past the parameter list parens.
    paren_seen = False
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
            paren_seen = True
        elif ch == ")":
            depth -= 1
            if depth == 0 and paren_seen:
                i += 1
                break
        i += 1
    while i < n and text[i] not in "{;":
        if text[i] == "=" and i + 1 < n and text[i + 1] == ">":
            return i + 2
        i += 1
    if i >= n:
        return -1
    if text[i] == "{":
        return i + 1
    return -1


def _resolve_imports_for_file(
    src_path: str,
    imports_list: list[dict],
    packages: dict[str, str],
    paths_set: set[str],
) -> list[_DartImport]:
    """Resolve every Dart import to a target file (or None for external).

    The resulting list preserves the import order. Aliases (``as foo``)
    are not captured by the analyzer yet, so this v1 implementation leaves
    ``alias=None`` — namespace-qualified lookup (``foo.bar()``) is a
    follow-up.
    """
    name_by_pkg = {v: k for k, v in packages.items()}
    src_dir = PurePosixPath(src_path).parent
    out: list[_DartImport] = []
    for imp in imports_list:
        spec = imp["source"]
        if imp.get("kind") not in ("import", "export"):
            continue
        if spec.startswith("dart:"):
            out.append(_DartImport(target_path=None, alias=None))
            continue
        if spec.startswith("package:"):
            body = spec[len("package:"):]
            pkg, _, rest = body.partition("/")
            if pkg in name_by_pkg:
                pkg_dir = name_by_pkg[pkg]
                base = f"{pkg_dir}/lib/" if pkg_dir else "lib/"
                target = base + rest
                if target in paths_set:
                    out.append(_DartImport(target_path=target, alias=None))
                    continue
            out.append(_DartImport(target_path=None, alias=None))
            continue
        # Relative
        raw = src_dir / spec
        target = _normalize_rel(raw.parts)
        if target in paths_set:
            out.append(_DartImport(target_path=target, alias=None))
        else:
            out.append(_DartImport(target_path=None, alias=None))
    return out


def _top_level_targets(chunks_in_file: list[dict]) -> dict[str, str]:
    """Map top-level symbol → chunk_id for this file."""
    out: dict[str, tuple[str, int]] = {}
    for c in chunks_in_file:
        if c.get("parent_symbol") is not None:
            continue
        if c.get("kind") not in {"function", "class"}:
            continue
        sym = c["symbol"]
        line = c.get("line_start", 0)
        if sym not in out or line > out[sym][1]:
            out[sym] = (c["chunk_id"], line)
    return {sym: cid for sym, (cid, _line) in out.items()}


def _source_chunk_lookup(chunks_in_file: list[dict]) -> dict[tuple[str, str | None], str]:
    """(symbol, parent) → chunk_id for every chunk in this file."""
    out: dict[tuple[str, str | None], str] = {}
    for c in chunks_in_file:
        if c.get("kind") not in {"function", "method", "class"}:
            continue
        out[(c["symbol"], c.get("parent_symbol"))] = c["chunk_id"]
    return out


def _global_targets_cache(ctx: PipelineCtx) -> dict[tuple[str, str], str]:
    """(path, top_level_symbol) → chunk_id across all Dart files in the bundle."""
    cached = ctx.scratch.get(_GLOBAL_TARGETS_CACHE_KEY)
    if cached is not None:
        return cast(dict, cached)
    out: dict[tuple[str, str], str] = {}
    by_key_line: dict[tuple[str, str], int] = {}
    for c in cast(list, ctx.indices.get("l2_10_chunks", [])):
        if c.get("parent_symbol") is not None:
            continue
        if c.get("kind") not in {"function", "class"}:
            continue
        key = (c["path"], c["symbol"])
        line = c.get("line_start", 0)
        if key not in by_key_line or line > by_key_line[key]:
            out[key] = c["chunk_id"]
            by_key_line[key] = line
    ctx.scratch[_GLOBAL_TARGETS_CACHE_KEY] = out
    return out


def _global_methods_cache(ctx: PipelineCtx) -> dict[tuple[str, str, str], str]:
    """(path, class_name, method_symbol) → chunk_id for methods/getters/setters."""
    cached = ctx.scratch.get(_GLOBAL_METHODS_CACHE_KEY)
    if cached is not None:
        return cast(dict, cached)
    out: dict[tuple[str, str, str], str] = {}
    by_key_line: dict[tuple[str, str, str], int] = {}
    for c in cast(list, ctx.indices.get("l2_10_chunks", [])):
        if c.get("kind") != "method":
            continue
        parent = c.get("parent_symbol")
        if parent is None:
            continue
        key = (c["path"], parent, c["symbol"])
        line = c.get("line_start", 0)
        if key not in by_key_line or line > by_key_line[key]:
            out[key] = c["chunk_id"]
            by_key_line[key] = line
    ctx.scratch[_GLOBAL_METHODS_CACHE_KEY] = out
    return out


def _resolve_call_target(
    name: str,
    record_path: str,
    intra: dict[str, str],
    imports: list[_DartImport],
    global_targets: dict[tuple[str, str], str],
) -> tuple[str, str] | None:
    """Resolve a bare-name call to ``(chunk_id, "intra"|"inter")``.

    Intra-file wins over imports — same posture as Python/Rust/TS resolvers.
    """
    if name in intra:
        return intra[name], "intra"
    for imp in imports:
        if imp.target_path is None:
            continue
        cid = global_targets.get((imp.target_path, name))
        if cid is not None:
            return cid, "inter"
    return None


def _resolve_class_target(
    name: str,
    record_path: str,
    intra: dict[str, str],
    imports: list[_DartImport],
    global_targets: dict[tuple[str, str], str],
) -> tuple[str, str] | None:
    """Same as ``_resolve_call_target`` but for class-typed names."""
    return _resolve_call_target(name, record_path, intra, imports, global_targets)


def _path_for_target(
    name: str,
    imports: list[_DartImport],
    global_targets: dict[tuple[str, str], str],
) -> str | None:
    for imp in imports:
        if imp.target_path is None:
            continue
        if (imp.target_path, name) in global_targets:
            return imp.target_path
    return None


def _find_import_for_name(name: str, imports: list[_DartImport]) -> _DartImport | None:
    """Return any import whose target file exists in the bundle.

    Without per-name show/hide tracking we can only say "some import
    could plausibly bind this name". Sufficient for the unresolved
    diagnostic; over-emitting is preferable to silence here.
    """
    for imp in imports:
        if imp.target_path is not None:
            return imp
    return None
