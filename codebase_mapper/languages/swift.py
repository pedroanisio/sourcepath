"""codebase_mapper.languages.swift."""
from __future__ import annotations

import re

from pathlib import PurePosixPath
from typing import Callable


from ..models import FileRecord
from ..ts_setup import _TS_LANGS, _TS_QUERIES, _ts_setup
from ..ts_setup import TS_AVAILABLE, ts


def extract_swift_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["swift"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors = ["parse_errors_present"] if tree.root_node.has_error else []
    cursor = ts.QueryCursor(_TS_QUERIES["swift"])
    captures = cursor.captures(tree.root_node)

    imports: list[dict] = []
    funcs: list[str] = []
    classes: list[str] = []
    for cap, nodes in captures.items():
        for node in nodes:
            text = content[node.start_byte:node.end_byte].decode("utf-8", "replace")
            if cap == "sw_import":
                imports.append({"kind": "import", "source": text,
                                "lineno": node.start_point[0] + 1})
            elif cap == "func_name":
                funcs.append(text)
            elif cap == "class_name":
                classes.append(text)
    imports.sort(key=lambda x: (x["lineno"], x["source"]))
    return {
        "language": "swift",
        "imports": imports,
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
    }, errors

def detect_swift_modules(records: list[FileRecord], read: Callable[[str], bytes]) -> dict:
    """Parse Package.swift to extract:

    - `local_modules`: in-repo target module name -> source root dir
    - `product_to_package`: external module name -> set of candidate
      package identifiers (URL host/path form AND short name).

    Both extractors are regex-based; they catch the conventional Swift
    Package Manager forms but will miss dynamic/computed configs. Misses
    are emitted as unresolved imports rather than guessed.
    """
    pkg_swift = next((r for r in records if r.path == "Package.swift"), None)
    if pkg_swift is None:
        return {"local_modules": {}, "product_to_package": {}}
    try:
        text = read(pkg_swift.path).decode("utf-8")
    except UnicodeDecodeError:
        return {"local_modules": {}, "product_to_package": {}}

    local_modules: dict[str, str] = {}
    for m in re.finditer(
        r"\.(?:target|executableTarget|testTarget|systemLibrary|plugin)\s*\(\s*name\s*:\s*\"([^\"]+)\"(?:[^)]*?path\s*:\s*\"([^\"]+)\")?",
        text, re.DOTALL,
    ):
        name = m.group(1)
        explicit_path = m.group(2)
        if explicit_path:
            local_modules[name] = explicit_path.strip("/")
        else:
            local_modules[name] = f"Sources/{name}"

    # Build a map from package identifier -> candidate identifiers (URL + short).
    # SPM allows referring to a package by `.package(name: "X", url: ...)` or
    # by the short name derived from the URL (last path segment, sans .git).
    package_aliases: dict[str, set[str]] = {}
    for m in re.finditer(
        r"\.package\s*\(\s*(?:name\s*:\s*\"([^\"]*)\"\s*,\s*)?url\s*:\s*\"([^\"]+)\"",
        text,
    ):
        explicit_name = (m.group(1) or "").lower()
        url = m.group(2).rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        short = url.rsplit("/", 1)[-1].lower()
        url_form = url.lower()
        aliases = {url_form, short}
        if explicit_name:
            aliases.add(explicit_name)
        for key in (explicit_name, short, url_form):
            if key:
                package_aliases.setdefault(key, set()).update(aliases)
    # Local-path packages: .package(name:"X", path:"Y")
    for m in re.finditer(r"\.package\s*\(\s*name\s*:\s*\"([^\"]+)\"\s*,\s*path", text):
        nm = m.group(1).lower()
        package_aliases.setdefault(nm, set()).add(nm)

    # Now extract .product(name:"ModuleA", package:"PackageRef") inside
    # .target(...) dependencies blocks. The package ref maps to one of the
    # alias sets above.
    product_to_package: dict[str, set[str]] = {}
    for m in re.finditer(
        r"\.product\s*\(\s*name\s*:\s*\"([^\"]+)\"\s*,\s*package\s*:\s*\"([^\"]+)\"",
        text,
    ):
        module_name = m.group(1)
        package_ref = m.group(2).lower()
        aliases = package_aliases.get(package_ref, {package_ref})
        product_to_package.setdefault(module_name, set()).update(aliases)

    return {"local_modules": local_modules, "product_to_package": product_to_package}

def resolve_swift_imports(
    src_path: str, summary: dict, swift_info: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    """For each `import Foo`:

    1. If Foo is a local target module, emit an in-repo edge to a
       representative file (preferring `Foo.swift` if present).
    2. Else if Foo appears in the product map, emit unresolved entries
       for each candidate package identifier so the caller can match
       against declared deps.
    3. Else emit Foo itself as unresolved.
    """
    local_modules = swift_info.get("local_modules", {})
    product_to_package = swift_info.get("product_to_package", {})
    dst: set[str] = set()
    unresolved: set[str] = set()
    for imp in summary.get("imports", []):
        module = imp["source"]
        if module in local_modules:
            src_dir = local_modules[module]
            prefix = src_dir + "/"
            module_files = [p for p in paths_set
                            if p.startswith(prefix) and p.endswith(".swift")]
            if not module_files:
                unresolved.add(module)
                continue
            preferred = next((p for p in module_files
                              if PurePosixPath(p).stem == module), None)
            if preferred:
                dst.add(preferred)
            else:
                dst.add(sorted(module_files)[0])
        elif module in product_to_package:
            # Emit all candidate package identifiers; caller filters against
            # declared_pkgs.
            unresolved.update(product_to_package[module])
        else:
            unresolved.add(module)
    return sorted(dst), sorted(unresolved)
