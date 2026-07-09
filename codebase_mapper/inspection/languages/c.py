"""codebase_mapper.languages.c."""
from __future__ import annotations

from pathlib import PurePosixPath


from ...ts_setup import _TS_LANGS, _TS_QUERIES, _strip_quotes, _ts_setup
from ...ts_setup import TS_AVAILABLE, ts
from ._treewalk import find_named_descendant, iter_named_pre_order

_DECL_KINDS = (
    "function_definition", "declaration",
    "struct_specifier", "union_specifier", "enum_specifier",
    "type_definition",
)
_AGGREGATE_KIND = {
    "struct_specifier": "struct", "union_specifier": "union",
    "enum_specifier": "enum",
}


def _node_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _c_item(kind: str, name: str, node) -> dict:
    return {
        "kind": kind,
        "name": name,
        "parent": None,
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "byte_start": node.start_byte,
        "byte_end": node.end_byte,
    }


def _c_end_before_body_or_semicolon(node, body) -> int:
    if body is not None:
        return body.start_byte
    semi = next((c for c in node.children if c.type == ";"), None)
    return semi.start_byte if semi is not None else node.end_byte


def _c_unwrap_to_function_declarator(declarator):
    """Follow a ``pointer_declarator`` chain down to the ``function_declarator``
    it wraps (e.g. ``char *make(void)``), or ``None`` if this declarator isn't
    a function at all (a plain variable declaration)."""
    node = declarator
    while node is not None and node.type == "pointer_declarator":
        node = node.child_by_field_name("declarator")
    return node if node is not None and node.type == "function_declarator" else None


def _c_declarator_name(declarator, content: bytes) -> str:
    """First identifier/type_identifier found in a declarator subtree —
    handles direct names and names wrapped in ``parenthesized_declarator``
    (function-pointer-typedef aliases)."""
    if declarator is None:
        return ""
    found = find_named_descendant(
        declarator, {"identifier", "type_identifier", "field_identifier"})
    return _node_text(found, content) if found is not None else ""


def _c_params(param_list, content: bytes) -> list[dict]:
    """Expand a ``parameter_list`` into ordered {name, type, default} records.

    ``type`` is reconstructed by splicing the name identifier back out of the
    parameter's full declaration text (preserving pointer stars / array
    brackets exactly as written); ``default`` is always None — C has no
    parameter defaults. A variadic ``...`` becomes ``{"name": "", "type":
    "...", "default": None}``.
    """
    out: list[dict] = []
    if param_list is None:
        return out
    for p in param_list.children:
        if not p.is_named:
            continue
        if p.type == "variadic_parameter":
            out.append({"name": "", "type": "...", "default": None})
            continue
        if p.type != "parameter_declaration":
            continue
        declarator = p.child_by_field_name("declarator")
        type_node = p.child_by_field_name("type")
        if declarator is None and type_node is not None and _node_text(type_node, content) == "void":
            continue  # f(void) is C for "no parameters", not one named ""
        full = _node_text(p, content)
        name_node = find_named_descendant(
            declarator, {"identifier", "field_identifier"}) if declarator is not None else None
        if name_node is None:
            out.append({"name": "", "type": _collapse(full), "default": None})
            continue
        name = _node_text(name_node, content)
        rel_start = name_node.start_byte - p.start_byte
        rel_end = name_node.end_byte - p.start_byte
        ptype = _collapse((full[:rel_start] + full[rel_end:]))
        out.append({"name": name, "type": ptype, "default": None})
    return out


def _c_callable_fields(node, type_field, fd, content: bytes) -> dict:
    end = _c_end_before_body_or_semicolon(node, node.child_by_field_name("body"))
    fields: dict = {"signature": _collapse(_node_text(node, content)[:end - node.start_byte])}
    params = _c_params(fd.child_by_field_name("parameters"), content)
    if params:
        fields["params"] = params
    if type_field is not None:
        returns = _collapse(content[type_field.start_byte:fd.start_byte].decode("utf-8", "replace"))
        if returns:
            fields["returns"] = returns
    return fields


