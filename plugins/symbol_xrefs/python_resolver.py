"""Python intra-file `calls` resolver — Phase 2 of the symbol-xref plan.

Resolves call sites where ``Call.func`` is a bare ``Name`` matching a
top-level function or class defined in the same module. Cross-file
resolution (imports) is Phase 4; attribute / method calls
(``obj.foo()``, ``self.foo()``) are not attempted here.

Contract:
    resolve(record, ctx) -> (list[SymbolXrefEdge], list[UnresolvedSymbolRef])

Pure function: no I/O, no globals, deterministic. Returns
``([], [])`` for any record whose ``ast_summary`` is missing or whose
chunks are absent from ``ctx.indices["l2_10_chunks"]``.

Phase 2 keeps ``unresolved`` empty — only `Name` calls that bind to a
same-file top-level symbol are considered "in scope". `Attribute` calls
and bare names that don't match a same-file symbol are silently skipped;
phases 4/8 take ownership of those buckets with the right reason codes.
"""
from __future__ import annotations

import ast
from typing import cast

from codebase_mapper.extensions import PipelineCtx
from codebase_mapper.languages.python import _jsonable_to_ast
from codebase_mapper.models import (
    FileRecord, SymbolXrefEdge, UnresolvedSymbolRef,
)


RESOLVER_NAME = "python_intra_file"


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

    targets = _top_level_targets(chunks_in_file)
    src_lookup = _source_chunk_lookup(chunks_in_file)

    try:
        module = _jsonable_to_ast(ast_json)
    except Exception:
        return [], []
    if not isinstance(module, ast.Module):
        return [], []

    edges: list[SymbolXrefEdge] = []
    _walk_module(module, edges, targets, src_lookup)
    return edges, []


def _top_level_targets(chunks_in_file: list[dict]) -> dict[str, str]:
    """Map a top-level symbol name → its chunk_id.

    Top-level = ``parent_symbol`` is None and ``kind`` ∈ {function, class}.
    If shadowing occurs (two top-level defs with the same name), the later
    one wins — that matches Python's runtime binding semantics.
    """
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
    """Map (symbol, parent_symbol) → chunk_id for every callable chunk.

    Covers top-level functions, top-level classes, and methods. Methods
    are keyed by (method_name, class_name); top-level defs by (name, None).
    """
    out: dict[tuple[str, str | None], str] = {}
    for c in chunks_in_file:
        if c.get("kind") not in {"function", "method", "class"}:
            continue
        out[(c["symbol"], c.get("parent_symbol"))] = c["chunk_id"]
    return out


def _walk_module(
    module: ast.Module,
    edges: list[SymbolXrefEdge],
    targets: dict[str, str],
    src_lookup: dict[tuple[str, str | None], str],
) -> None:
    scope: list[tuple[str, str]] = []  # [(kind, symbol), ...]

    def visit(node: ast.AST) -> None:
        pushed = _push_scope(node, scope)
        if isinstance(node, ast.Call):
            _emit_call(node, scope, edges, targets, src_lookup)
        for child in ast.iter_child_nodes(node):
            visit(child)
        if pushed:
            scope.pop()

    for stmt in module.body:
        visit(stmt)


def _push_scope(node: ast.AST, scope: list[tuple[str, str]]) -> bool:
    """Push the right scope frame for definition nodes; return True if pushed."""
    if isinstance(node, ast.ClassDef):
        scope.append(("class", node.name))
        return True
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        # Inside a class body → method; otherwise top-level or nested function.
        kind = "method" if scope and scope[-1][0] == "class" else "function"
        scope.append((kind, node.name))
        return True
    return False


def _emit_call(
    call: ast.Call,
    scope: list[tuple[str, str]],
    edges: list[SymbolXrefEdge],
    targets: dict[str, str],
    src_lookup: dict[tuple[str, str | None], str],
) -> None:
    # Phase 2 only resolves bare-Name call targets. Attribute / Subscript /
    # Lambda / Call-of-Call go to later phases.
    if not isinstance(call.func, ast.Name):
        return
    target_name = call.func.id
    dst_id = targets.get(target_name)
    if dst_id is None:
        return

    src_id = _src_chunk_id(scope, src_lookup)
    if src_id is None:
        return

    edges.append(SymbolXrefEdge(
        src_chunk_id=src_id,
        dst_chunk_id=dst_id,
        kind="calls",
        resolution="exact",
        resolver=RESOLVER_NAME,
    ))


def _src_chunk_id(
    scope: list[tuple[str, str]],
    src_lookup: dict[tuple[str, str | None], str],
) -> str | None:
    """The chunk that owns the call site, based on the enclosing scope stack.

    Method calls bind to the method chunk; top-level function/class calls
    bind to that def's chunk. Nested functions (def inside def) have no
    chunk of their own (the L2 chunker only emits top-level + methods),
    so calls inside them are unattributable and return None.
    """
    if not scope:
        return None
    kind, symbol = scope[-1]
    if kind == "method":
        parent = scope[-2][1] if len(scope) >= 2 and scope[-2][0] == "class" else None
        return src_lookup.get((symbol, parent))
    if kind in {"function", "class"}:
        # Only top-level function/class chunks exist in src_lookup; nested
        # defs return None.
        return src_lookup.get((symbol, None))
    return None
