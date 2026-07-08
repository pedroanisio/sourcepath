"""codebase_mapper.languages.go."""
from __future__ import annotations

import re

from pathlib import PurePosixPath
from typing import Callable


from ..models import FileRecord
from ...ts_setup import _TS_LANGS, _TS_QUERIES, _strip_quotes, _ts_setup
from ...ts_setup import TS_AVAILABLE, ts
from ._treewalk import find_named_descendant, iter_named_pre_order


def _node_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _go_item(kind: str, name: str, parent: str | None, node) -> dict:
    return {
        "kind": kind,
        "name": name,
        "parent": parent,
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
        "byte_start": node.start_byte,
        "byte_end": node.end_byte,
    }


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _go_params(param_list, content: bytes) -> list[dict]:
    """Expand a ``parameter_list`` into ordered {name, type, default} records
    (the canonical contract in plugins/chunks_embeddings/signatures.py).

    Grouped names (``a, b int``) become one entry per name sharing the type;
    a variadic keeps the ``...`` prefix on its type as written; an unnamed
    parameter (``func f(int)``) carries name ``""`` — nothing was written, and
    fabricating ``_`` would conflate it with an explicit blank identifier.
    ``default`` is always None: Go has no parameter defaults.
    """
    out: list[dict] = []
    if param_list is None:
        return out
    for decl in param_list.children:
        if not decl.is_named:
            continue
        type_node = decl.child_by_field_name("type")
        ptype = _collapse(_node_text(type_node, content)) if type_node is not None else None
        if decl.type == "variadic_parameter_declaration":
            name_node = decl.child_by_field_name("name")
            name = _node_text(name_node, content) if name_node is not None else ""
            out.append({"name": name, "type": "..." + ptype if ptype else "...",
                        "default": None})
        elif decl.type == "parameter_declaration":
            names = decl.children_by_field_name("name")
            if names:
                for n in names:
                    out.append({"name": _node_text(n, content), "type": ptype,
                                "default": None})
            else:
                out.append({"name": "", "type": ptype, "default": None})
    return out


def _go_type_params(owner, content: bytes) -> list[str]:
    """Generic type parameters as written, one entry per declaration."""
    tp_list = owner.child_by_field_name("type_parameters")
    if tp_list is None:
        return []
    return [_collapse(_node_text(c, content)) for c in tp_list.children
            if c.is_named and c.type == "type_parameter_declaration"]


def _go_callable_fields(node, content: bytes) -> dict:
    """Signature fields for a function/method declaration.

    ``signature`` is the header as written — everything before the body ``{``,
    whitespace-collapsed to one line — so a method's receiver is included.
    ``visibility``/``is_async``/``decorators`` are never set: Go has no
    visibility keywords (capitalization is recoverable from the name itself),
    no async functions, and no decorators. Empty fields are omitted entirely.
    """
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    fields: dict = {
        "signature": _collapse(content[node.start_byte:end].decode("utf-8", "replace")),
    }
    params = _go_params(node.child_by_field_name("parameters"), content)
    if params:
        fields["params"] = params
    result = node.child_by_field_name("result")
    if result is not None:
        fields["returns"] = _collapse(_node_text(result, content))
    type_params = _go_type_params(node, content)
    if type_params:
        fields["type_params"] = type_params
    return fields


def _go_type_fields(spec, content: bytes) -> dict:
    """Signature fields for a ``type_spec`` (struct / interface / plain type).

    ``signature`` is ``type <header>`` up to (excluding) the body braces.
    Interface ``type_elem`` members (embedded interfaces / constraint
    elements) become ``bases`` as written; struct embedded fields do NOT —
    Go struct embedding is composition, not subtyping.
    """
    type_node = spec.child_by_field_name("type")
    end = spec.end_byte
    fields: dict = {}
    if type_node is not None and type_node.type == "struct_type":
        body = next((c for c in type_node.children
                     if c.type == "field_declaration_list"), None)
        if body is not None:
            end = body.start_byte
    elif type_node is not None and type_node.type == "interface_type":
        brace = next((c for c in type_node.children if c.type == "{"), None)
        if brace is not None:
            end = brace.start_byte
        bases = [_collapse(_node_text(c, content)) for c in type_node.children
                 if c.is_named and c.type == "type_elem"]
        if bases:
            fields["bases"] = bases
    fields["signature"] = "type " + _collapse(
        content[spec.start_byte:end].decode("utf-8", "replace"))
    type_params = _go_type_params(spec, content)
    if type_params:
        fields["type_params"] = type_params
    return fields


