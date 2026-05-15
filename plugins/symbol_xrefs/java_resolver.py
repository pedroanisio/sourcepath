"""Java symbol-xref resolver (Tier-1 Stage 2).

Edge kinds covered: ``calls``, ``subclassOf``, ``overrides``.

Resolver names on emitted edges:

  - ``java_intra_file`` — target resolves to a top-level type / a method
    inside the same file.
  - ``java_inter_file`` — target resolves via an ``import`` whose target
    file is in-repo, or via the same-package convention (Java allows
    sibling-class references without an explicit import).

Call shapes in scope:

  - Bare identifier call: ``foo()``           [member or statically-imported]
  - Constructor call:     ``new Foo(...)``    [binds to Foo's class chunk]
  - Receiver call:        ``Foo.method()``    [binds to Foo's class chunk;
                                              we do *not* attempt to bind
                                              the method itself — no type
                                              inference]
  - ``this.method()``                         [bound to same-class method]

Out of scope:

  - Method dispatch via interface (``var x = factory(); x.foo();``) —
    requires type inference.
  - Method references (``Foo::method``) — needs a separate AST walk;
    deferred.
  - Generic-method invocation type arguments (``foo.<T>method()``) —
    tree-sitter parses these without issue and we resolve the receiver
    normally.
  - ``import static`` resolution to a method-level chunk — we resolve to
    the *class* chunk that owns the imported member, same posture as
    Rust/TS Stage-2.

Unresolved is data: populated only for names that *are* import-bound
or same-class-bound but couldn't be located. Bare identifiers that
aren't imported and aren't same-class methods (variables, locals) are
silently ignored.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from codebase_mapper.emission.models import SymbolXrefEdge, UnresolvedSymbolRef
from codebase_mapper.inspection.models import FileRecord
from codebase_mapper.ts_setup import TS_AVAILABLE, _TS_LANGS, _ts_setup, ts


RESOLVER_INTRA = "java_intra_file"
RESOLVER_INTER = "java_inter_file"

_GLOBAL_TARGETS_CACHE_KEY = "_xrefs_java_global_targets"
_GLOBAL_METHODS_CACHE_KEY = "_xrefs_java_global_methods"


@dataclass(frozen=True)
class _JavaImport:
    """A single resolved import binding.

    ``simple_name`` — the trailing segment (the name bound in scope).
                       For ``import com.x.Foo;`` it is ``"Foo"``.
                       For ``import static com.x.Math.max;`` it is
                       ``"max"``.
                       For wildcard imports (``import com.x.*;``) it is
                       ``None`` — every name resolves opportunistically.
    ``target_path`` — file path the import lands on (``None`` if external).
    ``static``      — ``import static`` flag.
    ``wildcard``    — ``import com.x.*;`` flag.
    ``package``     — for wildcards, the package whose contents are bound.
    """
    simple_name: str | None
    target_path: str | None
    static: bool
    wildcard: bool
    package: str


def resolve_java_calls(
    record: FileRecord, ctx: PipelineCtx,
) -> tuple[list[SymbolXrefEdge], list[UnresolvedSymbolRef]]:
    if not TS_AVAILABLE:
        return [], []
    if record.language != "java":
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

    by_fqn = cast(dict, ctx.indices.get("host:java_fqn", {}))
    by_pkg = cast(dict, ctx.indices.get("host:java_packages", {}))

    imports = _resolve_imports(summary, by_fqn, by_pkg)
    src_lookup = _source_chunk_lookup(chunks_in_file)
    intra_targets = _top_level_targets(chunks_in_file)

    global_targets = _global_targets_cache(ctx)
    global_methods = _global_methods_cache(ctx)

    edges: list[SymbolXrefEdge] = []
    unresolved: list[UnresolvedSymbolRef] = []

    # ------- subclassOf + overrides --------------------------------------
    # Same-package siblings are name-only references (no import needed).
    own_pkg = summary.get("package", "") or ""
    same_pkg_files = [
        p for p in by_pkg.get(own_pkg, []) if p != record.path
    ] if own_pkg else []

    for it in items:
        if it["kind"] not in {"class", "interface", "enum", "record"}:
            continue
        src_id = src_lookup.get((it["name"], it.get("parent")))
        if src_id is None:
            continue
        resolved_bases: list[tuple[str, str, str]] = []  # (path, class, kind)
        ext = it.get("extends")
        if ext:
            r = _resolve_class_target(
                ext, record.path, intra_targets, imports,
                global_targets, same_pkg_files, by_pkg, own_pkg,
            )
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
        for impl in (it.get("implements") or []):
            r = _resolve_class_target(
                impl, record.path, intra_targets, imports,
                global_targets, same_pkg_files, by_pkg, own_pkg,
            )
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
                if m.get("parent") == it["name"]
                and m["kind"] in ("method", "constructor")
            ]
            for meth in class_methods:
                method_chunk_id = src_lookup.get((meth["name"], it["name"]))
                if method_chunk_id is None:
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
                        src_chunk_id=method_chunk_id,
                        dst_chunk_id=base_method,
                        kind="overrides", resolution="exact",
                        resolver=RESOLVER_INTRA if kind == "intra"
                        else RESOLVER_INTER,
                    ))

    # ------- calls --------------------------------------------------------
    # Parse the file once for the body walker.
    _ts_setup()
    lang = _TS_LANGS["java"]
    parser = ts.Parser(lang)
    try:
        content = ctx.read_path(record.path)
    except Exception:
        return edges, unresolved
    tree = parser.parse(content)
    root = tree.root_node

    # For every method/constructor item, find its body and walk for
    # call sites. We index by (parent_class, method_name, byte_start)
    # to attach each call edge to the correct source chunk.
    callable_items = [
        it for it in items
        if it["kind"] in {"method", "constructor"} and it.get("parent")
    ]
    for it in callable_items:
        src_id = src_lookup.get((it["name"], it.get("parent")))
        if src_id is None:
            continue
        # Find the AST node that corresponds to this item by exact
        # byte-span match (cheap).
        method_node = _find_node_by_span(root, it["byte_start"], it["byte_end"])
        if method_node is None:
            continue
        _walk_for_calls(
            method_node, content, src_id,
            class_name=it.get("parent"),
            record_path=record.path,
            intra_targets=intra_targets,
            imports=imports,
            global_targets=global_targets,
            global_methods=global_methods,
            same_pkg_files=same_pkg_files,
            by_pkg=by_pkg,
            own_pkg=own_pkg,
            src_lookup=src_lookup,
            items_by_class={
                it["parent"]: [m for m in items if m.get("parent") == it["parent"]]
                for it in items if it.get("parent")
            },
            edges=edges, unresolved=unresolved,
        )

    return edges, unresolved


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_imports(
    summary: dict,
    by_fqn: dict[str, str],
    by_pkg: dict[str, list[str]],
) -> list[_JavaImport]:
    out: list[_JavaImport] = []
    for imp in summary.get("imports", []):
        fqn = imp["source"]
        is_static = bool(imp.get("static"))
        is_wildcard = bool(imp.get("wildcard"))
        if is_wildcard:
            pkg = fqn
            out.append(_JavaImport(
                simple_name=None, target_path=None,
                static=is_static, wildcard=True, package=pkg,
            ))
            continue
        # `import static com.x.Math.max;` — strip the trailing member
        # to recover the type FQN; bind the simple name as ``max``.
        if is_static and "." in fqn:
            type_fqn, _, member = fqn.rpartition(".")
            target = by_fqn.get(type_fqn)
            pkg = type_fqn.rpartition(".")[0] if "." in type_fqn else ""
            out.append(_JavaImport(
                simple_name=member, target_path=target,
                static=True, wildcard=False, package=pkg,
            ))
            continue
        target = by_fqn.get(fqn)
        if target is None and "." in fqn:
            # Inner class fallback: try the parent FQN.
            parent = fqn.rsplit(".", 1)[0]
            target = by_fqn.get(parent)
        simple = fqn.rsplit(".", 1)[-1] if "." in fqn else fqn
        pkg = fqn.rsplit(".", 1)[0] if "." in fqn else ""
        out.append(_JavaImport(
            simple_name=simple, target_path=target,
            static=False, wildcard=False, package=pkg,
        ))
    return out


def _top_level_targets(chunks_in_file: list[dict]) -> dict[str, str]:
    """Top-level type name (no parent) → chunk_id."""
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


def _resolve_class_target(
    name: str,
    record_path: str,
    intra_targets: dict[str, str],
    imports: list[_JavaImport],
    global_targets: dict[tuple[str, str], str],
    same_pkg_files: list[str],
    by_pkg: dict[str, list[str]],
    own_pkg: str,
) -> tuple[str, str] | None:
    """Return (chunk_id, 'intra'|'inter') for a class-typed name reference.

    Order:
      1. Same-file top-level type.
      2. Same-package sibling.
      3. Explicit import binding.
      4. Wildcard import containing the name.
    """
    if name in intra_targets:
        return intra_targets[name], "intra"
    for p in same_pkg_files:
        cid = global_targets.get((p, name))
        if cid is not None:
            return cid, "inter"
    for imp in imports:
        if imp.wildcard:
            files = by_pkg.get(imp.package, [])
            for p in files:
                cid = global_targets.get((p, name))
                if cid is not None:
                    return cid, "inter"
            continue
        if imp.simple_name == name and imp.target_path:
            cid = global_targets.get((imp.target_path, name))
            if cid is not None:
                return cid, "inter"
    return None


def _path_for_chunk(chunk_id: str, global_targets: dict[tuple[str, str], str]) -> str:
    """Reverse-lookup the file path for a given chunk_id. Linear scan;
    only invoked once per resolved subclassOf edge — acceptable."""
    for (path, _name), cid in global_targets.items():
        if cid == chunk_id:
            return path
    return ""


def _find_node_by_span(root, start: int, end: int):
    """Find the first named descendant whose byte span exactly matches."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.start_byte == start and node.end_byte == end:
            return node
        if node.start_byte <= start and node.end_byte >= end:
            stack.extend(node.children)
    return None


