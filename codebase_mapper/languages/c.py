"""codebase_mapper.languages.c."""
from __future__ import annotations

from pathlib import PurePosixPath


from ..ts_setup import _TS_LANGS, _TS_QUERIES, _strip_quotes, _ts_setup
from ..ts_setup import TS_AVAILABLE, ts


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
    return {
        "language": "c",
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
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
