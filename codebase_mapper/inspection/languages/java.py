"""codebase_mapper.languages.java — Tier-1 Java support.

Java sits between Kotlin (model-cousin: FQN-keyed resolution against
declared Maven coordinates) and Python (model-cousin: package + module
layout discovered from source-root heuristics). Both shapes are needed:

  * Imports are FQNs (``com.example.foo.Bar``) — same as Kotlin.
  * The file's *package* statement plus its location under
    ``src/main/java`` (or ``src/test/java``) determines its FQN. Unlike
    Kotlin, Java enforces the file-to-class one-to-one convention only
    weakly: a single ``.java`` file may contain several package-private
    classes alongside one ``public`` class. We index them all.
  * Wildcard imports (``import com.example.*``) bind every top-level
    name in that package. We don't enumerate; we record the prefix and
    let the resolver match candidate symbols lazily during xref
    resolution.

Public surface:

  * ``extract_java_ast_summary(content, path) -> (summary, errors)``
  * ``detect_java_source_roots(records) -> list[str]``
  * ``build_java_fqn_index(records) -> dict[str, str]``
  * ``build_java_package_index(records) -> dict[str, list[str]]``
  * ``resolve_java_imports(record, summary, by_fqn, by_pkg,
                            declared_pkgs) -> ResolveResult``
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Callable

from ..models import FileRecord
from ...ts_setup import _TS_LANGS, _TS_QUERIES, _ts_setup
from ...ts_setup import TS_AVAILABLE, parse_error_diagnostics, ts
from ._treewalk import iter_named_pre_order


# Item kinds recognised by the analyzer. Mapped onto the L2 chunk kind by
# the chunker.
_TYPE_NODE_TYPES = {
    "class_declaration", "interface_declaration", "enum_declaration",
    "annotation_type_declaration", "record_declaration",
}
_CALLABLE_NODE_TYPES = {
    "method_declaration", "constructor_declaration",
}


def _node_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _child_by_field(node, field: str):
    """Return the first named child reachable by tree-sitter field name.

    tree-sitter exposes both ``child_by_field_name`` and
    ``children_by_field_name``; we use the first as a uniform helper.
    """
    return node.child_by_field_name(field)


def _named_child_by_type(node, kind: str):
    for ch in node.children:
        if ch.is_named and ch.type == kind:
            return ch
    return None


# ---------------------------------------------------------------------------
# Canonical signature fields (plugins/chunks_embeddings/signatures.py)
# ---------------------------------------------------------------------------


_VISIBILITY_KEYWORDS = {"public", "private", "protected"}
_ANNOTATION_NODE_TYPES = {"marker_annotation", "annotation"}


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _modifier_info(node, content: bytes) -> tuple[str | None, list[str], list[tuple[int, int]]]:
    """Return ``(visibility, decorators, annotation_spans)``.

    In the tree-sitter Java grammar, annotations live inside the
    ``modifiers`` child of a declaration. Visibility is the explicit
    public/private/protected keyword only — package-private yields None.
    The annotation byte spans let the header builder excise them from
    the signature text.
    """
    mods = _named_child_by_type(node, "modifiers")
    if mods is None:
        return None, [], []
    visibility: str | None = None
    decorators: list[str] = []
    spans: list[tuple[int, int]] = []
    for ch in mods.children:
        if ch.type in _ANNOTATION_NODE_TYPES:
            decorators.append(_collapse(_node_text(ch, content)).removeprefix("@"))
            spans.append((ch.start_byte, ch.end_byte))
        elif ch.type in _VISIBILITY_KEYWORDS and visibility is None:
            visibility = ch.type
    return visibility, decorators, spans


def _header_text(node, content: bytes, skip_spans: list[tuple[int, int]]) -> str:
    """Declaration header up to (excluding) the body ``{`` — or the
    terminating ``;`` for bodyless methods — single-line-collapsed,
    with annotation spans excised.
    """
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    parts: list[bytes] = []
    cursor = node.start_byte
    for start, stop in sorted(skip_spans):
        if start >= end:
            break
        parts.append(content[cursor:start])
        cursor = max(cursor, stop)
    parts.append(content[cursor:end])
    text = _collapse(b" ".join(parts).decode("utf-8", "replace"))
    return text[:-1].rstrip() if text.endswith(";") else text


def _param_records(params_node, content: bytes) -> list[dict]:
    """Flatten a ``formal_parameters`` node into canonical param records.

    Java has no default parameter values, so ``default`` is always None.
    Varargs (``spread_parameter``) keep the ``...`` in the type as written.
    """
    out: list[dict] = []
    for ch in params_node.children:
        if not ch.is_named:
            continue
        if ch.type == "formal_parameter":
            type_node = ch.child_by_field_name("type")
            name_node = ch.child_by_field_name("name")
            if name_node is None:
                continue
            ptype = _collapse(_node_text(type_node, content)) if type_node is not None else None
            dims = ch.child_by_field_name("dimensions")
            if dims is not None and ptype is not None:
                # C-style dimensions (``int x[]``) belong to the type.
                ptype += _node_text(dims, content)
            out.append({"name": _node_text(name_node, content),
                        "type": ptype, "default": None})
        elif ch.type == "spread_parameter":
            decl = _named_child_by_type(ch, "variable_declarator")
            name_node = decl.child_by_field_name("name") if decl is not None else None
            if name_node is None:
                continue
            # Type text runs from the first non-modifier named child
            # through the ``...`` token (i.e. up to the declarator).
            type_start = ch.start_byte
            for sub in ch.children:
                if sub.is_named and sub.type != "modifiers":
                    type_start = sub.start_byte
                    break
            ptype = _collapse(
                content[type_start:decl.start_byte].decode("utf-8", "replace"))
            out.append({"name": _node_text(name_node, content),
                        "type": ptype or None, "default": None})
    return out


def _base_types(node, content: bytes) -> list[str]:
    """Supertypes as written in source (generic arguments preserved):
    ``[extends] + implements``, in source order. Distinct from the
    xref-consumed ``extends``/``implements`` item fields, which carry
    bare type identifiers.
    """
    out: list[str] = []
    for sub in node.children:
        if sub.type == "superclass":
            for tch in sub.children:
                if tch.is_named:
                    out.append(_collapse(_node_text(tch, content)))
        elif sub.type in {"super_interfaces", "extends_interfaces"}:
            tl = _named_child_by_type(sub, "type_list")
            if tl is not None:
                for tch in tl.children:
                    if tch.is_named:
                        out.append(_collapse(_node_text(tch, content)))
    return out


def _type_param_texts(node, content: bytes) -> list[str]:
    tp = node.child_by_field_name("type_parameters")
    if tp is None:
        return []
    return [_collapse(_node_text(ch, content)) for ch in tp.children if ch.is_named]


def _signature_fields(node, content: bytes) -> dict:
    """Canonical signature fields for one type/method/constructor node.

    Empty/unknown fields are omitted entirely (presence is evidence);
    ``is_async`` is never true for Java, so it is never emitted.
    """
    visibility, decorators, annotation_spans = _modifier_info(node, content)
    fields: dict = {"signature": _header_text(node, content, annotation_spans)}
    if visibility:
        fields["visibility"] = visibility
    if decorators:
        fields["decorators"] = decorators
    type_params = _type_param_texts(node, content)
    if type_params:
        fields["type_params"] = type_params
    if node.type in _TYPE_NODE_TYPES:
        bases = _base_types(node, content)
        if bases:
            fields["bases"] = bases
    params_node = node.child_by_field_name("parameters")
    if params_node is not None:
        params = _param_records(params_node, content)
        if params:
            fields["params"] = params
    if node.type == "method_declaration":
        return_type = node.child_by_field_name("type")
        if return_type is not None:
            fields["returns"] = _collapse(_node_text(return_type, content))
    return fields


def _walk_for_items(node, content: bytes, parent_name: str | None,
                    out: list[dict]) -> None:
    """Depth-first descend the AST, emitting one record per type
    declaration and per method/constructor. Inner classes inherit
    their enclosing class as ``parent`` so the xref resolver can
    distinguish ``Outer.foo()`` from ``Inner.foo()``.
    """
    if node.type in _TYPE_NODE_TYPES:
        name_node = _named_child_by_type(node, "identifier")
        if name_node is not None:
            type_name = _node_text(name_node, content)
            kind = {
                "class_declaration": "class",
                "interface_declaration": "interface",
                "enum_declaration": "enum",
                "annotation_type_declaration": "annotation",
                "record_declaration": "record",
            }[node.type]
            item = {
                "kind": kind,
                "name": type_name,
                "parent": parent_name,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "byte_start": node.start_byte,
                "byte_end": node.end_byte,
            }
            item.update(_signature_fields(node, content))
            out.append(item)
            # Descend into the body so nested types and methods get
            # ``parent=type_name``.
            body = _named_child_by_type(node, "class_body") or \
                _named_child_by_type(node, "interface_body") or \
                _named_child_by_type(node, "enum_body") or \
                _named_child_by_type(node, "annotation_type_body") or \
                _named_child_by_type(node, "record_body")
            if body is not None:
                for ch in body.children:
                    if ch.is_named:
                        _walk_for_items(ch, content, type_name, out)
            return
    if node.type in _CALLABLE_NODE_TYPES:
        name_node = _named_child_by_type(node, "identifier")
        if name_node is not None and parent_name is not None:
            method_name = _node_text(name_node, content)
            kind = "method" if node.type == "method_declaration" else "constructor"
            item = {
                "kind": kind,
                "name": method_name,
                "parent": parent_name,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "byte_start": node.start_byte,
                "byte_end": node.end_byte,
            }
            item.update(_signature_fields(node, content))
            out.append(item)
        return
    # Generic descent.
    for ch in node.children:
        if ch.is_named:
            _walk_for_items(ch, content, parent_name, out)


def _collect_imports(root, content: bytes) -> list[dict]:
    """Return the list of import records.

    Each record:

        {"source": "com.example.Foo",
         "lineno": 5,
         "static": False,         # `import static ...`
         "wildcard": False}       # `import com.example.*;`
    """
    out: list[dict] = []
    for ch in root.children:
        if ch.type != "import_declaration":
            continue
        is_static = False
        is_wildcard = False
        ident_node = None
        for sub in ch.children:
            if sub.type == "static" or _node_text(sub, content) == "static":
                is_static = True
            elif sub.type in {"scoped_identifier", "identifier"}:
                ident_node = sub
            elif sub.type == "asterisk":
                is_wildcard = True
        if ident_node is None:
            continue
        fqn = _node_text(ident_node, content)
        out.append({
            "kind": "import",
            "source": fqn,
            "lineno": ch.start_point[0] + 1,
            "static": is_static,
            "wildcard": is_wildcard,
        })
    out.sort(key=lambda x: (x["lineno"], x["source"]))
    return out


def _collect_package(root, content: bytes) -> str:
    for ch in root.children:
        if ch.type != "package_declaration":
            continue
        for sub in ch.children:
            if sub.type in {"scoped_identifier", "identifier"}:
                return _node_text(sub, content)
    return ""


def _collect_supertypes(node, content: bytes) -> tuple[str | None, list[str]]:
    """For a class/interface declaration node, return
    ``(extends_type, implements_types)``.

    ``extends`` for a class is a single type; for an interface it is a
    type list (``extends A, B``). We normalise both into a list returned
    as the *second* element and treat the first as a single optional
    string only for classes.
    """
    extends: str | None = None
    implements: list[str] = []
    for sub in node.children:
        if sub.type == "superclass":
            t = _named_child_by_type(sub, "type_identifier")
            if t is not None:
                extends = _node_text(t, content)
        elif sub.type in {"super_interfaces", "extends_interfaces"}:
            tl = _named_child_by_type(sub, "type_list")
            if tl is not None:
                for tch in tl.children:
                    if tch.is_named and tch.type == "type_identifier":
                        implements.append(_node_text(tch, content))
    return extends, implements


def _attach_supertypes(items: list[dict], root, content: bytes) -> None:
    """Walk the AST a second time and annotate every type item with its
    ``extends`` / ``implements`` (so xref's subclassOf doesn't have to
    re-walk the tree).
    """
    type_items_by_span = {
        (it["byte_start"], it["byte_end"]): it
        for it in items if it["kind"] in {"class", "interface", "enum",
                                           "annotation", "record"}
    }

    # Iterative full pre-order walk (see _treewalk): descends into method
    # bodies, so a recursive walk would overflow on a deeply-nested file.
    for node in iter_named_pre_order(root):
        if node.type in _TYPE_NODE_TYPES:
            key = (node.start_byte, node.end_byte)
            it = type_items_by_span.get(key)
            if it is not None:
                ext, impl = _collect_supertypes(node, content)
                if ext is not None:
                    it["extends"] = ext
                if impl:
                    it["implements"] = impl


def extract_java_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["java"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors = parse_error_diagnostics(tree.root_node)

    package_name = _collect_package(tree.root_node, content)
    imports = _collect_imports(tree.root_node, content)

    items: list[dict] = []
    for ch in tree.root_node.children:
        if ch.is_named:
            _walk_for_items(ch, content, None, items)
    _attach_supertypes(items, tree.root_node, content)
    items.sort(key=lambda x: (x["line_start"], x["kind"], x.get("parent") or "", x["name"]))

    top_level_classes = sorted({
        it["name"] for it in items
        if it.get("parent") is None
        and it["kind"] in {"class", "interface", "enum", "annotation", "record"}
    })
    # Java has no top-level free functions; the field exists for shape
    # parity with other analyzers but stays empty.
    top_level_functions: list[str] = []

    return {
        "language": "java",
        "package": package_name,
        "imports": imports,
        "top_level_functions": top_level_functions,
        "top_level_classes": top_level_classes,
        "items": items,
    }, errors


# ---------------------------------------------------------------------------
# Source-root + FQN indices
# ---------------------------------------------------------------------------


# Recognised Maven / Gradle layout markers. The conventional path under
# any of these roots is ``<pkg-as-dirs>/<ClassName>.java``.
_JAVA_SOURCE_ROOT_MARKERS = (
    "src/main/java",
    "src/test/java",
    "src/main/kotlin",   # Kotlin-friendly Maven layout — Java sometimes
                         # lives here in mixed projects. Skipping breaks
                         # nothing; matching is opportunistic.
)


def detect_java_source_roots(records: list[FileRecord]) -> list[str]:
    """Return every directory ending in one of the marker paths.

    A "source root" is a directory that the rest of the resolver treats
    as the anchor for FQN ↔ path math. For a Maven module at
    ``services/auth/``, this returns ``services/auth/src/main/java`` and
    ``services/auth/src/test/java``. For a flat project the roots are
    ``src/main/java`` and ``src/test/java`` at the repo root.

    Deterministic: roots are returned sorted longest-first so that nested
    modules (``services/auth/src/main/java`` inside the root layout)
    take precedence in the prefix match.
    """
    found: set[str] = set()
    for r in records:
        if r.language != "java":
            continue
        path = r.path
        for marker in _JAVA_SOURCE_ROOT_MARKERS:
            idx = path.rfind("/" + marker + "/")
            if idx == 0 or idx > 0:
                root = path[:idx + 1 + len(marker)]
                found.add(root.lstrip("/"))
            elif path.startswith(marker + "/"):
                found.add(marker)
    return sorted(found, key=lambda x: (-len(x), x))


def build_java_fqn_index(records: list[FileRecord]) -> dict[str, str]:
    """Map ``pkg.ClassName`` → file path for every top-level class.

    Ambiguous FQNs (two files declaring the same class) are dropped so
    the resolver doesn't bind an import to the wrong file.
    """
    cand: dict[str, list[str]] = defaultdict(list)
    for r in records:
        if r.language != "java" or r.ast_summary is None:
            continue
        pkg = r.ast_summary.get("package", "") or ""
        for cls in r.ast_summary.get("top_level_classes", []):
            fqn = f"{pkg}.{cls}" if pkg else cls
            cand[fqn].append(r.path)
    return {k: v[0] for k, v in cand.items() if len(set(v)) == 1}


def build_java_package_index(records: list[FileRecord]) -> dict[str, list[str]]:
    """Map ``pkg`` → sorted list of file paths declaring that package.

    Used to resolve wildcard imports (``import com.example.*;``) and to
    let the xref resolver find any same-package sibling.
    """
    out: dict[str, list[str]] = defaultdict(list)
    for r in records:
        if r.language != "java" or r.ast_summary is None:
            continue
        pkg = r.ast_summary.get("package", "") or ""
        if pkg:
            out[pkg].append(r.path)
    return {k: sorted(set(v)) for k, v in out.items()}


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------


def resolve_java_imports(
    src_path: str,
    summary: dict,
    by_fqn: dict[str, str],
    by_pkg: dict[str, list[str]],
    declared_pkgs: set[str],
) -> tuple[list[str], list[str], list[str]]:
    """Resolve Java imports.

    Returns ``(in_repo_paths, external_package_coords, prefix_matched_coords)``
    mirroring the Kotlin resolver's contract — the third element lets
    the host's manifest builder mark prefix-matched declared coords.

    Resolution order:

      1. Static imports: drop the trailing ``.member`` and try as a type
         FQN against ``by_fqn``. ``import static com.x.Math.max;`` →
         look for ``com.x.Math``.
      2. Wildcard imports (``com.x.*``): emit every in-repo file under
         that package as an edge. (We don't try to bind specific symbols
         here; the xref resolver does that lazily.)
      3. Exact FQN in ``by_fqn``.
      4. Parent-FQN fallback: ``com.x.Outer.Inner`` is an inner class —
         try ``com.x.Outer``.
      5. Longest-group-prefix match against declared Maven coordinates.
      6. Anything else gets a 3-segment external prefix.

    Same-package imports are NOT required in Java (the package is
    implicit) but are sometimes written; the by-fqn path handles them
    naturally.
    """
    coord_by_group: list[tuple[str, str]] = []
    for coord in declared_pkgs:
        if ":" in coord:
            group = coord.split(":", 1)[0]
            coord_by_group.append((group, coord))
    coord_by_group.sort(key=lambda x: (-len(x[0]), x[1]))

    dst: set[str] = set()
    exact_ext: set[str] = set()
    prefix_ext: set[str] = set()

    for imp in summary.get("imports", []):
        fqn = imp["source"]
        is_static = bool(imp.get("static"))
        is_wildcard = bool(imp.get("wildcard"))

        if is_wildcard:
            # ``com.example.*`` — every in-repo file in the package
            # becomes an in-repo edge. External wildcard imports
            # (``java.util.*``) fall through to the Maven-coord pass.
            # The analyzer may store the source either as ``com.example``
            # (preferred, ``wildcard=True`` flag) or as
            # ``com.example.*`` (defensive); strip the trailing ``.*``.
            pkg_lookup = fqn[:-2] if fqn.endswith(".*") else fqn
            files = by_pkg.get(pkg_lookup, [])
            if files:
                for p in files:
                    if p != src_path:
                        dst.add(p)
                continue
            # If no in-repo files, attempt the Maven-coord prefix match.
            matched = False
            for group, coord in coord_by_group:
                if fqn == group or fqn.startswith(group + "."):
                    prefix_ext.add(coord)
                    matched = True
                    break
            if not matched:
                parts = fqn.split(".")
                exact_ext.add(".".join(parts[:3]) if len(parts) >= 3 else fqn)
            continue

        lookup = fqn
        if is_static:
            # Static imports name a *member*, not a type. Strip the
            # trailing segment to recover the type FQN.
            if "." in lookup:
                lookup = lookup.rsplit(".", 1)[0]

        if lookup in by_fqn:
            if by_fqn[lookup] != src_path:
                dst.add(by_fqn[lookup])
            continue

        # Parent-FQN fallback for inner classes (``Outer.Inner``).
        parent = lookup.rsplit(".", 1)[0] if "." in lookup else lookup
        if parent != lookup and parent in by_fqn:
            if by_fqn[parent] != src_path:
                dst.add(by_fqn[parent])
            continue

        # Maven-coord prefix match.
        matched = False
        for group, coord in coord_by_group:
            if fqn == group or fqn.startswith(group + "."):
                prefix_ext.add(coord)
                matched = True
                break
        if matched:
            continue

        parts = fqn.split(".")
        exact_ext.add(".".join(parts[:3]) if len(parts) >= 3 else fqn)

    return sorted(dst), sorted(exact_ext | prefix_ext), sorted(prefix_ext)
