"""TypeScript / JavaScript ``calls`` resolver — intra-file + inter-file.

Two sub-resolvers share one entry point; the resolver name on each edge
tells you which path produced it:

  - ``tsjs_intra_file`` — call binds to a top-level function/class or a
    method in the same module.
  - ``tsjs_inter_file`` — call binds to a named ES6 import whose module
    resolves to an in-repo file and whose target is a top-level
    function/class chunk in that file.

The resolver re-parses with tree-sitter (cheap — same parser the L2
analyzer + L2 chunker use). The CST already lives in
``record.ast_summary["cst_json"]`` but doesn't carry node positions or
field accessors, so re-parsing is cleaner than rebuilding a usable tree
from the JSON form.

Scope-stack walk mirrors the Python resolver: descend into
function/class/method bodies, attribute call sites to the innermost
def. Method calls bind to ``(method_name, class_name)``; top-level
calls bind to ``(name, None)``.

Phase 8 in scope:
  - ES6 named imports: ``import { foo, bar as baz } from "./mod"``
  - Bare ``Identifier()`` call expressions

Phase 8 out of scope (mirrors Python's Phase 4 narrowing):
  - Default imports: ``import foo from "./mod"`` — default-export
    resolution requires walking the module's ``export default``; defer.
  - Namespace imports: ``import * as X from "./mod"; X.foo()`` —
    Attribute call, same restriction as Python's ``mod.foo()``.
  - CommonJS ``const X = require("./mod")`` — analogous to default
    imports; defer.
  - ``new ClassName()`` — call_expression wraps a ``new_expression``
    here, not a bare identifier; not a "calls" edge.
  - Method calls ``self.foo()`` / ``obj.foo()`` — Attribute, deferred.

Unresolved reasons follow the Python resolver: ``module_not_in_repo``,
``symbol_not_exported``. We populate ``unresolved`` only when the call
target *is* an import binding whose resolution failed — never for
arbitrary unknown identifiers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from codebase_mapper.extensions import PipelineCtx
from codebase_mapper.languages.tsjs import resolve_tsjs_import
from codebase_mapper.models import (
    FileRecord, SymbolXrefEdge, UnresolvedSymbolRef,
)
from codebase_mapper.ts_setup import (
    TS_AVAILABLE, _ts_grammar_for, _ts_setup, _TS_LANGS, ts,
)


RESOLVER_INTRA = "tsjs_intra_file"
RESOLVER_INTER = "tsjs_inter_file"

_GLOBAL_TARGETS_CACHE_KEY = "_xrefs_tsjs_global_targets"


@dataclass(frozen=True)
class _ImportBinding:
    """An ES6 named-import binding: ``import { target_name as local } from spec``."""
    spec: str          # the raw module specifier (e.g. "./foo", "@app/util")
    target_name: str   # the symbol name inside the imported module


def resolve_tsjs_calls(
    record: FileRecord, ctx: PipelineCtx,
) -> tuple[list[SymbolXrefEdge], list[UnresolvedSymbolRef]]:
    if not TS_AVAILABLE:
        return [], []
    grammar = _ts_grammar_for(record.path)
    if grammar not in ("typescript", "javascript", "tsx"):
        return [], []

    chunks_in_file = [
        c for c in cast(list, ctx.indices.get("l2_10_chunks", []))
        if c.get("path") == record.path
    ]
    if not chunks_in_file:
        return [], []

    content = ctx.read_path(record.path)
    _ts_setup()
    parser = ts.Parser(_TS_LANGS[grammar])
    tree = parser.parse(content)

    targets = _top_level_targets(chunks_in_file)
    src_lookup = _source_chunk_lookup(chunks_in_file)
    imports = _collect_imports(tree.root_node, content)
    global_targets = _global_targets_cache(ctx)
    tsconfigs = cast(dict, ctx.indices.get("host:tsconfigs", {}))
    paths_set = ctx.paths_set

    edges: list[SymbolXrefEdge] = []
    unresolved: list[UnresolvedSymbolRef] = []
    _walk_calls(
        tree.root_node, content, scope=[],
        edges=edges, unresolved=unresolved,
        targets=targets, src_lookup=src_lookup,
        imports=imports, global_targets=global_targets,
        tsconfigs=tsconfigs, paths_set=paths_set,
        src_path=record.path,
    )
    return edges, unresolved


# ---------------------------------------------------------------------------
# Chunk indices
# ---------------------------------------------------------------------------


def _top_level_targets(chunks_in_file: list[dict]) -> dict[str, str]:
    """Map a top-level symbol name → its chunk_id (same-file scope).

    Top-level = ``parent_symbol`` is None and ``kind`` ∈ {function, class}.
    Shadowing is rare in TS/JS top level; later-line wins to match the
    runtime binding semantics.
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
    out: dict[tuple[str, str | None], str] = {}
    for c in chunks_in_file:
        if c.get("kind") not in {"function", "method", "class"}:
            continue
        out[(c["symbol"], c.get("parent_symbol"))] = c["chunk_id"]
    return out


