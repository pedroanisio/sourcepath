"""codebase_mapper.manifests."""
from __future__ import annotations

import configparser
import json
import re

from pathlib import PurePosixPath

try:
    import tomllib
except ImportError:
    import tomli as tomllib

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


from .models import FileRecord


REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

def parse_requirements_txt(content: bytes) -> list[str]:
    pkgs: set[str] = set()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = REQ_LINE.match(line)
        if m:
            pkgs.add(m.group(1).lower())
    return sorted(pkgs)

def parse_pyproject_toml(content: bytes) -> list[str]:
    pkgs: set[str] = set()
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except Exception:
        return []
    project = data.get("project", {})
    for dep in project.get("dependencies", []) or []:
        m = REQ_LINE.match(dep)
        if m:
            pkgs.add(m.group(1).lower())
    for _g, deps in (project.get("optional-dependencies") or {}).items():
        for dep in deps or []:
            m = REQ_LINE.match(dep)
            if m:
                pkgs.add(m.group(1).lower())
    poetry = data.get("tool", {}).get("poetry", {})
    for name in (poetry.get("dependencies") or {}):
        if name.lower() != "python":
            pkgs.add(name.lower())
    for name in (poetry.get("dev-dependencies") or {}):
        pkgs.add(name.lower())
    return sorted(pkgs)

def parse_setup_cfg(content: bytes) -> list[str]:
    pkgs: set[str] = set()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return []
    if parser.has_option("options", "install_requires"):
        raw = parser.get("options", "install_requires")
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            m = REQ_LINE.match(line)
            if m:
                pkgs.add(m.group(1).lower())
    if parser.has_section("options.extras_require"):
        for _section, value in parser.items("options.extras_require"):
            for line in value.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = REQ_LINE.match(line)
                if m:
                    pkgs.add(m.group(1).lower())
    return sorted(pkgs)

def parse_package_json(content: bytes) -> list[str]:
    pkgs: set[str] = set()
    try:
        data = json.loads(content)
    except Exception:
        return []
    for key in ("dependencies", "devDependencies", "peerDependencies",
                "optionalDependencies"):
        for name in (data.get(key) or {}):
            pkgs.add(name.lower())
    return sorted(pkgs)

def parse_cargo_toml(content: bytes) -> list[str]:
    pkgs: set[str] = set()
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except Exception:
        return []
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        for name in (data.get(section) or {}):
            pkgs.add(name.lower())
    # Workspace dependencies
    workspace_deps = (data.get("workspace") or {}).get("dependencies") or {}
    for name in workspace_deps:
        pkgs.add(name.lower())
    return sorted(pkgs)

def parse_gemfile(content: bytes) -> list[str]:
    pkgs: set[str] = set()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    # Strip comments
    text = re.sub(r"#[^\n]*", "", text)
    # gem "name" or gem 'name'
    for m in re.finditer(r"\bgem\s+['\"]([A-Za-z0-9][A-Za-z0-9._-]*)['\"]", text):
        pkgs.add(m.group(1).lower())
    return sorted(pkgs)

def parse_gemspec(content: bytes) -> list[str]:
    pkgs: set[str] = set()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    text = re.sub(r"#[^\n]*", "", text)
    for m in re.finditer(
        r"\.add_(?:runtime_|development_)?dependency\s*[(\s]*['\"]([A-Za-z0-9][A-Za-z0-9._-]*)['\"]",
        text,
    ):
        pkgs.add(m.group(1).lower())
    return sorted(pkgs)

def parse_go_mod(content: bytes) -> list[str]:
    """Extract require entries. Format: `require X v1.2.3` or `require ( ... )` block."""
    pkgs: set[str] = set()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    # Strip line comments
    text = re.sub(r"(?<!:)//[^\n]*", "", text)
    # Single-line: require <path> <version>
    for m in re.finditer(r"^\s*require\s+(\S+)\s+\S+", text, re.MULTILINE):
        pkgs.add(m.group(1))
    # Block: require ( ... )
    for blk in re.finditer(r"require\s*\(([^)]*)\)", text, re.DOTALL):
        for line in blk.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                pkgs.add(parts[0])
    # Drop indirect markers / // indirect comments handled by the strip above.
    return sorted(p.lower() for p in pkgs)

