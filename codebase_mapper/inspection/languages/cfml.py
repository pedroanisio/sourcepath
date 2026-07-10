"""codebase_mapper.languages.cfml — Tier-1 CFML (ColdFusion Markup Language).

Grammar reality (verified 2026-07-10 against PyPI): ``tree-sitter-cfml``
ships a maintained wheel (github.com/cfmleditor/tree-sitter-cfml) exposing
three grammars — ``language_cfml`` (tag syntax), ``language_cfscript``
(script syntax), and ``language_cfquery``. This module consumes the first
two; cfquery SQL islands are out of scope for v1.

The package is imported module-locally, NOT via ``ts_setup.py``'s
all-or-nothing import block: a missing ``tree_sitter_cfml`` wheel must
degrade only CFML files (``tree_sitter_unavailable``), never take down
tree-sitter support for every other language.

Per-file grammar selection
--------------------------
A ``.cfc`` component can be written in tag syntax (``<cfcomponent>``) or
cfscript (``component { … }``); ``.cfm`` templates are tag/HTML-mixed. The
file is sniffed: leading non-whitespace ``<`` → tag grammar, else cfscript.
``<cfscript>`` islands inside tag files are re-parsed with the cfscript
grammar and their symbols/imports offset back to file coordinates.

Structural mapping onto the cbm symbol model
--------------------------------------------
  * component / interface     -> ``top_level_classes``  (item kind
    ``"component"`` / ``"interface"``, chunk ``class``). A CFML component's
    canonical identifier is its **file stem** — that is the name
    ``createObject("component", "a.b.Stem")`` and ``extends`` resolve —
    so ``displayname`` is signature material only.
  * function in a component   -> item kind ``"method"``, ``parent`` = the
    component name (chunk ``method``)
  * free function (.cfm, script outside a component) -> item kind
    ``"function"`` (chunk ``function``)

Import surface (each entry ``{"kind", "source", "lineno"}``):

  * ``<cfinclude template="…">`` / script ``include "…";``  -> ``cfinclude``
  * ``<cfimport taglib="…">``                               -> ``cfimport``
  * script ``import a.b.C;`` (or ``a.b.*``)                 -> ``import``
  * ``extends="a.b.C"`` (tag or script component header)    -> ``extends``
  * ``createObject("component", "a.b.C")``                  -> ``createObject``
  * ``new a.b.C()``                                          -> ``new``
  * ``<cfobject component="a.b.C">``                        -> ``cfobject``

Known limits (documented, not silent)
--------------------------------------
* Anonymous functions / closures are not a symbol surface (they have no
  name); only named ``function`` declarations are collected.
* Server-configured CF *mappings* (e.g. ``/coldbox`` -> external directory)
  cannot be known statically; dotted paths resolve against the repo root
  and the source directory only, and unresolved paths surface their first
  segment as an external package candidate (the pipeline keeps it only
  when a declared dependency matches — same posture as every resolver).
* ``<cfinvoke>`` and ``cfquery`` SQL content are not extracted in v1.
"""
from __future__ import annotations

import re
import threading

from pathlib import PurePosixPath

try:
    import tree_sitter as ts
    import tree_sitter_cfml as _tscfml
    CFML_TS_AVAILABLE = True
except Exception:  # pragma: no cover - environment without the wheel
    ts = _tscfml = None  # type: ignore[assignment]
    CFML_TS_AVAILABLE = False


CFML_EXTENSIONS = (".cfm", ".cfc")

_TAG_WORD_RE = re.compile(rb"[A-Za-z0-9_]+")

_LANGS: dict[str, "ts.Language"] = {}
_LANGS_LOCK = threading.Lock()


def _langs() -> dict[str, "ts.Language"]:
    """Lazily build the two Language objects (thread-safe, once)."""
    if not _LANGS:
        with _LANGS_LOCK:
            if not _LANGS:
                _LANGS["cfml"] = ts.Language(_tscfml.language_cfml())
                _LANGS["cfscript"] = ts.Language(_tscfml.language_cfscript())
    return _LANGS


def _text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] in ("'", '"') and s[-1] == s[0]:
        return s[1:-1]
    return s


