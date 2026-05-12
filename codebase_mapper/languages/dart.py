"""codebase_mapper.languages.dart."""
from __future__ import annotations

import re

from pathlib import PurePosixPath
from typing import Callable

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


from ..models import FileRecord


# Dart has no PyPI tree-sitter grammar. Use a regex-based extractor.
# Imports are reliably regular. Class/function declarations are inherently
# more fragile because Dart's grammar is richer than a regex can capture,
# but the common forms are recoverable.
_DART_IMPORT_RE = re.compile(
    r"^\s*(?:import|export|part)\s+(['\"])([^'\"]+)\1",
    re.MULTILINE,
)

# Class / mixin / enum / extension declarations at column 0 only (to avoid
# picking up nested declarations). The leading-anchor restriction sacrifices
# nested classes but reduces false positives.
_DART_DECL_RE = re.compile(
    r"^(?:abstract\s+|sealed\s+|base\s+|final\s+|interface\s+|mixin\s+)*"
    r"(?:class|mixin|enum|extension(?:\s+type)?|typedef)\s+"
    r"([A-Z][A-Za-z0-9_]*)",
    re.MULTILINE,
)

# Top-level function declarations. Conservative pattern: line starts at
# column 0, optional return type, identifier, optional generics, parens.
# Skips arrow functions and methods (anything indented).
_DART_FUNC_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_<>?,\s]*\s+)?"  # optional return type
    r"([a-z_][A-Za-z0-9_]*)\s*"  # function name (lowercase start)
    r"(?:<[^>]+>)?"               # optional generics
    r"\s*\([^)]*\)\s*"            # parameter list
    r"(?:async\s*\*?\s*|sync\s*\*\s*)?"  # optional async/sync*
    r"(?:\{|=>)",                 # body opens with { or =>
    re.MULTILINE,
)

_DART_FUNC_BLACKLIST = {"if", "for", "while", "switch", "return", "throw",
                        "assert", "rethrow", "yield", "break", "continue",
                        "set", "get", "operator", "new"}

def extract_dart_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]
    imports: list[dict] = []
    for m in _DART_IMPORT_RE.finditer(text):
        source = m.group(2)
        line = text.count("\n", 0, m.start()) + 1
        imports.append({"kind": "import", "source": source, "lineno": line})
    imports.sort(key=lambda x: (x["lineno"], x["source"]))
    classes = sorted({m.group(1) for m in _DART_DECL_RE.finditer(text)})
    funcs = sorted({m.group(1) for m in _DART_FUNC_RE.finditer(text)
                    if m.group(1) not in _DART_FUNC_BLACKLIST})
    return {
        "language": "dart",
        "imports": imports,
        "top_level_functions": funcs,
        "top_level_classes": classes,
        "extraction_method": "regex",
    }, []

def detect_dart_package_name(records: list[FileRecord], read: Callable[[str], bytes]) -> str | None:
    """Read pubspec.yaml to find this project's package name (used to recognize
    `package:my_app/...` as in-repo)."""
    if not YAML_AVAILABLE:
        return None
    ps = next((r for r in records if r.path == "pubspec.yaml"), None)
    if ps is None:
        candidates = [r for r in records if r.path.endswith("/pubspec.yaml")]
        if not candidates:
            return None
        ps = sorted(candidates, key=lambda r: r.path.count("/"))[0]
    try:
        data = yaml.safe_load(read(ps.path))
    except Exception:
        return None
    if isinstance(data, dict):
        return data.get("name")
    return None

def resolve_dart_imports(
    src_path: str, summary: dict, package_name: str | None, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    dst: set[str] = set()
    unresolved: set[str] = set()
    src_dir = PurePosixPath(src_path).parent
    for imp in summary.get("imports", []):
        spec = imp["source"]
        if spec.startswith("package:"):
            # package:<name>/<path>
            body = spec[len("package:"):]
            pkg, _, rest = body.partition("/")
            if package_name and pkg == package_name:
                # Resolve `package:foo/bar.dart` to `lib/bar.dart`
                target = f"lib/{rest}"
                if target in paths_set:
                    dst.add(target)
                else:
                    unresolved.add(pkg)
            else:
                unresolved.add(pkg)
        elif spec.startswith("dart:"):
            # Dart SDK module — always external
            unresolved.add(spec)
        else:
            # Relative path
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
    return sorted(dst), sorted(unresolved)
