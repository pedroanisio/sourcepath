"""codebase_mapper.languages.python."""
from __future__ import annotations

import ast
import base64
import math
import warnings

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Callable

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


from ..models import FileRecord


PY_AST_SCHEMA_VERSION = 1


def _ast_to_jsonable(node: Any) -> Any:
    """Serialize a Python ast.AST tree (or leaf) to a JSON-safe value.

    Captures only ``_fields`` (not ``lineno``/``col_offset``) so the encoding
    is independent of source positions. Bytes/complex/Ellipsis/inf/nan get
    tagged dict wrappers so JSON can round-trip them.
    """
    if isinstance(node, ast.AST):
        out: dict = {"_type": type(node).__name__}
        for field in node._fields:
            if hasattr(node, field):
                out[field] = _ast_to_jsonable(getattr(node, field))
        return out
    if isinstance(node, list):
        return [_ast_to_jsonable(x) for x in node]
    if isinstance(node, tuple):
        return {"_tuple": [_ast_to_jsonable(x) for x in node]}
    if isinstance(node, bytes):
        return {"_bytes": base64.b64encode(node).decode("ascii")}
    if isinstance(node, complex):
        return {"_complex": [node.real, node.imag]}
    if node is Ellipsis:
        return {"_ellipsis": True}
    if isinstance(node, float):
        if math.isinf(node):
            return {"_float": "inf" if node > 0 else "-inf"}
        if math.isnan(node):
            return {"_float": "nan"}
        return node
    # bool is a subclass of int — both pass through unchanged
    if isinstance(node, (str, bool, int)) or node is None:
        return node
    raise TypeError(f"unsupported ast value type {type(node).__name__}: {node!r}")


def _jsonable_to_ast(obj: Any) -> Any:
    """Inverse of _ast_to_jsonable."""
    if isinstance(obj, dict):
        if "_type" in obj:
            cls = getattr(ast, obj["_type"])
            kwargs = {k: _jsonable_to_ast(v) for k, v in obj.items() if k != "_type"}
            return cls(**kwargs)
        if "_tuple" in obj:
            return tuple(_jsonable_to_ast(x) for x in obj["_tuple"])
        if "_bytes" in obj:
            return base64.b64decode(obj["_bytes"])
        if "_complex" in obj:
            r, i = obj["_complex"]
            return complex(r, i)
        if "_ellipsis" in obj:
            return Ellipsis
        if "_float" in obj:
            v = obj["_float"]
            if v == "inf":
                return float("inf")
            if v == "-inf":
                return float("-inf")
            return float("nan")
        return obj
    if isinstance(obj, list):
        return [_jsonable_to_ast(x) for x in obj]
    return obj


def regenerate_python_source(summary: dict) -> str:
    """Reconstitute Python source from an ``extract_python_ast_summary`` result.

    Returns source that re-parses to the same AST as the original (semantic
    round-trip via ``ast.unparse``). NOT byte-identical: comments, blank
    lines, string-quote style, and trailing commas are dropped.
    """
    if "ast_json" not in summary or summary["ast_json"] is None:
        raise ValueError("summary missing 'ast_json' (schema_version < 1 or serialize failed)")
    module = _jsonable_to_ast(summary["ast_json"])
    if not isinstance(module, ast.Module):
        raise ValueError(f"expected ast.Module root, got {type(module).__name__}")
    ast.fix_missing_locations(module)
    return ast.unparse(module) + "\n"


def extract_python_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]
    try:
        # Python's parser emits SyntaxWarning for things like invalid escape
        # sequences in string literals (e.g. `"\d"` instead of `r"\d"`).
        # That's the mapped source's problem, not ours — silence it.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text, filename=path)
    except SyntaxError as e:
        return None, [f"syntax_error: line {e.lineno}: {e.msg}"]

    imports: list[dict] = []
    funcs: list[str] = []
    classes: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)

    # Imports are extracted from EVERY scope, not just module level, and each
    # record carries where it was found:
    #   scope == "module"  — unconditional top-level statement (a hard
    #                        dependency at import time);
    #   scope == "guarded" — module level but inside a compound statement
    #                        (`if TYPE_CHECKING:`, `try/except ImportError`,
    #                        loops) — real, but conditional at import time;
    #   scope == "nested"  — inside a function/method/class body (a lazy
    #                        dependency, paid on call instead of on import).
    # All three feed import resolution: a lazy import is still a true
    # file-to-file dependency, and dropping it silently understated the
    # dependency graph (found by an external recount of a shipped bundle).
    def _collect(nodes: Any, scope: str) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({"kind": "import", "module": alias.name,
                                    "lineno": node.lineno, "scope": scope})
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    imports.append({
                        "kind": "from", "module": mod, "name": alias.name,
                        "level": node.level, "lineno": node.lineno,
                        "scope": scope,
                    })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                _collect(node.body, "nested")
            else:
                # any other compound construct (If/Try + its ExceptHandlers,
                # With/For/While, Match + its cases): inner imports exist but
                # are conditional at import time. Recursing generically keeps
                # non-stmt containers (excepthandler, match_case) covered.
                inner = ("guarded"
                         if scope == "module" and isinstance(node, ast.stmt)
                         else scope)
                _collect(ast.iter_child_nodes(node), inner)

    _collect(tree.body, "module")

    ast_json: Any = None
    try:
        ast_json = _ast_to_jsonable(tree)
    except Exception as e:
        errors.append(f"ast_serialize_error: {type(e).__name__}: {e}")

    return {
        "language": "python",
        "schema_version": PY_AST_SCHEMA_VERSION,
        "imports": imports,
        "top_level_functions": sorted(funcs),
        "top_level_classes": sorted(classes),
        "ast_json": ast_json,
    }, errors

