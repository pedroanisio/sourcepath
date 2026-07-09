"""codebase_mapper.languages.ruby."""
from __future__ import annotations

from pathlib import PurePosixPath


from ...ts_setup import _TS_LANGS, _TS_QUERIES, _strip_quotes, _ts_setup
from ...ts_setup import TS_AVAILABLE, parse_error_diagnostics, ts
from ._treewalk import iter_named_pre_order

_DEF_KINDS = ("method", "singleton_method")
_CONTAINER_KINDS = ("class", "module")
_ITEM_KINDS = _DEF_KINDS + _CONTAINER_KINDS


def _node_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _ruby_item(kind: str, name: str, parent: str | None, node) -> dict:
    return {
        "kind": kind,
        "name": name,
        "parent": parent,
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "byte_start": node.start_byte,
        "byte_end": node.end_byte,
    }


def _ruby_params(param_list, content: bytes) -> list[dict]:
    """Expand a ``method_parameters``/``block_parameters`` node into ordered
    {name, type, default} records. ``type`` is always None — Ruby is untyped.
    Splat/double-splat/block params keep their ``*``/``**``/``&`` prefix on
    the name, matching the calling-convention-preserving contract used for
    Python's ``/``/``*`` separators."""
    out: list[dict] = []
    if param_list is None:
        return out
    for p in param_list.children:
        if not p.is_named:
            continue
        if p.type == "identifier":
            out.append({"name": _node_text(p, content), "type": None, "default": None})
        elif p.type == "optional_parameter":
            name_node = p.child_by_field_name("name")
            value_node = p.child_by_field_name("value")
            out.append({
                "name": _node_text(name_node, content) if name_node is not None else "",
                "type": None,
                "default": _node_text(value_node, content) if value_node is not None else None,
            })
        elif p.type == "splat_parameter":
            name_node = next((c for c in p.children if c.type == "identifier"), None)
            name = _node_text(name_node, content) if name_node is not None else ""
            out.append({"name": "*" + name, "type": None, "default": None})
        elif p.type == "hash_splat_parameter":
            name_node = next((c for c in p.children if c.type == "identifier"), None)
            name = _node_text(name_node, content) if name_node is not None else ""
            out.append({"name": "**" + name, "type": None, "default": None})
        elif p.type == "block_parameter":
            name_node = next((c for c in p.children if c.type == "identifier"), None)
            name = _node_text(name_node, content) if name_node is not None else ""
            out.append({"name": "&" + name, "type": None, "default": None})
        elif p.type == "keyword_parameter":
            name_node = p.child_by_field_name("name")
            value_node = p.child_by_field_name("value")
            out.append({
                "name": _node_text(name_node, content) if name_node is not None else "",
                "type": None,
                "default": _node_text(value_node, content) if value_node is not None else None,
            })
    return out


def _ruby_def_fields(node, content: bytes, *, is_singleton: bool) -> dict:
    """Signature fields for a ``method``/``singleton_method`` node.

    ``signature`` is the ``def`` header as written, up to (excluding) the
    first newline that opens the body — whitespace-collapsed to one line.
    """
    name_node = node.child_by_field_name("name")
    params_node = node.child_by_field_name("parameters")
    name = _node_text(name_node, content) if name_node is not None else ""
    rendered = _collapse(_node_text(params_node, content)) if params_node is not None else ""
    head = f"def self.{name}" if is_singleton else f"def {name}"
    signature = f"{head}{rendered}" if rendered else head
    fields: dict = {"signature": signature}
    params = _ruby_params(params_node, content)
    if params:
        fields["params"] = params
    return fields


def _ruby_container_fields(node, content: bytes, *, kind: str, body) -> dict:
    """Signature fields for a ``class``/``module`` node.

    ``signature`` is ``class Name < Super`` / ``module Name`` as written.
    ``bases`` is the superclass (if any) followed by ``include``d module
    names in source order — both are part of Ruby's method-resolution order,
    so both count as subtyping surface (unlike Go's composition-only struct
    embedding).
    """
    name_node = node.child_by_field_name("name")
    name = _node_text(name_node, content) if name_node is not None else ""
    head = f"{kind} {name}"
    superclass_node = node.child_by_field_name("superclass")
    bases: list[str] = []
    if superclass_node is not None:
        # superclass wraps "< Base"; the constant/scope_resolution is its
        # single named child.
        base_name = next((c for c in superclass_node.children if c.is_named), None)
        if base_name is not None:
            bases.append(_node_text(base_name, content))
            head += f" < {_node_text(base_name, content)}"
    if body is not None:
        for stmt in body.children:
            if stmt.type == "call":
                method_node = stmt.child_by_field_name("method")
                if method_node is not None and _node_text(method_node, content) == "include":
                    args = stmt.child_by_field_name("arguments")
                    if args is not None:
                        for a in args.children:
                            if a.is_named:
                                bases.append(_node_text(a, content))
    fields: dict = {"signature": head}
    if bases:
        fields["bases"] = bases
    return fields


_VISIBILITY_CALLS = {"private", "protected", "public"}


def _ruby_def_item(node, content: bytes, parent: str | None) -> dict:
    is_singleton = node.type == "singleton_method"
    name_node = node.child_by_field_name("name")
    name = _node_text(name_node, content) if name_node is not None else ""
    item = _ruby_item("method", name, parent, node)
    item.update(_ruby_def_fields(node, content, is_singleton=is_singleton))
    return item