def _global_targets_cache(ctx: PipelineCtx) -> dict[tuple[str, str], str]:
    """(path, top_level_symbol) → chunk_id across the whole codebase.

    Cached on ctx.scratch so we only scan ``l2_10_chunks`` once per run.
    The Python resolver maintains its own copy with the same shape; the
    two are independent because they need different sort tie-breaks.
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
# Import collection
# ---------------------------------------------------------------------------


def _collect_imports(root, content: bytes) -> dict[str, _ImportBinding]:
    """Walk top-level ``import_statement`` nodes; return local → binding.

    Only ES6 named imports are tracked (Phase 8 scope). Type-only imports
    (``import type {...}``) are skipped — they don't bind callable values
    at runtime.
    """
    out: dict[str, _ImportBinding] = {}
    for node in root.children:
        if node.type != "import_statement":
            continue
        if _is_type_only_import(node):
            continue
        spec = _import_source(node, content)
        if not spec:
            continue
        clause = next((c for c in node.children if c.type == "import_clause"), None)
        if clause is None:
            continue
        for c in clause.children:
            if c.type != "named_imports":
                continue
            for spec_node in c.children:
                if spec_node.type != "import_specifier":
                    continue
                ids = [n for n in spec_node.children if n.type == "identifier"]
                if not ids:
                    continue
                origin = _text(ids[0], content)
                local = _text(ids[1], content) if len(ids) > 1 else origin
                out[local] = _ImportBinding(spec=spec, target_name=origin)
    return out


def _is_type_only_import(import_node) -> bool:
    """``import type { ... } from ...`` is type-only at the statement level."""
    for c in import_node.children:
        # The anonymous `type` keyword appears right after `import` for
        # statement-level type-only imports.
        if not c.is_named and c.type == "type":
            return True
        if c.is_named:
            # We've reached named children; the keyword (if any) was
            # immediately after `import`, so stop scanning.
            break
    return False


def _import_source(import_node, content: bytes) -> str | None:
    src_node = next((c for c in import_node.children if c.type == "string"), None)
    if src_node is None:
        return None
    frag = next((c for c in src_node.children if c.type == "string_fragment"), None)
    if frag is None:
        return None
    return _text(frag, content)


def _text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


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
    imports: dict[str, _ImportBinding],
    global_targets: dict[tuple[str, str], str],
    tsconfigs: dict,
    paths_set: set[str],
    src_path: str,
) -> None:
    pushed = _push_scope(node, scope, content)
    if node.type == "call_expression":
        _emit_call(
            node, content, scope, edges, unresolved,
            targets=targets, src_lookup=src_lookup,
            imports=imports, global_targets=global_targets,
            tsconfigs=tsconfigs, paths_set=paths_set,
            src_path=src_path,
        )
    for child in node.children:
        if not child.is_named:
            continue
        _walk_calls(
            child, content,
            scope=scope, edges=edges, unresolved=unresolved,
            targets=targets, src_lookup=src_lookup,
            imports=imports, global_targets=global_targets,
            tsconfigs=tsconfigs, paths_set=paths_set,
            src_path=src_path,
        )
    if pushed:
        scope.pop()


def _push_scope(node, scope: list[tuple[str, str]], content: bytes) -> bool:
    """Push the right scope frame for definition nodes; return True if pushed.

    Mirrors the chunker's decisions so the (kind, name) frame matches the
    chunk identity. Method frames inherit their parent class from the
    previous frame at lookup time.
    """
    if node.type == "class_declaration":
        name = _class_decl_name(node, content)
        if name:
            scope.append(("class", name))
            return True
    elif node.type == "function_declaration":
        name = _named_child_text(node, "identifier", content)
        if name:
            scope.append(("function", name))
            return True
    elif node.type == "method_definition":
        name = _named_child_text(node, "property_identifier", content)
        if name:
            scope.append(("method", name))
            return True
    elif node.type in ("lexical_declaration", "variable_declaration"):
        # `const foo = () => ...` / `const foo = function () { ... }`
        # — push a function frame, but ONLY if exactly one declarator
        # with a function-shaped value (multiple bindings on one line is
        # rare and harmless to skip).
        names = _decl_function_bindings(node, content)
        if len(names) == 1:
            scope.append(("function", names[0]))
            return True
    return False


def _class_decl_name(class_node, content: bytes) -> str | None:
    return (
        _named_child_text(class_node, "type_identifier", content)
        or _named_child_text(class_node, "identifier", content)
    )


def _named_child_text(node, child_type: str, content: bytes) -> str | None:
    for c in node.children:
        if c.type == child_type:
            return _text(c, content)
    return None


def _decl_function_bindings(decl_node, content: bytes) -> list[str]:
    out: list[str] = []
    for c in decl_node.children:
        if c.type != "variable_declarator":
            continue
        name_node = next((n for n in c.children if n.type == "identifier"), None)
        value = c.child_by_field_name("value")
        if name_node is None or value is None:
            continue
        if value.type in ("arrow_function", "function_expression"):
            out.append(_text(name_node, content))
    return out


def _emit_call(
    call_node, content: bytes,
    scope: list[tuple[str, str]],
    edges: list[SymbolXrefEdge],
    unresolved: list[UnresolvedSymbolRef],
    *,
    targets: dict[str, str],
    src_lookup: dict[tuple[str, str | None], str],
    imports: dict[str, _ImportBinding],
    global_targets: dict[tuple[str, str], str],
    tsconfigs: dict,
    paths_set: set[str],
    src_path: str,
) -> None:
    # Phase 8 only attempts bare-identifier call targets. Member access
    # calls (`obj.foo()`, `self.foo()`) and `new` expressions fall to
    # later phases.
    func = call_node.child_by_field_name("function")
    if func is None or func.type != "identifier":
        return
    name = _text(func, content)

    src_id = _src_chunk_id(scope, src_lookup)
    if src_id is None:
        return

    # Intra-file wins.
    dst_id = targets.get(name)
    if dst_id is not None:
        edges.append(SymbolXrefEdge(
            src_chunk_id=src_id,
            dst_chunk_id=dst_id,
            kind="calls",
            resolution="exact",
            resolver=RESOLVER_INTRA,
        ))
        return

    binding = imports.get(name)
    if binding is None:
        return

    target_path = resolve_tsjs_import(src_path, binding.spec, paths_set, tsconfigs)
    if target_path is None:
        unresolved.append(UnresolvedSymbolRef(
            src_chunk_id=src_id,
            raw_target=f'import {{ {binding.target_name} }} from "{binding.spec}"',
            kind="calls",
            reason="module_not_in_repo",
            resolver=RESOLVER_INTER,
        ))
        return

    dst_id = global_targets.get((target_path, binding.target_name))
    if dst_id is None:
        unresolved.append(UnresolvedSymbolRef(
            src_chunk_id=src_id,
            raw_target=f'import {{ {binding.target_name} }} from "{binding.spec}"',
            kind="calls",
            reason="symbol_not_exported",
            resolver=RESOLVER_INTER,
        ))
        return

    edges.append(SymbolXrefEdge(
        src_chunk_id=src_id,
        dst_chunk_id=dst_id,
        kind="calls",
        resolution="exact",
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