def _walk_for_calls(
    method_node, content: bytes, src_id: str,
    *, class_name: str | None, record_path: str,
    intra_targets, imports, global_targets, global_methods,
    same_pkg_files, by_pkg, own_pkg, src_lookup,
    items_by_class,
    edges: list[SymbolXrefEdge],
    unresolved: list[UnresolvedSymbolRef],
) -> None:
    """Walk the method body looking for ``method_invocation`` and
    ``object_creation_expression`` nodes. Emit one edge per resolved
    call.
    """
    seen: set[tuple[str, str]] = set()

    def visit(node):
        if node.type == "method_invocation":
            _emit_method_invocation(
                node, content, src_id,
                class_name=class_name, record_path=record_path,
                intra_targets=intra_targets, imports=imports,
                global_targets=global_targets, global_methods=global_methods,
                same_pkg_files=same_pkg_files, by_pkg=by_pkg, own_pkg=own_pkg,
                src_lookup=src_lookup,
                items_by_class=items_by_class,
                edges=edges, unresolved=unresolved, seen=seen,
            )
        elif node.type == "object_creation_expression":
            _emit_object_creation(
                node, content, src_id,
                intra_targets=intra_targets, imports=imports,
                global_targets=global_targets, same_pkg_files=same_pkg_files,
                by_pkg=by_pkg,
                edges=edges, seen=seen,
            )
        for ch in node.children:
            if ch.is_named:
                visit(ch)

    visit(method_node)


