"""ChunkExtractor — RecordEnricher that splits source files into chunks.

Strategy:
  - Python: re-parse with `ast`, emit one chunk per top-level FunctionDef /
    AsyncFunctionDef / ClassDef, and one chunk per method inside classes
    (one nesting level). Byte ranges come from each node's own
    (lineno, col_offset)..(end_lineno, end_col_offset) span — not whole-line
    slicing — so single-line definitions stay distinct.
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

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from codebase_mapper.inspection.models import FileRecord


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
        elif record.language == "dart":
            chunks = _chunk_dart(content, record)
        elif record.language == "java":
            chunks = _chunk_java(content, record)
        elif record.language == "go":
            chunks = _chunk_go(content, record)
        elif record.language == "cpp":
            chunks = _chunk_cpp(content, record)
        elif record.language in ("objective-c", "objective-cpp"):
            chunks = _chunk_objc(content, record)
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

    line_byte_starts = _line_byte_starts(content)

    chunks: list[dict] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append(_chunk_from_node(node, "function", None, content, line_byte_starts))
        elif isinstance(node, ast.ClassDef):
            class_chunk = _chunk_from_node(node, "class", None, content, line_byte_starts)
            chunks.append(class_chunk)
            # also emit method-level chunks
            for inner in node.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    chunks.append(_chunk_from_node(inner, "method", node.name, content, line_byte_starts))

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


def _pos_to_byte(line_byte_starts: list[int], line: int, col: int) -> int:
    """Absolute byte offset of a (1-indexed line, column) position.

    Python's ``ast`` reports ``col_offset`` / ``end_col_offset`` as UTF-8 byte
    offsets within the line, so adding them to the line's start byte yields an
    exact absolute byte offset — correct even for multibyte source.
    """
    base = line_byte_starts[line] if line < len(line_byte_starts) else line_byte_starts[-1]
    return base + col


def _chunk_from_node(node: ast.AST, kind: str, parent: str | None,
                     content: bytes, line_byte_starts: list[int]) -> dict:
    # Span the node's *own* byte range, not the whole line(s) it sits on — a
    # single-line definition must not absorb its neighbours (defect D1). The
    # decorator list, when present, extends the start upward to cover ``@deco``.
    decorators = getattr(node, "decorator_list", []) or []
    if decorators:
        first = min(decorators, key=lambda d: (d.lineno, d.col_offset))
        line_start = first.lineno
        # col_offset points just past the ``@``; step back one byte to include it.
        start_col = max(0, first.col_offset - 1)
    else:
        line_start = node.lineno
        start_col = node.col_offset
    line_end = node.end_lineno or node.lineno
    end_col = node.end_col_offset if node.end_col_offset is not None else 0
    byte_start = _pos_to_byte(line_byte_starts, line_start, start_col)
    byte_end = _pos_to_byte(line_byte_starts, line_end, end_col)
    chunk_bytes = content[byte_start:byte_end]
    text = chunk_bytes.decode("utf-8", "replace")
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
        content.decode("utf-8")  # guard: skip files we can't decode as UTF-8
    except UnicodeDecodeError:
        return []

    _ts_setup()
    parser = ts.Parser(_TS_LANGS[grammar])
    tree = parser.parse(content)

    chunks: list[dict] = []
    for node in tree.root_node.children:
        # Unwrap `export ...` / `export default ...` so the inner decl is
        # what we chunk; the chunk's byte range still covers the export
        # keyword via `node` below.
        inner = _tsjs_unwrap_export(node)
        if inner.type == "function_declaration":
            name = _tsjs_named_child_text(inner, "identifier", content)
            if name:
                chunks.append(_chunk_from_ts_node(node, "function", None, name, content))
        elif inner.type == "class_declaration":
            name = _tsjs_class_name(inner, content)
            if not name:
                continue
            chunks.append(_chunk_from_ts_node(node, "class", None, name, content))
            for method in _tsjs_iter_methods(inner):
                m_name = _tsjs_named_child_text(method, "property_identifier", content)
                if m_name:
                    chunks.append(_chunk_from_ts_node(method, "method", name, m_name, content))
        elif inner.type in ("lexical_declaration", "variable_declaration"):
            for name in _tsjs_decl_function_bindings(inner, content):
                chunks.append(_chunk_from_ts_node(node, "function", None, name, content))

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
                        content: bytes) -> dict:
    # Span the node's own byte range — tree-sitter exposes exact byte offsets,
    # so a minified single-line file yields one distinct chunk per symbol
    # rather than N copies of the whole line (defect D1).
    byte_start = node.start_byte
    byte_end = node.end_byte
    chunk_bytes = content[byte_start:byte_end]
    text = chunk_bytes.decode("utf-8", "replace")
    return {
        "kind": kind,
        "symbol": name,
        "parent_symbol": parent,
        "byte_start": byte_start,
        "byte_end": byte_end,
        # Tree-sitter's points are zero-indexed; chunks expose 1-indexed lines.
        "line_start": node.start_point[0] + 1,
        "line_end": node.end_point[0] + 1,
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
        content.decode("utf-8")  # guard: skip files we can't decode as UTF-8
    except UnicodeDecodeError:
        return []

    _ts_setup()
    parser = ts.Parser(_TS_LANGS["rust"])
    tree = parser.parse(content)

    chunks: list[dict] = []
    for node in tree.root_node.children:
        if not node.is_named or node.type in ("attribute_item", "inner_attribute_item"):
            continue
        t = node.type
        if t == "function_item":
            name = _rust_chunk_name(node, content)
            if name:
                chunks.append(_chunk_from_ts_node(node, "function", None, name, content))
        elif t in ("struct_item", "enum_item", "union_item"):
            name = _rust_chunk_name(node, content)
            if name:
                chunks.append(_chunk_from_ts_node(node, "class", None, name, content))
        elif t == "trait_item":
            name = _rust_chunk_name(node, content)
            if not name:
                continue
            chunks.append(_chunk_from_ts_node(node, "class", None, name, content))
            body = _rust_decl_list(node)
            if body is not None:
                for m in body.children:
                    if m.is_named and m.type in ("function_item", "function_signature_item"):
                        m_name = _rust_chunk_name(m, content)
                        if m_name:
                            chunks.append(_chunk_from_ts_node(m, "method", name, m_name, content))
        elif t == "impl_item":
            impl_name = _rust_chunk_name(node, content)
            if not impl_name:
                continue
            chunks.append(_chunk_from_ts_node(node, "class", None, impl_name, content))
            body = _rust_decl_list(node)
            if body is not None:
                for m in body.children:
                    if m.is_named and m.type == "function_item":
                        m_name = _rust_chunk_name(m, content)
                        if m_name:
                            chunks.append(_chunk_from_ts_node(m, "method", impl_name, m_name, content))

    if not chunks:
        return _whole_file_chunk(content, path)
    return chunks


# ---------------------------------------------------------------------------
# Dart
# ---------------------------------------------------------------------------


# These declaration kinds become chunks. We map every Dart-side kind onto
# one of the canonical chunk kinds the embedder understands. The mapping
# preserves semantic distinction (method vs. function vs. getter/setter)
# in the symbol name when needed.
_DART_CHUNKABLE_KINDS = {
    "class", "mixin", "enum", "extension", "typedef",
    "function", "method", "constructor", "getter", "setter", "operator",
}

# Map Dart item kind → chunk kind. Getters/setters/operators/constructors
# share the "method" chunk kind (they all live inside classes); the
# original kind survives in the symbol name (e.g. "get balance").
_DART_TO_CHUNK_KIND = {
    "class": "class",
    "mixin": "class",
    "enum": "class",
    "extension": "class",
    "typedef": "class",
    "function": "function",
    "method": "method",
    "constructor": "method",
    "getter": "method",
    "setter": "method",
    "operator": "method",
}


def _dart_symbol_for(item: dict) -> str:
    """Render the symbol name for a Dart chunk. Getters/setters get a
    ``get `` / ``set `` prefix so they're distinguishable from a method
    of the same name (Dart allows ``foo`` as method *and* ``get foo``).
    """
    kind = item["kind"]
    name = item["name"]
    if kind == "getter":
        return f"get {name}"
    if kind == "setter":
        return f"set {name}"
    return name


def _chunk_dart(content: bytes, record: FileRecord) -> list[dict]:
    """Build per-symbol chunks from the Dart analyzer's ``items`` array.

    Why consume the analyzer's output instead of re-parsing here?

      * Avoids duplicating Dart's gnarly grammar inside two modules.
      * Guarantees the chunker and the symbol-xref resolver see *exactly*
        the same set of declarations — they all read ``items``.
      * Keeps the L2 plugin free of language-specific parsing logic,
        matching the architecture's separation between inspection
        (L1) and chunk extraction (L2).

    Falls back to a whole-file chunk if the analyzer produced no
    parseable items (e.g. a syntactically broken file).
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    summary = record.ast_summary or {}
    items = summary.get("items") or []
    if not items:
        return _whole_file_chunk(content, record.path)

    src_lines = text.splitlines(keepends=True)
    n_lines = len(src_lines)

    chunks: list[dict] = []
    for item in items:
        kind = item.get("kind")
        if kind not in _DART_CHUNKABLE_KINDS:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        byte_start = item["byte_start"]
        byte_end = item["byte_end"]
        # The analyzer's byte spans are computed against the raw bytes;
        # for the chunk's content_sha we hash the actual reconstructed
        # text bytes — matches the convention used by _chunk_from_node.
        chunks.append({
            "kind": _DART_TO_CHUNK_KIND.get(kind, "method"),
            "symbol": _dart_symbol_for(item),
            "parent_symbol": item.get("parent"),
            "byte_start": byte_start,
            "byte_end": byte_end,
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        })

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# Java
# ---------------------------------------------------------------------------


