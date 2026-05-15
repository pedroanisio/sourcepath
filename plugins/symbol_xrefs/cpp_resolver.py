"""C++ symbol-xref resolver (Tier-1 Stage 2).

Edge kinds covered: ``calls``, ``subclassOf``, ``overrides``.

Resolver names on emitted edges:

  - ``cpp_intra_file`` — target resolves to a top-level type / a method
    inside the same source file (e.g. inline method in a header,
    out-of-class definition in the same .cpp).
  - ``cpp_inter_file`` — target resolves via ``#include`` or via the
    cross-file symbol index ``host:cpp_symbols``.

Call shapes in scope:

  - Bare identifier call: ``foo()``
  - Qualified call:       ``Foo::method()`` (binds receiver to ``Foo`` class
                          chunk; if the method exists in ``global_methods``
                          we attach to the method chunk specifically)
  - Receiver method call: ``obj.method()`` / ``obj->method()`` — silent
                          unless the receiver is a known same-file
                          variable whose type we recovered from a
                          local declaration (rare — deferred for v1).
  - Constructor call:     ``new Foo(...)``  (binds to ``Foo``'s class chunk)
  - Direct-init:          ``Foo x(args);``  (binds to ``Foo``'s class chunk)

Out of scope:

  - Member-function dispatch via a receiver — requires type inference.
    Matches the Rust/TS/Java Stage-2 posture.
  - Friend declarations.
  - C++20 ``import std;`` / module units — deferred.
  - Operator overloads.
"""
from __future__ import annotations

from typing import cast

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from codebase_mapper.emission.models import SymbolXrefEdge, UnresolvedSymbolRef
from codebase_mapper.inspection.models import FileRecord
from codebase_mapper.ts_setup import TS_AVAILABLE, _TS_LANGS, _ts_setup, ts


RESOLVER_INTRA = "cpp_intra_file"
RESOLVER_INTER = "cpp_inter_file"

_GLOBAL_TARGETS_CACHE_KEY = "_xrefs_cpp_global_targets"
_GLOBAL_METHODS_CACHE_KEY = "_xrefs_cpp_global_methods"


def resolve_cpp_calls(
    record: FileRecord, ctx: PipelineCtx,
) -> tuple[list[SymbolXrefEdge], list[UnresolvedSymbolRef]]:
    if not TS_AVAILABLE:
        return [], []
    if record.language != "cpp":
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

    cpp_symbols = cast(dict, ctx.indices.get("host:cpp_symbols", {}))
    src_lookup = _source_chunk_lookup(chunks_in_file)
    intra_targets = _top_level_targets(chunks_in_file)
    global_targets = _global_targets_cache(ctx)
    global_methods = _global_methods_cache(ctx)

    edges: list[SymbolXrefEdge] = []
    unresolved: list[UnresolvedSymbolRef] = []

    # ---- subclassOf + overrides ----------------------------------------
    classes = [it for it in items
               if it["kind"] in {"class", "struct"}
               and it.get("parent") is None]
    for cls_item in classes:
        src_id = src_lookup.get((cls_item["name"], None))
        if src_id is None:
            continue
        resolved_bases: list[tuple[str, str, str]] = []  # (path, name, kind)
        ext = cls_item.get("extends")
        if ext:
            r = _resolve_class(ext, record.path, intra_targets, cpp_symbols,
                               global_targets)
            if r:
                dst_id, kind = r
                edges.append(SymbolXrefEdge(
                    src_chunk_id=src_id, dst_chunk_id=dst_id,
                    kind="subclassOf", resolution="exact",
                    resolver=RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER,
                ))
                resolved_bases.append((
                    record.path if kind == "intra"
                    else _path_for_chunk(dst_id, global_targets),
                    ext, kind,
                ))
        for impl in (cls_item.get("implements") or []):
            r = _resolve_class(impl, record.path, intra_targets, cpp_symbols,
                               global_targets)
            if r:
                dst_id, kind = r
                edges.append(SymbolXrefEdge(
                    src_chunk_id=src_id, dst_chunk_id=dst_id,
                    kind="subclassOf", resolution="heuristic",
                    resolver=RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER,
                ))
                resolved_bases.append((
                    record.path if kind == "intra"
                    else _path_for_chunk(dst_id, global_targets),
                    impl, kind,
                ))
        if resolved_bases:
            class_methods = [
                m for m in items
                if m.get("parent") == cls_item["name"]
                and m["kind"] in {"method", "constructor", "destructor"}
            ]
            for meth in class_methods:
                method_id = src_lookup.get((meth["name"], cls_item["name"]))
                if method_id is None:
                    continue
                for base_path, base_class, kind in resolved_bases:
                    if not base_path:
                        continue
                    base_method = global_methods.get(
                        (base_path, base_class, meth["name"])
                    )
                    if base_method is None:
                        continue
                    edges.append(SymbolXrefEdge(
                        src_chunk_id=method_id,
                        dst_chunk_id=base_method,
                        kind="overrides", resolution="exact",
                        resolver=RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER,
                    ))

    # ---- calls ---------------------------------------------------------
    _ts_setup()
    lang = _TS_LANGS["cpp"]
    parser = ts.Parser(lang)
    try:
        content = ctx.read_path(record.path)
    except Exception:
        return edges, unresolved
    tree = parser.parse(content)
    root = tree.root_node

    callable_items = [
        it for it in items
        if it["kind"] in {"method", "constructor", "function", "destructor"}
    ]
    for it in callable_items:
        src_id = src_lookup.get((it["name"], it.get("parent")))
        if src_id is None:
            continue
        method_node = _find_node_by_span(root, it["byte_start"], it["byte_end"])
        if method_node is None:
            continue
        _walk_for_calls(
            method_node, content, src_id,
            class_name=it.get("parent"),
            record_path=record.path,
            intra_targets=intra_targets,
            cpp_symbols=cpp_symbols,
            global_targets=global_targets,
            global_methods=global_methods,
            src_lookup=src_lookup,
            edges=edges, unresolved=unresolved,
        )

    return edges, unresolved


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _top_level_targets(chunks_in_file: list[dict]) -> dict[str, str]:
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
    return {sym: cid for sym, (cid, _) in out.items()}


