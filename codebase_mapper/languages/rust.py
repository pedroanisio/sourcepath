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

# Schema version for the Rust ast_summary payload. Bumped when the
# extractor's output shape changes in a backwards-incompatible way
# (regenerate depends on cst_json being present + schema-compatible).
RUST_AST_SCHEMA_VERSION = 1


def _ts_node_to_jsonable(node, content: bytes):
    """Serialize a tree-sitter Node to a JSON-safe structure with full
    byte coverage. Mirrors the encoding used by
    ``codebase_mapper.languages.tsjs`` so the regenerator can replay
    bytes exactly.

    Encoding:
      * Anonymous leaves whose ``type == text`` (keywords like ``fn``,
        punctuation like ``;``) collapse to a **bare string**.
      * Named leaves (identifiers, strings, numbers) become
        ``{"type": ..., "text": ...}``.
      * Interstitial gaps between siblings (whitespace, line breaks) are
        emitted as **bare strings** in the children list.
      * Internal nodes are ``{"type": ..., "children": [...]}``.

    Walking the result pre-order and concatenating every string /
    ``text`` field reproduces the original bytes exactly (for valid
    UTF-8 input).
    """
    children = node.children
    if not children:
        text = content[node.start_byte:node.end_byte].decode("utf-8")
        if not node.is_named and node.type == text:
            return text
        return {"type": node.type, "text": text}
    out_children: list = []
    cursor = node.start_byte
    for child in children:
        if child.start_byte > cursor:
            gap = content[cursor:child.start_byte].decode("utf-8")
            if gap:
                out_children.append(gap)
        out_children.append(_ts_node_to_jsonable(child, content))
        cursor = child.end_byte
    if cursor < node.end_byte:
        gap = content[cursor:node.end_byte].decode("utf-8")
        if gap:
            out_children.append(gap)
    return {"type": node.type, "children": out_children}


def regenerate_rust_source(summary: dict) -> str:
    """Reconstitute Rust source from an ``extract_rust_ast_summary`` result.

    Byte-identical to the original file (for valid UTF-8 input) because
    the extractor stored every leaf token's text plus interstitial gaps
    and the optional header/footer bytes outside the root node's span.

    Raises ``ValueError`` when the summary lacks ``cst_json`` — that
    indicates the bundle was produced before Stage 6 or the extraction
    hit a UTF-8 error and skipped CST capture.
    """
    if summary.get("cst_json") is None:
        raise ValueError(
            "summary missing 'cst_json' "
            "(schema_version < 1 or extraction failed)"
        )
    parts: list[str] = []

    def walk(node) -> None:
        if isinstance(node, str):
            parts.append(node)
            return
        if "text" in node:
            parts.append(node["text"])
            return
        for c in node.get("children", ()):
            walk(c)

    parts.append(summary.get("header", "") or "")
    walk(summary["cst_json"])
    parts.append(summary.get("footer", "") or "")
    return "".join(parts)


_RUST_ITEM_KINDS: dict[str, str] = {
    "function_item": "function",
    "function_signature_item": "function",
    "struct_item": "struct",
    "enum_item": "enum",
    "union_item": "union",
    "trait_item": "trait",
    "impl_item": "impl",
    "mod_item": "mod",
    "type_item": "type_alias",
    "const_item": "const",
    "static_item": "static",
}


def _ts_node_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _rust_node_name(node, content: bytes) -> str | None:
    """Best-effort name extraction. Prefers the ``name`` field; falls back
    to the first identifier-like child. Used for both items (function/struct)
    and impl blocks (where the ``type`` field carries the target type)."""
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _ts_node_text(name_node, content)
    type_node = node.child_by_field_name("type")
    if type_node is not None:
        return _ts_node_text(type_node, content)
    for c in node.children:
        if c.type in ("identifier", "type_identifier", "scoped_type_identifier"):
            return _ts_node_text(c, content)
    return None


def _rust_has_pub_modifier(node) -> bool:
    return any(c.type == "visibility_modifier" for c in node.children)


def _rust_has_async_modifier(node, content: bytes) -> bool:
    for c in node.children:
        if c.type == "function_modifiers" and "async" in _ts_node_text(c, content):
            return True
    return False


def _rust_find_decl_list(node):
    for c in node.children:
        if c.type == "declaration_list":
            return c
    return None


