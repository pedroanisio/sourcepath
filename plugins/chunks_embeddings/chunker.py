"""ChunkExtractor — RecordEnricher that splits source files into chunks.

Strategy:
  - Python: re-parse with `ast`, emit one chunk per top-level FunctionDef /
    AsyncFunctionDef / ClassDef, and one chunk per method inside classes
    (one nesting level). Bytes ranges come from `ast.get_source_segment`-
    equivalent line-based slicing.
  - TypeScript / JavaScript: re-parse with tree-sitter, emit one chunk per
    top-level ``function_declaration`` / ``class_declaration`` /
    ``lexical_declaration`` containing an arrow or function expression, and
    one chunk per ``method_definition`` inside a class body. Symbol-level
    coverage matches Python so the symbol-xref layer can attach edges.
  - Other text source files: emit a single "file" chunk covering the whole
    content.
  - Binary, symlinks, generated, asset: skip.

Each chunk is a dict:
    {
        "kind": "function" | "class" | "method" | "file",
        "symbol": str,                 # e.g. "calculate_score" or "<file>"
        "parent_symbol": str | None,   # for methods, the class name
        "byte_start": int,
        "byte_end": int,               # exclusive
        "line_start": int,             # 1-indexed
        "line_end": int,               # 1-indexed inclusive
        "text": str,                   # may be truncated for embedding
        "content_sha256": str,         # sha256 of the full chunk bytes
    }

Chunks are stored at `ctx.scratch["chunks"][path]` as a sorted list. The list
is empty for files with no chunkable content.
"""
from __future__ import annotations

import ast
import hashlib
import warnings
from typing import cast

from codebase_mapper.extensions import PipelineCtx
from codebase_mapper.models import FileRecord


SKIP_TYPES = {"binary", "asset", "license", "lockfile", "generated"}


class ChunkExtractor:
    name = "l2_10_chunker"

    def enrich(self, record: FileRecord, content: bytes, ctx: PipelineCtx) -> None:
        chunks_map = cast(dict, ctx.scratch.setdefault("chunks", {}))
        # symlinks have mode 120000; reflected as language None and type
        # "configuration" by the host, but mode isn't on the record itself.
        # We use language as a proxy: text source files have a known language.
        if record.type_ in SKIP_TYPES:
            chunks_map[record.path] = []
            return

        chunks: list[dict] = []
        if record.language == "python":
            chunks = _chunk_python(content, record.path)
        elif record.language in ("typescript", "javascript"):
            chunks = _chunk_tsjs(content, record.path)
        elif record.language == "rust":
            chunks = _chunk_rust(content, record.path)
        elif record.language is not None or record.type_ in {"documentation", "configuration", "test_code", "source_code"}:
            # whole-file chunk for any text file we recognize
            chunks = _whole_file_chunk(content, record.path)
        else:
            chunks = []

        chunks_map[record.path] = chunks


def _whole_file_chunk(content: bytes, path: str) -> list[dict]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    line_end = text.count("\n") + (0 if text.endswith("\n") else 1)
    if not text.strip():
        return []
    return [{
        "kind": "file",
        "symbol": "<file>",
        "parent_symbol": None,
        "byte_start": 0,
        "byte_end": len(content),
        "line_start": 1,
        "line_end": max(1, line_end),
        "text": text,
        "content_sha256": hashlib.sha256(content).hexdigest(),
    }]


def _chunk_python(content: bytes, path: str) -> list[dict]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    try:
        # Silence SyntaxWarning (invalid escape sequences etc.) — that's a
        # diagnostic about the mapped source, not about us.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text, filename=path)
    except SyntaxError:
        # Fall back to whole-file chunk so the file still gets some L2 coverage.
        return _whole_file_chunk(content, path)

    src_lines = text.splitlines(keepends=True)
    line_byte_starts = _line_byte_starts(content)

    chunks: list[dict] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append(_chunk_from_node(node, "function", None, src_lines, line_byte_starts))
        elif isinstance(node, ast.ClassDef):
            class_chunk = _chunk_from_node(node, "class", None, src_lines, line_byte_starts)
            chunks.append(class_chunk)
            # also emit method-level chunks
            for inner in node.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    chunks.append(_chunk_from_node(inner, "method", node.name, src_lines, line_byte_starts))

    # If nothing chunkable at top level, emit a whole-file chunk so embedding
    # coverage is uniform.
    if not chunks:
        return _whole_file_chunk(content, path)
    return chunks


def _line_byte_starts(content: bytes) -> list[int]:
    """Byte offset where each 1-indexed line starts. Index 0 is unused."""
    starts = [0, 0]  # line 0 unused, line 1 starts at byte 0
    for i, b in enumerate(content):
        if b == 0x0A:  # \n
            starts.append(i + 1)
    return starts