def parse_pubspec_yaml(content: bytes) -> list[str]:
    """Read dependencies + dev_dependencies from a Dart/Flutter pubspec.yaml."""
    if not YAML_AVAILABLE:
        return []
    try:
        data = yaml.safe_load(content)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    pkgs: set[str] = set()
    for key in ("dependencies", "dev_dependencies", "dependency_overrides"):
        section = data.get(key) or {}
        if isinstance(section, dict):
            for name in section.keys():
                pkgs.add(str(name).lower())
    return sorted(pkgs)

def parse_build_gradle(content: bytes) -> list[str]:
    """Regex-extract dependencies from build.gradle (Groovy) or .gradle.kts.

    Catches `implementation 'group:name:version'`, `api(...)`, `testImplementation`,
    `kapt`, etc. Misses dynamic / computed configs. Returns 'group:name' (no version).
    """
    pkgs: set[str] = set()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    text = re.sub(r"(?<!:)//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # implementation 'group:name:version'  OR  implementation("group:name:version")
    for m in re.finditer(
        r"\b(?:implementation|api|compileOnly|runtimeOnly|testImplementation|"
        r"testApi|androidTestImplementation|kapt|ksp|annotationProcessor|"
        r"classpath)\s*[(\s]['\"]([A-Za-z0-9._-]+):([A-Za-z0-9._-]+)(?::[A-Za-z0-9._\-+]+)?['\"]",
        text,
    ):
        pkgs.add(f"{m.group(1)}:{m.group(2)}".lower())
    return sorted(pkgs)

def parse_package_swift(content: bytes) -> list[str]:
    """Extract Swift Package Manager dependencies from a Package.swift file.

    Catches `.package(url: "...", ...)` forms. Misses computed / dynamic configs.
    Emits BOTH the URL identifier (host/path) AND the short name (last URL
    segment, sans .git) — and the explicit `name:` value if present —
    so that imports referring to packages by short name (the common case
    for `.product(package: "swift-nio")`) can match.
    """
    pkgs: set[str] = set()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    # Strip line comments BUT NOT URL scheme separators (https://, file://).
    text = re.sub(r"(?<!:)//[^\n]*", "", text)
    for m in re.finditer(
        r"\.package\s*\(\s*(?:name\s*:\s*\"([^\"]*)\"\s*,\s*)?url\s*:\s*\"([^\"]+)\"",
        text,
    ):
        explicit_name = (m.group(1) or "").lower()
        url = m.group(2).rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        url_form = url.lower()
        short = url.rsplit("/", 1)[-1].lower()
        pkgs.add(url_form)
        pkgs.add(short)
        if explicit_name:
            pkgs.add(explicit_name)
    # Local-path packages: .package(name:..., path:...)
    for m in re.finditer(r"\.package\s*\(\s*name\s*:\s*\"([^\"]+)\"\s*,\s*path", text):
        pkgs.add(m.group(1).lower())
    return sorted(pkgs)

def declared_dependencies(record: FileRecord, content: bytes) -> list[str]:
    name = PurePosixPath(record.path).name
    if name == "requirements.txt" or re.fullmatch(r"requirements.*\.txt", name):
        return parse_requirements_txt(content)
    if name == "pyproject.toml":
        return parse_pyproject_toml(content)
    if name == "setup.cfg":
        return parse_setup_cfg(content)
    if name == "package.json":
        return parse_package_json(content)
    if name == "Cargo.toml":
        return parse_cargo_toml(content)
    if name == "Gemfile":
        return parse_gemfile(content)
    if name.endswith(".gemspec"):
        return parse_gemspec(content)
    if name == "go.mod":
        return parse_go_mod(content)
    if name == "pubspec.yaml":
        return parse_pubspec_yaml(content)
    if name in ("build.gradle", "build.gradle.kts", "settings.gradle",
                "settings.gradle.kts"):
        return parse_build_gradle(content)
    if name == "Package.swift":
        return parse_package_swift(content)
    return []
