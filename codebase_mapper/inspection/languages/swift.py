"""codebase_mapper.languages.swift."""
from __future__ import annotations

import re

from pathlib import PurePosixPath
from typing import Callable


from ..models import FileRecord
from ...ts_setup import _TS_LANGS, _TS_QUERIES, _ts_setup
from ...ts_setup import TS_AVAILABLE, ts


_DEF_KINDS = ("function_declaration", "protocol_function_declaration")
_CONTAINER_KINDS = ("class_declaration", "protocol_declaration")
_BODY_KINDS = ("class_body", "protocol_body", "enum_class_body")
_VISIBILITY_WORDS = {"private", "protected", "public", "internal", "fileprivate", "open"}


def _sw_node_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _sw_item(kind: str, name: str, parent: str | None, node) -> dict:
    return {
        "kind": kind,
        "name": name,
        "parent": parent,
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "byte_start": node.start_byte,
        "byte_end": node.end_byte,
    }


def _sw_modifiers(node, content: bytes) -> str | None:
    """Explicit visibility keyword only — never derived from naming."""
    modifiers = next((c for c in node.children if c.type == "modifiers"), None)
    if modifiers is None:
        return None
    for m in modifiers.children:
        if m.type == "visibility_modifier":
            word = _sw_node_text(m, content)
            if word in _VISIBILITY_WORDS:
                return word
    return None


def _sw_type_params(node, content: bytes) -> list[str]:
    tp = next((c for c in node.children if c.type == "type_parameters"), None)
    if tp is None:
        return []
    return [_sw_node_text(c, content) for c in tp.children
            if c.is_named and c.type == "type_parameter"]


def _sw_params(func_decl, content: bytes) -> list[dict]:
    """Expand a function declaration's flat parameter/default children into
    ordered {name, type, default} records. Swift's grammar has no dedicated
    parameter-list node: ``(``, ``parameter``, ``,``, ``=``/default-value
    siblings sit directly among the declaration's own children (mirroring
    Kotlin's ``function_value_parameters`` layout, minus the wrapper).

    ``name`` is the *internal* parameter name — the second ``simple_identifier``
    when both an external label and internal name are written, else the sole
    one. An external label of ``_`` (no label) is simply not that identifier.
    """
    children = list(func_decl.children)
    n = len(children)
    open_idx = next((i for i, c in enumerate(children) if c.type == "("), None)
    if open_idx is None:
        return []
    close_idx = next((i for i in range(open_idx, n) if children[i].type == ")"), n)
    out: list[dict] = []
    i = open_idx + 1
    while i < close_idx:
        c = children[i]
        if c.type != "parameter":
            i += 1
            continue
        idents = [ch for ch in c.children if ch.type == "simple_identifier"]
        name = _sw_node_text(idents[-1], content) if idents else ""
        colon_idx = next((j for j, ch in enumerate(c.children) if ch.type == ":"), None)
        ptype = None
        if colon_idx is not None:
            type_nodes = [ch for ch in c.children[colon_idx + 1:] if ch.is_named]
            if type_nodes:
                ptype = _collapse(_sw_node_text(type_nodes[0], content))
        default = None
        if i + 1 < close_idx and children[i + 1].type == "=":
            if i + 2 < close_idx and children[i + 2].is_named:
                default = _collapse(_sw_node_text(children[i + 2], content))
                i += 2
        out.append({"name": name, "type": ptype, "default": default})
        i += 1
    return out


def _sw_callable_fields(node, content: bytes) -> dict:
    children = list(node.children)
    close_idx = next((i for i, c in enumerate(children) if c.type == ")"), None)
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    fields: dict = {"signature": _collapse(
        _sw_node_text(node, content)[:end - node.start_byte])}
    if close_idx is not None:
        params = _sw_params(node, content)
        if params:
            fields["params"] = params
        for i in range(close_idx + 1, len(children)):
            if children[i].type == "async":
                fields["is_async"] = True
            elif children[i].type == "->":
                for j in range(i + 1, len(children)):
                    if children[j].is_named and children[j].type != "function_body":
                        fields["returns"] = _collapse(_sw_node_text(children[j], content))
                        break
                break
    type_params = _sw_type_params(node, content)
    if type_params:
        fields["type_params"] = type_params
    vis = _sw_modifiers(node, content)
    if vis:
        fields["visibility"] = vis
    return fields


def _sw_container_fields(node, content: bytes, *, body) -> dict:
    end = body.start_byte if body is not None else node.end_byte
    fields: dict = {"signature": _collapse(
        _sw_node_text(node, content)[:end - node.start_byte])}
    bases = [
        _sw_node_text(next(c for c in spec.children if c.type == "user_type"), content)
        for spec in node.children if spec.type == "inheritance_specifier"
    ]
    if bases:
        fields["bases"] = bases
    type_params = _sw_type_params(node, content)
    if type_params:
        fields["type_params"] = type_params
    vis = _sw_modifiers(node, content)
    if vis:
        fields["visibility"] = vis
    return fields