def _count_error_nodes(root) -> int:
    if not root.has_error:
        return 0
    n = 0
    cursor = root.walk()
    while True:
        node = cursor.node
        if node.is_error or node.is_missing:
            n += 1
        if cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return n


def _iter_pre_order(root):
    """Document-order traversal without Python recursion (a generated or
    deeply-nested template must not overflow the interpreter stack)."""
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in reversed(node.children):
            stack.append(child)


# ---------------------------------------------------------------------------
# Shared item/import builders
# ---------------------------------------------------------------------------


def _item(kind: str, name: str, parent: str | None, node,
          byte_off: int = 0, row_off: int = 0) -> dict:
    return {
        "kind": kind,
        "name": name,
        "parent": parent,
        "line_start": node.start_point[0] + row_off + 1,
        "line_end": node.end_point[0] + row_off + 1,
        "byte_start": node.start_byte + byte_off,
        "byte_end": node.end_byte + byte_off,
    }


def _import(kind: str, source: str, node, row_off: int = 0) -> dict:
    return {"kind": kind, "source": source,
            "lineno": node.start_point[0] + row_off + 1}


# ---------------------------------------------------------------------------
# Tag-grammar helpers
# ---------------------------------------------------------------------------


def _tag_word(node, content: bytes) -> str:
    """Recover the tag word after the ``<cf`` token. The grammar's special
    tags (``cf_selfclose_tag``, ``cf_component_open_tag``, …) do not expose
    the tag name as a node — ``<cfinclude`` parses as ``<cf`` + attributes —
    so the word ("include") is sliced from the bytes that follow the first
    token. Lowercased: CFML is case-insensitive."""
    if not node.children:
        return ""
    m = _TAG_WORD_RE.match(content, node.children[0].end_byte)
    return m.group(0).decode("utf-8", "replace").lower() if m else ""


def _tag_attrs(node, content: bytes) -> dict[str, str]:
    """Attribute name→value for a tag node's own attributes (direct
    ``cf_attribute`` children, possibly wrapped in ``cf_tag_attributes``) —
    never the attributes of nested tags. Names lowercased; values unquoted."""
    out: dict[str, str] = {}

    def _one(attr) -> None:
        name = value = None
        for c in attr.children:
            if c.type == "cf_attribute_name":
                name = _text(c, content).lower()
            elif "attribute_value" in c.type:
                inner = next((g for g in c.children
                              if g.type == "attribute_value"), None)
                value = _text(inner, content) if inner is not None \
                    else _strip_quotes(_text(c, content))
        if name is not None:
            out[name] = value if value is not None else ""

    for child in node.children:
        if child.type == "cf_attribute":
            _one(child)
        elif child.type == "cf_tag_attributes":
            for g in child.children:
                if g.type == "cf_attribute":
                    _one(g)
    return out


def _tag_open_signature(node, content: bytes) -> str:
    """The open-tag text ``<cffunction …>`` — up to and including the first
    ``>`` token child (for container tags the body follows that token)."""
    end = node.end_byte
    for c in node.children:
        if c.type == ">":
            end = c.end_byte
            break
    return _collapse(content[node.start_byte:end].decode("utf-8", "replace"))


def _tag_function_item(node, content: bytes, parent: str | None) -> dict:
    attrs = _tag_attrs(node, content)
    kind = "method" if parent is not None else "function"
    item = _item(kind, attrs.get("name", ""), parent, node)
    item["signature"] = _tag_open_signature(node, content)
    if attrs.get("access"):
        item["visibility"] = attrs["access"]
    if attrs.get("returntype"):
        item["returns"] = attrs["returntype"]
    params: list[dict] = []
    for child in _iter_pre_order(node):
        if child is node or child.type != "cf_selfclose_tag":
            continue
        if _tag_word(child, content) != "argument":
            continue
        a = _tag_attrs(child, content)
        if a.get("name"):
            params.append({"name": a["name"],
                           "type": a.get("type") or None,
                           "default": a.get("default") or None})
    if params:
        item["params"] = params
    return item


# ---------------------------------------------------------------------------
# cfscript-grammar helpers
# ---------------------------------------------------------------------------