def detect_python_source_roots(
    records: list[FileRecord], read: Callable[[str], bytes]
) -> list[str]:
    roots: list[str] = []
    pp = next((r for r in records if r.path == "pyproject.toml"), None)
    if pp is not None:
        try:
            data = tomllib.loads(read(pp.path).decode("utf-8"))
        except Exception:
            data = {}
        find = (data.get("tool", {}).get("setuptools", {}).get("packages", {}) or {}).get("find", {})
        for where in find.get("where", []) or []:
            roots.append(where.strip("/"))
        hatch = (data.get("tool", {}).get("hatch", {}).get("build", {}) or {}).get("targets", {})
        for target in hatch.values():
            for pkg in target.get("packages", []) or []:
                parent = str(PurePosixPath(pkg).parent)
                if parent and parent != ".":
                    roots.append(parent.strip("/"))
        for spec in data.get("tool", {}).get("poetry", {}).get("packages", []) or []:
            frm = spec.get("from")
            if frm:
                roots.append(frm.strip("/"))

    paths = {r.path for r in records}
    if any(p.startswith("src/") for p in paths) and "src" not in roots:
        roots.append("src")
    if any(p.startswith("tests/") for p in paths) and "tests" not in roots:
        roots.append("tests")
    if "" not in roots:
        roots.append("")
    seen: set[str] = set()
    out: list[str] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out

def build_python_module_index(
    records: list[FileRecord], roots: list[str]
) -> tuple[dict[str, str], dict[str, str]]:
    by_module_cand: dict[str, list[str]] = defaultdict(list)
    by_suffix_cand: dict[str, list[str]] = defaultdict(list)
    for r in records:
        if r.language != "python":
            continue
        for root in roots:
            prefix = (root + "/") if root else ""
            if not r.path.startswith(prefix):
                continue
            rest = r.path[len(prefix):]
            parts = PurePosixPath(rest).with_suffix("").parts
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            by_module_cand[".".join(parts)].append(r.path)
            for i in range(1, len(parts)):
                by_suffix_cand[".".join(parts[i:])].append(r.path)
    by_module = {k: v[0] for k, v in by_module_cand.items() if len(set(v)) == 1}
    by_suffix = {k: v[0] for k, v in by_suffix_cand.items() if len(set(v)) == 1}
    return by_module, by_suffix

def resolve_python_imports(
    src_path: str, summary: dict, roots: list[str],
    by_module: dict[str, str], by_suffix: dict[str, str],
    declared_external: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[str], list[str]]:
    """Returns (in_repo_dst_paths, unresolved_top_level_module_names).

    ``declared_external`` guards the suffix heuristic against name
    shadowing: the suffix index maps any unique dotted-suffix of an
    internal module to its file, so a repo file like ``tools/psycopg.py``
    would silently capture every ``import psycopg`` in the tree. When the
    top-level name is a declared dependency, the exact-path match still
    wins but the suffix *heuristic* defers to the external
    classification.
    """
    dst: set[str] = set()
    unresolved: set[str] = set()
    src_pkg: list[str] = []
    for root in sorted(roots, key=len, reverse=True):
        prefix = (root + "/") if root else ""
        if src_path.startswith(prefix):
            rest = src_path[len(prefix):]
            parts = list(PurePosixPath(rest).with_suffix("").parts)
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            src_pkg = parts[:-1] if parts else []
            break

    for imp in summary.get("imports", []):
        original_module = imp.get("module") if imp["kind"] == "from" else imp["module"]
        if imp["kind"] == "import":
            module = imp["module"]
        else:
            level = imp.get("level", 0)
            module = imp.get("module") or ""
            if level > 0:
                if level - 1 > len(src_pkg):
                    continue
                base = src_pkg[: len(src_pkg) - (level - 1)] if level > 1 else src_pkg
                module = ".".join([*base, module]) if module else ".".join(base)
            if imp.get("name") and imp["name"] != "*":
                candidate = f"{module}.{imp['name']}" if module else imp["name"]
                if candidate in by_module:
                    dst.add(by_module[candidate])
                    continue

        if module:
            if module in by_module:
                dst.add(by_module[module])
            elif (module in by_suffix
                  and module.split(".", 1)[0] not in declared_external):
                dst.add(by_suffix[module])
            else:
                # Track the top-level name for external-package matching.
                # Skip empty module (pure relative imports we couldn't resolve)
                top = module.split(".", 1)[0]
                if top:
                    unresolved.add(top)
    return sorted(dst), sorted(unresolved)