def _collect_swift_items(root, content: bytes) -> list[dict]:
    """One item per top-level/member ``func`` and ``class``/``struct``/
    ``enum``/``protocol``, with byte+line spans (powers L2 chunking + the
    symbol surface). Iterative (explicit stack over bodies): a deeply-nested
    or generated Swift file must not overflow the interpreter's recursion
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
                name = _sw_node_text(name_node, content) if name_node is not None else ""
                kind = "method" if parent is not None else "function"
                item = _sw_item(kind, name, parent, child)
                item.update(_sw_callable_fields(child, content))
                items.append(item)
            elif child.type in _CONTAINER_KINDS:
                name_node = child.child_by_field_name("name")
                name = _sw_node_text(name_node, content) if name_node is not None else ""
                cbody = next((c for c in child.children if c.type in _BODY_KINDS), None)
                item = _sw_item("class", name, parent, child)
                item.update(_sw_container_fields(child, content, body=cbody))
                items.append(item)
                if cbody is not None:
                    stack.append((cbody, name))
    return items


def extract_swift_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["swift"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors = ["parse_errors_present"] if tree.root_node.has_error else []
    cursor = ts.QueryCursor(_TS_QUERIES["swift"])
    captures = cursor.captures(tree.root_node)

    imports: list[dict] = []
    funcs: list[str] = []
    classes: list[str] = []
    for cap, nodes in captures.items():
        for node in nodes:
            text = content[node.start_byte:node.end_byte].decode("utf-8", "replace")
            if cap == "sw_import":
                imports.append({"kind": "import", "source": text,
                                "lineno": node.start_point[0] + 1})
            elif cap == "func_name":
                funcs.append(text)
            elif cap == "class_name":
                classes.append(text)
    imports.sort(key=lambda x: (x["lineno"], x["source"]))
    items = _collect_swift_items(tree.root_node, content)
    items.sort(key=lambda x: (x["line_start"], x["kind"],
                              x.get("parent") or "", x["name"]))
    return {
        "language": "swift",
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
        "items": items,
    }, errors

def detect_swift_modules(records: list[FileRecord], read: Callable[[str], bytes]) -> dict:
    """Parse Package.swift to extract:

    - `local_modules`: in-repo target module name -> source root dir
    - `product_to_package`: external module name -> set of candidate
      package identifiers (URL host/path form AND short name).

    Both extractors are regex-based; they catch the conventional Swift
    Package Manager forms but will miss dynamic/computed configs. Misses
    are emitted as unresolved imports rather than guessed.
    """
    pkg_swift = next((r for r in records if r.path == "Package.swift"), None)
    if pkg_swift is None:
        return {"local_modules": {}, "product_to_package": {}}
    try:
        text = read(pkg_swift.path).decode("utf-8")
    except UnicodeDecodeError:
        return {"local_modules": {}, "product_to_package": {}}

    local_modules: dict[str, str] = {}
    for m in re.finditer(
        r"\.(?:target|executableTarget|testTarget|systemLibrary|plugin)\s*\(\s*name\s*:\s*\"([^\"]+)\"(?:[^)]*?path\s*:\s*\"([^\"]+)\")?",
        text, re.DOTALL,
    ):
        name = m.group(1)
        explicit_path = m.group(2)
        if explicit_path:
            local_modules[name] = explicit_path.strip("/")
        else:
            local_modules[name] = f"Sources/{name}"

    # Build a map from package identifier -> candidate identifiers (URL + short).
    # SPM allows referring to a package by `.package(name: "X", url: ...)` or
    # by the short name derived from the URL (last path segment, sans .git).
    package_aliases: dict[str, set[str]] = {}
    for m in re.finditer(
        r"\.package\s*\(\s*(?:name\s*:\s*\"([^\"]*)\"\s*,\s*)?url\s*:\s*\"([^\"]+)\"",
        text,
    ):
        explicit_name = (m.group(1) or "").lower()
        url = m.group(2).rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        short = url.rsplit("/", 1)[-1].lower()
        url_form = url.lower()
        aliases = {url_form, short}
        if explicit_name:
            aliases.add(explicit_name)
        for key in (explicit_name, short, url_form):
            if key:
                package_aliases.setdefault(key, set()).update(aliases)
    # Local-path packages: .package(name:"X", path:"Y")
    for m in re.finditer(r"\.package\s*\(\s*name\s*:\s*\"([^\"]+)\"\s*,\s*path", text):
        nm = m.group(1).lower()
        package_aliases.setdefault(nm, set()).add(nm)

    # Now extract .product(name:"ModuleA", package:"PackageRef") inside
    # .target(...) dependencies blocks. The package ref maps to one of the
    # alias sets above.
    product_to_package: dict[str, set[str]] = {}
    for m in re.finditer(
        r"\.product\s*\(\s*name\s*:\s*\"([^\"]+)\"\s*,\s*package\s*:\s*\"([^\"]+)\"",
        text,
    ):
        module_name = m.group(1)
        package_ref = m.group(2).lower()
        aliases = package_aliases.get(package_ref, {package_ref})
        product_to_package.setdefault(module_name, set()).update(aliases)

    return {"local_modules": local_modules, "product_to_package": product_to_package}

def resolve_swift_imports(
    src_path: str, summary: dict, swift_info: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """For each `import Foo`:

    1. If Foo is a local target module, emit an in-repo edge to a
       representative file (preferring `Foo.swift` if present).
    2. Else if Foo appears in the product map, emit unresolved entries
       for each candidate package identifier so the caller can match
       against declared deps.
    3. Else emit Foo itself as unresolved.
    """
    local_modules = swift_info.get("local_modules", {})
    product_to_package = swift_info.get("product_to_package", {})
    dst: set[str] = set()
    unresolved: set[str] = set()
    for imp in summary.get("imports", []):
        module = imp["source"]
        if module in local_modules:
            src_dir = local_modules[module]
            prefix = src_dir + "/"
            module_files = [p for p in paths_set
                            if p.startswith(prefix) and p.endswith(".swift")]
            if not module_files:
                unresolved.add(module)
                continue
            preferred = next((p for p in module_files
                              if PurePosixPath(p).stem == module), None)
            if preferred:
                dst.add(preferred)
            else:
                dst.add(sorted(module_files)[0])
        elif module in product_to_package:
            # Emit all candidate package identifiers; caller filters against
            # declared_pkgs.
            unresolved.update(product_to_package[module])
        else:
            unresolved.add(module)
    return sorted(dst), sorted(unresolved)
