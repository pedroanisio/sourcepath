"""codebase_mapper.languages.tsjs."""
from __future__ import annotations

import json
import re

from pathlib import PurePosixPath
from typing import Callable


from ..models import FileRecord
from ...ts_setup import _TS_LANGS, _TS_QUERIES, _strip_quotes, _ts_setup
from ...ts_setup import TS_AVAILABLE, ts
from ._treewalk import node_to_jsonable, regenerate_cst_text


TSJS_AST_SCHEMA_VERSION = 1


# The CST serializer is the iterative ``node_to_jsonable`` from ``_treewalk``
# (no recursion-depth ceiling on deeply-nested files), kept under the historical
# private name for any caller that imports it. Its encoding is byte-identical to
# the prior recursive serializer.
_ts_node_to_jsonable = node_to_jsonable


def regenerate_tsjs_source(summary: dict) -> str:
    """Reconstitute TS/JS source from an ``extract_tsjs_ast_summary`` result.

    Byte-identical to the original file (for valid UTF-8 input) because the
    extractor stored every leaf token's text plus interstitial gaps and the
    optional header/footer bytes outside the root node's span.
    """
    if summary.get("cst_json") is None:
        raise ValueError("summary missing 'cst_json' (schema_version < 1 or extraction failed)")
    # Iterative concatenation (see _treewalk): header + CST body + footer.
    return (
        (summary.get("header", "") or "")
        + regenerate_cst_text(summary["cst_json"])
        + (summary.get("footer", "") or "")
    )


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

    cst_json: dict | None = None
    header = ""
    footer = ""
    try:
        # Strict decode; if the file isn't valid UTF-8 we skip CST capture
        # rather than silently lose bytes. classify() should already have
        # flagged truly-binary files before we got here.
        root = tree.root_node
        cst_json = _ts_node_to_jsonable(root, content)
        if root.start_byte > 0:
            header = content[:root.start_byte].decode("utf-8")
        if root.end_byte < len(content):
            footer = content[root.end_byte:].decode("utf-8")
    except UnicodeDecodeError as e:
        errors.append(f"cst_decode_error: {e}")
        cst_json = None
    except Exception as e:
        errors.append(f"cst_serialize_error: {type(e).__name__}: {e}")
        cst_json = None

    return {
        "language": grammar,
        "schema_version": TSJS_AST_SCHEMA_VERSION,
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
        "cst_json": cst_json,
        "header": header,
        "footer": footer,
    }, errors

TSJS_EXT_CANDIDATES = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs")

TSJS_INDEX_CANDIDATES = ("index.ts", "index.tsx", "index.js", "index.jsx",
                         "index.mjs", "index.cjs")

def _strip_jsonc_comments(text: str) -> str:
    """Strip ``//`` line and ``/* */`` block comments from JSONC, then drop
    trailing commas.

    String-aware: comment-like sequences *inside* string literals are preserved.
    A regex that ignores strings corrupts any tsconfig whose ``paths`` /
    ``include`` values contain ``/*``, ``*/`` or ``//`` — e.g. ``"@/*"`` (a
    ``/*``) followed by ``"**/*.ts"`` (a ``*/``) makes the naive block-comment
    pattern eat everything between them, silently deleting ``paths`` and
    disabling alias resolution.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:  # keep the escape pair intact
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":  # line comment
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":  # block comment
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2  # consume the closing */
            continue
        out.append(c)
        i += 1
    # Drop trailing commas (jsonc allows them). Safe to run over the result:
    # a comma inside a string would need to be followed by whitespace then a
    # closing brace/bracket within the same string, which JSON config values
    # never contain.
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))

def _normalize_posix(parts) -> str:
    """Collapse '.' and '..' segments in a POSIX path's parts into a string."""
    norm: list[str] = []
    for part in parts:
        if part == "..":
            if norm and norm[-1] != "..":
                norm.pop()
            else:
                norm.append(part)
        elif part not in ("", "."):
            norm.append(part)
    return "/".join(norm)


def _resolve_extends_path(cfg_dir: str, ext: str, available: set[str]) -> str | None:
    """Resolve a tsconfig ``extends`` target to a repo-relative file path.

    Only relative/absolute path forms are resolvable. A bare package specifier
    (e.g. ``@tsconfig/node18/tsconfig.json``, ``@vue/tsconfig``) lives under
    ``node_modules`` — untracked — so it returns None. TypeScript appends
    ``.json`` when the target has no extension and treats a directory as
    ``<dir>/tsconfig.json``; both fallbacks are tried.
    """
    if not (ext.startswith("./") or ext.startswith("../") or ext.startswith("/")):
        return None
    raw = (PurePosixPath(cfg_dir) / ext) if cfg_dir else PurePosixPath(ext)
    cand = _normalize_posix(raw.parts)
    for c in (cand, cand + ".json", cand + "/tsconfig.json"):
        if c in available:
            return c
    return None


