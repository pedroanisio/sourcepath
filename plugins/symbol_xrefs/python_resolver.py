"""Python xref resolver — calls (Phases 2, 4) + subclassOf / overrides (Phase 10).

One entry point, several edge kinds. Resolver name on each edge marks
the bind path:

  - ``python_intra_file`` — target resolves in the same module
  - ``python_inter_file`` — target resolves via ``from X import Y``

Edge kinds:

  - ``calls`` (Phase 2 / 4) — bare-Name call expression whose target is
    a top-level def/class or an imported binding.
  - ``subclassOf`` (Phase 10) — ``class Sub(Base):`` where ``Base`` is
    a same-file class or an imported binding. One edge per resolved
    base; multiple inheritance produces multiple edges.
  - ``overrides`` (Phase 10) — a method whose name matches a method in
    a resolved base class. One edge per (base) pair; if multiple bases
    define the same method, multiple override edges.

Resolution order for ``calls``: intra-file wins over import. Documented
in the original Phase 2 design — see the original module docstring for
context.

Unresolved is data: populated only for names that *were* import
bindings whose resolution failed. We don't add "unresolved" entries for
arbitrary unknown names (e.g. builtins, locals) — that would bury the
signal. Phase 10 follows the same rule for ``subclassOf``: a missing
``object``/builtin base class is silent; a missing imported base is
tracked.

Unresolved reasons (unchanged from Phase 4 — Phase 10 piggybacks):
  - ``module_not_in_repo`` — the imported module isn't in
    ``host:python_by_module`` / ``host:python_by_suffix``.
  - ``symbol_not_exported`` — the module is in repo but the target
    symbol isn't a top-level def/class in it.

Out of scope (deferred):
  - ``Attribute`` references for any kind (``obj.foo()``,
    ``pkg.Base``).
  - Function-local imports.
  - ``import X`` followed by ``X.foo()``.
  - ``from X import *``.
  - Metaclass-style ``class Sub(metaclass=Base)``.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from codebase_mapper.inspection.languages.python import _jsonable_to_ast
from codebase_mapper.emission.models import SymbolXrefEdge, UnresolvedSymbolRef
from codebase_mapper.inspection.models import FileRecord


RESOLVER_INTRA = "python_intra_file"
RESOLVER_INTER = "python_inter_file"
# Back-compat: Phase 2 exported a single RESOLVER_NAME constant; preserve it
# so any external import still works. Maps to the intra-file resolver.
RESOLVER_NAME = RESOLVER_INTRA

_GLOBAL_TARGETS_CACHE_KEY = "_xrefs_python_global_targets"
_GLOBAL_METHODS_CACHE_KEY = "_xrefs_python_global_methods"


@dataclass(frozen=True)
class _ImportBinding:
    """A `from <module> import <target_name> [as <local>]` binding."""
    module: str           # the module string ("" for `from . import X`)
    level: int            # number of leading dots (0 for absolute)
    target_name: str      # the name inside the imported module


def resolve_python_intra_file(
    record: FileRecord, ctx: PipelineCtx,
) -> tuple[list[SymbolXrefEdge], list[UnresolvedSymbolRef]]:
    ast_json = (record.ast_summary or {}).get("ast_json")
    if ast_json is None:
        return [], []
    chunks_in_file = [
        c for c in cast(list, ctx.indices.get("l2_10_chunks", []))
        if c.get("path") == record.path
    ]
    if not chunks_in_file:
        return [], []

    try:
        module = _jsonable_to_ast(ast_json)
    except Exception:
        return [], []
    if not isinstance(module, ast.Module):
        return [], []

    targets = _top_level_targets(chunks_in_file)
    src_lookup = _source_chunk_lookup(chunks_in_file)
    imports_map = _collect_imports(module)
    src_pkg = _source_package_parts(
        record.path,
        cast(list, ctx.indices.get("host:python_source_roots", [])),
    )
    global_targets = _global_targets_cache(ctx)
    global_methods = _global_methods_cache(ctx)
    by_module = cast(dict, ctx.indices.get("host:python_by_module", {}))
    by_suffix = cast(dict, ctx.indices.get("host:python_by_suffix", {}))

    edges: list[SymbolXrefEdge] = []
    unresolved: list[UnresolvedSymbolRef] = []
    _walk_module(
        module, edges, unresolved,
        record_path=record.path,
        targets=targets, src_lookup=src_lookup,
        imports_map=imports_map, src_pkg=src_pkg,
        global_targets=global_targets,
        global_methods=global_methods,
        by_module=by_module, by_suffix=by_suffix,
    )
    return edges, unresolved


# ---------------------------------------------------------------------------
# Chunk indices
# ---------------------------------------------------------------------------


def _top_level_targets(chunks_in_file: list[dict]) -> dict[str, str]:
    """Map a top-level symbol name → its chunk_id (same-file scope)."""
    by_symbol: dict[str, tuple[str, int]] = {}
    for c in chunks_in_file:
        if c.get("parent_symbol") is not None:
            continue
        if c.get("kind") not in {"function", "class"}:
            continue
        sym = c["symbol"]
        line = c.get("line_start", 0)
        if sym not in by_symbol or line > by_symbol[sym][1]:
            by_symbol[sym] = (c["chunk_id"], line)
    return {sym: cid for sym, (cid, _line) in by_symbol.items()}


def _source_chunk_lookup(chunks_in_file: list[dict]) -> dict[tuple[str, str | None], str]:
    """Map (symbol, parent_symbol) → chunk_id for every callable chunk."""
    out: dict[tuple[str, str | None], str] = {}
    for c in chunks_in_file:
        if c.get("kind") not in {"function", "method", "class"}:
            continue
        out[(c["symbol"], c.get("parent_symbol"))] = c["chunk_id"]
    return out


def _global_targets_cache(ctx: PipelineCtx) -> dict[tuple[str, str], str]:
    """(path, top_level_symbol) → chunk_id across the whole codebase.

    Cached on ctx.scratch so we only scan ``l2_10_chunks`` once per run.
    Phase 4 needs cross-file targets; this index is hot-path for any
    record with imports.
    """
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
    """(path, class_name, method_name) → chunk_id across the codebase.

    Phase 10's ``overrides`` lookup is the hot path here: given a
    resolved base class ``(base_path, base_name)`` and a candidate
    method name, we want to know whether that method exists. Same
    cache strategy as ``_global_targets_cache``: scan once, lazy.
    """
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


# ---------------------------------------------------------------------------
# Import bindings + package context
# ---------------------------------------------------------------------------


def _collect_imports(module: ast.Module) -> dict[str, _ImportBinding]:
    """Walk module-level ``from X import Y [as Z]`` statements.

    Returns a map ``local_name → _ImportBinding``. Function-local imports
    are intentionally skipped; tracking shadowing across scopes would
    complicate the walker and the case is rare in practice.
    """
    out: dict[str, _ImportBinding] = {}
    for stmt in module.body:
        if not isinstance(stmt, ast.ImportFrom):
            continue
        mod = stmt.module or ""
        level = stmt.level or 0
        for alias in stmt.names:
            if alias.name == "*":
                continue  # wildcard binds nothing definite
            local = alias.asname or alias.name
            out[local] = _ImportBinding(module=mod, level=level, target_name=alias.name)
    return out


def _source_package_parts(path: str, roots: list[str]) -> list[str]:
    """Dotted-path parts of the package containing ``path``.

    Mirrors ``codebase_mapper.languages.python.resolve_python_imports``'s
    own resolution. Sort longest root first so e.g. ``src/`` beats ``""``.
    Returns the path-parts of the file's *parent* (used for relative
    imports: ``from .sibling import foo``).
    """
    for root in sorted(roots, key=len, reverse=True):
        prefix = (root + "/") if root else ""
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        parts = list(PurePosixPath(rest).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return parts[:-1] if parts else []
    return []


def _resolve_import_path(
    binding: _ImportBinding, src_pkg: list[str],
    by_module: dict, by_suffix: dict,
) -> str | None:
    """Apply relative-level math, then look up in by_module → by_suffix."""
    mod = binding.module
    if binding.level > 0:
        # `from . import x`  → level=1, drop nothing
        # `from .. import x` → level=2, drop one package
        drop = binding.level - 1
        if drop > len(src_pkg):
            return None
        base = src_pkg[: len(src_pkg) - drop] if drop > 0 else src_pkg
        mod = ".".join([*base, mod]) if mod else ".".join(base)
    if not mod:
        return None
    return by_module.get(mod) or by_suffix.get(mod)


# ---------------------------------------------------------------------------
# Walker
# ---------------------------------------------------------------------------


def _walk_module(
    module: ast.Module,
    edges: list[SymbolXrefEdge],
    unresolved: list[UnresolvedSymbolRef],
    *,
    record_path: str,
    targets: dict[str, str],
    src_lookup: dict[tuple[str, str | None], str],
    imports_map: dict[str, _ImportBinding],
    src_pkg: list[str],
    global_targets: dict[tuple[str, str], str],
    global_methods: dict[tuple[str, str, str], str],
    by_module: dict,
    by_suffix: dict,
) -> None:
    scope: list[tuple[str, str]] = []
    # Parallel stack: for each ClassDef in scope, the list of resolved
    # bases as (base_path, base_class_name, resolver_name). Used by the
    # method-override emitter when descending into the class body.
    class_bases: list[list[tuple[str, str, str]]] = []

    def visit(node: ast.AST) -> None:
        pushed_scope = _push_scope(node, scope)
        pushed_bases = False

        # Phase 10: subclassOf — at the ClassDef boundary, resolve bases,
        # emit edges, and stash the resolved set for override lookups
        # inside the class body.
        if isinstance(node, ast.ClassDef):
            resolved = _emit_subclass_of(
                node, edges, unresolved,
                record_path=record_path,
                targets=targets, src_lookup=src_lookup,
                imports_map=imports_map, src_pkg=src_pkg,
                global_targets=global_targets,
                by_module=by_module, by_suffix=by_suffix,
            )
            class_bases.append(resolved)
            pushed_bases = True

        # Phase 10: overrides — at a method def, check the enclosing
        # class's resolved bases for a same-named method.
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and pushed_scope
            and scope[-1][0] == "method"
            and class_bases
        ):
            _emit_overrides(
                node, scope, edges,
                resolved_bases=class_bases[-1],
                src_lookup=src_lookup,
                global_methods=global_methods,
            )

        if isinstance(node, ast.Call):
            _emit_call(
                node, scope, edges, unresolved,
                targets=targets, src_lookup=src_lookup,
                imports_map=imports_map, src_pkg=src_pkg,
                global_targets=global_targets,
                by_module=by_module, by_suffix=by_suffix,
            )
        for child in ast.iter_child_nodes(node):
            visit(child)
        if pushed_bases:
            class_bases.pop()
        if pushed_scope:
            scope.pop()

    for stmt in module.body:
        visit(stmt)


def _push_scope(node: ast.AST, scope: list[tuple[str, str]]) -> bool:
    if isinstance(node, ast.ClassDef):
        scope.append(("class", node.name))
        return True
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        kind = "method" if scope and scope[-1][0] == "class" else "function"
        scope.append((kind, node.name))
        return True
    return False


def _emit_call(
    call: ast.Call,
    scope: list[tuple[str, str]],
    edges: list[SymbolXrefEdge],
    unresolved: list[UnresolvedSymbolRef],
    *,
    targets: dict[str, str],
    src_lookup: dict[tuple[str, str | None], str],
    imports_map: dict[str, _ImportBinding],
    src_pkg: list[str],
    global_targets: dict[tuple[str, str], str],
    by_module: dict,
    by_suffix: dict,
) -> None:
    if not isinstance(call.func, ast.Name):
        return
    name = call.func.id
    src_id = _src_chunk_id(scope, src_lookup)
    if src_id is None:
        return

    # 1) Intra-file first. See module docstring for the shadowing decision.
    if name in targets:
        edges.append(SymbolXrefEdge(
            src_chunk_id=src_id, dst_chunk_id=targets[name],
            kind="calls", resolution="exact",
            resolver=RESOLVER_INTRA,
        ))
        return

    # 2) Inter-file: matches an import binding.
    binding = imports_map.get(name)
    if binding is None:
        return  # out of scope (local var, builtin, etc.) — not unresolved

    dst_path = _resolve_import_path(binding, src_pkg, by_module, by_suffix)
    raw_target = _format_raw_target(binding, name)
    if dst_path is None:
        unresolved.append(UnresolvedSymbolRef(
            src_chunk_id=src_id, raw_target=raw_target,
            kind="calls", reason="module_not_in_repo",
            resolver=RESOLVER_INTER,
        ))
        return

    dst_id = global_targets.get((dst_path, binding.target_name))
    if dst_id is None:
        unresolved.append(UnresolvedSymbolRef(
            src_chunk_id=src_id, raw_target=raw_target,
            kind="calls", reason="symbol_not_exported",
            resolver=RESOLVER_INTER,
        ))
        return

    edges.append(SymbolXrefEdge(
        src_chunk_id=src_id, dst_chunk_id=dst_id,
        kind="calls", resolution="exact",
        resolver=RESOLVER_INTER,
    ))


def _src_chunk_id(
    scope: list[tuple[str, str]],
    src_lookup: dict[tuple[str, str | None], str],
) -> str | None:
    if not scope:
        return None
    kind, symbol = scope[-1]
    if kind == "method":
        parent = scope[-2][1] if len(scope) >= 2 and scope[-2][0] == "class" else None
        return src_lookup.get((symbol, parent))
    if kind in {"function", "class"}:
        return src_lookup.get((symbol, None))
    return None


def _format_raw_target(binding: _ImportBinding, local_name: str) -> str:
    """Pretty-print the import for inclusion in ``raw_target``."""
    dots = "." * binding.level
    if binding.target_name == local_name:
        return f"from {dots}{binding.module} import {binding.target_name}"
    return f"from {dots}{binding.module} import {binding.target_name} as {local_name}"


# ---------------------------------------------------------------------------
# Phase 10: subclassOf + overrides
# ---------------------------------------------------------------------------


def _emit_subclass_of(
    class_node: ast.ClassDef,
    edges: list[SymbolXrefEdge],
    unresolved: list[UnresolvedSymbolRef],
    *,
    record_path: str,
    targets: dict[str, str],
    src_lookup: dict[tuple[str, str | None], str],
    imports_map: dict[str, _ImportBinding],
    src_pkg: list[str],
    global_targets: dict[tuple[str, str], str],
    by_module: dict,
    by_suffix: dict,
) -> list[tuple[str, str, str]]:
    """Walk ``class Sub(Base, ...)`` bases, emit subclassOf edges.

    Returns the resolved bases as a list of ``(base_path, base_class_name,
    resolver_name)`` so the override pass can look up methods. Only bare
    ``Name`` bases are attempted; ``Attribute`` / ``Subscript`` /
    ``Call`` bases (e.g. ``pkg.Base``, ``Generic[T]``) are deferred and
    return no resolution.
    """
    sub_chunk_id = src_lookup.get((class_node.name, None))
    if sub_chunk_id is None:
        return []

    resolved: list[tuple[str, str, str]] = []
    for base in class_node.bases:
        if not isinstance(base, ast.Name):
            continue
        base_name = base.id

        # 1) Same-file class win.
        if base_name in targets:
            edges.append(SymbolXrefEdge(
                src_chunk_id=sub_chunk_id, dst_chunk_id=targets[base_name],
                kind="subclassOf", resolution="exact",
                resolver=RESOLVER_INTRA,
            ))
            resolved.append((record_path, base_name, RESOLVER_INTRA))
            continue

        # 2) Imported base?
        binding = imports_map.get(base_name)
        if binding is None:
            # Unknown name — could be `object` / a builtin / a runtime
            # construct. Skip silently per the coverage-as-data rule.
            continue

        dst_path = _resolve_import_path(binding, src_pkg, by_module, by_suffix)
        raw_target = _format_raw_target(binding, base_name)
        if dst_path is None:
            unresolved.append(UnresolvedSymbolRef(
                src_chunk_id=sub_chunk_id, raw_target=raw_target,
                kind="subclassOf", reason="module_not_in_repo",
                resolver=RESOLVER_INTER,
            ))
            continue

        dst_id = global_targets.get((dst_path, binding.target_name))
        if dst_id is None:
            unresolved.append(UnresolvedSymbolRef(
                src_chunk_id=sub_chunk_id, raw_target=raw_target,
                kind="subclassOf", reason="symbol_not_exported",
                resolver=RESOLVER_INTER,
            ))
            continue

        edges.append(SymbolXrefEdge(
            src_chunk_id=sub_chunk_id, dst_chunk_id=dst_id,
            kind="subclassOf", resolution="exact",
            resolver=RESOLVER_INTER,
        ))
        resolved.append((dst_path, binding.target_name, RESOLVER_INTER))

    return resolved


def _emit_overrides(
    method_node: ast.FunctionDef | ast.AsyncFunctionDef,
    scope: list[tuple[str, str]],
    edges: list[SymbolXrefEdge],
    *,
    resolved_bases: list[tuple[str, str, str]],
    src_lookup: dict[tuple[str, str | None], str],
    global_methods: dict[tuple[str, str, str], str],
) -> None:
    """Emit override edges when ``method_node.name`` shadows a method
    on any resolved base class.

    Multiple bases that define the same method produce multiple edges
    (Python MRO doesn't pick one at structural-analysis time; record all
    candidates and let downstream consumers decide).
    """
    # The enclosing class name is the previous frame (scope[-2]).
    if len(scope) < 2 or scope[-2][0] != "class":
        return
    sub_class = scope[-2][1]
    method_name = method_node.name
    method_chunk_id = src_lookup.get((method_name, sub_class))
    if method_chunk_id is None:
        return

    for base_path, base_class, resolver_name in resolved_bases:
        base_method_id = global_methods.get((base_path, base_class, method_name))
        if base_method_id is None:
            continue
        edges.append(SymbolXrefEdge(
            src_chunk_id=method_chunk_id,
            dst_chunk_id=base_method_id,
            kind="overrides", resolution="exact",
            resolver=resolver_name,
        ))
