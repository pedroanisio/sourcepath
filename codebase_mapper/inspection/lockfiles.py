"""codebase_mapper.lockfiles."""
from __future__ import annotations

import json
import re

from pathlib import PurePosixPath

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


from .models import FileRecord


def parse_uv_lock(content: bytes) -> list[tuple[str, str]]:
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except Exception:
        return []
    pkgs: set[tuple[str, str]] = set()
    for entry in data.get("package", []) or []:
        name = (entry.get("name") or "").lower()
        version = entry.get("version") or ""
        if name and version:
            pkgs.add((name, version))
    return sorted(pkgs)

def parse_cargo_lock(content: bytes) -> list[tuple[str, str]]:
    # Identical structural shape to uv.lock / poetry.lock
    return parse_uv_lock(content)

def parse_package_lock_json(content: bytes) -> list[tuple[str, str]]:
    try:
        data = json.loads(content)
    except Exception:
        return []
    pkgs: set[tuple[str, str]] = set()
    for key, info in (data.get("packages") or {}).items():
        if not key:
            continue
        name = info.get("name") or key.rsplit("node_modules/", 1)[-1]
        version = info.get("version")
        if name and version:
            pkgs.add((name.lower(), version))
    return sorted(pkgs)

def parse_pnpm_lock_yaml(content: bytes) -> list[tuple[str, str]]:
    if not YAML_AVAILABLE:
        return []
    try:
        data = yaml.safe_load(content)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    pkgs: set[tuple[str, str]] = set()
    pkg_map = data.get("packages") or {}
    if not isinstance(pkg_map, dict):
        return []
    pat = re.compile(r"^/?(?:(@[^/]+)/)?([^/@][^@]*)@([^@()]+?)(?:\(.*\))?$")
    for key in pkg_map.keys():
        if not isinstance(key, str):
            continue
        m = pat.match(key)
        if not m:
            continue
        scope, name, version = m.group(1), m.group(2), m.group(3)
        full = f"{scope}/{name}".lower() if scope else name.lower()
        pkgs.add((full, version))
    return sorted(pkgs)

def parse_gemfile_lock(content: bytes) -> list[tuple[str, str]]:
    """Gemfile.lock format: GEM section with `specs:` containing `<name> (<version>)`."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    pkgs: set[tuple[str, str]] = set()
    in_specs = False
    for line in text.splitlines():
        if re.match(r"^\s*specs:\s*$", line):
            in_specs = True
            continue
        if in_specs:
            if not line.strip():
                in_specs = False
                continue
            # Top-level deps under specs are 4-space indented; nested deps 6+.
            # Top-level: "    name (1.2.3)" - just take name + version
            m = re.match(r"^\s{4}([A-Za-z0-9][A-Za-z0-9._-]*)\s*\(([^)]+)\)\s*$", line)
            if m:
                pkgs.add((m.group(1).lower(), m.group(2).strip()))
    return sorted(pkgs)

def parse_go_sum(content: bytes) -> list[tuple[str, str]]:
    """go.sum lines: `<module> <version>[/go.mod] h1:<hash>`. We want unique
    (module, version) pairs, ignoring the /go.mod marker entries."""
    pkgs: set[tuple[str, str]] = set()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        module, version = parts[0], parts[1]
        if version.endswith("/go.mod"):
            continue
        pkgs.add((module.lower(), version))
    return sorted(pkgs)

def parse_pubspec_lock(content: bytes) -> list[tuple[str, str]]:
    """pubspec.lock is YAML with `packages: { name: {version: x, ...}, ...}`."""
    if not YAML_AVAILABLE:
        return []
    try:
        data = yaml.safe_load(content)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    pkgs: set[tuple[str, str]] = set()
    for name, info in (data.get("packages") or {}).items():
        if isinstance(info, dict):
            version = info.get("version")
            if version:
                pkgs.add((str(name).lower(), str(version)))
    return sorted(pkgs)

def parse_package_resolved(content: bytes) -> list[tuple[str, str]]:
    """Swift Package.resolved is JSON. Two versions exist; both have an array of
    pinned packages with identity/name and a state.version."""
    try:
        data = json.loads(content)
    except Exception:
        return []
    pkgs: set[tuple[str, str]] = set()
    # v2 (newer): pins under 'pins'
    for entry in data.get("pins", []) or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("identity") or entry.get("package") or ""
        state = entry.get("state") or {}
        version = state.get("version") or state.get("revision") or ""
        if name and version:
            pkgs.add((name.lower(), version))
    # v1 (older): pins under object.pins
    obj = data.get("object") or {}
    for entry in obj.get("pins", []) or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("package") or entry.get("identity") or ""
        state = entry.get("state") or {}
        version = state.get("version") or state.get("revision") or ""
        if name and version:
            pkgs.add((name.lower(), version))
    return sorted(pkgs)

def parse_gradle_lockfile(content: bytes) -> list[tuple[str, str]]:
    """gradle.lockfile: lines of `group:name:version=...`."""
    pkgs: set[tuple[str, str]] = set()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        ident, _ = line.split("=", 1)
        parts = ident.split(":")
        if len(parts) >= 3:
            group, name, version = parts[0], parts[1], parts[2]
            pkgs.add((f"{group}:{name}".lower(), version))
    return sorted(pkgs)

def pinned_dependencies(record: FileRecord, content: bytes) -> list[tuple[str, str]]:
    name = PurePosixPath(record.path).name
    if name in ("uv.lock", "poetry.lock"):
        return parse_uv_lock(content)
    if name == "Cargo.lock":
        return parse_cargo_lock(content)
    if name == "package-lock.json":
        return parse_package_lock_json(content)
    if name == "pnpm-lock.yaml":
        return parse_pnpm_lock_yaml(content)
    if name == "Gemfile.lock":
        return parse_gemfile_lock(content)
    if name == "go.sum":
        return parse_go_sum(content)
    if name == "pubspec.lock":
        return parse_pubspec_lock(content)
    if name == "Package.resolved":
        return parse_package_resolved(content)
    if name == "gradle.lockfile" or name.endswith(".lockfile"):
        return parse_gradle_lockfile(content)
    return []