def _walk_rust_decl_list(parent_node, content: bytes, items: list[dict], parent: str | None) -> None:
    """Walk a ``source_file`` root or any ``declaration_list`` body, emitting
    items into ``items``. Attributes appear as siblings preceding the item
    they decorate (tree-sitter-rust grammar convention)."""
    pending_attrs: list[str] = []
    for child in parent_node.children:
        if not child.is_named:
            continue
        ct = child.type
        if ct in ("attribute_item", "inner_attribute_item"):
            pending_attrs.append(_ts_node_text(child, content))
            continue
        kind = _RUST_ITEM_KINDS.get(ct)
        if kind is None:
            # Attribute didn't decorate a known item; drop the pending set so
            # it can't accidentally attach to a later item.
            pending_attrs = []
            continue
        name = _rust_node_name(child, content) or f"<{kind}>"
        item_kind = "method" if (kind == "function" and parent is not None) else kind
        items.append({
            "kind": item_kind,
            "name": name,
            "parent": parent,
            "begin_byte": child.start_byte,
            "end_byte": child.end_byte,
            "begin_line": child.start_point[0] + 1,
            "end_line": child.end_point[0] + 1,
            "attributes": pending_attrs,
            "is_pub": _rust_has_pub_modifier(child),
            "is_async": (kind == "function" and _rust_has_async_modifier(child, content)),
        })
        pending_attrs = []
        # Recurse into bodies that can hold further items.
        if ct in ("impl_item", "trait_item", "mod_item"):
            body = _rust_find_decl_list(child)
            if body is not None:
                _walk_rust_decl_list(body, content, items, parent=name)


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

    # Stage 1: rich item walk. The ``items`` list carries per-symbol metadata
    # (kind, name, parent, byte/line range, attributes, is_pub, is_async).
    # No source-text bodies are stored here — those live in L2 chunks. This
    # keeps the AST summary lightweight while making every function, method,
    # struct, enum, trait, impl, mod, type alias, const, and static
    # individually addressable.
    items: list[dict] = []
    _walk_rust_decl_list(tree.root_node, content, items, parent=None)

    # Stage 6: leaf-text CST capture for byte-identical regenerate.
    # Tree-sitter has no unparse, so we serialize every leaf token's
    # text plus interstitial whitespace into ``cst_json``. ``header``
    # and ``footer`` capture any bytes outside the root node's span
    # (rare for Rust, but possible with shebang lines etc.).
    cst_json: dict | None = None
    header = ""
    footer = ""
    try:
        root = tree.root_node
        cst_json = _ts_node_to_jsonable(root, content)
        if root.start_byte > 0:
            header = content[:root.start_byte].decode("utf-8")
        if root.end_byte < len(content):
            footer = content[root.end_byte:].decode("utf-8")
    except UnicodeDecodeError as e:
        errors.append(f"cst_decode_error: {e}")
        cst_json = None
    except Exception as e:  # noqa: BLE001 — defensive against grammar surprises
        errors.append(f"cst_serialize_error: {type(e).__name__}: {e}")
        cst_json = None

    return {
        "language": "rust",
        "schema_version": RUST_AST_SCHEMA_VERSION,
        "imports": use_paths,
        "mod_decls": sorted(set(mods)),
        "top_level_functions": sorted(set(funcs)),
        "top_level_classes": sorted(set(classes)),
        "items": items,
        "cst_json": cst_json,
        "header": header,
        "footer": footer,
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

def _file_module_path(src_path: str, crate_dir: str) -> list[str]:
    """Return the Rust module-path segments for a source file under a crate.

    Maps filesystem position to Rust's module hierarchy::

        <crate_dir>/src/lib.rs              → []                 (crate root)
        <crate_dir>/src/main.rs             → []                 (crate root)
        <crate_dir>/src/foo.rs              → ["foo"]
        <crate_dir>/src/foo/mod.rs          → ["foo"]
        <crate_dir>/src/foo/bar.rs          → ["foo", "bar"]
        <crate_dir>/src/foo/bar/mod.rs      → ["foo", "bar"]

    Used by ``resolve_rust_imports`` to interpret ``self::`` and
    ``super::`` use-paths against the file's actual position in the
    module tree. Returns ``[]`` defensively for files outside the
    crate's ``src/`` (e.g. ``tests/foo.rs`` integration tests, which
    don't have a stable in-source module path).
    """
    src_prefix = (crate_dir + "/src/") if crate_dir else "src/"
    if not src_path.startswith(src_prefix):
        return []
    relative = src_path[len(src_prefix):]
    if not relative.endswith(".rs"):
        return []
    stem = relative[:-3]  # strip ``.rs``
    if stem in ("lib", "main"):
        return []
    parts = stem.split("/")
    if parts[-1] == "mod":
        parts = parts[:-1]
    return parts


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
        elif head == "self":
            # ``self::X::Y::…`` is resolved relative to THIS file's module.
            # Rewrite to ``<file_module>::X::Y::…`` and resolve like ``crate::``.
            file_mod = _file_module_path(src_path, crate_dir)
            rest = file_mod + rest
            in_repo_root = src_prefix
        elif head == "super":
            # ``super::…`` strips one segment off the file's module path
            # per ``super`` keyword (``super::super::X`` strips two, etc.).
            n_super = 1
            while rest and rest[0] == "super":
                n_super += 1
                rest = rest[1:]
            file_mod = _file_module_path(src_path, crate_dir)
            if n_super > len(file_mod):
                # Walked above the crate root — invalid Rust. Skip
                # silently to match the v0.3 contract for malformed
                # relative paths.
                continue
            cut = file_mod[:-n_super] if n_super < len(file_mod) else []
            rest = cut + rest
            in_repo_root = src_prefix
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