def _emit_method_invocation(
    node, content: bytes, src_id: str, *, class_name, record_path,
    intra_targets, imports, global_targets, global_methods,
    same_pkg_files, by_pkg, own_pkg, src_lookup, items_by_class,
    edges, unresolved, seen,
) -> None:
    """Resolve one ``method_invocation`` AST node.

    Shapes:
      - bare:    ``foo()``         (one identifier child + argument_list)
      - this:    ``this.foo()``    (this + identifier + argument_list)
      - typed:   ``Foo.bar()``     (identifier + identifier + argument_list)
                 First identifier may name a class; if so, bind to its chunk.
      - chained: ``new Foo().bar()`` (object_creation + identifier + args)
                 ``new Foo()`` is handled separately by _emit_object_creation.
      - field:   ``obj.method()``  (identifier-as-var + identifier + args)
                 Receiver is an unknown variable — silent.
    """
    name_nodes = [c for c in node.children
                  if c.is_named and c.type == "identifier"]
    if not name_nodes:
        return
    if len(name_nodes) == 1:
        # Bare call.
        method_name = _txt(name_nodes[0], content)
        # Same-class method?
        if class_name:
            same_class = src_lookup.get((method_name, class_name))
            if same_class:
                key = (src_id, same_class)
                if key not in seen:
                    seen.add(key)
                    edges.append(SymbolXrefEdge(
                        src_chunk_id=src_id, dst_chunk_id=same_class,
                        kind="calls", resolution="exact",
                        resolver=RESOLVER_INTRA,
                    ))
                return
        # `import static X.foo;` — bind to X's class chunk.
        for imp in imports:
            if imp.static and imp.simple_name == method_name and imp.target_path:
                # Class FQN's last segment is in the package's bucket. Look
                # for the type in target_path's chunks.
                type_name_guess = imp.simple_name  # not actually the type
                # Better: scan global_targets for entries on imp.target_path
                # and use the first match (each file has 1 public top-level
                # type typically).
                for (path, cname), cid in global_targets.items():
                    if path == imp.target_path:
                        key = (src_id, cid)
                        if key not in seen:
                            seen.add(key)
                            edges.append(SymbolXrefEdge(
                                src_chunk_id=src_id, dst_chunk_id=cid,
                                kind="calls", resolution="heuristic",
                                resolver=RESOLVER_INTER,
                            ))
                        return
        return
    # Receiver-form. The receiver is the first identifier; the method
    # name is the last.
    method_name_node = name_nodes[-1]
    method_name = _txt(method_name_node, content)
    receiver_node = name_nodes[0]
    receiver = _txt(receiver_node, content)
    if receiver == "this":
        if class_name:
            same_class = src_lookup.get((method_name, class_name))
            if same_class:
                key = (src_id, same_class)
                if key not in seen:
                    seen.add(key)
                    edges.append(SymbolXrefEdge(
                        src_chunk_id=src_id, dst_chunk_id=same_class,
                        kind="calls", resolution="exact",
                        resolver=RESOLVER_INTRA,
                    ))
        return
    # Try receiver as a class name.
    cls = _resolve_class_target(
        receiver, record_path, intra_targets, imports,
        global_targets, same_pkg_files, by_pkg, own_pkg,
    )
    if cls is None:
        return
    cid, kind = cls
    # Try to bind specifically to the method on that class.
    target_path = _path_for_chunk(cid, global_targets) if kind == "inter" else record_path
    if target_path:
        method_id = global_methods.get((target_path, receiver, method_name))
        if method_id is not None:
            key = (src_id, method_id)
            if key not in seen:
                seen.add(key)
                edges.append(SymbolXrefEdge(
                    src_chunk_id=src_id, dst_chunk_id=method_id,
                    kind="calls", resolution="exact",
                    resolver=RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER,
                ))
            return
    # Fallback: bind to the class chunk (we know the receiver type but
    # not the method's chunk).
    key = (src_id, cid)
    if key not in seen:
        seen.add(key)
        edges.append(SymbolXrefEdge(
            src_chunk_id=src_id, dst_chunk_id=cid,
            kind="calls", resolution="heuristic",
            resolver=RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER,
        ))


def _emit_object_creation(
    node, content: bytes, src_id: str, *,
    intra_targets, imports, global_targets, same_pkg_files, by_pkg,
    edges, seen,
) -> None:
    """``new Foo(...)`` — bind to Foo's class chunk as a ``calls`` edge."""
    type_id = None
    for ch in node.children:
        if ch.is_named and ch.type == "type_identifier":
            type_id = ch
            break
    if type_id is None:
        return
    name = _txt(type_id, content)
    r = _resolve_class_target(
        name, "", intra_targets, imports,
        global_targets, same_pkg_files, by_pkg, "",
    )
    if r is None:
        return
    cid, kind = r
    key = (src_id, cid)
    if key in seen:
        return
    seen.add(key)
    edges.append(SymbolXrefEdge(
        src_chunk_id=src_id, dst_chunk_id=cid,
        kind="calls", resolution="exact",
        resolver=RESOLVER_INTRA if kind == "intra" else RESOLVER_INTER,
    ))


def _txt(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")