def _chunk_from_node(node: ast.AST, kind: str, parent: str | None,
                     src_lines: list[str], line_byte_starts: list[int]) -> dict:
    # decorator_list[0].lineno is earlier than node.lineno when decorators present
    decorators = getattr(node, "decorator_list", []) or []
    if decorators:
        line_start = min(d.lineno for d in decorators)
    else:
        line_start = node.lineno
    line_end = node.end_lineno or node.lineno
    text = "".join(src_lines[line_start - 1: line_end])
    chunk_bytes = text.encode("utf-8")
    byte_start = line_byte_starts[line_start] if line_start < len(line_byte_starts) else 0
    # Compute byte_end from line_end + length of last line; safer to use
    # byte_start + len(chunk_bytes) which is exact given the slicing above.
    byte_end = byte_start + len(chunk_bytes)
    name = getattr(node, "name", "<unknown>")
    return {
        "kind": kind,
        "symbol": name,
        "parent_symbol": parent,
        "byte_start": byte_start,
        "byte_end": byte_end,
        "line_start": line_start,
        "line_end": line_end,
        "text": text,
        "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
    }


# ---------------------------------------------------------------------------
# TypeScript / JavaScript chunker (tree-sitter)
# ---------------------------------------------------------------------------


def _chunk_tsjs(content: bytes, path: str) -> list[dict]:
    """Symbol-level chunks for TS/JS.

    Re-parses with tree-sitter (cheap; the LanguageAnalyzer parses too but
    its tree isn't kept on the record). Emits:
      - one chunk per top-level ``function_declaration``
      - one chunk per top-level ``class_declaration`` + one per
        ``method_definition`` inside its ``class_body``
      - one chunk per top-level ``lexical_declaration`` /
        ``variable_declaration`` whose RHS is an arrow or function
        expression (``const foo = () => ...`` / ``const foo = function() ...``)

    Falls back to a whole-file chunk if tree-sitter is unavailable or no
    chunkable decls are found.
    """
    from codebase_mapper.ts_setup import TS_AVAILABLE, _ts_setup, _TS_LANGS, ts
    from codebase_mapper.ts_setup import _ts_grammar_for

    if not TS_AVAILABLE:
        return _whole_file_chunk(content, path)
    grammar = _ts_grammar_for(path)
    if grammar not in ("typescript", "javascript", "tsx"):
        return _whole_file_chunk(content, path)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []

    _ts_setup()
    parser = ts.Parser(_TS_LANGS[grammar])
    tree = parser.parse(content)
    src_lines = text.splitlines(keepends=True)
    line_byte_starts = _line_byte_starts(content)

    chunks: list[dict] = []
    for node in tree.root_node.children:
        # Unwrap `export ...` / `export default ...` so the inner decl is
        # what we chunk; the chunk's byte range still covers the export
        # keyword via `target` below.
        inner = _tsjs_unwrap_export(node)
        if inner.type == "function_declaration":
            name = _tsjs_named_child_text(inner, "identifier", content)
            if name:
                chunks.append(_chunk_from_ts_node(node, "function", None, name,
                                                  src_lines, line_byte_starts))
        elif inner.type == "class_declaration":
            name = _tsjs_class_name(inner, content)
            if not name:
                continue
            chunks.append(_chunk_from_ts_node(node, "class", None, name,
                                              src_lines, line_byte_starts))
            for method in _tsjs_iter_methods(inner):
                m_name = _tsjs_named_child_text(method, "property_identifier", content)
                if m_name:
                    chunks.append(_chunk_from_ts_node(method, "method", name, m_name,
                                                      src_lines, line_byte_starts))
        elif inner.type in ("lexical_declaration", "variable_declaration"):
            for name in _tsjs_decl_function_bindings(inner, content):
                chunks.append(_chunk_from_ts_node(node, "function", None, name,
                                                  src_lines, line_byte_starts))

    if not chunks:
        return _whole_file_chunk(content, path)
    return chunks


def _tsjs_unwrap_export(node):
    """If ``node`` is ``export ...`` return the wrapped declaration node."""
    if node.type != "export_statement":
        return node
    for c in node.children:
        if c.is_named and c.type in (
            "function_declaration", "class_declaration",
            "lexical_declaration", "variable_declaration",
        ):
            return c
    return node


def _tsjs_named_child_text(node, child_type: str, content: bytes) -> str | None:
    for c in node.children:
        if c.type == child_type:
            return content[c.start_byte:c.end_byte].decode("utf-8", "replace")
    return None


def _tsjs_class_name(class_node, content: bytes) -> str | None:
    # TypeScript uses type_identifier; JavaScript uses identifier.
    return (
        _tsjs_named_child_text(class_node, "type_identifier", content)
        or _tsjs_named_child_text(class_node, "identifier", content)
    )


def _tsjs_iter_methods(class_node):
    """Yield method_definition nodes inside a class_declaration's body."""
    body = next((c for c in class_node.children if c.type == "class_body"), None)
    if body is None:
        return
    for c in body.children:
        if c.type == "method_definition":
            yield c


