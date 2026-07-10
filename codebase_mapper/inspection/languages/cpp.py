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

import re

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Callable

from ..models import FileRecord
from ...ts_setup import _TS_LANGS, _ts_setup, TS_AVAILABLE, parse_error_diagnostics, ts
from ._treewalk import find_named_descendant, iter_named_pre_order


_TYPE_NODE_TYPES = {
    "class_specifier", "struct_specifier", "union_specifier",
    "enum_specifier",
}
# A function_definition at file scope (or inside a namespace) is a free
# function; the same node inside a field_declaration_list is an
# in-class method. We classify by enclosing context during the walk.


def _node_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _cpp_unwrap_to_function_declarator(declarator):
    """Follow a ``pointer_declarator``/``reference_declarator`` chain down to
    the ``function_declarator`` it wraps (e.g. ``char *make()``), or ``None``
    if this declarator isn't a function at all."""
    node = declarator
    while node is not None and node.type in ("pointer_declarator", "reference_declarator"):
        node = node.child_by_field_name("declarator")
    return node if node is not None and node.type == "function_declarator" else None


def _cpp_end_before_body(node) -> int:
    """Byte offset where a declaration header ends: the earliest of its
    compound-statement body, constructor member-initializer-list, or
    trailing ``;`` — whichever is present."""
    candidates: list[int] = []
    body = node.child_by_field_name("body")
    if body is not None:
        candidates.append(body.start_byte)
    finit = next((c for c in node.children if c.type == "field_initializer_list"), None)
    if finit is not None:
        candidates.append(finit.start_byte)
    semi = next((c for c in node.children if c.type == ";"), None)
    if semi is not None:
        candidates.append(semi.start_byte)
    return min(candidates) if candidates else node.end_byte


def _cpp_params(param_list, content: bytes) -> list[dict]:
    """Expand a ``parameter_list`` into ordered {name, type, default} records.

    ``type`` is reconstructed by splicing the name identifier back out of the
    parameter's own text (preserving ``const``/pointer/reference decoration
    exactly as written); ``default`` comes from ``optional_parameter_declaration``'s
    ``default_value`` field. A variadic ``...`` becomes ``{"name": "", "type":
    "...", "default": None}``.
    """
    out: list[dict] = []
    if param_list is None:
        return out
    for p in param_list.children:
        if not p.is_named:
            continue
        if p.type == "variadic_parameter_declaration":
            out.append({"name": "", "type": "...", "default": None})
            continue
        if p.type not in ("parameter_declaration", "optional_parameter_declaration"):
            continue
        declarator = p.child_by_field_name("declarator")
        default_node = p.child_by_field_name("default_value")
        default = _collapse(_node_text(default_node, content)) if default_node is not None else None
        eq_node = next((c for c in p.children if c.type == "="), None)
        end = eq_node.start_byte if eq_node is not None else p.end_byte
        name_node = find_named_descendant(
            declarator, {"identifier", "field_identifier"}) if declarator is not None else None
        if name_node is None:
            ptype = _collapse(content[p.start_byte:end].decode("utf-8", "replace"))
            out.append({"name": "", "type": ptype, "default": default})
            continue
        pre = content[p.start_byte:name_node.start_byte].decode("utf-8", "replace")
        post = content[name_node.end_byte:end].decode("utf-8", "replace")
        out.append({
            "name": _node_text(name_node, content),
            "type": _collapse(pre + post),
            "default": default,
        })
    return out


def _cpp_callable_fields(node, type_field, fd, content: bytes,
                          type_params: list[str] | None = None) -> dict:
    end = _cpp_end_before_body(node)
    fields: dict = {"signature": _collapse(
        content[node.start_byte:end].decode("utf-8", "replace"))}
    params = _cpp_params(fd.child_by_field_name("parameters"), content)
    if params:
        fields["params"] = params
    if type_field is not None:
        returns = _collapse(content[type_field.start_byte:fd.start_byte].decode("utf-8", "replace"))
        if returns:
            fields["returns"] = returns
    if type_params:
        fields["type_params"] = type_params
    return fields


def _cpp_template_type_params(template_node, content: bytes) -> list[str]:
    tp_list = _find_first(template_node, "template_parameter_list")
    if tp_list is None:
        return []
    return [_collapse(_node_text(c, content)) for c in tp_list.children if c.is_named]