def _source_chunk_lookup(chunks_in_file: list[dict]) -> dict[tuple[str, str | None], str]:
    out: dict[tuple[str, str | None], str] = {}
    for c in chunks_in_file:
        if c.get("kind") not in {"function", "method", "class"}:
            continue
        out[(c["symbol"], c.get("parent_symbol"))] = c["chunk_id"]
    return out


def _global_targets_cache(ctx: PipelineCtx) -> dict[tuple[str, str], str]:
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


def _resolve_class(
    name: str, record_path: str,
    intra_targets: dict[str, str],
    cpp_symbols: dict[str, list[str]],
    global_targets: dict[tuple[str, str], str],
) -> tuple[str, str] | None:
    """Return ``(chunk_id, 'intra'|'inter')`` for a class name reference.

    Lookup order:
      1. Same-file top-level type.
      2. ``host:cpp_symbols`` — any file declaring the class at top level.
         Multi-file classes (declaration in .h + definition in .cpp)
         keep both files; we pick the first.
    """
    if name in intra_targets:
        return intra_targets[name], "intra"
    paths = cpp_symbols.get(name) or []
    for p in paths:
        if p == record_path:
            # Already covered by the intra path; the symbol index lists
            # all definers regardless of intra/inter.
            continue
        cid = global_targets.get((p, name))
        if cid is not None:
            return cid, "inter"
    return None


def _path_for_chunk(chunk_id: str,
                    global_targets: dict[tuple[str, str], str]) -> str:
    for (path, _name), cid in global_targets.items():
        if cid == chunk_id:
            return path
    return ""


def _find_node_by_span(root, start: int, end: int):
    stack = [root]
    while stack:
        node = stack.pop()
        if node.start_byte == start and node.end_byte == end:
            return node
        if node.start_byte <= start and node.end_byte >= end:
            stack.extend(node.children)
    return None


def _walk_for_calls(
    method_node, content: bytes, src_id: str, *,
    class_name, record_path, intra_targets, cpp_symbols,
    global_targets, global_methods, src_lookup,
    edges, unresolved,
) -> None:
    seen: set[tuple[str, str]] = set()

    def visit(node):
        nt = node.type
        if nt == "call_expression":
            _emit_call(
                node, content, src_id,
                class_name=class_name, record_path=record_path,
                intra_targets=intra_targets, cpp_symbols=cpp_symbols,
                global_targets=global_targets, global_methods=global_methods,
                src_lookup=src_lookup,
                edges=edges, seen=seen,
            )
        elif nt == "new_expression":
            _emit_new(
                node, content, src_id,
                intra_targets=intra_targets, cpp_symbols=cpp_symbols,
                global_targets=global_targets,
                record_path=record_path,
                edges=edges, seen=seen,
            )
        elif nt == "declaration":
            _emit_direct_init(
                node, content, src_id,
                intra_targets=intra_targets, cpp_symbols=cpp_symbols,
                global_targets=global_targets,
                record_path=record_path,
                edges=edges, seen=seen,
            )
        for ch in node.children:
            if ch.is_named:
                visit(ch)

    visit(method_node)


