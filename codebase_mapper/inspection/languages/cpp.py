"""codebase_mapper.languages.cpp — Tier-1 C++ support.

C++ shares the ``#include`` resolution machinery with C (see
``languages.c.resolve_c_includes``) but needs a richer AST walker for
classes / structs / templates / namespaces / out-of-class method
definitions. The analyzer surfaces:

  * ``imports``  — every ``#include "x"`` / ``#include <x>`` with a
                   ``kind`` of ``"local_include"`` or ``"system_include"``.
  * ``items``    — one record per type (class / struct / union / enum
                   / template instantiation target), per top-level
                   function, per in-class method, and per out-of-class
                   method definition (``Dog::speak``). Each item carries
                   ``line_start/end``, ``byte_start/end``, and ``parent``
                   (the enclosing class for methods, ``None`` for
                   top-level types/functions). Class items also carry
                   ``extends`` (the *first* public base class, mirroring
                   the single-inheritance convention used by the
                   xref-resolver's ``subclassOf`` semantics) and
                   ``implements`` (every additional base).

Namespace-aware naming: types and functions defined inside
``namespace foo`` get a synthetic ``namespace`` attribute set to
``foo`` (or ``foo::bar`` for nested namespaces). The xref resolver
uses this to disambiguate ``foo::Bar`` vs ``Bar`` at call sites.

Public surface mirrors the Java module:

  * ``extract_cpp_ast_summary(content, path) -> (summary, errors)``
  * ``build_cpp_symbol_index(records) -> dict``
  * ``refine_cpp_header_languages(records) -> list[FileRecord]``
                                  — sibling-aware re-tagging of ``.h``
                                    files in directories that contain
                                    C++ source as ``"cpp"``.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from ..models import FileRecord
from ...ts_setup import _TS_LANGS, _ts_setup, TS_AVAILABLE, ts


_TYPE_NODE_TYPES = {
    "class_specifier", "struct_specifier", "union_specifier",
    "enum_specifier",
}
# A function_definition at file scope (or inside a namespace) is a free
# function; the same node inside a field_declaration_list is an
# in-class method. We classify by enclosing context during the walk.


def _node_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


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


def _collect_includes(root, content: bytes) -> list[dict]:
    out: list[dict] = []
    # preproc_include is at translation-unit level (not nested in funcs).
    def visit(node):
        if node.type == "preproc_include":
            path_node = node.child_by_field_name("path")
            if path_node is not None:
                raw = _node_text(path_node, content).strip()
                if path_node.type == "string_literal":
                    # "header.h" — strip the quotes.
                    inner = raw[1:-1] if raw.startswith('"') and raw.endswith('"') else raw
                    out.append({
                        "kind": "local_include",
                        "source": inner,
                        "lineno": node.start_point[0] + 1,
                    })
                elif path_node.type == "system_lib_string":
                    inner = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
                    out.append({
                        "kind": "system_include",
                        "source": inner,
                        "lineno": node.start_point[0] + 1,
                    })
            return
        for ch in node.children:
            if ch.is_named:
                visit(ch)

    visit(root)
    out.sort(key=lambda x: (x["lineno"], x["source"]))
    return out


def _collect_base_classes(class_node, content: bytes) -> tuple[str | None, list[str]]:
    """Return ``(extends_first_base, implements_other_bases)``.

    C++ has no formal ``extends`` vs ``implements`` distinction — all
    base classes go through the same ``base_class_clause`` mechanism.
    We split them anyway: the first base becomes ``extends`` (matching
    the single-inheritance convention used by Java/Dart/Python in the
    xref resolver), and any additional bases become ``implements``
    (heuristic, since C++ multiple inheritance has no special semantics
    here).
    """
    clause = _find_first(class_node, "base_class_clause")
    if clause is None:
        return None, []
    bases: list[str] = []
    for ch in clause.children:
        if not ch.is_named:
            continue
        if ch.type == "type_identifier":
            bases.append(_node_text(ch, content))
        elif ch.type == "qualified_identifier":
            # Take the last segment as the simple name.
            seg = _node_text(ch, content)
            bases.append(seg.rsplit("::", 1)[-1])
    if not bases:
        return None, []
    return bases[0], bases[1:]


def _function_name(declarator, content: bytes) -> tuple[str | None, str | None]:
    """Pull the function name from a ``function_declarator``.

    Returns ``(simple_name, qualifier)``:
      * For ``foo()``           → ``("foo", None)``
      * For ``Dog::speak()``     → ``("speak", "Dog")``
      * For ``ns::sub::fn()``    → ``("fn", "ns::sub")``
      * For a destructor ``~Dog()`` → the analyzer represents it as
        ``destructor_name``; we surface ``("~Dog", None)``.
    """
    if declarator is None or declarator.type != "function_declarator":
        return None, None
    inner = declarator.child_by_field_name("declarator")
    if inner is None:
        return None, None
    if inner.type == "identifier":
        return _node_text(inner, content), None
    if inner.type == "field_identifier":
        return _node_text(inner, content), None
    if inner.type == "destructor_name":
        return _node_text(inner, content), None
    if inner.type == "operator_name":
        return _node_text(inner, content), None
    if inner.type == "qualified_identifier":
        # qualifier::name or namespace::Type::method
        # Walk innermost first.
        name_part = None
        qualifier_parts: list[str] = []
        # tree-sitter-cpp nests qualified_identifier left-associative.
        node = inner
        while node is not None and node.is_named:
            if node.type == "qualified_identifier":
                left = node.child_by_field_name("scope")
                right = node.child_by_field_name("name")
                if right is not None:
                    if right.type in {"identifier", "field_identifier",
                                       "destructor_name", "operator_name",
                                       "type_identifier"}:
                        name_part = _node_text(right, content)
                if left is not None:
                    if left.type == "qualified_identifier":
                        # Recurse left to flatten qualifiers.
                        node = left
                        continue
                    if left.type in {"namespace_identifier", "type_identifier",
                                      "identifier"}:
                        qualifier_parts.insert(0, _node_text(left, content))
                break
            break
        qualifier = "::".join(qualifier_parts) if qualifier_parts else None
        return name_part, qualifier
    # Templated function: try descending one level.
    inner2 = inner.child_by_field_name("declarator")
    if inner2 is not None:
        return _function_name(inner2, content)
    return None, None


def _emit_class_methods(class_node, content: bytes, class_name: str,
                        ns_qual: str, items: list[dict]) -> None:
    """Walk a class/struct/union body for nested types and methods."""
    body = _find_first(class_node, "field_declaration_list")
    if body is None:
        return
    for ch in body.children:
        if not ch.is_named:
            continue
        # A method body or signature.
        if ch.type == "function_definition":
            decl = _find_first(ch, "function_declarator")
            simple, qual = _function_name(decl, content)
            if simple:
                kind = "constructor" if simple == class_name else "method"
                if simple.startswith("~"):
                    kind = "destructor"
                items.append({
                    "kind": kind,
                    "name": simple,
                    "parent": class_name,
                    "namespace": ns_qual,
                    "line_start": ch.start_point[0] + 1,
                    "line_end": ch.end_point[0] + 1,
                    "byte_start": ch.start_byte,
                    "byte_end": ch.end_byte,
                })
        elif ch.type == "field_declaration":
            # Declaration-only methods: `void foo() const;` — the
            # function_declarator is a child of the field_declaration.
            decl = _find_descendant(ch, {"function_declarator"})
            if decl is None:
                continue
            simple, _qual = _function_name(decl, content)
            if simple is None:
                continue
            kind = "constructor" if simple == class_name else "method"
            if simple.startswith("~"):
                kind = "destructor"
            items.append({
                "kind": kind,
                "name": simple,
                "parent": class_name,
                "namespace": ns_qual,
                "line_start": ch.start_point[0] + 1,
                "line_end": ch.end_point[0] + 1,
                "byte_start": ch.start_byte,
                "byte_end": ch.end_byte,
            })
        elif ch.type in _TYPE_NODE_TYPES:
            # Nested type — recurse.
            _emit_type(ch, content, parent_class=class_name, ns_qual=ns_qual,
                       items=items)


def _emit_type(type_node, content: bytes, parent_class: str | None,
               ns_qual: str, items: list[dict]) -> None:
    name_node = _find_first(type_node, "type_identifier")
    if name_node is None:
        # Anonymous struct/union — skip.
        return
    name = _node_text(name_node, content)
    kind_map = {
        "class_specifier": "class",
        "struct_specifier": "struct",
        "union_specifier": "union",
        "enum_specifier": "enum",
    }
    kind = kind_map.get(type_node.type, "class")
    item = {
        "kind": kind,
        "name": name,
        "parent": parent_class,
        "namespace": ns_qual,
        "line_start": type_node.start_point[0] + 1,
        "line_end": type_node.end_point[0] + 1,
        "byte_start": type_node.start_byte,
        "byte_end": type_node.end_byte,
    }
    ext, impl = _collect_base_classes(type_node, content)
    if ext is not None:
        item["extends"] = ext
    if impl:
        item["implements"] = impl
    items.append(item)
    # Recurse into the body for methods + nested types.
    if kind in ("class", "struct", "union"):
        _emit_class_methods(type_node, content, name, ns_qual, items)


def _namespace_qual(parts: list[str]) -> str:
    return "::".join(parts)


def _walk_tu(node, content: bytes, ns_stack: list[str],
             items: list[dict]) -> None:
    """Walk a translation_unit / declaration_list. ``ns_stack`` is the
    *current* namespace nesting; pushed/popped on namespace boundaries.
    """
    if node.type == "namespace_definition":
        # Identify the namespace name (simple or nested).
        ns_name_node = _find_first(node, "namespace_identifier")
        nested = _find_first(node, "nested_namespace_specifier")
        if nested is not None:
            # `namespace foo::bar::baz` — record each level so an item
            # inside this block reports namespace = "foo::bar::baz".
            parts: list[str] = []
            for ch in nested.children:
                if ch.is_named and ch.type == "namespace_identifier":
                    parts.append(_node_text(ch, content))
            ns_stack = ns_stack + parts
        elif ns_name_node is not None:
            ns_stack = ns_stack + [_node_text(ns_name_node, content)]
        body = _find_first(node, "declaration_list")
        if body is not None:
            for ch in body.children:
                if ch.is_named:
                    _walk_tu(ch, content, ns_stack, items)
        return
    if node.type == "template_declaration":
        # Descend one level — the wrapped class/function is what we
        # actually want to record.
        for ch in node.children:
            if ch.is_named:
                _walk_tu(ch, content, ns_stack, items)
        return
    if node.type in _TYPE_NODE_TYPES:
        _emit_type(node, content, parent_class=None,
                   ns_qual=_namespace_qual(ns_stack), items=items)
        return
    if node.type == "function_definition":
        decl = _find_first(node, "function_declarator")
        simple, qualifier = _function_name(decl, content)
        if simple is None:
            return
        if qualifier is not None:
            # Out-of-class method definition: ``Type::method``.
            # Record as a method with parent=Type. The qualifier may
            # itself be a namespace_qualified type (e.g.
            # ``ns::Type::method``) — strip the leading namespace
            # segments and keep just the type name.
            parent_class = qualifier.rsplit("::", 1)[-1]
            kind = "method"
            if simple == parent_class:
                kind = "constructor"
            elif simple.startswith("~"):
                kind = "destructor"
            items.append({
                "kind": kind,
                "name": simple,
                "parent": parent_class,
                "namespace": _namespace_qual(ns_stack),
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "byte_start": node.start_byte,
                "byte_end": node.end_byte,
            })
        else:
            items.append({
                "kind": "function",
                "name": simple,
                "parent": None,
                "namespace": _namespace_qual(ns_stack),
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "byte_start": node.start_byte,
                "byte_end": node.end_byte,
            })
        return
    if node.type == "declaration":
        # `void foo();` at file scope — prototype only. Same shape as a
        # field_declaration for our purposes.
        decl = _find_descendant(node, {"function_declarator"})
        if decl is not None:
            simple, qualifier = _function_name(decl, content)
            if simple is not None and qualifier is None:
                items.append({
                    "kind": "function",
                    "name": simple,
                    "parent": None,
                    "namespace": _namespace_qual(ns_stack),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "byte_start": node.start_byte,
                    "byte_end": node.end_byte,
                })
        return
    # Recurse into anything that might contain a namespace_definition or
    # type/function declaration.
    for ch in node.children:
        if ch.is_named:
            _walk_tu(ch, content, ns_stack, items)


def extract_cpp_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["cpp"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors: list[str] = []
    if tree.root_node.has_error:
        errors.append("parse_errors_present")

    imports = _collect_includes(tree.root_node, content)
    items: list[dict] = []
    for ch in tree.root_node.children:
        if ch.is_named:
            _walk_tu(ch, content, [], items)
    items.sort(key=lambda x: (x["line_start"], x["kind"],
                              x.get("parent") or "", x["name"]))

    top_level_classes = sorted({
        it["name"] for it in items
        if it.get("parent") is None
        and it["kind"] in {"class", "struct", "union", "enum"}
    })
    top_level_functions = sorted({
        it["name"] for it in items
        if it.get("parent") is None and it["kind"] == "function"
    })

    # Surface the file's "primary namespace" — the namespace containing
    # the largest number of items (any depth). Useful for downstream
    # display ("file dog.cpp implements acme::Dog"). Out-of-class
    # method definitions live at namespace scope, so they're the
    # signal for source files; top-level types are the signal for
    # headers.
    ns_counts: dict[str, int] = defaultdict(int)
    for it in items:
        if it.get("namespace"):
            ns_counts[it["namespace"]] += 1
    primary_ns = max(ns_counts, key=ns_counts.get) if ns_counts else ""

    return {
        "language": "cpp",
        "namespace": primary_ns,
        "imports": imports,
        "top_level_functions": top_level_functions,
        "top_level_classes": top_level_classes,
        "items": items,
    }, errors


# ---------------------------------------------------------------------------
# Header-routing helper
# ---------------------------------------------------------------------------


def refine_cpp_header_languages(records: list[FileRecord]) -> None:
    """In-place: re-tag ``.h`` files as ``language="cpp"`` based on
    cross-repo evidence of C++ usage.

    Two passes:

      1. **Sibling rule** — a ``.h`` file in a directory that also
         contains any C++ source/header is C++. Catches the common
         layout where headers and implementations are co-resident
         (``src/foo.h`` + ``src/foo.cpp``).
      2. **Project-wide rule** — if *any* C++ source exists in the repo
         AND no sibling C source disambiguates it (no ``.c`` next to
         the header), the ``.h`` is treated as C++. This catches
         ``include/`` vs ``src/`` separations where the C++ source
         lives in a different directory from the public headers.

    Pure-C repos (no ``.cpp/.cc/.cxx``) are completely untouched.
    """
    has_cpp_source = any(
        r.language == "cpp"
        and PurePosixPath(r.path).suffix in {".cpp", ".cc", ".cxx", ".c++"}
        for r in records
    )
    if not has_cpp_source:
        return

    cpp_dirs: set[str] = set()
    c_source_dirs: set[str] = set()
    for r in records:
        d = str(PurePosixPath(r.path).parent)
        if r.language == "cpp":
            cpp_dirs.add(d)
        elif r.language == "c" and PurePosixPath(r.path).suffix == ".c":
            c_source_dirs.add(d)

    for r in records:
        if r.language != "c" or PurePosixPath(r.path).suffix != ".h":
            continue
        d = str(PurePosixPath(r.path).parent)
        # Pass 1: sibling rule.
        if d in cpp_dirs:
            r.language = "cpp"
            continue
        # Pass 2: project-wide rule, suppressed when a sibling .c file
        # at the same directory level rules it out.
        if d not in c_source_dirs:
            r.language = "cpp"


# ---------------------------------------------------------------------------
# Cross-file symbol index
# ---------------------------------------------------------------------------


def build_cpp_symbol_index(records: list[FileRecord]) -> dict[str, list[str]]:
    """Map a top-level class/struct/function name to the list of files
    where it's defined.

    Used by the xref resolver to bind ``new Foo(...)`` and ``Foo::bar()``
    receivers to their definition file. Ambiguous names (same class in
    two files) keep both entries; the resolver picks the first match.
    Multi-file definitions are common when a class is declared in ``.h``
    and methods are defined in ``.cpp`` — both files contribute.
    """
    out: dict[str, list[str]] = defaultdict(list)
    for r in records:
        if r.language != "cpp" or r.ast_summary is None:
            continue
        for it in r.ast_summary.get("items", []):
            if it.get("parent") is not None:
                continue
            if it["kind"] in {"class", "struct", "union", "enum", "function"}:
                if r.path not in out[it["name"]]:
                    out[it["name"]].append(r.path)
    return dict(out)
