"""codebase_mapper.languages.c."""
from __future__ import annotations

from pathlib import PurePosixPath


from ...ts_setup import _TS_LANGS, _TS_QUERIES, _strip_quotes, _ts_setup
from ...ts_setup import TS_AVAILABLE, parse_error_diagnostics, ts
from ._treewalk import find_named_descendant, iter_named_pre_order

_DECL_KINDS = (
    "function_definition", "declaration",
    "struct_specifier", "union_specifier", "enum_specifier",
    "type_definition",
    # Macro definitions ARE symbols — a kernel-style header of #defines
    # previously extracted zero items (plan E3: 7,380 silent-zero files).
    "preproc_def", "preproc_function_def",
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
                # Not a callable: a top-level object declaration (extern or
                # otherwise) is still a symbol (plan E3) — a header of
                # `extern struct alpha_machine_vector alpha_mv;` lines
                # previously yielded nothing.
                if nt == "declaration" and declarator is not None:
                    name = _c_declarator_name(declarator, content)
                    if name:
                        item = _c_item("variable", name, node)
                        item["signature"] = _collapse(
                            content[node.start_byte:node.end_byte].decode("utf-8", "replace"))
                        items.append(item)
                continue
            name = _c_declarator_name(fd.child_by_field_name("declarator"), content)
            if not name:
                continue
            item = _c_item("function", name, node)
            item.update(_c_callable_fields(node, node.child_by_field_name("type"), fd, content))
            items.append(item)
        elif nt in ("preproc_def", "preproc_function_def"):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            items.append(_c_item("macro", _node_text(name_node, content), node))
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


def extract_c_ast_summary(
    content: bytes, path: str, macro_table=None,
) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["c"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors = parse_error_diagnostics(tree.root_node)

    # E1 retry: when the vanilla grammar chokes and the repo's macro table
    # is available, re-parse a byte-preserving neutralized buffer and keep
    # whichever parse has fewer error nodes. Spans stay valid against the
    # original content; the substitution is disclosed via ``parse_buffer``.
    neutralized_used = False
    if errors and macro_table:
        from ..macro_neutralize import neutralize
        from ..coverage import parse_error_node_count
        candidate = neutralize(content, macro_table)
        if candidate is not content:
            tree2 = parser.parse(candidate)
            errors2 = parse_error_diagnostics(tree2.root_node)
            if parse_error_node_count(errors2) < parse_error_node_count(errors):
                tree, errors, content = tree2, errors2, candidate
                neutralized_used = True
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
    summary = {
        "language": "c",
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
        "items": items,
    }
    if neutralized_used:
        summary["parse_buffer"] = "macro_neutralized"
    if not items and not imports:
        # Zero symbols must be an explained state, never a silent one
        # (plan E3): with macros/variables now captured, an empty item
        # list means the file genuinely declares nothing.
        summary["zero_symbol_reason"] = "no_declarations_found"
    return summary, errors

def build_c_include_index(paths_set: set[str]) -> dict[str, list[str]]:
    """Basename → every repo path whose final component is that basename.

    Build this ONCE per repository (the host pipeline stashes it in
    ``ctx.indices["host:c_basename_index"]``; the C/C++ resolvers lazily
    build-and-stash it for contexts that bypass the host index phase) and
    pass it to :func:`resolve_c_includes`. Building is a single O(N) sweep
    over the repo file list; each include lookup then costs one dict hit
    plus a filter over the handful of files sharing a basename, instead of
    an O(N) scan per include. At kernel scale (~95k files × dozens of
    includes per file) the per-include scan is O(files × includes) ≈ 10^10
    string comparisons — this index is what makes resolution feasible.
    """
    index: dict[str, list[str]] = {}
    for p in paths_set:
        index.setdefault(PurePosixPath(p).name, []).append(p)
    return index


#: Ambiguous-candidate cap per include spec — a pathological basename shared
#: by hundreds of files must not explode the possible-import tier. Drops are
#: logged by the caller's edge count vs this cap, never silent.
MAX_AMBIGUOUS_CANDIDATES = 16


def include_roots_from_compile_commands(text: str) -> list[str]:
    """Repo-relative ``-I``/``-isystem`` roots from a compile_commands.json.

    Build evidence beats convention (plan E4): when a project ships its
    compilation database, the compiler's real include roots resolve angle
    includes exactly. Absolute (out-of-repo) roots are skipped — they can
    never name in-repo files. Malformed input yields [] (the caller falls
    back to suffix matching), never an exception.
    """
    import json as _json
    import shlex

    try:
        entries = _json.loads(text)
    except ValueError:
        return []
    roots: list[str] = []
    seen: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        args = entry.get("arguments")
        if not args:
            try:
                args = shlex.split(entry.get("command", ""))
            except ValueError:
                continue
        i = 0
        while i < len(args):
            a = args[i]
            root = None
            if a in ("-I", "-isystem") and i + 1 < len(args):
                root = args[i + 1]
                i += 1
            elif a.startswith("-I") and len(a) > 2:
                root = a[2:]
            elif a.startswith("-isystem") and len(a) > len("-isystem"):
                root = a[len("-isystem"):]
            if root and not root.startswith("/") and root not in seen:
                seen.add(root)
                roots.append(root.rstrip("/"))
            i += 1
    return roots


def resolve_c_includes(
    src_path: str, summary: dict, paths_set: set[str],
    basename_index: dict[str, list[str]] | None = None,
    *,
    include_roots: list[str] | None = None,
    ambiguous_out: dict[str, list[str]] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve #includes to in-repo files.

    #include "x.h" (quoted) from a/b/file.c:
    1. a/b/x.h — the spec joined onto the including file's directory,
       with ``.``/``..`` segments normalized (so ``../include/foo.h``
       works from ``src/``).
    2. Any in-repo file whose final path component is ``x.h``
       (last resort; ambiguous matches dropped).

    #include <linux/foo.h> (angle) — one in-repo attempt, then external:
    1. Any in-repo file whose path ends with the full spec as a
       path suffix (``<linux/foo.h>`` → the path ending in
       ``/linux/foo.h``, e.g. ``include/linux/foo.h``). Accepted ONLY
       when exactly one repo path matches: without the compiler's real
       ``-I`` search order we cannot pick between e.g. the many
       ``arch/*/include/asm/io.h`` candidates for ``<asm/io.h>``, so an
       ambiguous suffix is deliberately left unresolved rather than
       guessed (a wrong edge is worse than a missing one).
    2. No match (``<stdio.h>``) or an ambiguous match → the
       external/unresolved bucket, exactly as quoted-fallback misses.

    ``basename_index`` is the once-per-repo product of
    :func:`build_c_include_index`; when ``None`` (direct/legacy callers)
    it is rebuilt here, which is correct but O(N) per call — hosts must
    pass the prebuilt index.
    """
    if basename_index is None:
        basename_index = build_c_include_index(paths_set)
    dst: set[str] = set()
    unresolved: set[str] = set()
    src_dir = PurePosixPath(src_path).parent

    for imp in summary.get("imports", []):
        spec = imp["source"]
        if imp["kind"] == "system_include":
            # Evidence first (plan E4): the project's own include roots
            # (compile_commands.json) resolve the spec exactly.
            if include_roots:
                rooted = next(
                    (f"{root}/{spec}" for root in include_roots
                     if f"{root}/{spec}" in paths_set), None)
                if rooted is not None:
                    dst.add(rooted)
                    continue
            # In-repo attempt: unique path-suffix match (see docstring —
            # ambiguity is resolved by NOT resolving in the hard tier).
            candidates = basename_index.get(PurePosixPath(spec).name, [])
            suffix = "/" + spec
            matches = [p for p in candidates
                       if p == spec or p.endswith(suffix)]
            if len(matches) == 1:
                dst.add(matches[0])
            else:
                unresolved.add(spec)
                # Multi-candidate: recall becomes queryable data instead of
                # absent — the caller emits cbm:possibleImport candidates.
                if ambiguous_out is not None and 1 < len(matches):
                    ambiguous_out[spec] = sorted(
                        matches)[:MAX_AMBIGUOUS_CANDIDATES]
            continue
        # Quoted include. Try relative first.
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
        # Basename match — accept only if unambiguous. The index lists
        # exactly the paths whose final component equals the basename,
        # i.e. the same set the previous O(N) scan produced.
        matches = basename_index.get(PurePosixPath(spec).name, [])
        # The relative path we tried is already in `target` and didn't hit,
        # so the basename match is necessarily a different location. Accept
        # only if exactly one match exists.
        if len(matches) == 1:
            dst.add(matches[0])
    return sorted(dst), sorted(unresolved)