def _emit_call(node, content, src_id, *, class_name, record_path,
               intra_targets, cpp_symbols, global_targets, global_methods,
               src_lookup, edges, seen) -> None:
    """Resolve a ``call_expression`` AST node.

    Shapes:
      - bare:     ``call_expression > identifier``
      - qualified ``call_expression > qualified_identifier``
      - receiver  ``call_expression > field_expression`` (skipped — needs
                   type inference)
    """
    func = node.child_by_field_name("function") or _first_named(node)
    if func is None:
        return
    if func.type == "identifier":
        name = _txt(func, content)
        if class_name:
            same_class = src_lookup.get((name, class_name))
            if same_class:
                _emit(edges, seen, src_id, same_class, "calls", "exact",
                      RESOLVER_INTRA)
                return
        # Same-file top-level function?
        cid = intra_targets.get(name)
        if cid is not None:
            _emit(edges, seen, src_id, cid, "calls", "exact",
                  RESOLVER_INTRA)
            return
        # Cross-file via cpp_symbols.
        paths = cpp_symbols.get(name) or []
        for p in paths:
            if p == record_path:
                continue
            cid = global_targets.get((p, name))
            if cid is not None:
                _emit(edges, seen, src_id, cid, "calls", "exact",
                      RESOLVER_INTER)
                return
        return
    if func.type == "qualified_identifier":
        # ``Type::method`` or ``ns::function``.
        scope = func.child_by_field_name("scope")
        nm = func.child_by_field_name("name")
        if scope is None or nm is None:
            return
        scope_name = _txt(scope, content).rsplit("::", 1)[-1]
        method_name = _txt(nm, content)
        # First, treat ``scope_name`` as a class and try to bind a method.
        cls = _resolve_class(scope_name, record_path, intra_targets,
                             cpp_symbols, global_targets)
        if cls is not None:
            cid_cls, kind = cls
            base_path = (record_path if kind == "intra"
                         else _path_for_chunk(cid_cls, global_targets))
            if base_path:
                method_id = global_methods.get(
                    (base_path, scope_name, method_name)
                )
                if method_id is not None:
                    _emit(edges, seen, src_id, method_id, "calls", "exact",
                          RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER)
                    return
            # Fall back to the class chunk.
            _emit(edges, seen, src_id, cid_cls, "calls", "heuristic",
                  RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER)
            return
        # ``scope_name`` is a namespace, not a class. Treat as a free
        # function: search cpp_symbols for the method_name.
        paths = cpp_symbols.get(method_name) or []
        for p in paths:
            if p == record_path:
                cid = intra_targets.get(method_name)
                if cid is not None:
                    _emit(edges, seen, src_id, cid, "calls", "exact",
                          RESOLVER_INTRA)
                    return
                continue
            cid = global_targets.get((p, method_name))
            if cid is not None:
                _emit(edges, seen, src_id, cid, "calls", "exact",
                      RESOLVER_INTER)
                return
        return
    # field_expression / parenthesized / other — skip.


def _emit_new(node, content, src_id, *, intra_targets, cpp_symbols,
              global_targets, record_path, edges, seen) -> None:
    """``new Foo(...)`` — bind to Foo's class chunk."""
    ti = _find_descendant(node, {"type_identifier", "qualified_identifier"})
    if ti is None:
        return
    name = _txt(ti, content).rsplit("::", 1)[-1]
    r = _resolve_class(name, record_path, intra_targets, cpp_symbols,
                       global_targets)
    if r is None:
        return
    cid, kind = r
    _emit(edges, seen, src_id, cid, "calls", "exact",
          RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER)


def _emit_direct_init(node, content, src_id, *, intra_targets, cpp_symbols,
                      global_targets, record_path, edges, seen) -> None:
    """``Type var(args);`` direct-initialization. The tree-sitter-cpp
    shape is ``declaration > [type_identifier|qualified_identifier]``
    followed by ``init_declarator > [identifier, argument_list]``.

    We bind a ``calls`` edge from the enclosing scope to ``Type``'s
    class chunk *only when* an argument_list is present (otherwise it's
    a plain declaration like ``int x;``).
    """
    init = _find_first(node, "init_declarator")
    if init is None:
        return
    args = _find_first(init, "argument_list")
    if args is None:
        return
    type_node = _find_first(node, "type_identifier") \
        or _find_first(node, "qualified_identifier")
    if type_node is None:
        return
    name = _txt(type_node, content).rsplit("::", 1)[-1]
    r = _resolve_class(name, record_path, intra_targets, cpp_symbols,
                       global_targets)
    if r is None:
        return
    cid, kind = r
    _emit(edges, seen, src_id, cid, "calls", "exact",
          RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER)


def _emit(edges, seen, src_id, dst_id, kind, resolution, resolver) -> None:
    key = (src_id, dst_id)
    if key in seen:
        return
    seen.add(key)
    edges.append(SymbolXrefEdge(
        src_chunk_id=src_id, dst_chunk_id=dst_id,
        kind=kind, resolution=resolution, resolver=resolver,
    ))


def _txt(node, content) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _first_named(node):
    for ch in node.children:
        if ch.is_named:
            return ch
    return None


def _find_first(node, kind: str):
    for ch in node.children:
        if ch.is_named and ch.type == kind:
            return ch
    return None


def _find_descendant(node, kinds: set[str]):
    if node.type in kinds:
        return node
    for ch in node.children:
        if ch.is_named:
            r = _find_descendant(ch, kinds)
            if r is not None:
                return r
    return None