def _collect_c_items(root, content: bytes) -> list[dict]:
    """One item per top-level function (definition or prototype), named
    struct/union/enum, and typedef — with byte+line spans (powers L2 chunking
    + the symbol surface).

    Iterative pre-order (see ``_treewalk``), pruned at every matched
    declaration kind: a ``type_definition``'s own subtree (which may contain
    an anonymous or named struct/union/enum specifier) is never independently
    re-visited, so a typedef'd aggregate is always exactly one item, under
    its alias name — never double-counted.
    """
    items: list[dict] = []
    for node in iter_named_pre_order(root, descend=lambda n: n.type not in _DECL_KINDS):
        nt = node.type
        if nt in ("function_definition", "declaration"):
            declarator = node.child_by_field_name("declarator")
            fd = _c_unwrap_to_function_declarator(declarator) if declarator is not None else None
            if fd is None:
                continue
            name = _c_declarator_name(fd.child_by_field_name("declarator"), content)
            if not name:
                continue
            item = _c_item("function", name, node)
            item.update(_c_callable_fields(node, node.child_by_field_name("type"), fd, content))
            items.append(item)
        elif nt in _AGGREGATE_KIND:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue  # anonymous, standalone (no typedef alias) — not addressable
            name = _node_text(name_node, content)
            item = _c_item(_AGGREGATE_KIND[nt], name, node)
            end = _c_end_before_body_or_semicolon(node, node.child_by_field_name("body"))
            item["signature"] = _collapse(content[node.start_byte:end].decode("utf-8", "replace"))
            items.append(item)
        elif nt == "type_definition":
            type_field = node.child_by_field_name("type")
            declarator_field = node.child_by_field_name("declarator")
            alias = _c_declarator_name(declarator_field, content) if declarator_field is not None else ""
            if type_field is not None and type_field.type in _AGGREGATE_KIND:
                inner_name_node = type_field.child_by_field_name("name")
                inner_name = _node_text(inner_name_node, content) if inner_name_node is not None else ""
                name = alias or inner_name
                if not name:
                    continue
                item = _c_item(_AGGREGATE_KIND[type_field.type], name, node)
                end = _c_end_before_body_or_semicolon(node, type_field.child_by_field_name("body"))
                item["signature"] = _collapse(content[node.start_byte:end].decode("utf-8", "replace"))
                items.append(item)
            else:
                if not alias:
                    continue
                end = _c_end_before_body_or_semicolon(node, None)
                item = _c_item("typedef", alias, node)
                item["signature"] = _collapse(content[node.start_byte:end].decode("utf-8", "replace"))
                items.append(item)
    return items


def extract_c_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["c"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors = ["parse_errors_present"] if tree.root_node.has_error else []
    cursor = ts.QueryCursor(_TS_QUERIES["c"])
    captures = cursor.captures(tree.root_node)

    imports: list[dict] = []
    funcs: list[str] = []
    classes: list[str] = []
    for cap, nodes in captures.items():
        for node in nodes:
            raw_text = content[node.start_byte:node.end_byte].decode("utf-8", "replace")
            if cap == "c_local_include":
                imports.append({"kind": "local_include", "source": _strip_quotes(raw_text),
                                "lineno": node.start_point[0] + 1})
            elif cap == "c_system_include":
                # <stdio.h> — strip the angle brackets
                s = raw_text.strip()
                if s.startswith("<") and s.endswith(">"):
                    s = s[1:-1]
                imports.append({"kind": "system_include", "source": s,
                                "lineno": node.start_point[0] + 1})
            elif cap == "func_name":
                funcs.append(raw_text)
            elif cap == "class_name":
                classes.append(raw_text)
    imports.sort(key=lambda x: (x["lineno"], x["source"]))
    items = _collect_c_items(tree.root_node, content)
    items.sort(key=lambda x: (x["line_start"], x["kind"], x["name"]))
    return {
        "language": "c",
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
        "items": items,
    }, errors

def resolve_c_includes(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """Resolve local #includes to in-repo files.

    Search order for #include "x.h" from /a/b/file.c:
    1. /a/b/x.h (relative to including file)
    2. /a/x.h (one level up — common when src/ uses ../include/foo.h)
    3. Any in-repo file whose path ends with /x.h or equals x.h
       (last resort; ambiguous matches dropped).
    """
    dst: set[str] = set()
    unresolved: set[str] = set()
    src_dir = PurePosixPath(src_path).parent

    # Build a basename → set-of-paths index, computed once per file is cheap
    # since this is called per-file but uses paths_set.
    for imp in summary.get("imports", []):
        if imp["kind"] == "system_include":
            unresolved.add(imp["source"])
            continue
        spec = imp["source"]
        # Try relative
        raw = src_dir / spec
        norm: list[str] = []
        for part in raw.parts:
            if part == "..":
                if norm and norm[-1] != "..":
                    norm.pop()
            elif part not in ("", "."):
                norm.append(part)
        target = "/".join(norm)
        if target in paths_set:
            dst.add(target)
            continue
        # Suffix match — accept only if unambiguous.
        basename = PurePosixPath(spec).name
        matches = [p for p in paths_set
                   if p == basename or p.endswith("/" + basename)]
        # The relative path we tried is already in `target` and didn't hit,
        # so the suffix match is necessarily a different location. Accept only
        # if exactly one match exists.
        if len(matches) == 1:
            dst.add(matches[0])
    return sorted(dst), sorted(unresolved)