# Java item kinds → canonical chunk kind. Constructors share "method"
# (they live inside a class); their original kind is recoverable from
# the symbol name when needed (constructors share the class name).
_JAVA_TO_CHUNK_KIND = {
    "class": "class",
    "interface": "class",
    "enum": "class",
    "annotation": "class",
    "record": "class",
    "method": "method",
    "constructor": "method",
}


def _chunk_java(content: bytes, record: FileRecord) -> list[dict]:
    """Build per-symbol chunks from the Java analyzer's ``items`` array.

    Architecturally identical to ``_chunk_dart``: the analyzer is the
    single source of truth for the AST shape; the chunker just translates
    each item into the canonical chunk record. Inner classes (which carry
    a ``parent`` pointing to the enclosing class) get their own chunks
    and their methods get ``parent_symbol`` set to the inner class name.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    summary = record.ast_summary or {}
    items = summary.get("items") or []
    if not items:
        return _whole_file_chunk(content, record.path)

    src_lines = text.splitlines(keepends=True)
    n_lines = len(src_lines)

    chunks: list[dict] = []
    for item in items:
        kind = item.get("kind")
        if kind not in _JAVA_TO_CHUNK_KIND:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append({
            "kind": _JAVA_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        })

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


_GO_TO_CHUNK_KIND = {
    "function": "function",
    "method": "method",
    "struct": "class",
    "interface": "class",
    "type": "class",
}


def _chunk_go(content: bytes, record: FileRecord) -> list[dict]:
    """Per-symbol chunks from the Go analyzer's ``items`` array — one chunk per
    top-level func / method / struct / interface / type. Methods carry
    ``parent_symbol`` = the receiver type. Same items-based shape as
    ``_chunk_java``."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    summary = record.ast_summary or {}
    items = summary.get("items") or []
    if not items:
        return _whole_file_chunk(content, record.path)

    src_lines = text.splitlines(keepends=True)
    n_lines = len(src_lines)

    chunks: list[dict] = []
    for item in items:
        kind = item.get("kind")
        if kind not in _GO_TO_CHUNK_KIND:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append({
            "kind": _GO_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        })

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# C++
# ---------------------------------------------------------------------------