def _tsjs_decl_function_bindings(decl_node, content: bytes) -> list[str]:
    """For ``const foo = () => ...`` / ``const foo = function() ...``
    declarations, yield the bound name(s). Skips declarators whose RHS isn't
    a function/arrow (we only chunk callable bindings)."""
    out: list[str] = []
    for c in decl_node.children:
        if c.type != "variable_declarator":
            continue
        # variable_declarator: name = value
        # children include an identifier (name) and the value node.
        name_node = next((n for n in c.children if n.type == "identifier"), None)
        if name_node is None:
            continue
        # The value is the last named child after the `=`. Tree-sitter
        # exposes it cleanly via the `value` field; use that when available.
        value = c.child_by_field_name("value")
        if value is None:
            continue
        if value.type in ("arrow_function", "function_expression"):
            out.append(content[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace"))
    return out


def _chunk_from_ts_node(node, kind: str, parent: str | None, name: str,
                        src_lines: list[str], line_byte_starts: list[int]) -> dict:
    # Tree-sitter's points are zero-indexed; chunks expose 1-indexed lines.
    line_start = node.start_point[0] + 1
    line_end = node.end_point[0] + 1
    text = "".join(src_lines[line_start - 1: line_end])
    chunk_bytes = text.encode("utf-8")
    byte_start = line_byte_starts[line_start] if line_start < len(line_byte_starts) else 0
    byte_end = byte_start + len(chunk_bytes)
    return {
        "kind": kind,
        "symbol": name,
        "parent_symbol": parent,
        "byte_start": byte_start,
        "byte_end": byte_end,
        "line_start": line_start,
        "line_end": line_end,
        "text": text,
        "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
    }


# ---------------------------------------------------------------------------
# Rust chunker (tree-sitter)
# ---------------------------------------------------------------------------


def _rust_chunk_name(node, content: bytes) -> str | None:
    """Pull the symbol name. Prefers tree-sitter's ``name``/``type`` fields;
    falls back to the first identifier-like child."""
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return content[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
    type_node = node.child_by_field_name("type")
    if type_node is not None:
        return content[type_node.start_byte:type_node.end_byte].decode("utf-8", "replace")
    for c in node.children:
        if c.type in ("identifier", "type_identifier", "scoped_type_identifier"):
            return content[c.start_byte:c.end_byte].decode("utf-8", "replace")
    return None


def _rust_decl_list(node):
    """Return the inner ``declaration_list`` body for impl/trait/mod, or None."""
    for c in node.children:
        if c.type == "declaration_list":
            return c
    return None


def _chunk_rust(content: bytes, path: str) -> list[dict]:
    """Symbol-level chunks for Rust source files.

    Emits one chunk per:
      - top-level ``function_item``
      - ``struct_item`` / ``enum_item`` / ``union_item`` (kind: ``class``)
      - ``trait_item`` (kind: ``class``) + one chunk per
        ``function_item`` / ``function_signature_item`` inside its body
        (kind: ``method``)
      - ``impl_item`` (kind: ``class``, symbol = implementing type) +
        one chunk per inner ``function_item`` (kind: ``method``)

    Falls back to a whole-file chunk if tree-sitter is unavailable, the
    grammar disagrees, or no top-level chunkable items are found.
    """
    from codebase_mapper.ts_setup import TS_AVAILABLE, _ts_setup, _TS_LANGS, ts

    if not TS_AVAILABLE:
        return _whole_file_chunk(content, path)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []

    _ts_setup()
    parser = ts.Parser(_TS_LANGS["rust"])
    tree = parser.parse(content)
    src_lines = text.splitlines(keepends=True)
    line_byte_starts = _line_byte_starts(content)

    chunks: list[dict] = []
    for node in tree.root_node.children:
        if not node.is_named or node.type in ("attribute_item", "inner_attribute_item"):
            continue
        t = node.type
        if t == "function_item":
            name = _rust_chunk_name(node, content)
            if name:
                chunks.append(_chunk_from_ts_node(node, "function", None, name,
                                                  src_lines, line_byte_starts))
        elif t in ("struct_item", "enum_item", "union_item"):
            name = _rust_chunk_name(node, content)
            if name:
                chunks.append(_chunk_from_ts_node(node, "class", None, name,
                                                  src_lines, line_byte_starts))
        elif t == "trait_item":
            name = _rust_chunk_name(node, content)
            if not name:
                continue
            chunks.append(_chunk_from_ts_node(node, "class", None, name,
                                              src_lines, line_byte_starts))
            body = _rust_decl_list(node)
            if body is not None:
                for m in body.children:
                    if m.is_named and m.type in ("function_item", "function_signature_item"):
                        m_name = _rust_chunk_name(m, content)
                        if m_name:
                            chunks.append(_chunk_from_ts_node(m, "method", name, m_name,
                                                              src_lines, line_byte_starts))
        elif t == "impl_item":
            impl_name = _rust_chunk_name(node, content)
            if not impl_name:
                continue
            chunks.append(_chunk_from_ts_node(node, "class", None, impl_name,
                                              src_lines, line_byte_starts))
            body = _rust_decl_list(node)
            if body is not None:
                for m in body.children:
                    if m.is_named and m.type == "function_item":
                        m_name = _rust_chunk_name(m, content)
                        if m_name:
                            chunks.append(_chunk_from_ts_node(m, "method", impl_name, m_name,
                                                              src_lines, line_byte_starts))

    if not chunks:
        return _whole_file_chunk(content, path)
    return chunks