def _collect_go_items(root, content: bytes) -> list[dict]:
    """One item per top-level func / method / struct / interface / type, with
    byte+line spans (powers L2 chunking + the symbol surface).

    Iterative pre-order (see ``_treewalk``): prune at the declaration kinds so
    function/method bodies aren't descended into — top-level only, and safe on
    deeply-nested files. Methods carry ``parent`` = the receiver type name
    (``*T`` and ``T`` receivers both map to ``T``).
    """
    decl_kinds = ("function_declaration", "method_declaration", "type_declaration")
    items: list[dict] = []
    for node in iter_named_pre_order(root, descend=lambda n: n.type not in decl_kinds):
        nt = node.type
        if nt == "function_declaration":
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                item = _go_item("function", _node_text(name_node, content), None, node)
                item.update(_go_callable_fields(node, content))
                items.append(item)
        elif nt == "method_declaration":
            name_node = node.child_by_field_name("name")
            recv = node.child_by_field_name("receiver")
            parent = None
            if recv is not None:
                tid = find_named_descendant(recv, {"type_identifier"})
                if tid is not None:
                    parent = _node_text(tid, content)
            if name_node is not None:
                item = _go_item("method", _node_text(name_node, content), parent, node)
                item.update(_go_callable_fields(node, content))
                items.append(item)
        elif nt == "type_declaration":
            for spec in node.children:
                if not (spec.is_named and spec.type == "type_spec"):
                    continue
                name_node = spec.child_by_field_name("name")
                type_node = spec.child_by_field_name("type")
                if name_node is None:
                    continue
                kind = "type"
                if type_node is not None and type_node.type == "struct_type":
                    kind = "struct"
                elif type_node is not None and type_node.type == "interface_type":
                    kind = "interface"
                item = _go_item(kind, _node_text(name_node, content), None, spec)
                item.update(_go_type_fields(spec, content))
                items.append(item)
    return items


def extract_go_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["go"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors = ["parse_errors_present"] if tree.root_node.has_error else []
    cursor = ts.QueryCursor(_TS_QUERIES["go"])
    captures = cursor.captures(tree.root_node)

    imports: list[dict] = []
    funcs: list[str] = []
    classes: list[str] = []
    for cap, nodes in captures.items():
        for node in nodes:
            text = content[node.start_byte:node.end_byte].decode("utf-8", "replace")
            if cap == "go_import":
                imports.append({"kind": "import", "source": _strip_quotes(text),
                                "lineno": node.start_point[0] + 1})
            elif cap == "func_name":
                funcs.append(text)
            elif cap == "class_name":
                classes.append(text)
    imports.sort(key=lambda x: (x["lineno"], x["source"]))
    items = _collect_go_items(tree.root_node, content)
    items.sort(key=lambda x: (x["line_start"], x["kind"],
                              x.get("parent") or "", x["name"]))
    return {
        "language": "go",
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
        "items": items,
    }, errors

def detect_go_module(records: list[FileRecord], read: Callable[[str], bytes]) -> dict | None:
    """Find go.mod at repo root; extract the module path. Returns dict with
    {module_path, mod_file_dir} or None.

    Multi-module repos (workspaces) aren't handled in v0.4 — only the
    top-level go.mod is read. If there are nested go.mod files, their files
    won't resolve their `crate-local` imports against the right module path.
    """
    gomod = next((r for r in records if r.path == "go.mod"), None)
    if gomod is None:
        # Look for any go.mod, prefer the shallowest
        candidates = [r for r in records if r.path.endswith("/go.mod")]
        if not candidates:
            return None
        gomod = sorted(candidates, key=lambda r: r.path.count("/"))[0]
    try:
        text = read(gomod.path).decode("utf-8")
    except UnicodeDecodeError:
        return None
    m = re.search(r"^\s*module\s+(\S+)", text, re.MULTILINE)
    if not m:
        return None
    mod_dir = str(PurePosixPath(gomod.path).parent)
    if mod_dir == ".":
        mod_dir = ""
    return {"module_path": m.group(1), "mod_file_dir": mod_dir}

def resolve_go_imports(
    src_path: str, summary: dict, module: dict | None, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    dst: set[str] = set()
    unresolved: set[str] = set()
    for imp in summary.get("imports", []):
        spec = imp["source"]
        if not spec:
            continue
        if module and spec.startswith(module["module_path"]):
            # Strip module prefix; remainder is a path relative to mod_file_dir.
            remainder = spec[len(module["module_path"]):].lstrip("/")
            base = (module["mod_file_dir"] + "/" + remainder) if module["mod_file_dir"] else remainder
            base = base.rstrip("/")
            # Go imports name a directory; resolve by trying any .go file in
            # that directory that isn't a _test.go file.
            prefix = (base + "/") if base else ""
            matches = [p for p in paths_set
                       if p.startswith(prefix) and p.endswith(".go")
                       and "/" not in p[len(prefix):]
                       and not p.endswith("_test.go")]
            if matches:
                # Prefer the package's main file if present; else first lexically.
                for cand in sorted(matches):
                    dst.add(cand)
                    break
            else:
                # Also try the dir itself as a single .go file (rare)
                if base + ".go" in paths_set:
                    dst.add(base + ".go")
        else:
            # External: take the first two path segments as the package root
            # for matching against declared deps (go.mod entries use the same form).
            unresolved.add(spec)
    return sorted(dst), sorted(unresolved)

def go_package_root(spec: str) -> str:
    """For 'github.com/foo/bar/sub' return 'github.com/foo/bar'."""
    parts = spec.split("/")
    if len(parts) >= 3 and parts[0] in ("github.com", "gitlab.com", "bitbucket.org"):
        return "/".join(parts[:3])
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return spec