def _find_first(node, kind: str):
    for ch in node.children:
        if ch.is_named and ch.type == kind:
            return ch
    return None


def _find_descendant(node, kinds: set[str]):
    # Iterative (see _treewalk): same pre-order, root-inclusive first-match
    # semantics, but safe on deeply-nested subtrees.
    return find_named_descendant(node, kinds)


def _collect_includes(root, content: bytes) -> list[dict]:
    out: list[dict] = []
    # Iterative pre-order (see _treewalk): this walk descends into function
    # bodies, so a deeply-nested file would overflow a recursive visitor. Prune
    # at preproc_include (no nested includes), matching the old early ``return``.
    for node in iter_named_pre_order(root, descend=lambda n: n.type != "preproc_include"):
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
    """Walk a class/struct/union body for nested types and methods.

    Visibility tracking: an ``access_specifier`` (``public:``/``private:``/
    ``protected:``) toggles the label applied to every subsequent member in
    this same body — explicit written evidence only; a member appearing
    before the first label gets no ``visibility`` field (never defaulted
    from the enclosing ``class``/``struct``/``union`` keyword).
    """
    body = _find_first(class_node, "field_declaration_list")
    if body is None:
        return
    current_vis: str | None = None
    for ch in body.children:
        if not ch.is_named:
            continue
        if ch.type == "access_specifier":
            current_vis = _node_text(ch, content)
            continue
        # A method body or signature.
        if ch.type == "function_definition":
            decl = _cpp_unwrap_to_function_declarator(ch.child_by_field_name("declarator"))
            simple, qual = _function_name(decl, content)
            if simple:
                kind = "constructor" if simple == class_name else "method"
                if simple.startswith("~"):
                    kind = "destructor"
                fd = decl
                item = {
                    "kind": kind,
                    "name": simple,
                    "parent": class_name,
                    "namespace": ns_qual,
                    "line_start": ch.start_point[0] + 1,
                    "line_end": ch.end_point[0] + 1,
                    "byte_start": ch.start_byte,
                    "byte_end": ch.end_byte,
                }
                if fd is not None:
                    item.update(_cpp_callable_fields(
                        ch, ch.child_by_field_name("type"), fd, content))
                if current_vis is not None:
                    item["visibility"] = current_vis
                items.append(item)
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
            item = {
                "kind": kind,
                "name": simple,
                "parent": class_name,
                "namespace": ns_qual,
                "line_start": ch.start_point[0] + 1,
                "line_end": ch.end_point[0] + 1,
                "byte_start": ch.start_byte,
                "byte_end": ch.end_byte,
            }
            item.update(_cpp_callable_fields(
                ch, ch.child_by_field_name("type"), decl, content))
            if current_vis is not None:
                item["visibility"] = current_vis
            items.append(item)
        elif ch.type in _TYPE_NODE_TYPES:
            # Nested type — recurse.
            _emit_type(ch, content, parent_class=class_name, ns_qual=ns_qual,
                       items=items)


def _emit_type(type_node, content: bytes, parent_class: str | None,
               ns_qual: str, items: list[dict],
               type_params: list[str] | None = None) -> None:
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
    if ext is not None or impl:
        item["bases"] = ([ext] if ext is not None else []) + impl
    body = _find_first(type_node, "field_declaration_list") or _find_first(type_node, "enumerator_list")
    end = body.start_byte if body is not None else type_node.end_byte
    item["signature"] = _collapse(content[type_node.start_byte:end].decode("utf-8", "replace"))
    if type_params:
        item["type_params"] = type_params
    items.append(item)
    # Recurse into the body for methods + nested types.
    if kind in ("class", "struct", "union"):
        _emit_class_methods(type_node, content, name, ns_qual, items)


def _namespace_qual(parts: list[str]) -> str:
    return "::".join(parts)