def _script_params(params_node, content: bytes) -> list[dict]:
    """Flatten ``formal_parameters``. The grammar wraps a *bare* parameter
    name in ``parameter_type`` (``(x)`` → parameter_type(identifier x)), so
    a lone parameter_type with no companion name IS the name. The CFML
    ``required`` marker is not a param field (it survives in signature)."""
    groups: list[list] = [[]]
    for c in params_node.children:
        if c.type == ",":
            groups.append([])
        elif c.type not in ("(", ")"):
            groups[-1].append(c)

    out: list[dict] = []
    for group in groups:
        name = ptype = default = None
        type_node = None
        for c in group:
            if c.type == "parameter_type":
                type_node = c
            elif c.type == "identifier":
                name = _text(c, content)
            elif c.type == "assignment_pattern":
                left = c.child_by_field_name("left")
                right = c.child_by_field_name("right")
                if left is not None:
                    name = _text(left, content)
                if right is not None:
                    default = _text(right, content)
        if type_node is not None:
            if name is None:
                name = _text(type_node, content)  # bare param wrapped as type
            else:
                ptype = _text(type_node, content)
        if name:
            out.append({"name": name, "type": ptype, "default": default})
    return out


def _script_function_item(node, content: bytes, parent: str | None,
                          byte_off: int, row_off: int) -> dict:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        # `function topLevel(...)` — the grammar sometimes yields the name
        # as a plain identifier child before the parameters.
        name_node = next((c for c in node.children
                          if c.type == "identifier"), None)
    name = _text(name_node, content) if name_node is not None else ""
    kind = "method" if parent is not None else "function"
    item = _item(kind, name, parent, node, byte_off, row_off)

    body = node.child_by_field_name("body")
    sig_end = body.start_byte if body is not None else node.end_byte
    signature = _collapse(
        content[node.start_byte:sig_end].decode("utf-8", "replace")
    ).rstrip(";").strip()
    if signature:
        item["signature"] = signature

    returns = None
    for c in node.children:
        if c.type == "access_type":
            item["visibility"] = _text(c, content)
        elif c.type == "function":
            break
        elif c.is_named and c.type != "access_type" \
                and c is not name_node:
            returns = _text(c, content)
    if returns:
        item["returns"] = returns

    params_node = node.child_by_field_name("parameters")
    if params_node is None:
        params_node = next((c for c in node.children
                            if c.type == "formal_parameters"), None)
    if params_node is not None:
        params = _script_params(params_node, content)
        if params:
            item["params"] = params
    return item


def _dotted_of(node, content: bytes) -> str:
    """Whitespace-free dotted-path text of an import_path/member_expression."""
    return re.sub(r"\s+", "", _text(node, content))


def _scan_expression(node, content: bytes, row_off: int,
                     imports: list[dict]) -> None:
    """Harvest component references from one expression node:
    ``createObject("component", "a.b.C")`` and ``new a.b.C(...)``. Works
    identically in the tag grammar (cfset/cfreturn embed expressions) and
    the cfscript grammar."""
    if node.type == "call_expression":
        head = node.children[0] if node.children else None
        if head is not None and head.type == "identifier" \
                and _text(head, content).lower() == "createobject":
            args = next((c for c in node.children
                         if c.type == "arguments"), None)
            if args is not None:
                strings = [c for c in args.children if c.type == "string"]
                if len(strings) >= 2:
                    obj_type = _strip_quotes(_text(strings[0], content))
                    target = _strip_quotes(_text(strings[1], content))
                    if obj_type.lower() == "component" and target:
                        imports.append(_import(
                            "createObject", target, node, row_off))
    elif node.type == "new_expression":
        target = next(
            (c for c in node.children
             if c.type in ("member_expression", "identifier")), None)
        if target is not None:
            imports.append(_import(
                "new", _dotted_of(target, content), node, row_off))


def _script_component_attrs(node, content: bytes) -> dict[str, str]:
    """``component_attribute`` name→value (names lowercased, values
    unquoted) for a cfscript component/interface header."""
    out: dict[str, str] = {}
    for c in node.children:
        if c.type != "component_attribute":
            continue
        name_node = next((g for g in c.children
                          if g.type == "identifier"), None)
        value_node = next((g for g in c.children
                           if g.type == "string"), None)
        if name_node is not None:
            value = _strip_quotes(_text(value_node, content)) \
                if value_node is not None else ""
            out[_text(name_node, content).lower()] = value
    return out


