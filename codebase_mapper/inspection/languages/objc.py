"""codebase_mapper.languages.objc — Tier-1 Objective-C / Objective-C++ support.

Both ``.m`` (ObjC) and ``.mm`` (ObjC++) files are handled by this module.
tree-sitter-objc parses ObjC structures correctly in both dialects; for
``.mm`` files the C++ method bodies may produce non-fatal
``parse_errors_present`` diagnostics, but the structural items
(``@interface``/``@implementation``/``@protocol``/methods/categories)
are recovered uniformly.

The analyzer emits:

  * ``imports``  — every ``#include``/``#import`` (kind ``"local_include"``
                   or ``"system_include"``), plus ``@import <Module>``
                   directives (kind ``"module_import"`` with the module
                   name as the source).
  * ``items``    — one record per type (class interface, class
                   implementation, protocol, category) and per method
                   (declaration in interface OR definition in
                   implementation). Items carry ``line_start/end``,
                   ``byte_start/end``, ``parent`` (the enclosing class
                   for methods, ``None`` for top-level types), and
                   ``selector`` for methods (the ObjC dispatch key,
                   e.g. ``initWithName:`` or ``randomBetween:and:``).
  * ``protocols`` — protocol references on a class
                    (``<NSCopying, UIScrollViewDelegate>``).

Public surface:

  * ``extract_objc_ast_summary(content, path) -> (summary, errors)``
  * ``build_objc_symbol_index(records) -> dict``
  * ``resolve_objc_includes(record, summary, paths_set) -> (in_repo, external)``
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from ..models import FileRecord
from ...ts_setup import _TS_LANGS, _ts_setup, TS_AVAILABLE, ts
from ._treewalk import find_named_descendant, iter_named_pre_order


# Objective-C dialect tags we accept. Both share the same grammar and
# analyzer; the resolver / chunker dispatch on either.
OBJC_LANGUAGE_TAGS = frozenset({"objective-c", "objective-cpp"})


def _node_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _find_first(node, kind: str):
    for ch in node.children:
        if ch.is_named and ch.type == kind:
            return ch
    return None


def _find_descendant(node, kinds: set[str]):
    # Iterative (see _treewalk): a recursive search overflows on deeply-nested
    # subtrees. Same pre-order, root-inclusive first-match semantics.
    return find_named_descendant(node, kinds)


def _collapse(text: str) -> str:
    """Collapse all whitespace runs to single spaces (single-line headers)."""
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Includes / @import
# ---------------------------------------------------------------------------


def _collect_imports(root, content: bytes) -> list[dict]:
    out: list[dict] = []

    # Iterative pre-order (see _treewalk): this walk descends into function
    # bodies, so a deeply-nested file would overflow a recursive visitor. Prune
    # at import nodes — they have no nested imports — matching the recursive
    # visitor's early ``return``.
    for node in iter_named_pre_order(
        root, descend=lambda n: n.type not in ("preproc_include", "module_import"),
    ):
        nt = node.type
        if nt == "preproc_include":
            path_node = node.child_by_field_name("path")
            if path_node is not None:
                raw = _node_text(path_node, content).strip()
                if path_node.type == "string_literal":
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
        elif nt == "module_import":
            # `@import Foundation;` / `@import UIKit.UIView;` — text minus the
            # `@import` prefix and trailing `;`.
            t = _node_text(node, content).strip()
            if t.startswith("@import"):
                t = t[len("@import"):].strip()
            if t.endswith(";"):
                t = t[:-1].strip()
            if t:
                out.append({
                    "kind": "module_import",
                    "source": t,
                    "lineno": node.start_point[0] + 1,
                })
    out.sort(key=lambda x: (x["lineno"], x["source"]))
    return out


# ---------------------------------------------------------------------------
# Method selector composition
# ---------------------------------------------------------------------------


def _method_selector(node, content: bytes) -> tuple[str, str]:
    """Compose the ObjC selector and a short symbol name for a method
    declaration / definition node.

    ObjC methods are dispatched by selector:

      * ``- (NSString *)hello;``                  → selector = ``hello``
      * ``- (instancetype)initWithName:(NSString *)name;``
                                                    → selector = ``initWithName:``
      * ``+ (NSInteger)randomBetween:(NSInteger)lo and:(NSInteger)hi;``
                                                    → selector = ``randomBetween:and:``

    The "short symbol name" is the first selector segment, which is
    what the L2 chunker uses to label the chunk. The full selector is
    surfaced via the item's ``selector`` attribute for downstream
    xref binding.

    For unrecognised shapes returns ``("", "")``.
    """
    parts: list[str] = []
    saw_first = False
    for ch in node.children:
        if not ch.is_named:
            continue
        if ch.type == "identifier" and not saw_first:
            parts.append(_node_text(ch, content))
            saw_first = True
        elif ch.type == "method_parameter":
            label_node = None
            # The first child identifier (preceding the `:` if any) is
            # the parameter label. Without a label the grammar still
            # emits an identifier for the parameter name only — but
            # that's not how dispatch works. Walk through identifiers.
            ids = [c for c in ch.children if c.is_named and c.type == "identifier"]
            if len(ids) >= 2:
                # First id is the param-label-before-colon, second is
                # the param name. Some grammars only have one when the
                # label is empty.
                label_node = ids[0]
            elif ids:
                label_node = ids[0]
            if label_node is not None:
                # The label appears AFTER an existing parts entry; the
                # convention is that the previous identifier is part
                # of the selector. We append a trailing colon to the
                # previous entry to indicate "expects an argument".
                if parts:
                    parts[-1] = parts[-1] + ":"
            # The "extra label" identifier sandwiched between
            # method_parameters (e.g. `and` in ``randomBetween:and:``)
            # is a separate top-level identifier child of the method
            # node — handled below.
        elif ch.type == "identifier" and saw_first:
            # A bare identifier after we already captured the leading
            # selector word but BEFORE the next method_parameter is
            # the next selector label.
            parts.append(_node_text(ch, content))
    selector = "".join(parts) if parts else ""
    short = parts[0].rstrip(":") if parts else ""
    return selector, short


# ---------------------------------------------------------------------------
# Canonical signature fields (plugins/chunks_embeddings/signatures.py)
# ---------------------------------------------------------------------------


# Direct children that belong to a type declaration's header line; the header
# ends at the first child of any other kind (ivars, properties, methods,
# ``@end``).
_TYPE_HEADER_CHILD_TYPES = frozenset({
    "@interface", "@implementation", "@protocol", "identifier",
    ":", "(", ")", "parameterized_arguments", "protocol_qualifiers",
    "protocol_reference_list",
})

# The ``<...>`` list node kinds a type header may carry (grammar versions
# differ on the node name).
_ANGLE_LIST_TYPES = frozenset({
    "parameterized_arguments", "protocol_qualifiers", "protocol_reference_list",
})


def _protocol_names(list_node, content: bytes) -> list[str]:
    """Names inside a ``<Proto1, Proto2>`` list node, as written."""
    out: list[str] = []
    for ch in list_node.children:
        if ch.is_named and ch.type in {"type_identifier", "identifier"}:
            out.append(_node_text(ch, content))
        elif ch.is_named and ch.type == "type_name":
            ti = _find_first(ch, "type_identifier") or _find_first(ch, "identifier")
            if ti is not None:
                out.append(_node_text(ti, content))
    return out


def _split_angle_lists(node, content: bytes) -> tuple[list[str], list[str]]:
    """Split a type header's ``<...>`` lists into ``(type_params, protocols)``.

    Position disambiguates lightweight generics from protocol adoption:
    ``@interface Box<ObjectType> : NSObject <NSCopying>`` puts the generics
    list BEFORE the superclass identifier and the protocol list AFTER it.
    The anchor is the last direct identifier child (class name, superclass,
    or category name — whichever comes last).
    """
    ids = [c for c in node.children if c.is_named and c.type == "identifier"]
    anchor = ids[-1].end_byte if ids else node.start_byte
    type_params: list[str] = []
    protocols: list[str] = []
    for ch in node.children:
        if not (ch.is_named and ch.type in _ANGLE_LIST_TYPES):
            continue
        names = _protocol_names(ch, content)
        if ch.start_byte >= anchor:
            protocols.extend(names)
        else:
            type_params.extend(names)
    return type_params, protocols


def _type_header_signature(node, content: bytes) -> str | None:
    """The declaration header as written — e.g. ``@interface Dog : Animal
    <NSCopying>`` — cut before the first non-header child and collapsed to
    a single line."""
    end = None
    for ch in node.children:
        if ch.type not in _TYPE_HEADER_CHILD_TYPES:
            break
        end = ch.end_byte
    if end is None:
        return None
    return _collapse(content[node.start_byte:end].decode("utf-8", "replace")) or None


def _type_signature_fields(node, content: bytes, superclass: str | None) -> dict:
    """Canonical signature fields for an ``@interface`` / ``@implementation``
    / ``@protocol`` node. Empty fields are omitted (presence is evidence);
    ``visibility`` / ``is_async`` / ``decorators`` never apply to ObjC types.
    """
    fields: dict = {}
    signature = _type_header_signature(node, content)
    if signature:
        fields["signature"] = signature
    type_params, protocols = _split_angle_lists(node, content)
    bases = ([superclass] if superclass else []) + protocols
    if bases:
        fields["bases"] = bases
    if type_params:
        fields["type_params"] = type_params
    return fields


def _method_signature_fields(node, content: bytes) -> dict:
    """Canonical signature fields for a ``method_declaration`` /
    ``method_definition`` node.

    ObjC has no default arguments (``default`` is always None), no member
    visibility keywords, and no async marker — those fields are never
    emitted. ``params`` carries one entry per selector segment that takes
    an argument (each ``method_parameter`` child).
    """
    fields: dict = {}
    sig_end = node.end_byte
    for ch in node.children:
        if ch.type in {";", "compound_statement"}:
            sig_end = ch.start_byte
            break
    signature = _collapse(
        content[node.start_byte:sig_end].decode("utf-8", "replace"))
    if signature:
        fields["signature"] = signature
    ret = _find_first(node, "method_type")  # return type; param types nest deeper
    if ret is not None:
        tn = _find_first(ret, "type_name")
        if tn is not None:
            returns = _collapse(_node_text(tn, content))
            if returns:
                fields["returns"] = returns
    params: list[dict] = []
    for ch in node.children:
        if not (ch.is_named and ch.type == "method_parameter"):
            continue
        ids = [c for c in ch.children if c.is_named and c.type == "identifier"]
        if not ids:
            continue
        ptype = None
        mt = _find_first(ch, "method_type")
        if mt is not None:
            tn = _find_first(mt, "type_name")
            if tn is not None:
                ptype = _collapse(_node_text(tn, content)) or None
        params.append({"name": _node_text(ids[-1], content),
                       "type": ptype, "default": None})
    if params:
        fields["params"] = params
    return fields


# ---------------------------------------------------------------------------
# Class interface / implementation / protocol walker
# ---------------------------------------------------------------------------


def _class_interface_name(node, content: bytes) -> tuple[str | None, str | None,
                                                          list[str], str | None]:
    """Return ``(class_name, superclass, protocols, category)`` for an
    ``@interface`` node.

    Categories:

      * ``@interface NSString (Greet)`` — extends NSString with new
        methods. ``class_name = "NSString"``, ``category = "Greet"``,
        ``superclass = None``.
      * ``@interface Dog : Animal <NSCopying>`` —
        ``class_name = "Dog"``, ``superclass = "Animal"``,
        ``protocols = ["NSCopying"]``.

    Disambiguator: if the raw source between the first and second
    identifiers contains ``(``, the second identifier is the category
    name; if it contains ``:``, the second identifier is the superclass.
    """
    ids = [c for c in node.children if c.is_named and c.type == "identifier"]
    if not ids:
        return None, None, [], None
    class_name = _node_text(ids[0], content)
    superclass = None
    category = None
    if len(ids) >= 2:
        # Check the raw bytes between the two identifiers.
        between = content[ids[0].end_byte:ids[1].start_byte].decode("utf-8", "replace")
        second = _node_text(ids[1], content)
        if "(" in between:
            category = second
        else:
            superclass = second
    protocols: list[str] = []
    pl = _find_first(node, "parameterized_arguments") \
        or _find_first(node, "protocol_qualifiers") \
        or _find_first(node, "protocol_reference_list")
    if pl is not None:
        protocols = _protocol_names(pl, content)
    return class_name, superclass, protocols, category


def _walk_tu(root, content: bytes, items: list[dict]) -> None:
    """Walk the translation_unit, emitting one item per type / method /
    protocol / function."""

    terminal = ("class_interface", "class_implementation",
                "protocol_declaration", "function_definition")
    # Iterative pre-order (see _treewalk): prune at the terminal declaration
    # kinds — each is handled and never descended into, matching the recursive
    # visitor's early ``return``.
    for node in iter_named_pre_order(root, descend=lambda n: n.type not in terminal):
        nt = node.type
        if nt == "class_interface":
            name, superclass, protocols, category = _class_interface_name(node, content)
            if name is not None:
                item_kind = "category" if category else "class_interface"
                item_name = f"{name}({category})" if category else name
                item: dict = {
                    "kind": item_kind,
                    "name": item_name,
                    "parent": None,
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "byte_start": node.start_byte,
                    "byte_end": node.end_byte,
                }
                if superclass:
                    item["extends"] = superclass
                if protocols:
                    item["implements"] = protocols
                item.update(_type_signature_fields(node, content, superclass))
                items.append(item)
                # Walk methods inside the interface body.
                _emit_class_methods(node, content, item_name, items,
                                    decl_only=True)
        elif nt == "class_implementation":
            name, _su, _pr, category = _class_interface_name(node, content)
            if name is not None:
                item_kind = "category_impl" if category else "class_implementation"
                item_name = f"{name}({category})" if category else name
                impl_item: dict = {
                    "kind": item_kind,
                    "name": item_name,
                    "parent": None,
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "byte_start": node.start_byte,
                    "byte_end": node.end_byte,
                }
                # An @implementation states no supertypes: only the header
                # signature applies (superclass=None keeps bases out).
                impl_item.update(
                    {k: v for k, v in
                     _type_signature_fields(node, content, None).items()
                     if k != "bases"})
                items.append(impl_item)
                _emit_class_methods(node, content, item_name, items,
                                    decl_only=False)
        elif nt == "protocol_declaration":
            name_node = _find_first(node, "identifier")
            if name_node is not None:
                proto_name = _node_text(name_node, content)
                proto_refs: list[str] = []
                rl = _find_first(node, "protocol_reference_list")
                if rl is not None:
                    for ch in rl.children:
                        if ch.is_named and ch.type == "identifier":
                            proto_refs.append(_node_text(ch, content))
                item = {
                    "kind": "protocol",
                    "name": proto_name,
                    "parent": None,
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "byte_start": node.start_byte,
                    "byte_end": node.end_byte,
                }
                if proto_refs:
                    item["implements"] = proto_refs
                item.update(_type_signature_fields(node, content, None))
                items.append(item)
                _emit_class_methods(node, content, proto_name, items,
                                    decl_only=True)
        elif nt == "function_definition":
            # C-style free function (common in ObjC for utility helpers).
            decl = _find_descendant(node, {"function_declarator"})
            if decl is not None:
                ident = _find_first(decl, "identifier")
                if ident is not None:
                    items.append({
                        "kind": "function",
                        "name": _node_text(ident, content),
                        "parent": None,
                        "line_start": node.start_point[0] + 1,
                        "line_end": node.end_point[0] + 1,
                        "byte_start": node.start_byte,
                        "byte_end": node.end_byte,
                    })


def _emit_class_methods(class_node, content: bytes, parent_name: str,
                        items: list[dict], decl_only: bool) -> None:
    """Walk an ``@interface`` or ``@implementation`` body for methods.

    ``decl_only=True`` means we're in an interface — methods are
    declarations only (kind = ``method``).
    ``decl_only=False`` means we're in an implementation — methods
    are definitions (kind = ``method``, but the body is parseable
    by the chunker as a real chunk text).
    """

    def visit(node, depth=0):
        if depth > 5:
            return
        nt = node.type
        if nt in ("method_declaration", "method_definition"):
            selector, short = _method_selector(node, content)
            if short:
                method_item = {
                    "kind": "method",
                    "name": short,
                    "parent": parent_name,
                    "selector": selector,
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "byte_start": node.start_byte,
                    "byte_end": node.end_byte,
                }
                method_item.update(_method_signature_fields(node, content))
                items.append(method_item)
            return
        for ch in node.children:
            if ch.is_named:
                visit(ch, depth + 1)

    visit(class_node)


def extract_objc_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["objc"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors: list[str] = []
    if tree.root_node.has_error:
        errors.append("parse_errors_present")

    imports = _collect_imports(tree.root_node, content)
    items: list[dict] = []
    _walk_tu(tree.root_node, content, items)
    items.sort(key=lambda x: (x["line_start"], x["kind"],
                              x.get("parent") or "", x["name"]))

    top_level_classes = sorted({
        it["name"] for it in items
        if it.get("parent") is None
        and it["kind"] in {"class_interface", "class_implementation",
                            "protocol", "category", "category_impl"}
    })
    # ObjC has no top-level free *functions* in the Java/Dart sense, but
    # C-style ``static int foo(int x) { ... }`` is common in ObjC code
    # for module-internal helpers. Surface them so consumers can index
    # them.
    top_level_functions = sorted({
        it["name"] for it in items
        if it.get("parent") is None and it["kind"] == "function"
    })

    return {
        "language": "objc",
        "imports": imports,
        "top_level_functions": top_level_functions,
        "top_level_classes": top_level_classes,
        "items": items,
    }, errors


# ---------------------------------------------------------------------------
# Cross-file symbol index
# ---------------------------------------------------------------------------


def refine_objc_header_languages(records: list[FileRecord]) -> None:
    """In-place: re-tag ``.h`` files as ``language="objective-c"`` when
    the project contains ObjC source files (``.m``/``.mm``).

    Apple convention: a ``Foo.h`` header that ships alongside ``Foo.m``
    is ObjC, not C. The tree-sitter-c grammar errors out on ``@interface``
    / ``@protocol`` / ``#import``; routing those headers to the ObjC
    analyzer is mandatory for Tier-1 coverage.

    Sibling rule first (catches ``Foo.h`` + ``Foo.m`` co-residence);
    project-wide rule second (covers ``include/`` vs ``src/`` splits)
    suppressed by a co-resident ``.c`` file at the same directory.

    Runs BEFORE the C++ retag so that in mixed Apple repos (which is
    rare but possible — e.g. macOS apps mixing ObjC and C++) ObjC takes
    precedence for ``.h`` files in directories containing ObjC sources.
    """
    has_objc_source = any(
        r.language in OBJC_LANGUAGE_TAGS
        and PurePosixPath(r.path).suffix in {".m", ".mm"}
        for r in records
    )
    if not has_objc_source:
        return

    objc_dirs: set[str] = set()
    c_source_dirs: set[str] = set()
    for r in records:
        d = str(PurePosixPath(r.path).parent)
        if r.language in OBJC_LANGUAGE_TAGS:
            objc_dirs.add(d)
        elif r.language == "c" and PurePosixPath(r.path).suffix == ".c":
            c_source_dirs.add(d)

    for r in records:
        if r.language != "c" or PurePosixPath(r.path).suffix != ".h":
            continue
        d = str(PurePosixPath(r.path).parent)
        if d in objc_dirs:
            r.language = "objective-c"
            continue
        if d not in c_source_dirs:
            r.language = "objective-c"


def build_objc_symbol_index(records: list[FileRecord]) -> dict[str, list[str]]:
    """Map an ObjC class / protocol / category-host class name → defining files.

    A class declared in ``Animal.h`` and implemented in ``Animal.m`` is
    indexed under both ``Animal.h`` and ``Animal.m`` so the xref
    resolver can bind both a *declaration*-keyed reference and a
    *definition*-keyed one to the appropriate chunk.

    Categories register under both their full name (``NSString(Greet)``)
    AND the bare host class name (``NSString``) so receiver-style xrefs
    against the base class still resolve.
    """
    out: dict[str, list[str]] = defaultdict(list)
    for r in records:
        if r.language not in OBJC_LANGUAGE_TAGS or r.ast_summary is None:
            continue
        for it in r.ast_summary.get("items", []):
            if it.get("parent") is not None:
                continue
            kind = it["kind"]
            if kind in {"class_interface", "class_implementation",
                         "protocol", "function"}:
                if r.path not in out[it["name"]]:
                    out[it["name"]].append(r.path)
            elif kind in {"category", "category_impl"}:
                full = it["name"]  # e.g. NSString(Greet)
                if r.path not in out[full]:
                    out[full].append(r.path)
                host = full.split("(", 1)[0]
                if r.path not in out[host]:
                    out[host].append(r.path)
    return dict(out)


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------


def resolve_objc_includes(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """Resolve ``#include``/``#import`` to in-repo paths.

    Uses the same algorithm as ``resolve_c_includes`` (relative-then-
    suffix), but tolerates the additional ObjC-style header forms:

      * ``"Foundation/Foundation.h"`` (framework-qualified) — falls
        through to suffix matching against any same-named header
        anywhere in the repo (this works for Apple's umbrella headers
        when they happen to live in-repo).
      * ``@import Module;`` directives — always external; the module
        name surfaces as an external dependency entry.
    """
    dst: set[str] = set()
    external: set[str] = set()
    src_dir = PurePosixPath(src_path).parent
    for imp in summary.get("imports", []):
        kind = imp["kind"]
        spec = imp["source"]
        if kind == "module_import":
            # `@import UIKit;` or `@import Foundation.NSString;`
            # — always external.
            external.add(spec.split(".", 1)[0])
            continue
        if kind == "system_include":
            external.add(spec)
            # `<Foundation/Foundation.h>` may also resolve in-repo if
            # the user vendored the framework. Try a suffix match.
            basename = PurePosixPath(spec).name
            matches = [p for p in paths_set
                       if p == basename or p.endswith("/" + basename)]
            if len(matches) == 1:
                dst.add(matches[0])
            continue
        # local include / import.
        # Try relative-to-source resolution first.
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
        # Suffix-match (Apple-style ``"FrameworkName/Header.h"``).
        basename = PurePosixPath(spec).name
        matches = [p for p in paths_set
                   if p == basename or p.endswith("/" + basename)]
        if len(matches) == 1:
            dst.add(matches[0])
    dst.discard(src_path)
    return sorted(dst), sorted(external)
