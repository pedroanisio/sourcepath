"""codebase_mapper.languages.tsjs."""
from __future__ import annotations

import json
import re

from pathlib import PurePosixPath
from typing import Callable


from ..models import FileRecord
from ..ts_setup import _TS_LANGS, _TS_QUERIES, _strip_quotes, _ts_setup
from ..ts_setup import TS_AVAILABLE, ts


def extract_tsjs_ast_summary(content: bytes, path: str, grammar: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS[grammar]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors = ["parse_errors_present"] if tree.root_node.has_error else []
    cursor = ts.QueryCursor(_TS_QUERIES[grammar])
    captures = cursor.captures(tree.root_node)

    imports: list[dict] = []
    funcs: list[str] = []
    classes: list[str] = []
    for cap, nodes in captures.items():
        for node in nodes:
            text = content[node.start_byte:node.end_byte].decode("utf-8", "replace")
            if cap == "import_src":
                imports.append({"kind": "import", "source": _strip_quotes(text),
                                "lineno": node.start_point[0] + 1})
            elif cap == "require_src":
                imports.append({"kind": "require", "source": _strip_quotes(text),
                                "lineno": node.start_point[0] + 1})
            elif cap in ("func_name", "export_func"):
                funcs.append(text)
            elif cap in ("class_name", "export_class"):
                classes.append(text)
    imports.sort(key=lambda x: (x["lineno"], x["source"]))
    return {
        "language": grammar,
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
    }, errors

TSJS_EXT_CANDIDATES = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs")

TSJS_INDEX_CANDIDATES = ("index.ts", "index.tsx", "index.js", "index.jsx",
                         "index.mjs", "index.cjs")

def _strip_jsonc_comments(text: str) -> str:
    # Drop // line comments and /* */ block comments. Naive but adequate for
    # tsconfig.json and biome.jsonc.
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(?<!:)//[^\n]*", "", text)
    # Drop trailing commas (jsonc allows them).
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text

def load_tsconfigs(records: list[FileRecord], read: Callable[[str], bytes]) -> dict[str, dict]:
    """For each tsconfig.json file, return its parsed content keyed by path.

    Each value carries: baseUrl (relative to the tsconfig's directory),
    paths (dict of alias-pattern -> [replacement, ...]), tsconfig_dir (the
    directory the tsconfig lives in, POSIX-relative to repo root).
    """
    out: dict[str, dict] = {}
    for r in records:
        if r.path == "tsconfig.json" or r.path.endswith("/tsconfig.json") or \
           r.path == "tsconfig.base.json" or r.path.endswith("/tsconfig.base.json"):
            try:
                data = json.loads(_strip_jsonc_comments(read(r.path).decode("utf-8")))
            except Exception:
                continue
            co = data.get("compilerOptions") or {}
            base_url = co.get("baseUrl") or "."
            paths = co.get("paths") or {}
            tsconfig_dir = str(PurePosixPath(r.path).parent)
            if tsconfig_dir == ".":
                tsconfig_dir = ""
            out[r.path] = {
                "baseUrl": base_url, "paths": paths, "tsconfig_dir": tsconfig_dir,
            }
    return out

def find_governing_tsconfig(src_path: str, tsconfigs: dict[str, dict]) -> dict | None:
    """Return the nearest tsconfig (by directory depth) governing src_path."""
    src_dir = str(PurePosixPath(src_path).parent)
    best, best_depth = None, -1
    for cfg in tsconfigs.values():
        d = cfg["tsconfig_dir"]
        if d == "" or src_dir == d or src_dir.startswith(d + "/"):
            depth = len(PurePosixPath(d).parts) if d else 0
            if depth > best_depth:
                best, best_depth = cfg, depth
    return best

def _resolve_tsjs_target(target: str, paths_set: set[str]) -> str | None:
    if target in paths_set:
        return target
    if target.endswith(".js"):
        for ext in (".ts", ".tsx"):
            alt = target[:-3] + ext
            if alt in paths_set:
                return alt
    for ext in TSJS_EXT_CANDIDATES:
        c = target + ext
        if c in paths_set:
            return c
    for idx in TSJS_INDEX_CANDIDATES:
        c = target + "/" + idx
        if c in paths_set:
            return c
    return None

def resolve_tsjs_import(
    src_path: str, spec: str, paths_set: set[str],
    tsconfigs: dict[str, dict],
) -> str | None:
    # Relative
    if spec.startswith("./") or spec.startswith("../") or spec in (".", ".."):
        src_dir = PurePosixPath(src_path).parent
        raw = src_dir / spec
        norm: list[str] = []
        for part in raw.parts:
            if part == "..":
                if norm and norm[-1] != "..":
                    norm.pop()
                else:
                    norm.append(part)
            elif part not in ("", "."):
                norm.append(part)
        return _resolve_tsjs_target("/".join(norm), paths_set)
    # Aliased via tsconfig
    cfg = find_governing_tsconfig(src_path, tsconfigs)
    if cfg:
        ts_dir = cfg["tsconfig_dir"]
        base_url = cfg["baseUrl"]
        base_root = str(PurePosixPath(ts_dir) / base_url) if ts_dir else base_url
        if base_root == ".":
            base_root = ""
        for pattern, replacements in (cfg["paths"] or {}).items():
            if "*" in pattern:
                pre, _, post = pattern.partition("*")
                if spec.startswith(pre) and spec.endswith(post) and \
                   len(spec) >= len(pre) + len(post):
                    captured = spec[len(pre): len(spec) - len(post)] if post else spec[len(pre):]
                    for repl in replacements:
                        r_pre, _, r_post = repl.partition("*")
                        target_rel = r_pre + captured + r_post
                        target = (PurePosixPath(base_root) / target_rel).as_posix()
                        if target.startswith("./"):
                            target = target[2:]
                        # Normalize . prefix
                        target = target.lstrip("/")
                        if target.startswith("./"):
                            target = target[2:]
                        result = _resolve_tsjs_target(target, paths_set)
                        if result:
                            return result
            else:
                if spec == pattern:
                    for repl in replacements:
                        target = (PurePosixPath(base_root) / repl).as_posix().lstrip("/")
                        result = _resolve_tsjs_target(target, paths_set)
                        if result:
                            return result
    return None

def tsjs_bare_package_root(spec: str) -> str | None:
    """For `lodash/cloneDeep` -> `lodash`. For `@scope/pkg/sub` -> `@scope/pkg`.

    Returns None for relative or aliased specifiers.
    """
    if spec.startswith(".") or spec.startswith("/"):
        return None
    parts = spec.split("/")
    if spec.startswith("@") and len(parts) >= 2:
        return "/".join(parts[:2]).lower()
    return parts[0].lower()
