"""codebase_mapper.languages.kotlin."""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Callable


from ..models import FileRecord
from ..ts_setup import _TS_LANGS, _TS_QUERIES, _ts_setup
from ..ts_setup import TS_AVAILABLE, ts


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
    return {
        "language": "kotlin",
        "package": package_name,
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
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