def _ruby_container_item(node, content: bytes, parent: str | None) -> tuple[dict, object | None, str]:
    name_node = node.child_by_field_name("name")
    name = _node_text(name_node, content) if name_node is not None else ""
    body = node.child_by_field_name("body")
    item = _ruby_item("class", name, parent, node)
    item.update(_ruby_container_fields(node, content, kind=node.type, body=body))
    return item, body, name


def _collect_ruby_items(root, content: bytes) -> list[dict]:
    """One item per ``def``/``class``/``module``, with byte+line spans
    (powers L2 chunking + the symbol surface). Nested defs carry ``parent`` =
    the immediately enclosing class/module name.

    Visibility tracking: within a class/module body, a bare ``private`` /
    ``protected`` / ``public`` statement toggles the visibility applied to
    every subsequent method definition in that same body — explicit written
    evidence, never derived from the method name.

    Iterative (explicit stack over bodies, not recursive Python calls): a
    deeply-nested or generated Ruby file must not overflow the interpreter's
    recursion limit (see ``_treewalk`` module docstring for the same concern
    in other analyzers).
    """
    items: list[dict] = []
    # Stack of (body-like node whose direct children to scan, parent name).
    # The root program node has no visibility semantics but is scanned the
    # same way — a bare `private` at file scope is inert (harmless).
    stack: list[tuple] = [(root, None)]

    while stack:
        body, parent = stack.pop()
        current_vis: str | None = None
        for child in body.children:
            if not child.is_named:
                continue
            if child.type == "identifier" and _node_text(child, content) in _VISIBILITY_CALLS:
                word = _node_text(child, content)
                current_vis = None if word == "public" else word
                continue
            if child.type in _DEF_KINDS:
                item = _ruby_def_item(child, content, parent)
                if current_vis is not None:
                    item["visibility"] = current_vis
                items.append(item)
            elif child.type in _CONTAINER_KINDS:
                item, nested_body, name = _ruby_container_item(child, content, parent)
                items.append(item)
                if nested_body is not None:
                    stack.append((nested_body, name))
            else:
                # Search inside non-container statements (if/begin/case, …)
                # for defs/classes without recursing our own call stack.
                # Pruned at any found def/container: its own body is handled
                # by the branches above once yielded, so descending further
                # here would duplicate it (or mis-parent nested members).
                for desc in iter_named_pre_order(
                    child, descend=lambda n: n.type not in _ITEM_KINDS,
                ):
                    if desc is child:
                        continue
                    if desc.type in _DEF_KINDS:
                        items.append(_ruby_def_item(desc, content, parent))
                    elif desc.type in _CONTAINER_KINDS:
                        item, nested_body, name = _ruby_container_item(desc, content, parent)
                        items.append(item)
                        if nested_body is not None:
                            stack.append((nested_body, name))
    return items


def extract_ruby_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["ruby"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors = parse_error_diagnostics(tree.root_node)
    cursor = ts.QueryCursor(_TS_QUERIES["ruby"])
    captures = cursor.captures(tree.root_node)

    imports: list[dict] = []
    # Need to know which method was used; the @_m capture parallels @ruby_str.
    # In Python bindings, captures returns a dict by name, not paired matches.
    # Use matches() to get pairings.
    cursor2 = ts.QueryCursor(_TS_QUERIES["ruby"])
    for _pattern_idx, match in cursor2.matches(tree.root_node):
        m = match.get("_m")
        s = match.get("ruby_str")
        if m and s:
            method_node = m[0]
            str_node = s[0]
            method = content[method_node.start_byte:method_node.end_byte].decode("utf-8", "replace")
            source = _strip_quotes(content[str_node.start_byte:str_node.end_byte].decode("utf-8", "replace"))
            imports.append({"kind": method, "source": source,
                            "lineno": str_node.start_point[0] + 1})

    funcs: list[str] = []
    classes: list[str] = []
    for cap, nodes in captures.items():
        if cap == "func_name":
            for node in nodes:
                funcs.append(content[node.start_byte:node.end_byte].decode("utf-8", "replace"))
        elif cap == "class_name":
            for node in nodes:
                classes.append(content[node.start_byte:node.end_byte].decode("utf-8", "replace"))

    imports.sort(key=lambda x: (x["lineno"], x["source"]))
    items = _collect_ruby_items(tree.root_node, content)
    items.sort(key=lambda x: (x["line_start"], x["kind"],
                              x.get("parent") or "", x["name"]))
    return {
        "language": "ruby",
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
        "items": items,
    }, errors

def resolve_ruby_imports(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """Resolve require_relative to in-repo files; collect require strings as external."""
    dst: set[str] = set()
    unresolved: set[str] = set()
    src_dir = PurePosixPath(src_path).parent

    for imp in summary.get("imports", []):
        kind = imp["kind"]
        spec = imp["source"]
        if kind == "require_relative":
            raw = src_dir / spec
            norm: list[str] = []
            for part in raw.parts:
                if part == "..":
                    if norm and norm[-1] != "..":
                        norm.pop()
                elif part not in ("", "."):
                    norm.append(part)
            target = "/".join(norm)
            for cand in (target + ".rb", target):
                if cand in paths_set:
                    dst.add(cand)
                    break
        elif kind in ("require", "load"):
            # Take top-level name as the external package guess.
            top = spec.split("/", 1)[0]
            if top:
                # Try resolving as an in-repo absolute path first (Rails apps
                # often use require with project-rooted-style paths).
                target = spec if spec.endswith(".rb") else spec + ".rb"
                if target in paths_set:
                    dst.add(target)
                    continue
                unresolved.add(top.lower())
        # autoload: spec is usually a constant + relative path; complex, skip.
    return sorted(dst), sorted(unresolved)