def _collect_script(root, content: bytes, stem: str,
                    items: list[dict], imports: list[dict],
                    byte_off: int = 0, row_off: int = 0,
                    parent: str | None = None) -> None:
    """Collect items/imports from a cfscript tree (whole script file or a
    ``<cfscript>`` island). Iterative pre-order with an explicit parent per
    stack entry — component members get ``parent`` = component name."""
    stack: list[tuple] = [(root, parent)]
    while stack:
        node, cur_parent = stack.pop()
        node_parent = cur_parent

        if node.type == "import_statement":
            path = next((c for c in node.children
                         if c.type == "import_path"), None)
            if path is not None:
                imports.append(_import(
                    "import", _dotted_of(path, content), node, row_off))
        elif node.type == "include_statement":
            s = next((c for c in node.children if c.type == "string"), None)
            if s is not None:
                imports.append(_import(
                    "cfinclude", _strip_quotes(_text(s, content)),
                    node, row_off))
        elif node.type == "component" and node.is_named:
            # Covers both `component { … }` and `interface { … }` — the
            # grammar reuses one node; the leading token disambiguates.
            # ``is_named`` matters: the anonymous ``component`` KEYWORD
            # token inside this very node also carries type "component".
            is_iface = bool(node.children) \
                and node.children[0].type == "interface"
            attrs = _script_component_attrs(node, content)
            item = _item("interface" if is_iface else "component",
                         stem, cur_parent, node, byte_off, row_off)
            body = node.child_by_field_name("body")
            sig_end = body.start_byte if body is not None else node.end_byte
            item["signature"] = _collapse(
                content[node.start_byte:sig_end].decode("utf-8", "replace"))
            if attrs.get("extends"):
                item["bases"] = [attrs["extends"]]
                imports.append(_import(
                    "extends", attrs["extends"], node, row_off))
            items.append(item)
            node_parent = stem
        elif node.type == "function_declaration":
            item = _script_function_item(
                node, content, cur_parent, byte_off, row_off)
            if item["name"]:
                items.append(item)
        else:
            _scan_expression(node, content, row_off, imports)

        for child in reversed(node.children):
            stack.append((child, node_parent))


