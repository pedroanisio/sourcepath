"""codebase_mapper.languages.kotlin."""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Callable


from ..models import FileRecord
from ...ts_setup import _TS_LANGS, _TS_QUERIES, _ts_setup
from ...ts_setup import TS_AVAILABLE, ts


_DEF_KINDS = ("function_declaration",)
_CONTAINER_KINDS = ("class_declaration", "object_declaration")
_VISIBILITY_WORDS = {"private", "protected", "public", "internal"}


def _kt_node_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _kt_item(kind: str, name: str, parent: str | None, node) -> dict:
    return {
        "kind": kind,
        "name": name,
        "parent": parent,
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "byte_start": node.start_byte,
        "byte_end": node.end_byte,
    }


def _kt_modifiers(node, content: bytes) -> tuple[str | None, bool]:
    """(visibility, is_suspend) from a declaration's ``modifiers`` child, if
    present — explicit written keywords only."""
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    if modifiers is None:
        return None, False
    vis: str | None = None
    is_suspend = False
    for m in modifiers.children:
        if m.type == "visibility_modifier":
            word = _kt_node_text(m, content)
            if word in _VISIBILITY_WORDS:
                vis = word
        elif m.type == "function_modifier" and _kt_node_text(m, content) == "suspend":
            is_suspend = True
    return vis, is_suspend


def _kt_type_params(node, content: bytes) -> list[str]:
    tp = next((c for c in node.children if c.type == "type_parameters"), None)
    if tp is None:
        return []
    return [_kt_node_text(c, content) for c in tp.children
            if c.is_named and c.type == "type_parameter"]


def _kt_params(func_decl, content: bytes) -> list[dict]:
    """Expand a ``function_value_parameters`` node into ordered {name, type,
    default} records. A default value and a ``vararg`` modifier are *siblings*
    of the ``parameter`` node in this grammar (not children of it), so they
    are consumed by scanning the flat child sequence around each parameter."""
    plist = next((c for c in func_decl.children if c.type == "function_value_parameters"), None)
    out: list[dict] = []
    if plist is None:
        return out
    children = list(plist.children)
    n = len(children)
    pending_vararg = False
    i = 0
    while i < n:
        c = children[i]
        if c.type == "parameter_modifiers":
            if any(_kt_node_text(m, content) == "vararg"
                   for m in c.children if m.is_named):
                pending_vararg = True
            i += 1
            continue
        if c.type != "parameter":
            i += 1
            continue
        name_node = next((ch for ch in c.children if ch.type == "identifier"), None)
        name = _kt_node_text(name_node, content) if name_node is not None else ""
        if pending_vararg:
            name = "vararg " + name
            pending_vararg = False
        colon_idx = next((j for j, ch in enumerate(c.children) if ch.type == ":"), None)
        ptype = None
        if colon_idx is not None:
            type_nodes = [ch for ch in c.children[colon_idx + 1:] if ch.is_named]
            if type_nodes:
                ptype = _collapse(_kt_node_text(type_nodes[0], content))
        default = None
        if i + 1 < n and children[i + 1].type == "=":
            if i + 2 < n and children[i + 2].is_named:
                default = _collapse(_kt_node_text(children[i + 2], content))
                i += 2
        out.append({"name": name, "type": ptype, "default": default})
        i += 1
    return out


def _kt_callable_fields(node, content: bytes) -> dict:
    children = list(node.children)
    plist_idx = next((i for i, c in enumerate(children)
                       if c.type == "function_value_parameters"), None)
    body = next((c for c in children if c.type == "function_body"), None)
    end = body.start_byte if body is not None else node.end_byte
    fields: dict = {"signature": _collapse(
        _kt_node_text(node, content)[:end - node.start_byte])}
    if plist_idx is not None:
        params = _kt_params(node, content)
        if params:
            fields["params"] = params
        for i in range(plist_idx + 1, len(children)):
            if children[i].type == ":":
                for j in range(i + 1, len(children)):
                    if children[j].is_named and children[j].type != "function_body":
                        fields["returns"] = _collapse(_kt_node_text(children[j], content))
                        break
                break
    type_params = _kt_type_params(node, content)
    if type_params:
        fields["type_params"] = type_params
    vis, is_suspend = _kt_modifiers(node, content)
    if vis:
        fields["visibility"] = vis
    if is_suspend:
        fields["is_async"] = True
    return fields


