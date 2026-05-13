"""Python `calls` resolver — intra-file (Phase 2) + inter-file (Phase 4).

Two sub-resolvers, one entry point. The resolver name attached to each
edge tells you which path produced it:

  - ``python_intra_file`` — call binds to a top-level def/class in the
    same module.
  - ``python_inter_file`` — call binds to a ``from X import Y`` whose
    module resolves to an in-repo file and whose ``Y`` is a top-level
    def/class in that file.

Resolution order: intra-file wins over import. In real Python the
later binding wins at runtime, but mixed shadowing is rare and the
intra-file-first rule is what users mean 99% of the time. Documented
here so a future change can revisit it deliberately.

Phase 4 populates ``unresolved`` only for names that *are* imports
whose binding failed — never for arbitrary unknown names. This keeps
coverage signal meaningful: ``n_unresolved`` answers "how many
imported call targets did we fail to bind?", not "how many random
names did we see?".

Unresolved reasons:
  - ``module_not_in_repo`` — the imported module isn't in
    ``host:python_by_module`` / ``host:python_by_suffix``.
  - ``symbol_not_exported`` — the module is in repo but the target
    symbol isn't a top-level def/class in it (or it's a method, or
    L2 didn't emit a chunk for it).

Out of scope (deferred):
  - ``Attribute`` calls (``obj.foo()``, ``self.foo()``, ``mod.foo()``).
  - Function-local ``from X import Y`` statements (only module-level
    imports are tracked).
  - ``import X`` followed by ``X.foo()`` — that's an Attribute call.
  - ``from X import *`` — wildcard, no specific binding.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from codebase_mapper.extensions import PipelineCtx
from codebase_mapper.languages.python import _jsonable_to_ast
from codebase_mapper.models import (
    FileRecord, SymbolXrefEdge, UnresolvedSymbolRef,
)


RESOLVER_INTRA = "python_intra_file"
RESOLVER_INTER = "python_inter_file"
# Back-compat: Phase 2 exported a single RESOLVER_NAME constant; preserve it
# so any external import still works. Maps to the intra-file resolver.
RESOLVER_NAME = RESOLVER_INTRA

_GLOBAL_TARGETS_CACHE_KEY = "_xrefs_python_global_targets"


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
    by_module = cast(dict, ctx.indices.get("host:python_by_module", {}))
    by_suffix = cast(dict, ctx.indices.get("host:python_by_suffix", {}))

    edges: list[SymbolXrefEdge] = []
    unresolved: list[UnresolvedSymbolRef] = []
    _walk_module(
        module, edges, unresolved,
        targets=targets, src_lookup=src_lookup,
        imports_map=imports_map, src_pkg=src_pkg,
        global_targets=global_targets,
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
    targets: dict[str, str],
    src_lookup: dict[tuple[str, str | None], str],
    imports_map: dict[str, _ImportBinding],
    src_pkg: list[str],
    global_targets: dict[tuple[str, str], str],
    by_module: dict,
    by_suffix: dict,
) -> None:
    scope: list[tuple[str, str]] = []

    def visit(node: ast.AST) -> None:
        pushed = _push_scope(node, scope)
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
        if pushed:
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