# C++ item kinds → canonical chunk kind. Out-of-class method definitions
# (``Dog::speak`` at file scope) and in-class method definitions are
# both ``method``; the analyzer's ``parent`` field carries the enclosing
# class either way.
_CPP_TO_CHUNK_KIND = {
    "class": "class",
    "struct": "class",
    "union": "class",
    "enum": "class",
    "function": "function",
    "method": "method",
    "constructor": "method",
    "destructor": "method",
}


def _chunk_cpp(content: bytes, record: FileRecord) -> list[dict]:
    """Build per-symbol chunks from the C++ analyzer's ``items`` array.

    Items already carry byte/line spans and a (possibly empty)
    ``namespace`` qualifier; the chunker stays language-agnostic by
    not surfacing namespace in the symbol name — same posture as Java.

    Declaration vs definition deduplication: a class header may
    contain ``void foo();`` (declaration) and the same source file
    may later contain ``void Class::foo() { … }`` (definition). Both
    appear in ``items``; we keep only one chunk per
    ``(name, parent, byte_span)`` tuple to avoid duplicates.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    summary = record.ast_summary or {}
    items = summary.get("items") or []
    if not items:
        return _whole_file_chunk(content, record.path)

    src_lines = text.splitlines(keepends=True)
    n_lines = len(src_lines)

    chunks: list[dict] = []
    seen_ids: set[tuple[str, str | None, int, int]] = set()
    for item in items:
        kind = item.get("kind")
        if kind not in _CPP_TO_CHUNK_KIND:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        key = (item["name"], item.get("parent"),
               item["byte_start"], item["byte_end"])
        if key in seen_ids:
            continue
        seen_ids.add(key)
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append({
            "kind": _CPP_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        })

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# Objective-C / Objective-C++
# ---------------------------------------------------------------------------


# ObjC item kinds → canonical chunk kind. Categories and protocols share
# the ``class`` chunk kind (they're all top-level type declarations);
# method declarations and definitions both become ``method`` chunks.
_OBJC_TO_CHUNK_KIND = {
    "class_interface": "class",
    "class_implementation": "class",
    "category": "class",
    "category_impl": "class",
    "protocol": "class",
    "function": "function",
    "method": "method",
}


def _chunk_objc(content: bytes, record: FileRecord) -> list[dict]:
    """Build per-symbol chunks from the ObjC analyzer's ``items`` array.

    Both the ``@interface`` declaration and the matching
    ``@implementation`` definition produce chunks; consumers can
    distinguish them by the analyzer's original ``kind`` if they care,
    but at the chunk level both are simply ``class`` chunks. Method
    chunks carry the *short* selector name as ``symbol`` (so
    ``initWithName:`` becomes symbol ``initWithName``); the full
    selector lives on the analyzer item for downstream xref binding.

    Declaration vs definition deduplication: a method may appear once
    in the interface header and once in the implementation. We key on
    ``(name, parent, byte_span)`` so each appearance gets its own
    chunk — they're at different byte offsets so the deduplication
    only fires for accidental duplicates.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    summary = record.ast_summary or {}
    items = summary.get("items") or []
    if not items:
        return _whole_file_chunk(content, record.path)

    src_lines = text.splitlines(keepends=True)
    n_lines = len(src_lines)

    chunks: list[dict] = []
    seen_ids: set[tuple[str, str | None, int, int]] = set()
    for item in items:
        kind = item.get("kind")
        if kind not in _OBJC_TO_CHUNK_KIND:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        key = (item["name"], item.get("parent"),
               item["byte_start"], item["byte_end"])
        if key in seen_ids:
            continue
        seen_ids.add(key)
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append({
            "kind": _OBJC_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        })

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks
