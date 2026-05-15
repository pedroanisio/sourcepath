"""Objective-C / Objective-C++ symbol-xref resolver (Tier-1 Stage 2).

Edge kinds covered: ``calls``, ``subclassOf``, ``overrides``.

Resolver names on emitted edges:

  - ``objc_intra_file`` — target resolves to a class / method declared
    in the same source file (e.g. a method calling another method on
    its own ``@implementation``).
  - ``objc_inter_file`` — target resolves via ``#import`` /
    ``host:objc_symbols`` to a class declared in another file.

Call shapes in scope:

  - Bare C-function call:   ``arc4random_uniform(x)``
  - Class message:          ``[NSString stringWithFormat:fmt]``
                            → binds to the class chunk; if the method
                              exists in ``global_methods`` we attach
                              to the method chunk specifically.
  - Self message:           ``[self bark]`` → binds to same-class method.
  - Super message:          ``[super doSomething:42]`` → binds to the
                            superclass's method via the resolved
                            base-class chain.
  - Nested message:         ``[[Sound alloc] initWithText:@"x"]`` —
                            outer wraps inner; we walk and emit edges
                            for both segments.

Out of scope:

  - Receiver-of-type-unknown messages (``[obj method]`` where ``obj``
    is a local variable). Requires type inference.
  - ``@selector(foo)`` literals (selectors-as-values).
  - Block invocations.
"""
from __future__ import annotations

from typing import cast

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from codebase_mapper.emission.models import SymbolXrefEdge, UnresolvedSymbolRef
from codebase_mapper.inspection.models import FileRecord
from codebase_mapper.inspection.languages.objc import OBJC_LANGUAGE_TAGS
from codebase_mapper.ts_setup import TS_AVAILABLE, _TS_LANGS, _ts_setup, ts


RESOLVER_INTRA = "objc_intra_file"
RESOLVER_INTER = "objc_inter_file"

_GLOBAL_TARGETS_CACHE_KEY = "_xrefs_objc_global_targets"
_GLOBAL_METHODS_CACHE_KEY = "_xrefs_objc_global_methods"


def resolve_objc_calls(
    record: FileRecord, ctx: PipelineCtx,
) -> tuple[list[SymbolXrefEdge], list[UnresolvedSymbolRef]]:
    if not TS_AVAILABLE:
        return [], []
    if record.language not in OBJC_LANGUAGE_TAGS:
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

    objc_symbols = cast(dict, ctx.indices.get("host:objc_symbols", {}))
    src_lookup = _source_chunk_lookup(chunks_in_file)
    intra_targets = _top_level_targets(chunks_in_file)
    global_targets = _global_targets_cache(ctx)
    global_methods = _global_methods_cache(ctx)

    edges: list[SymbolXrefEdge] = []
    unresolved: list[UnresolvedSymbolRef] = []

    # ---- subclassOf + overrides ----------------------------------------
    type_items = [
        it for it in items
        if it["kind"] in {"class_interface", "class_implementation"}
        and it.get("parent") is None
    ]
    # Build a same-file extends/implements map so we can resolve
    # `[super …]` even when ``extends`` is set on the interface and
    # `super` is referenced from the implementation. We key by class
    # name (the short form before ``(`` for categories).
    extends_by_class: dict[str, tuple[str, str] | None] = {}
    for it in type_items:
        ext = it.get("extends")
        if not ext:
            continue
        cname = it["name"].split("(", 1)[0]
        r = _resolve_class(ext, record.path, intra_targets, objc_symbols,
                           global_targets)
        if r is not None:
            extends_by_class[cname] = r

    for it in type_items:
        src_id = src_lookup.get((it["name"], None))
        if src_id is None:
            continue
        resolved_bases: list[tuple[str, str, str]] = []
        ext = it.get("extends")
        if ext:
            r = _resolve_class(ext, record.path, intra_targets, objc_symbols,
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
        # ``implements`` in ObjC means "conforms to protocol" — treat as
        # heuristic subclassOf.
        for impl in (it.get("implements") or []):
            r = _resolve_class(impl, record.path, intra_targets, objc_symbols,
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
                if m.get("parent") == it["name"] and m["kind"] == "method"
            ]
            for meth in class_methods:
                method_id = src_lookup.get((meth["name"], it["name"]))
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
    lang = _TS_LANGS["objc"]
    parser = ts.Parser(lang)
    try:
        content = ctx.read_path(record.path)
    except Exception:
        return edges, unresolved
    tree = parser.parse(content)
    root = tree.root_node

    callable_items = [
        it for it in items
        if it["kind"] == "method"
    ]
    for it in callable_items:
        src_id = src_lookup.get((it["name"], it.get("parent")))
        if src_id is None:
            continue
        method_node = _find_node_by_span(root, it["byte_start"], it["byte_end"])
        if method_node is None:
            continue
        # Resolve `super` for this method's enclosing class once.
        parent_class = (it.get("parent") or "").split("(", 1)[0]
        super_target = extends_by_class.get(parent_class)
        _walk_for_calls(
            method_node, content, src_id,
            enclosing_class=parent_class,
            super_target=super_target,
            record_path=record.path,
            intra_targets=intra_targets,
            objc_symbols=objc_symbols,
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
        # Bind both the full parent (``Dog`` or ``NSString(Greet)``) and
        # the short host name (``NSString``) so receiver references find
        # category-defined methods without needing the category name.
        keys = [(c["path"], parent, c["symbol"])]
        if "(" in parent:
            host = parent.split("(", 1)[0]
            keys.append((c["path"], host, c["symbol"]))
        for key in keys:
            line = c.get("line_start", 0)
            if key not in by_key_line or line > by_key_line[key]:
                out[key] = c["chunk_id"]
                by_key_line[key] = line
    ctx.scratch[_GLOBAL_METHODS_CACHE_KEY] = out
    return out


def _resolve_class(
    name: str, record_path: str,
    intra_targets: dict[str, str],
    objc_symbols: dict[str, list[str]],
    global_targets: dict[tuple[str, str], str],
) -> tuple[str, str] | None:
    """Return ``(chunk_id, 'intra'|'inter')`` for an ObjC class reference."""
    if name in intra_targets:
        return intra_targets[name], "intra"
    paths = objc_symbols.get(name) or []
    for p in paths:
        if p == record_path:
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
    enclosing_class, super_target, record_path, intra_targets,
    objc_symbols, global_targets, global_methods, src_lookup,
    edges, unresolved,
) -> None:
    seen: set[tuple[str, str]] = set()

    def visit(node):
        nt = node.type
        if nt == "message_expression":
            _emit_message(
                node, content, src_id,
                enclosing_class=enclosing_class,
                super_target=super_target,
                record_path=record_path,
                intra_targets=intra_targets,
                objc_symbols=objc_symbols,
                global_targets=global_targets,
                global_methods=global_methods,
                src_lookup=src_lookup,
                edges=edges, seen=seen,
            )
            # Recurse to handle nested message_expressions inside the
            # receiver / argument positions.
        elif nt == "call_expression":
            _emit_call(
                node, content, src_id,
                intra_targets=intra_targets,
                objc_symbols=objc_symbols,
                global_targets=global_targets,
                record_path=record_path,
                edges=edges, seen=seen,
            )
        for ch in node.children:
            if ch.is_named:
                visit(ch)

    visit(method_node)


def _emit_message(node, content, src_id, *, enclosing_class, super_target,
                  record_path, intra_targets, objc_symbols, global_targets,
                  global_methods, src_lookup, edges, seen) -> None:
    """``[receiver selector:arg ...]`` — bind to the receiver's class
    chunk and (if known) to the specific method chunk for the selector."""
    # The first named child is the receiver. It may be a bare identifier
    # (``self``, ``super``, ``Dog``) OR a nested message_expression (the
    # ``alloc`` chain) OR a parenthesized_expression.
    children = [c for c in node.children if c.is_named]
    if not children:
        return
    receiver = children[0]
    short_selector = _short_selector_from_message(node, content)
    if short_selector is None:
        return
    receiver_name: str | None = None
    if receiver.type == "identifier":
        receiver_name = _txt(receiver, content)
    elif receiver.type == "message_expression":
        # Inner message — its result class is whatever the *outer call's
        # receiver* would have inferred. ObjC convention: ``[[Class
        # alloc] init…]`` returns ``Class``. Heuristic: take the FIRST
        # identifier reachable from the inner message's receiver.
        ti = _find_descendant_id(receiver)
        if ti is not None:
            receiver_name = _txt(ti, content)
    if receiver_name is None:
        return

    if receiver_name == "self":
        if enclosing_class is None:
            return
        method_id = src_lookup.get((short_selector, enclosing_class))
        if method_id is not None:
            _emit(edges, seen, src_id, method_id, "calls", "exact",
                  RESOLVER_INTRA)
            return
        # Also try the same-class-via-category bind: a category may have
        # added the method.
        return

    if receiver_name == "super":
        if super_target is None:
            return
        cid_cls, kind = super_target
        base_path = (record_path if kind == "intra"
                     else _path_for_chunk(cid_cls, global_targets))
        if base_path:
            base_class_name = _class_name_for_chunk(cid_cls, global_targets)
            method_id = global_methods.get(
                (base_path, base_class_name, short_selector)
            )
            if method_id is not None:
                _emit(edges, seen, src_id, method_id, "calls", "exact",
                      RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER)
                return
        # Fall back to the superclass chunk.
        _emit(edges, seen, src_id, cid_cls, "calls", "heuristic",
              RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER)
        return

    # The receiver should look like a class. Bind it.
    if not receiver_name or not receiver_name[0].isupper():
        return
    cls = _resolve_class(receiver_name, record_path, intra_targets,
                         objc_symbols, global_targets)
    if cls is None:
        return
    cid_cls, kind = cls
    base_path = (record_path if kind == "intra"
                 else _path_for_chunk(cid_cls, global_targets))
    if base_path:
        method_id = global_methods.get((base_path, receiver_name, short_selector))
        if method_id is not None:
            _emit(edges, seen, src_id, method_id, "calls", "exact",
                  RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER)
            return
    _emit(edges, seen, src_id, cid_cls, "calls", "heuristic",
          RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER)


def _short_selector_from_message(node, content: bytes) -> str | None:
    """Recover the short (first-segment) selector name from a
    message_expression node. ``[NSString stringWithFormat:fmt]`` → ``stringWithFormat``."""
    # The receiver is the first named child; the selector identifier
    # comes next. Skip the receiver, then take the next ``identifier``.
    children = [c for c in node.children if c.is_named]
    if len(children) < 2:
        return None
    # Find first identifier AFTER position 0.
    for ch in children[1:]:
        if ch.type == "identifier":
            return _txt(ch, content)
    return None


def _emit_call(node, content, src_id, *, intra_targets, objc_symbols,
               global_targets, record_path, edges, seen) -> None:
    """C-style ``foo()`` call (rare in ObjC; common for stdlib helpers
    like ``arc4random_uniform``)."""
    func = node.child_by_field_name("function") or _first_named(node)
    if func is None or func.type != "identifier":
        return
    name = _txt(func, content)
    cid = intra_targets.get(name)
    if cid is not None:
        _emit(edges, seen, src_id, cid, "calls", "exact", RESOLVER_INTRA)
        return
    paths = objc_symbols.get(name) or []
    for p in paths:
        if p == record_path:
            continue
        cid = global_targets.get((p, name))
        if cid is not None:
            _emit(edges, seen, src_id, cid, "calls", "exact",
                  RESOLVER_INTER)
            return


def _class_name_for_chunk(chunk_id: str,
                          global_targets: dict[tuple[str, str], str]) -> str:
    """Reverse-lookup the class name (the symbol) for a class chunk_id."""
    for (_path, name), cid in global_targets.items():
        if cid == chunk_id:
            return name
    return ""


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


def _find_descendant_id(node):
    """Find the first ``identifier`` descendant (BFS) — used to recover
    the receiver class from ``[[Class alloc] …]``."""
    queue = list(node.children)
    while queue:
        n = queue.pop(0)
        if n.is_named and n.type == "identifier":
            return n
        queue.extend(n.children)
    return None