def _walk_tu(node, content: bytes, ns_stack: list[str],
             items: list[dict], type_params: list[str] | None = None) -> None:
    """Walk a translation_unit / declaration_list. ``ns_stack`` is the
    *current* namespace nesting; pushed/popped on namespace boundaries.

    ``type_params`` carries a top-level ``template<...>`` parameter list one
    level down (from ``template_declaration`` to the class/function it
    wraps) — not propagated into nested recursion, so a template's own body
    doesn't leak its parameters onto unrelated siblings.
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
        tp = _cpp_template_type_params(node, content)
        for ch in node.children:
            if ch.is_named:
                _walk_tu(ch, content, ns_stack, items, type_params=tp)
        return
    if node.type in _TYPE_NODE_TYPES:
        _emit_type(node, content, parent_class=None,
                   ns_qual=_namespace_qual(ns_stack), items=items,
                   type_params=type_params)
        return
    if node.type == "function_definition":
        decl = _cpp_unwrap_to_function_declarator(node.child_by_field_name("declarator"))
        simple, qualifier = _function_name(decl, content)
        if simple is None:
            return
        callable_fields = (
            _cpp_callable_fields(node, node.child_by_field_name("type"), decl,
                                  content, type_params=type_params)
            if decl is not None else {}
        )
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
            item = {
                "kind": kind,
                "name": simple,
                "parent": parent_class,
                "namespace": _namespace_qual(ns_stack),
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "byte_start": node.start_byte,
                "byte_end": node.end_byte,
            }
            item.update(callable_fields)
            items.append(item)
        else:
            item = {
                "kind": "function",
                "name": simple,
                "parent": None,
                "namespace": _namespace_qual(ns_stack),
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "byte_start": node.start_byte,
                "byte_end": node.end_byte,
            }
            item.update(callable_fields)
            items.append(item)
        return
    if node.type == "declaration":
        # `void foo();` at file scope — prototype only. Same shape as a
        # field_declaration for our purposes.
        decl = _find_descendant(node, {"function_declarator"})
        if decl is not None:
            simple, qualifier = _function_name(decl, content)
            if simple is not None and qualifier is None:
                item = {
                    "kind": "function",
                    "name": simple,
                    "parent": None,
                    "namespace": _namespace_qual(ns_stack),
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "byte_start": node.start_byte,
                    "byte_end": node.end_byte,
                }
                item.update(_cpp_callable_fields(
                    node, node.child_by_field_name("type"), decl, content,
                    type_params=type_params))
                items.append(item)
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
    errors = parse_error_diagnostics(tree.root_node)

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
    primary_ns = max(ns_counts, key=lambda ns: ns_counts[ns]) if ns_counts else ""

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


#: Positive C++ evidence at the start of a line — constructs plain C can
#: never contain. Shared C constructs (#include, struct, comments) do NOT
#: count: this predicate gates the project-wide retag of ``.h`` files that
#: plain-C headers must never trip (flaw F20: 246 cpp files under the
#: kernel's tools/ armed the rule and claimed 13,536 pure-C headers).
_CPP_EVIDENCE_RE = re.compile(
    rb"(?m)^\s*(?:"
    rb"namespace\s+\w*\s*\{"
    rb"|template\s*<"
    rb"|class\s+\w+[^;{]*[;{:]"
    rb"|(?:public|private|protected)\s*:"
    rb"|virtual\s+\w"
    rb"|extern\s+\"C\+\+\""
    rb"|using\s+namespace\s"
    rb")"
)


def has_cpp_markers(content: bytes) -> bool:
    """True when *content* carries positive C++ evidence."""
    return _CPP_EVIDENCE_RE.search(content) is not None


def refine_cpp_header_languages(
    records: list[FileRecord],
    read_content: Callable[[str], bytes] | None = None,
) -> None:
    """In-place: re-tag ``.h`` files as ``language="cpp"`` based on
    cross-repo evidence of C++ usage.

    Two passes:

      1. **Sibling rule** — a ``.h`` file in a directory that also
         contains any C++ source/header is C++. Catches the common
         layout where headers and implementations are co-resident
         (``src/foo.h`` + ``src/foo.cpp``).
      2. **Project-wide rule** — if *any* C++ source exists in the repo,
         no sibling C source disambiguates the header, AND the header's
         own content carries C++ markers, the ``.h`` is treated as C++.
         This catches ``include/`` vs ``src/`` separations without
         repeating flaw F1's shape: a handful of C++ files elsewhere in
         a C repo must not flip plain-C headers repo-wide (F20). With no
         ``read_content`` accessor there is no evidence, so pass 2 does
         not fire.

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
        # Pass 2: project-wide rule — suppressed when a sibling .c file
        # rules it out, and gated on the header's own C++ evidence so a
        # C repo with incidental C++ tooling keeps its headers (F20).
        if d not in c_source_dirs and read_content is not None:
            try:
                content = read_content(r.path)
            except Exception:
                continue
            if has_cpp_markers(content):
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