def _read_tsconfig_options(
    path: str,
    read: Callable[[str], bytes],
    available: set[str],
    seen: frozenset[str] = frozenset(),
) -> dict:
    """Return a config's effective ``{baseUrl, paths}`` with ``extends`` merged.

    ``extends`` parents are resolved first so the inheriting config wins on
    conflict; both the string and the TS-5.0 array form are supported, and
    cyclic chains are broken via ``seen``. baseUrl/paths are interpreted
    relative to the *inheriting* config's directory — correct for the dominant
    case of co-located configs (Vite's ``tsconfig.json`` + ``tsconfig.app.json``,
    a monorepo package root and its ``tsconfig.base.json``). Cross-directory
    ``extends`` that *redefines* ``paths`` is a known limitation.
    """
    if path in seen:
        return {"baseUrl": None, "paths": {}}
    try:
        data = json.loads(_strip_jsonc_comments(read(path).decode("utf-8")))
    except Exception:
        return {"baseUrl": None, "paths": {}}

    base_url: str | None = None
    paths: dict = {}

    cfg_dir = str(PurePosixPath(path).parent)
    if cfg_dir == ".":
        cfg_dir = ""
    ext = data.get("extends")
    ext_list = [ext] if isinstance(ext, str) else (ext if isinstance(ext, list) else [])
    for raw in ext_list:
        if not isinstance(raw, str) or not raw:
            continue
        target = _resolve_extends_path(cfg_dir, raw, available)
        if target is None:
            continue
        inherited = _read_tsconfig_options(target, read, available, seen | {path})
        if inherited.get("baseUrl") is not None:
            base_url = inherited["baseUrl"]
        if inherited.get("paths"):
            paths = {**paths, **inherited["paths"]}

    co = data.get("compilerOptions") or {}
    if co.get("baseUrl") is not None:
        base_url = co.get("baseUrl")
    if co.get("paths"):
        paths = {**paths, **co["paths"]}
    return {"baseUrl": base_url, "paths": paths}


def load_tsconfigs(records: list[FileRecord], read: Callable[[str], bytes]) -> dict[str, dict]:
    """For each tsconfig/jsconfig file, return its effective config keyed by path.

    Recognizes any ``tsconfig*.json`` (``tsconfig.json``, ``tsconfig.base.json``,
    ``tsconfig.app.json``, ``tsconfig.node.json``, ...) and ``jsconfig.json``,
    and follows ``extends`` chains. This matters because the default Vite scaffold
    puts ``compilerOptions.paths`` in a referenced ``tsconfig.app.json`` while the
    root ``tsconfig.json`` is a paths-less solution file — reading only the root
    would silently lose every ``@/...`` alias.

    Each value carries: baseUrl (relative to the config's directory), paths
    (dict of alias-pattern -> [replacement, ...]), tsconfig_dir (the directory
    the config lives in, POSIX-relative to repo root).
    """
    available = {r.path for r in records}
    out: dict[str, dict] = {}
    for r in records:
        base = PurePosixPath(r.path).name
        if not (base == "jsconfig.json"
                or (base.startswith("tsconfig") and base.endswith(".json"))):
            continue
        opts = _read_tsconfig_options(r.path, read, available)
        tsconfig_dir = str(PurePosixPath(r.path).parent)
        if tsconfig_dir == ".":
            tsconfig_dir = ""
        out[r.path] = {
            "baseUrl": opts.get("baseUrl") or ".",
            "paths": opts.get("paths") or {},
            "tsconfig_dir": tsconfig_dir,
        }
    return out

def find_governing_tsconfig(src_path: str, tsconfigs: dict[str, dict]) -> dict | None:
    """Return the config governing src_path: the deepest by directory, and among
    equal-depth configs the one that actually declares ``paths``.

    The tie-break stops a paths-less root ``tsconfig.json`` from shadowing a
    sibling ``tsconfig.app.json`` (or ``tsconfig.base.json``) that carries the
    aliases — the exact Vite layout that otherwise left aliases unresolved.
    """
    src_dir = str(PurePosixPath(src_path).parent)
    best: dict | None = None
    best_key: tuple[int, int] = (-1, -1)
    for cfg in tsconfigs.values():
        d = cfg["tsconfig_dir"]
        if d == "" or src_dir == d or src_dir.startswith(d + "/"):
            depth = len(PurePosixPath(d).parts) if d else 0
            key = (depth, 1 if cfg.get("paths") else 0)
            if key > best_key:
                best, best_key = cfg, key
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