def _kt_container_fields(node, content: bytes, *, body) -> dict:
    end = body.start_byte if body is not None else node.end_byte
    fields: dict = {"signature": _collapse(
        _kt_node_text(node, content)[:end - node.start_byte])}
    delegation = next((c for c in node.children if c.type == "delegation_specifiers"), None)
    bases: list[str] = []
    if delegation is not None:
        for spec in delegation.children:
            if spec.type != "delegation_specifier":
                continue
            ut = next((c for c in spec.children if c.type == "user_type"), None)
            if ut is None:
                inv = next((c for c in spec.children if c.type == "constructor_invocation"), None)
                if inv is not None:
                    ut = next((c for c in inv.children if c.type == "user_type"), None)
            if ut is not None:
                bases.append(_kt_node_text(ut, content))
    if bases:
        fields["bases"] = bases
    type_params = _kt_type_params(node, content)
    if type_params:
        fields["type_params"] = type_params
    vis, _ = _kt_modifiers(node, content)
    if vis:
        fields["visibility"] = vis
    return fields


def _collect_kotlin_items(root, content: bytes) -> list[dict]:
    """One item per top-level/member ``fun`` and ``class``/``interface``/
    ``object``, with byte+line spans (powers L2 chunking + the symbol
    surface). Iterative (explicit stack over bodies): a deeply-nested or
    generated Kotlin file must not overflow the interpreter's recursion
    limit (see ``_treewalk`` module docstring for the same concern in other
    analyzers)."""
    items: list[dict] = []
    stack: list[tuple] = [(root, None)]
    while stack:
        scope, parent = stack.pop()
        for child in scope.children:
            if not child.is_named:
                continue
            if child.type in _DEF_KINDS:
                name_node = child.child_by_field_name("name")
                name = _kt_node_text(name_node, content) if name_node is not None else ""
                kind = "method" if parent is not None else "function"
                item = _kt_item(kind, name, parent, child)
                item.update(_kt_callable_fields(child, content))
                items.append(item)
            elif child.type in _CONTAINER_KINDS:
                name_node = child.child_by_field_name("name")
                name = _kt_node_text(name_node, content) if name_node is not None else ""
                cbody = next((c for c in child.children if c.type == "class_body"), None)
                item = _kt_item("class", name, parent, child)
                item.update(_kt_container_fields(child, content, body=cbody))
                items.append(item)
                if cbody is not None:
                    stack.append((cbody, name))
    return items


