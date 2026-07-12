"""codebase_mapper.inspection.languages.yaml — first-class YAML support.

Unlike the other regex/hand-parser languages, YAML has a real parser already
in the dependency set: PyYAML. This analyzer uses ``yaml.compose_all`` to
build a genuine node AST (MappingNode / SequenceNode / ScalarNode) carrying
source marks, and emits one ``item`` per mapping key — top level and one
nested level — across every document in a multi-document (``---``) stream.
(``import yaml`` resolves to the PyYAML package, not this module; Python 3
absolute imports keep them distinct.)

Import edges: JSON-Schema / OpenAPI ``$ref`` file references (collected at any
depth) and the ``!include`` custom tag. Internal pointers (``#/...``) and
http(s) URLs are external; relative file refs (fragment stripped) resolve
in-repo.
"""
from __future__ import annotations

import re

from pathlib import PurePosixPath

import yaml


def _line_byte_starts(content: bytes) -> list[int]:
    starts = [0, 0]
    for i, b in enumerate(content):
        if b == 0x0A:
            starts.append(i + 1)
    return starts


def _line_of(byte_idx: int, line_byte_starts: list[int]) -> int:
    lo, hi = 1, len(line_byte_starts) - 1
    if hi <= 0:
        return 1
    if byte_idx >= line_byte_starts[hi]:
        return hi
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_byte_starts[mid] <= byte_idx:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _node_type(node) -> str:
    if isinstance(node, yaml.MappingNode):
        return "object"
    if isinstance(node, yaml.SequenceNode):
        return "array"
    return "scalar"


_INCLUDE_RE = re.compile(r"(?:^|\s)!include\s+(?P<path>\S+)")


def extract_yaml_ast_summary(content: bytes, path: str) -> tuple[dict | None, list[str]]:
    """Return ``(summary, errors)`` for a YAML file (possibly multi-document)."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        return None, [f"decode_error: {e}"]
    try:
        docs = list(yaml.compose_all(text))
    except yaml.YAMLError as e:
        return None, [f"yaml_parse_error: {e}"]

    lbs = _line_byte_starts(content)
    _cache: dict[int, int] = {}

    def cb(char_idx: int) -> int:
        v = _cache.get(char_idx)
        if v is None:
            v = len(text[:char_idx].encode("utf-8"))
            _cache[char_idx] = v
        return v

    items: list[dict] = []
    imports: list[dict] = []

    def emit(node, parent: str | None, depth: int) -> None:
        if not isinstance(node, yaml.MappingNode):
            return
        for k_node, v_node in node.value:
            if not isinstance(k_node, yaml.ScalarNode):
                continue
            key = str(k_node.value)
            b_start = cb(k_node.start_mark.index)
            b_end = cb(v_node.end_mark.index)
            if b_end <= b_start:
                b_end = b_start + max(1, len(key) + 1)
            items.append({
                "kind": "member",
                "name": key,
                "parent": parent,
                "value_type": _node_type(v_node),
                "line_start": _line_of(b_start, lbs),
                "line_end": _line_of(max(b_start, b_end - 1), lbs),
                "byte_start": b_start,
                "byte_end": b_end,
                "signature": _collapse(
                    text[k_node.start_mark.index:min(v_node.start_mark.index + 40,
                                                     v_node.end_mark.index)])[:120],
            })
            if depth < 2 and isinstance(v_node, yaml.MappingNode):
                emit(v_node, key, depth + 1)

    def collect_refs(node) -> None:
        if isinstance(node, yaml.MappingNode):
            for k_node, v_node in node.value:
                if (isinstance(k_node, yaml.ScalarNode) and str(k_node.value) == "$ref"
                        and isinstance(v_node, yaml.ScalarNode)):
                    imports.append({
                        "kind": "ref",
                        "source": str(v_node.value),
                        "lineno": k_node.start_mark.line + 1,
                    })
                collect_refs(v_node)
        elif isinstance(node, yaml.SequenceNode):
            for el in node.value:
                collect_refs(el)

    for doc in docs:
        if doc is not None:
            emit(doc, None, 1)
            collect_refs(doc)

    for m in _INCLUDE_RE.finditer(text):
        imports.append({
            "kind": "include",
            "source": m.group("path").strip("\"'"),
            "lineno": _line_of(m.start("path"), lbs),
        })

    items.sort(key=lambda x: (x["line_start"], x["byte_start"]))
    imports.sort(key=lambda x: (x["lineno"], x["source"]))

    summary = {
        "language": "yaml",
        "extraction_method": "pyyaml",
        "documents": len(docs),
        "imports": imports,
        "items": items,
    }
    if not items:
        # Empty/comment-only/scalar documents have no addressable members —
        # a property of the source, not an extraction failure. Disclose or
        # the coverage gate counts the file as silent (c.py convention).
        summary["zero_symbol_reason"] = "document_has_no_members"
    return summary, []


# ---------------------------------------------------------------------------
# Import resolution
# ---------------------------------------------------------------------------


def _normalize_rel(parts: tuple[str, ...]) -> str:
    norm: list[str] = []
    for part in parts:
        if part == "..":
            if norm and norm[-1] != "..":
                norm.pop()
        elif part not in ("", "."):
            norm.append(part)
    return "/".join(norm)


def resolve_yaml_imports(
    src_path: str, summary: dict, paths_set: set[str],
) -> tuple[list[str], list[str]]:
    src_dir = PurePosixPath(src_path).parent
    in_repo: set[str] = set()
    external: set[str] = set()

    for imp in summary.get("imports", []):
        spec = str(imp.get("source", "")).strip()
        if not spec or spec.startswith("#"):
            continue
        if re.match(r"^[a-z][a-z0-9+.-]*://", spec) or spec.startswith("//"):
            external.add(spec)
            continue
        clean = spec.split("#", 1)[0]
        if not clean:
            continue
        target = _normalize_rel((src_dir / clean).parts)
        if target in paths_set:
            in_repo.add(target)
        else:
            external.add(spec)

    in_repo.discard(src_path)
    return sorted(in_repo), sorted(external)
