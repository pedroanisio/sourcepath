"""codebase_mapper.languages.go."""
from __future__ import annotations

import re

from pathlib import PurePosixPath
from typing import Callable


from ..models import FileRecord
from ...ts_setup import _TS_LANGS, _TS_QUERIES, _strip_quotes, _ts_setup
from ...ts_setup import TS_AVAILABLE, ts


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
    return {
        "language": "go",
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
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