def extract_kotlin_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["kotlin"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors = ["parse_errors_present"] if tree.root_node.has_error else []
    cursor = ts.QueryCursor(_TS_QUERIES["kotlin"])
    captures = cursor.captures(tree.root_node)

    # Also pick up the package_header so we can compute the file's own package.
    pkg_q = ts.Query(lang, "(package_header (qualified_identifier) @pkg)")
    pkg_cursor = ts.QueryCursor(pkg_q)
    pkg_captures = pkg_cursor.captures(tree.root_node)
    package_name = ""
    if "pkg" in pkg_captures and pkg_captures["pkg"]:
        node = pkg_captures["pkg"][0]
        package_name = content[node.start_byte:node.end_byte].decode("utf-8", "replace")

    imports: list[dict] = []
    funcs: list[str] = []
    classes: list[str] = []
    for cap, nodes in captures.items():
        for node in nodes:
            text = content[node.start_byte:node.end_byte].decode("utf-8", "replace")
            if cap == "kt_import":
                imports.append({"kind": "import", "source": text,
                                "lineno": node.start_point[0] + 1})
            elif cap == "func_name":
                funcs.append(text)
            elif cap == "class_name":
                classes.append(text)
    imports.sort(key=lambda x: (x["lineno"], x["source"]))
    items = _collect_kotlin_items(tree.root_node, content)
    items.sort(key=lambda x: (x["line_start"], x["kind"],
                              x.get("parent") or "", x["name"]))
    return {
        "language": "kotlin",
        "package": package_name,
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
        "items": items,
    }, errors

def build_kotlin_fqn_index(records: list[FileRecord], read: Callable[[str], bytes]) -> dict[str, str]:
    """Map fully-qualified class name (package + ClassName) -> file path.

    Only one quick pass: read each Kotlin file's package_header + top-level
    class names from its AST summary (already computed). Ambiguous → dropped.
    """
    cand: dict[str, list[str]] = defaultdict(list)
    for r in records:
        if r.language != "kotlin" or r.ast_summary is None:
            continue
        pkg = r.ast_summary.get("package", "") or ""
        for cls in r.ast_summary.get("top_level_classes", []):
            fqn = f"{pkg}.{cls}" if pkg else cls
            cand[fqn].append(r.path)
        # Also register just the package + filename without ext as a fallback.
        stem = PurePosixPath(r.path).stem
        fqn_file = f"{pkg}.{stem}" if pkg else stem
        cand[fqn_file].append(r.path)
    return {k: v[0] for k, v in cand.items() if len(set(v)) == 1}

def resolve_kotlin_imports(
    src_path: str, summary: dict, by_fqn: dict[str, str],
    declared_pkgs: set[str],
) -> tuple[list[str], list[str], list[str]]:
    """Resolve Kotlin imports.

    Returns (in_repo_paths, external_package_coords, prefix_matched_coords).
    The third element lists coordinates whose match was by group-prefix
    (not exact FQN), so the caller can mark them in the run manifest.
    """
    # Index declared coords by group prefix. Maven coords look like
    # "group:name"; Kotlin FQNs are dotted. A coord matches a FQN when the
    # FQN starts with `group.`. Longest group wins on ambiguity.
    coord_by_group: list[tuple[str, str]] = []
    for coord in declared_pkgs:
        if ":" in coord:
            group = coord.split(":", 1)[0]
            coord_by_group.append((group, coord))
    # Sort longest group first; on tie, sort coord lexicographically so the
    # match is deterministic across runs (declared_pkgs is a set with
    # non-deterministic iteration order).
    coord_by_group.sort(key=lambda x: (-len(x[0]), x[1]))

    dst: set[str] = set()
    exact_ext: set[str] = set()
    prefix_ext: set[str] = set()
    for imp in summary.get("imports", []):
        fqn = imp["source"]
        # 1. Try exact FQN in the in-repo index
        if fqn in by_fqn:
            dst.add(by_fqn[fqn])
            continue
        # 2. Try dropping the last segment (Foo.Bar.Baz → Foo.Bar)
        parent = fqn.rsplit(".", 1)[0] if "." in fqn else fqn
        if parent != fqn and parent in by_fqn:
            dst.add(by_fqn[parent])
            continue
        # 3. Longest-prefix match against declared Maven coordinates
        matched = False
        for group, coord in coord_by_group:
            if fqn == group or fqn.startswith(group + "."):
                prefix_ext.add(coord)
                matched = True
                break
        if not matched:
            # 4. Emit the 3-segment prefix as a non-matching unresolved
            # (will not match declared_pkgs but recorded for completeness).
            parts = fqn.split(".")
            if len(parts) >= 3:
                exact_ext.add(".".join(parts[:3]))
            else:
                exact_ext.add(fqn)
    return sorted(dst), sorted(exact_ext | prefix_ext), sorted(prefix_ext)