def _collect_tag(root, content: bytes, stem: str,
                 items: list[dict], imports: list[dict],
                 island_errors: list[int]) -> None:
    """Collect items/imports from a tag-grammar tree. Document-order
    traversal; ``cf_component_open_tag`` / ``cf_component_close_tag`` are
    *sibling* nodes (the grammar does not nest the body), so component
    membership is tracked as open/close state in document order. Parse
    errors inside ``<cfscript>`` islands are appended to ``island_errors``
    (a per-call list — extraction runs on a thread pool, so no module
    state)."""
    open_component: dict | None = None
    open_interface: dict | None = None

    def current_parent() -> str | None:
        if open_component is not None:
            return open_component["name"]
        if open_interface is not None:
            return open_interface["name"]
        return None

    for node in _iter_pre_order(root):
        t = node.type
        if open_interface is not None \
                and node.start_byte >= open_interface["byte_end"]:
            # ``cf_tag`` CONTAINS its body, so leaving the interface's byte
            # span in document order ends its membership scope.
            open_interface = None
        if t == "cf_component_open_tag":
            attrs = _tag_attrs(node, content)
            open_component = _item("component", stem, None, node)
            open_component["signature"] = _tag_open_signature(node, content)
            if attrs.get("extends"):
                open_component["bases"] = [attrs["extends"]]
                imports.append(_import("extends", attrs["extends"], node))
            items.append(open_component)
        elif t == "cf_component_close_tag":
            if open_component is not None:
                open_component["line_end"] = node.end_point[0] + 1
                open_component["byte_end"] = node.end_byte
                open_component = None
        elif t == "cf_tag":
            start = next((c for c in node.children
                          if c.type == "cf_start_tag"), None)
            tag_name = next(
                (_text(c, content).lower() for c in start.children
                 if c.type == "cf_tag_name"), "") if start is not None else ""
            if tag_name == "interface":
                open_interface = _item("interface", stem, None, node)
                open_interface["signature"] = _tag_open_signature(
                    start, content)
                items.append(open_interface)
        elif t == "cf_function_tag":
            items.append(_tag_function_item(node, content, current_parent()))
        elif t == "cf_selfclose_tag":
            word = _tag_word(node, content)
            attrs = None
            if word == "include":
                attrs = _tag_attrs(node, content)
                if attrs.get("template"):
                    imports.append(_import(
                        "cfinclude", attrs["template"], node))
            elif word == "import":
                attrs = _tag_attrs(node, content)
                if attrs.get("taglib"):
                    imports.append(_import("cfimport", attrs["taglib"], node))
            elif word == "object":
                attrs = _tag_attrs(node, content)
                if attrs.get("component"):
                    imports.append(_import(
                        "cfobject", attrs["component"], node))
        elif t == "cf_script_tag":
            blob = next((c for c in node.children
                         if c.type == "cf_script_content"), None)
            if blob is not None and blob.end_byte > blob.start_byte:
                island = content[blob.start_byte:blob.end_byte]
                tree = ts.Parser(_langs()["cfscript"]).parse(island)
                _collect_script(
                    tree.root_node, island, stem, items, imports,
                    byte_off=blob.start_byte, row_off=blob.start_point[0],
                    parent=current_parent())
                if tree.root_node.has_error:
                    island_errors.append(_count_error_nodes(tree.root_node))
        else:
            _scan_expression(node, content, 0, imports)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def extract_cfml_ast_summary(
    content: bytes, path: str,
) -> tuple[dict | None, list[str]]:
    """Return ``(summary, errors)`` for a ``.cfm``/``.cfc`` file.

    The summary matches the SPEC first-class contract (``language`` /
    ``imports`` / ``top_level_functions`` / ``top_level_classes``) plus
    ``items`` with per-symbol line/byte spans and canonical signature
    fields, consumed by the L2 chunker. ``extraction_method`` is
    ``"tree_sitter"`` — verified real, not assumed (PALS's Law).
    """
    if not CFML_TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]

    stem = PurePosixPath(path).stem
    syntax = "tag" if content.lstrip()[:1] == b"<" else "script"

    items: list[dict] = []
    imports: list[dict] = []
    island_errors: list[int] = []

    grammar = "cfml" if syntax == "tag" else "cfscript"
    tree = ts.Parser(_langs()[grammar]).parse(content)
    if syntax == "tag":
        _collect_tag(tree.root_node, content, stem, items, imports,
                     island_errors)
    else:
        _collect_script(tree.root_node, content, stem, items, imports)

    n_errors = _count_error_nodes(tree.root_node) + sum(island_errors)
    errors = ["parse_errors_present", f"parse_error_nodes:{n_errors}"] \
        if n_errors else []

    items.sort(key=lambda x: (x["line_start"], x["kind"],
                              x.get("parent") or "", x["name"]))
    # Dedupe identical (kind, source) imports, keep first lineno.
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for imp in sorted(imports, key=lambda x: (x["lineno"], x["source"])):
        key = (imp["kind"], imp["source"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(imp)

    return {
        "language": "cfml",
        "extraction_method": "tree_sitter",
        "syntax": syntax,
        "imports": deduped,
        "top_level_functions": sorted(
            {i["name"] for i in items if i["kind"] in ("function", "method")}),
        "top_level_classes": sorted(
            {i["name"] for i in items
             if i["kind"] in ("component", "interface")}),
        "items": items,
    }, errors


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------


_PATH_KINDS = ("cfinclude", "cfimport")


def _norm_path(parts: list[str]) -> str:
    norm: list[str] = []
    for part in parts:
        if part == "..":
            if norm and norm[-1] != "..":
                norm.pop()
        elif part not in ("", "."):
            norm.append(part)
    return "/".join(norm)


def _path_candidates(src_dir: str, spec: str) -> list[str]:
    """Candidate repo-relative paths for a template/taglib path spec.
    Leading ``/`` means the webroot, approximated by the repo root; a
    relative spec resolves against the source directory first, then the
    repo root (mappings often mirror the root)."""
    if spec.startswith("/"):
        return [_norm_path(spec.split("/"))]
    rel = _norm_path((src_dir + "/" + spec).split("/")) if src_dir \
        else _norm_path(spec.split("/"))
    root = _norm_path(spec.split("/"))
    return [rel] if rel == root else [rel, root]


def _dir_children(dir_path: str, paths_set: set[str],
                  exts: tuple[str, ...]) -> list[str]:
    """Direct-child files of ``dir_path`` with one of ``exts``."""
    prefix = dir_path.rstrip("/") + "/"
    out = [p for p in paths_set
           if p.startswith(prefix) and "/" not in p[len(prefix):]
           and p.lower().endswith(exts)]
    return sorted(out)


def _resolve_dotted(spec: str, src_dir: str,
                    paths_set: set[str]) -> str | None:
    """Resolve ``a.b.C`` → an in-repo ``a/b/C.cfc``. Tries the repo root,
    then the source directory, then a unique path-suffix match (CF
    mappings can root the dotted path anywhere in the tree)."""
    rel = spec.replace(".", "/") + ".cfc"
    if rel in paths_set:
        return rel
    if src_dir:
        cand = _norm_path((src_dir + "/" + rel).split("/"))
        if cand in paths_set:
            return cand
    suffix = "/" + rel
    matches = sorted(p for p in paths_set if p.endswith(suffix))
    return matches[0] if matches else None


def resolve_cfml_imports(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """Resolve the analyzer's import records to ``(in_repo, external)``.

    * ``cfinclude`` / ``cfimport`` specs are *paths*: resolved against the
      source directory and the repo root (leading ``/`` = webroot ≈ repo
      root). A taglib that names a directory yields its direct-child
      ``.cfm``/``.cfc`` custom tags. Unresolved path specs carry no
      package identity, so they are dropped (same posture as Ruby's
      unresolved ``require_relative``).
    * Dotted component specs (``import`` / ``extends`` / ``createObject``
      / ``new`` / ``cfobject``) resolve ``a.b.C`` → ``a/b/C.cfc``;
      ``a.b.*`` → the direct ``.cfc`` children of ``a/b``. An unresolved
      dotted spec surfaces its first segment, lowercased, as the external
      package candidate — the host pipeline emits an ImportExternalEdge
      only when it matches a declared dependency (pipeline.py posture,
      shared by every resolver).
    """
    in_repo: set[str] = set()
    external: set[str] = set()
    src_dir = str(PurePosixPath(src_path).parent)
    if src_dir == ".":
        src_dir = ""

    for imp in summary.get("imports", []):
        kind = imp.get("kind")
        spec = imp.get("source") or ""
        if not spec:
            continue
        if kind in _PATH_KINDS:
            candidates = _path_candidates(src_dir, spec)
            hit = next((c for c in candidates if c in paths_set), None)
            if hit is None and kind == "cfimport":
                for cand in candidates:
                    children = _dir_children(cand, paths_set,
                                             (".cfm", ".cfc"))
                    if children:
                        in_repo.update(children)
                        break
            elif hit is not None:
                in_repo.add(hit)
            # unresolved path spec: dropped — no package identity to declare
        elif kind in ("import", "extends", "createObject", "new", "cfobject"):
            if spec.endswith(".*"):
                pkg_dir = spec[:-2].replace(".", "/")
                for base in ((pkg_dir,) if not src_dir
                             else (pkg_dir,
                                   _norm_path((src_dir + "/" + pkg_dir)
                                              .split("/")))):
                    children = _dir_children(base, paths_set, (".cfc",))
                    if children:
                        in_repo.update(children)
                        break
                else:
                    external.add(spec.split(".", 1)[0].lower())
                continue
            target = _resolve_dotted(spec, src_dir, paths_set)
            if target is not None:
                in_repo.add(target)
            else:
                external.add(spec.split(".", 1)[0].lower())

    in_repo.discard(src_path)
    return sorted(in_repo), sorted(external)
