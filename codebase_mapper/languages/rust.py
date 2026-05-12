"""codebase_mapper.languages.rust."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Callable

try:
    import tomllib
except ImportError:
    import tomli as tomllib


from ..models import FileRecord
from ..ts_setup import _TS_LANGS, _TS_QUERIES, _ts_setup
from ..ts_setup import TS_AVAILABLE, ts


def extract_rust_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    if not TS_AVAILABLE:
        return None, ["tree_sitter_unavailable"]
    _ts_setup()
    lang = _TS_LANGS["rust"]
    parser = ts.Parser(lang)
    tree = parser.parse(content)
    errors = ["parse_errors_present"] if tree.root_node.has_error else []
    cursor = ts.QueryCursor(_TS_QUERIES["rust"])
    captures = cursor.captures(tree.root_node)

    use_paths: list[dict] = []
    mods: list[str] = []
    funcs: list[str] = []
    classes: list[str] = []
    for cap, nodes in captures.items():
        for node in nodes:
            text = content[node.start_byte:node.end_byte].decode("utf-8", "replace")
            if cap == "use_arg":
                # Take the leading path segments before any '{' or 'as'.
                head = text.split("{", 1)[0].split(" as ", 1)[0].strip().rstrip(":")
                # If the use is a brace-group, also expand to capture the first item.
                use_paths.append({"path": head, "raw": text[:200], "lineno": node.start_point[0] + 1})
            elif cap == "mod_name":
                mods.append(text)
            elif cap == "func_name":
                funcs.append(text)
            elif cap == "class_name":
                classes.append(text)
    use_paths.sort(key=lambda x: (x["lineno"], x["path"]))
    return {
        "language": "rust",
        "imports": use_paths,
        "mod_decls": sorted(set(mods)),
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
    }, errors

def detect_rust_workspaces(records: list[FileRecord], read: Callable[[str], bytes]) -> list[dict]:
    """Each entry: {crate_root: 'src', crate_dir: '', name: 'foo'}.

    For a workspace, returns one entry per member; for a single crate, one entry.
    """
    crates: list[dict] = []
    for r in records:
        if r.path != "Cargo.toml" and not r.path.endswith("/Cargo.toml"):
            continue
        try:
            data = tomllib.loads(read(r.path).decode("utf-8"))
        except Exception:
            continue
        crate_dir = str(PurePosixPath(r.path).parent)
        if crate_dir == ".":
            crate_dir = ""
        # If this is a workspace root, the workspace members will have their
        # own Cargo.toml which we'll encounter in the same loop. We still try
        # to register the crate here if it has [package].
        pkg = data.get("package") or {}
        name = pkg.get("name")
        if name:
            crates.append({"crate_dir": crate_dir, "name": name})
    return crates

def crate_for_file(file_path: str, crates: list[dict]) -> dict | None:
    """Return the crate (workspace member) whose directory is the longest prefix of file_path."""
    best, best_depth = None, -1
    for c in crates:
        d = c["crate_dir"]
        # File must be under <crate_dir>/src/ etc.
        if d == "":
            if best_depth < 0:
                best, best_depth = c, 0
        else:
            if file_path == d or file_path.startswith(d + "/"):
                depth = len(d)
                if depth > best_depth:
                    best, best_depth = c, depth
    return best

def resolve_rust_imports(
    src_path: str, summary: dict, crates: list[dict], paths_set: set[str],
) -> tuple[list[str], list[str]]:
    dst: set[str] = set()
    unresolved: set[str] = set()
    crate = crate_for_file(src_path, crates)
    crate_dir = crate["crate_dir"] if crate else ""
    src_prefix = (crate_dir + "/src/") if crate_dir else "src/"

    # Map crate-name (with hyphens converted to underscores per Cargo conventions) -> crate
    name_to_crate: dict[str, dict] = {}
    for c in crates:
        # Cargo replaces hyphens with underscores in the lib name by default.
        normalized = c["name"].replace("-", "_")
        name_to_crate[normalized] = c
        name_to_crate[c["name"]] = c

    for imp in summary.get("imports", []):
        raw_path = imp.get("path", "")
        if not raw_path:
            continue
        segs = [s.strip() for s in raw_path.split("::") if s.strip()]
        if not segs:
            continue
        head = segs[0]
        rest = segs[1:]

        # Determine the in-repo prefix root for this use.
        in_repo_root: str | None = None
        if head == "crate":
            in_repo_root = src_prefix
        elif head == "super" or head == "self":
            # Relative inside the file's module. Heuristic: don't resolve in v0.3.
            continue
        elif head in name_to_crate:
            other_crate = name_to_crate[head]
            other_dir = other_crate["crate_dir"]
            in_repo_root = (other_dir + "/src/") if other_dir else "src/"
        else:
            # External
            unresolved.add(head.lower().replace("_", "-"))
            continue

        # Try resolution with various interpretations of where the item ends
        # and the module begins.
        found = False
        for take in range(len(rest), -1, -1):
            module_segs = rest[:take]
            base = in_repo_root + "/".join(module_segs) if module_segs else in_repo_root.rstrip("/")
            candidates = [
                base + ".rs",
                base + "/mod.rs",
                base + "/lib.rs",
            ]
            # Also try the root file (lib.rs / main.rs)
            for cand in candidates:
                if cand in paths_set:
                    dst.add(cand)
                    found = True
                    break
            if found:
                break
        if not found and not module_segs:
            # last resort: lib.rs/main.rs of the crate
            for cand in (in_repo_root + "lib.rs", in_repo_root + "main.rs"):
                if cand in paths_set:
                    dst.add(cand)
                    break

    return sorted(dst), sorted(unresolved)
