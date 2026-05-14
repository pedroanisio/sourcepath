"""Rust xref resolver — Stage 2 of the Rust first-class plan.

Edge kind covered: ``calls``. Subclass/overrides for Rust traits
(``impl Trait for Type``) need separate semantic design — Rust has no
inheritance and trait impls aren't quite subclassing — so they're
deferred to a later stage.

Resolver names on emitted edges:

  - ``rust_intra_file`` — target resolves to a top-level item in the
    same source file (function, struct method via inherent impl, etc.)
  - ``rust_inter_file`` — target resolves via a ``use`` binding whose
    module path lands on an in-repo crate member's source file

Call shapes in scope:

  - Bare identifier: ``foo()``                      [most common case]
  - Scoped tail:     ``Type::method()`` / ``mod::foo()``
                     (only the last ``::`` segment is matched today —
                     the path prefix is preserved for use-binding lookup)

Call shapes deliberately out of scope (Stage 2 narrowing):

  - Method calls on a receiver: ``obj.method()`` — tree-sitter renders
    this as ``call_expression { function: field_expression }``. Same
    restriction as the Python and TS/JS Stage-2 resolvers.
  - Macro invocations: ``vec![]``, ``println!()`` — these are
    ``macro_invocation`` nodes, not ``call_expression``. They never
    bind to a function-item the way calls do.
  - Closures invoked via ``(|...| ...)()`` — rare and adds no value.
  - Generic function paths ``foo::<T>()`` — the call site is still a
    scoped_identifier; we strip the type-args and use the last segment.

Unresolved is data: populated only for names that *were* ``use``
bindings whose target file or symbol couldn't be located. Bare names
that aren't imported (locals, builtins, captured variables) are
silently ignored — surfacing them would bury the signal.

Out of scope (deferred to later stages):
  - Glob imports: ``use foo::*``  — expand to no specific symbol.
  - ``super::foo()`` / ``self::foo()`` — Rust v0.3 resolver caveat;
    we leave the local-binding entry off the map so calls fall through
    to "unknown name" silence.
  - Method dispatch via traits (``T::method()`` where ``T`` is a
    generic parameter).
  - ``#[cfg(...)]``-gated ``use`` statements (the binding exists in
    the AST regardless of feature flags, so we treat them as present).
  - Calls inside ``mod foo { ... }`` blocks: the L2 chunker doesn't
    emit chunks for items nested inside inline mods, so the call site
    has no src_chunk_id to attach to. We skip silently rather than
    over-attribute.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from codebase_mapper.extensions import PipelineCtx
from codebase_mapper.models import (
    FileRecord, SymbolXrefEdge, UnresolvedSymbolRef,
)
from codebase_mapper.ts_setup import (
    TS_AVAILABLE, _ts_setup, _TS_LANGS, ts,
)


RESOLVER_INTRA = "rust_intra_file"
RESOLVER_INTER = "rust_inter_file"

_GLOBAL_TARGETS_CACHE_KEY = "_xrefs_rust_global_targets"


@dataclass(frozen=True)
class _UseBinding:
    """A ``use <module>::<target_name>[ as <local>];`` binding.

    For brace groups (``use foo::{bar, baz}``) we synthesize one
    binding per inner name with the same module prefix.
    """
    module: str        # path before the last ``::`` segment (e.g. "crate::foo")
    target_name: str   # the symbol imported (e.g. "bar")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def resolve_rust_calls(
    record: FileRecord, ctx: PipelineCtx,
) -> tuple[list[SymbolXrefEdge], list[UnresolvedSymbolRef]]:
    if not TS_AVAILABLE:
        return [], []
    if record.language != "rust":
        return [], []

    chunks_in_file = [
        c for c in cast(list, ctx.indices.get("l2_10_chunks", []))
        if c.get("path") == record.path
    ]
    if not chunks_in_file:
        return [], []

    content = ctx.read_path(record.path)
    _ts_setup()
    parser = ts.Parser(_TS_LANGS["rust"])
    tree = parser.parse(content)

    targets = _top_level_targets(chunks_in_file)
    src_lookup = _source_chunk_lookup(chunks_in_file)
    imports = _collect_rust_imports(tree.root_node, content)
    global_targets = _global_targets_cache(ctx)
    crates = cast(list, ctx.indices.get("host:rust_crates", []))
    paths_set = ctx.paths_set

    edges: list[SymbolXrefEdge] = []
    unresolved: list[UnresolvedSymbolRef] = []
    _walk_calls(
        tree.root_node, content, scope=[],
        edges=edges, unresolved=unresolved,
        targets=targets, src_lookup=src_lookup,
        imports=imports, global_targets=global_targets,
        crates=crates, paths_set=paths_set,
        src_path=record.path,
    )
    return edges, unresolved


# ---------------------------------------------------------------------------
# Chunk indices
# ---------------------------------------------------------------------------


def _top_level_targets(chunks_in_file: list[dict]) -> dict[str, str]:
    """Top-level callable target → chunk_id (same-file scope).

    For Rust the L2 chunker emits ``parent_symbol=None`` for items at
    the file root: functions (``function``), structs/enums/unions/traits
    (``class``), and impl blocks (``class``). Only functions are valid
    call targets; structs/enums/traits aren't called directly.
    """
    by_symbol: dict[str, tuple[str, int]] = {}
    for c in chunks_in_file:
        if c.get("parent_symbol") is not None:
            continue
        if c.get("kind") != "function":
            continue
        sym = c["symbol"]
        line = c.get("line_start", 0)
        if sym not in by_symbol or line > by_symbol[sym][1]:
            by_symbol[sym] = (c["chunk_id"], line)
    return {sym: cid for sym, (cid, _line) in by_symbol.items()}


def _source_chunk_lookup(chunks_in_file: list[dict]) -> dict[tuple[str, str | None], str]:
    out: dict[tuple[str, str | None], str] = {}
    for c in chunks_in_file:
        if c.get("kind") not in {"function", "method", "class"}:
            continue
        out[(c["symbol"], c.get("parent_symbol"))] = c["chunk_id"]
    return out


def _global_targets_cache(ctx: PipelineCtx) -> dict[tuple[str, str], str]:
    """(path, top_level_function_name) → chunk_id across the codebase.

    Cached on ``ctx.scratch`` so we only scan ``l2_10_chunks`` once per
    run. Mirrors the python/tsjs cache shape; key is separate so
    different resolvers can tune tie-breaks independently.
    """
    cached = ctx.scratch.get(_GLOBAL_TARGETS_CACHE_KEY)
    if cached is not None:
        return cast(dict, cached)
    out: dict[tuple[str, str], str] = {}
    by_key_line: dict[tuple[str, str], int] = {}
    for c in cast(list, ctx.indices.get("l2_10_chunks", [])):
        if c.get("parent_symbol") is not None:
            continue
        if c.get("kind") != "function":
            continue
        key = (c["path"], c["symbol"])
        line = c.get("line_start", 0)
        if key not in by_key_line or line > by_key_line[key]:
            out[key] = c["chunk_id"]
            by_key_line[key] = line
    ctx.scratch[_GLOBAL_TARGETS_CACHE_KEY] = out
    return out


# ---------------------------------------------------------------------------
# Import collection
# ---------------------------------------------------------------------------


def _text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _collect_rust_imports(root, content: bytes) -> dict[str, _UseBinding]:
    """Top-level ``use_declaration`` nodes → local-name → binding map.

    Supports:
      - ``use crate::foo::bar;``                → local "bar"
      - ``use crate::foo::bar as alias;``       → local "alias"
      - ``use crate::foo::{bar, baz};``         → locals "bar", "baz"
      - ``use crate::foo::{bar as a, baz as b}``→ locals "a", "b"

    Glob (``use foo::*;``) and re-exports (``pub use ...;``) are
    parsed and skipped — they don't yield a single resolvable target.
    """
    out: dict[str, _UseBinding] = {}
    for node in root.children:
        if node.type != "use_declaration":
            continue
        arg = node.child_by_field_name("argument")
        if arg is None:
            # Fallback: some grammar versions don't field-label the arg.
            arg = next((c for c in node.children if c.is_named and c.type != "visibility_modifier"), None)
        if arg is None:
            continue
        _expand_use_path(arg, content, prefix="", out=out)
    return out


def _expand_use_path(node, content: bytes, prefix: str, out: dict[str, _UseBinding]) -> None:
    """Recursively expand a ``use`` path tree into local→binding pairs.

    The grammar produces:
      - ``identifier`` / ``crate`` / ``self`` / ``super`` — leaf segment
      - ``scoped_identifier`` — ``path::name``
      - ``use_as_clause`` — ``path as alias``
      - ``use_list``       — ``{a, b, c}``
      - ``scoped_use_list``— ``prefix::{a, b, c}``
      - ``use_wildcard``   — ``prefix::*``

    ``prefix`` is the accumulated path string ending in ``::``
    (or empty at the root call).
    """
    nt = node.type
    if nt == "scoped_identifier":
        # path :: name
        path_node = node.child_by_field_name("path")
        name_node = node.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            return
        path_text = _text(path_node, content) if path_node is not None else ""
        full_prefix = (prefix + path_text) if path_text else prefix
        if full_prefix and not full_prefix.endswith("::"):
            full_prefix += "::"
        name = _text(name_node, content)
        # Edge case: e.g. `use std;` — a bare identifier at top, no `::`
        module = full_prefix.rstrip(":")
        out[name] = _UseBinding(module=module, target_name=name)
    elif nt == "identifier":
        # bare ``use foo;`` — rare but valid for crate-root items
        name = _text(node, content)
        out[name] = _UseBinding(module=prefix.rstrip(":"), target_name=name)
    elif nt == "use_as_clause":
        # The thing being aliased is in field "path"; the alias is "alias"
        path = node.child_by_field_name("path")
        alias = node.child_by_field_name("alias")
        if path is None or alias is None:
            return
        # Recurse on the path to compute target_name + module prefix.
        tmp: dict[str, _UseBinding] = {}
        _expand_use_path(path, content, prefix, tmp)
        if not tmp:
            return
        # Take the single entry we just produced; rebind under the alias.
        (orig_name, binding), = tmp.items()
        out[_text(alias, content)] = _UseBinding(
            module=binding.module, target_name=binding.target_name,
        )
    elif nt == "scoped_use_list":
        # prefix :: { list }
        path_node = node.child_by_field_name("path")
        list_node = node.child_by_field_name("list")
        if list_node is None:
            return
        path_text = _text(path_node, content) if path_node is not None else ""
        new_prefix = (prefix + path_text) if path_text else prefix
        if new_prefix and not new_prefix.endswith("::"):
            new_prefix += "::"
        for c in list_node.children:
            if c.is_named:
                _expand_use_path(c, content, new_prefix, out)
    elif nt == "use_list":
        for c in node.children:
            if c.is_named:
                _expand_use_path(c, content, prefix, out)
    elif nt == "use_wildcard":
        # ``foo::*`` — yields no specific symbol; deliberately skipped.
        return
    # Unrecognized — silently skip (forward-compat with grammar additions).


# ---------------------------------------------------------------------------
# Walk
# ---------------------------------------------------------------------------


def _walk_calls(
    node, content: bytes,
    *,
    scope: list[tuple[str, str]],
    edges: list[SymbolXrefEdge],
    unresolved: list[UnresolvedSymbolRef],
    targets: dict[str, str],
    src_lookup: dict[tuple[str, str | None], str],
    imports: dict[str, _UseBinding],
    global_targets: dict[tuple[str, str], str],
    crates: list[dict],
    paths_set: set[str],
    src_path: str,
) -> None:
    pushed = _push_scope(node, scope, content)

    if node.type == "call_expression":
        _emit_call(
            node, content, scope, edges, unresolved,
            targets=targets, src_lookup=src_lookup,
            imports=imports, global_targets=global_targets,
            crates=crates, paths_set=paths_set,
            src_path=src_path,
        )

    for child in node.children:
        if not child.is_named:
            continue
        _walk_calls(
            child, content, scope=scope,
            edges=edges, unresolved=unresolved,
            targets=targets, src_lookup=src_lookup,
            imports=imports, global_targets=global_targets,
            crates=crates, paths_set=paths_set,
            src_path=src_path,
        )

    if pushed:
        scope.pop()


def _push_scope(node, scope: list[tuple[str, str]], content: bytes) -> bool:
    """Push a scope frame for definition nodes that bound a call site.

    We track:
      - "function" for top-level ``function_item``
      - "method"   for ``function_item`` nested inside an ``impl_item``
                   or ``trait_item`` body (the outer "class" frame is
                   already on the stack)
      - "class"    for ``impl_item`` (uses the implementing type's name)
                   and for ``trait_item`` / ``struct_item`` etc.

    The decision between "function" and "method" mirrors the L2 chunker:
    a ``function_item`` directly under a ``declaration_list`` of an
    impl/trait is a method; one at file root is a function.
    """
    nt = node.type
    if nt == "function_item":
        name = _rust_name(node, content)
        if name is None:
            return False
        is_method = bool(scope) and scope[-1][0] == "class"
        scope.append(("method" if is_method else "function", name))
        return True
    if nt == "impl_item":
        name = _rust_name(node, content)
        if name is None:
            return False
        scope.append(("class", name))
        return True
    if nt in ("trait_item", "struct_item", "enum_item", "union_item"):
        name = _rust_name(node, content)
        if name is None:
            return False
        scope.append(("class", name))
        return True
    return False


def _rust_name(node, content: bytes) -> str | None:
    nn = node.child_by_field_name("name")
    if nn is not None:
        return _text(nn, content)
    tn = node.child_by_field_name("type")
    if tn is not None:
        return _text(tn, content)
    for c in node.children:
        if c.type in ("identifier", "type_identifier", "scoped_type_identifier"):
            return _text(c, content)
    return None


def _emit_call(
    call_node, content: bytes,
    scope: list[tuple[str, str]],
    edges: list[SymbolXrefEdge],
    unresolved: list[UnresolvedSymbolRef],
    *,
    targets: dict[str, str],
    src_lookup: dict[tuple[str, str | None], str],
    imports: dict[str, _UseBinding],
    global_targets: dict[tuple[str, str], str],
    crates: list[dict],
    paths_set: set[str],
    src_path: str,
) -> None:
    func = call_node.child_by_field_name("function")
    if func is None:
        return
    # Strip generic parameters: ``foo::<T>()`` → use the inner path.
    if func.type == "generic_function":
        inner = func.child_by_field_name("function")
        if inner is None:
            return
        func = inner

    if func.type == "identifier":
        name = _text(func, content)
    elif func.type == "scoped_identifier":
        name_node = func.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            return
        name = _text(name_node, content)
    else:
        return  # field_expression, macro_invocation, etc.

    src_id = _src_chunk_id(scope, src_lookup)
    if src_id is None:
        return

    # 1) Intra-file top-level function (Stage 2 doesn't bind to impl methods
    #    yet; method dispatch via ``obj.method()`` is out of scope and we
    #    don't try to guess inherent-impl resolution from a scoped call).
    dst_id = targets.get(name)
    if dst_id is not None:
        edges.append(SymbolXrefEdge(
            src_chunk_id=src_id, dst_chunk_id=dst_id,
            kind="calls", resolution="exact",
            resolver=RESOLVER_INTRA,
        ))
        return

    # 2) Imported via a ``use`` binding.
    binding = imports.get(name)
    if binding is None:
        return  # silent — bare name not from a use; locals/builtins

    raw_target = f"use {binding.module}::{binding.target_name}" if binding.module else f"use {binding.target_name}"

    target_path = _resolve_use_to_path(binding, crates, paths_set, src_path)
    if target_path is None:
        unresolved.append(UnresolvedSymbolRef(
            src_chunk_id=src_id, raw_target=raw_target,
            kind="calls", reason="module_not_in_repo",
            resolver=RESOLVER_INTER,
        ))
        return

    dst_id = global_targets.get((target_path, binding.target_name))
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


# ---------------------------------------------------------------------------
# Use-binding → in-repo file path
# ---------------------------------------------------------------------------


def _resolve_use_to_path(
    binding: _UseBinding,
    crates: list[dict],
    paths_set: set[str],
    src_path: str,
) -> str | None:
    """Map a ``use`` binding to an in-repo file path, mirroring the
    logic in ``codebase_mapper.languages.rust.resolve_rust_imports``
    but for a single binding.

    Returns the path of the .rs file that *would* contain the target
    symbol if it's in-repo, or None if the head crate isn't ours
    (third-party).
    """
    segs = [s for s in binding.module.split("::") if s]
    if not segs:
        return None
    head = segs[0]
    rest = segs[1:]

    # Determine the in-repo source root for this use.
    src_root: str | None = None
    if head == "crate":
        crate_dir = _crate_dir_for(src_path, crates)
        src_root = (crate_dir + "/src/") if crate_dir else "src/"
    elif head in ("super", "self"):
        # v0.3 caveat: don't resolve. Surfaces as "module_not_in_repo".
        return None
    else:
        # Try crate-name match (Cargo replaces hyphens with underscores
        # in lib names).
        name_to_dir = {}
        for c in crates:
            name_to_dir[c["name"]] = c["crate_dir"]
            name_to_dir[c["name"].replace("-", "_")] = c["crate_dir"]
        if head not in name_to_dir:
            return None  # external crate
        crate_dir = name_to_dir[head]
        src_root = (crate_dir + "/src/") if crate_dir else "src/"

    # Try resolution with various interpretations of where the module
    # ends and the item begins. (Same heuristic as resolve_rust_imports.)
    for take in range(len(rest), -1, -1):
        module_segs = rest[:take]
        base = src_root + "/".join(module_segs) if module_segs else src_root.rstrip("/")
        for cand in (base + ".rs", base + "/mod.rs", base + "/lib.rs"):
            if cand in paths_set:
                return cand
    # Last resort: root files of the crate
    for cand in (src_root + "lib.rs", src_root + "main.rs"):
        if cand in paths_set:
            return cand
    return None


def _crate_dir_for(src_path: str, crates: list[dict]) -> str:
    """Find the crate whose ``crate_dir`` is the longest path-prefix of
    ``src_path``. Mirrors ``crate_for_file`` in
    ``codebase_mapper.languages.rust``."""
    best, best_depth = "", -1
    for c in crates:
        d = c["crate_dir"]
        if d == "":
            if best_depth < 0:
                best, best_depth = "", 0
            continue
        if src_path == d or src_path.startswith(d + "/"):
            depth = len(d)
            if depth > best_depth:
                best, best_depth = d, depth
    return best
