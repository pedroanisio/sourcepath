"""codebase_mapper.languages.python."""
from __future__ import annotations

import ast

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Callable

try:
    import tomllib
except ImportError:
    import tomli as tomllib


from ..models import FileRecord


def extract_python_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as e:
        return None, [f"syntax_error: line {e.lineno}: {e.msg}"]

    imports: list[dict] = []
    funcs: list[str] = []
    classes: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({"kind": "import", "module": alias.name, "lineno": node.lineno})
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                imports.append({
                    "kind": "from", "module": mod, "name": alias.name,
                    "level": node.level, "lineno": node.lineno,
                })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
    return {
        "language": "python",
        "imports": imports,
        "top_level_functions": sorted(funcs),
        "top_level_classes": sorted(classes),
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
) -> tuple[list[str], list[str]]:
    """Returns (in_repo_dst_paths, unresolved_top_level_module_names)."""
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
            elif module in by_suffix:
                dst.add(by_suffix[module])
            else:
                # Track the top-level name for external-package matching.
                # Skip empty module (pure relative imports we couldn't resolve)
                top = module.split(".", 1)[0]
                if top:
                    unresolved.add(top)
    return sorted(dst), sorted(unresolved)
