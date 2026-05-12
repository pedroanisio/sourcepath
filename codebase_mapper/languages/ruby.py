"""codebase_mapper.languages.ruby."""
from __future__ import annotations

from pathlib import PurePosixPath


from ..ts_setup import _TS_LANGS, _TS_QUERIES, _strip_quotes, _ts_setup
from ..ts_setup import TS_AVAILABLE, ts


def extract_ruby_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["ruby"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors = ["parse_errors_present"] if tree.root_node.has_error else []
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
    return {
        "language": "ruby",
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
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
