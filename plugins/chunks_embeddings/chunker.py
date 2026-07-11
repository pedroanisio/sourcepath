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

Symbol-level chunks additionally MAY carry the optional signature/type fields
defined in plugins/chunks_embeddings/signatures.py (signature, params,
returns, bases, type_params, visibility, is_async, decorators) — omitted when
empty/unknown, never emitted as placeholders.

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
from plugins.chunks_embeddings.signatures import (
    apply_signature_fields,
    python_signature_fields,
    signature_fields_from_item,
)


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
        elif record.language == "clojure":
            chunks = _chunk_clojure(content, record)
        elif record.language == "cobol":
            chunks = _chunk_cobol(content, record)
        elif record.language == "cfml":
            chunks = _chunk_cfml(content, record)
        elif record.language == "cpp":
            chunks = _chunk_cpp(content, record)
        elif record.language in ("objective-c", "objective-cpp"):
            chunks = _chunk_objc(content, record)
        elif record.language == "ruby":
            chunks = _chunk_ruby(content, record)
        elif record.language == "c":
            chunks = _chunk_c(content, record)
        elif record.language == "kotlin":
            chunks = _chunk_kotlin(content, record)
        elif record.language == "swift":
            chunks = _chunk_swift(content, record)
        elif record.language == "sql":
            chunks = _chunk_sql(content, record)
        elif record.language == "html":
            chunks = _chunk_html(content, record)
        elif record.language in ("css", "scss"):
            chunks = _chunk_css(content, record)
        elif record.language == "json":
            chunks = _chunk_key_members(content, record)
        elif record.language == "yaml":
            chunks = _chunk_key_members(content, record)
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


def _chunk_from_node(node: ast.stmt, kind: str, parent: str | None,
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
    chunk = {
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
    return apply_signature_fields(chunk, python_signature_fields(node))


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
                chunks.append(_chunk_from_ts_node(
                    node, "function", None, name, content,
                    _tsjs_callable_fields(inner, content)))
        elif inner.type == "class_declaration":
            name = _tsjs_class_name(inner, content)
            if not name:
                continue
            chunks.append(_chunk_from_ts_node(
                node, "class", None, name, content,
                _tsjs_class_fields(inner, content)))
            for method in _tsjs_iter_methods(inner):
                m_name = _tsjs_named_child_text(method, "property_identifier", content)
                if m_name:
                    chunks.append(_chunk_from_ts_node(
                        method, "method", name, m_name, content,
                        _tsjs_callable_fields(method, content)))
        elif inner.type in ("lexical_declaration", "variable_declaration"):
            for name, declarator in _tsjs_decl_function_bindings(inner, content):
                chunks.append(_chunk_from_ts_node(
                    node, "function", None, name, content,
                    _tsjs_arrow_fields(inner, declarator, content)))

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


def _tsjs_decl_function_bindings(decl_node, content: bytes) -> list[tuple[str, object]]:
    """For ``const foo = () => ...`` / ``const foo = function() ...``
    declarations, yield ``(name, declarator_node)`` pairs. Skips declarators
    whose RHS isn't a function/arrow (we only chunk callable bindings)."""
    out: list[tuple[str, object]] = []
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
            name = content[name_node.start_byte:name_node.end_byte].decode("utf-8", "replace")
            out.append((name, c))
    return out


def _chunk_from_ts_node(node, kind: str, parent: str | None, name: str,
                        content: bytes, fields: dict | None = None) -> dict:
    # Span the node's own byte range — tree-sitter exposes exact byte offsets,
    # so a minified single-line file yields one distinct chunk per symbol
    # rather than N copies of the whole line (defect D1).
    byte_start = node.start_byte
    byte_end = node.end_byte
    chunk_bytes = content[byte_start:byte_end]
    text = chunk_bytes.decode("utf-8", "replace")
    chunk = {
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
    return apply_signature_fields(chunk, fields or {})


# ---------------------------------------------------------------------------
# TS/JS signature extraction (canonical fields — see signatures.py)
# ---------------------------------------------------------------------------


def _ts_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _ws_collapse(text: str) -> str:
    return " ".join(text.split())


def _tsjs_annotation_type(node, content: bytes) -> str | None:
    # A type_annotation node's text is ": T" — drop the leading colon.
    return _ws_collapse(_ts_text(node, content).lstrip(":")) or None


def _tsjs_params(params_node, content: bytes) -> list[dict]:
    """Flatten formal_parameters. Names are as written: rest params keep the
    ``...`` prefix and TS optional params keep the ``?`` suffix."""
    out: list[dict] = []
    for p in params_node.children:
        if not p.is_named:
            continue
        if p.type in ("required_parameter", "optional_parameter"):   # TS
            pattern = p.child_by_field_name("pattern")
            if pattern is None:
                continue
            name = _ws_collapse(_ts_text(pattern, content))
            if p.type == "optional_parameter":
                name += "?"
            ta = p.child_by_field_name("type")
            value = p.child_by_field_name("value")
            out.append({
                "name": name,
                "type": _tsjs_annotation_type(ta, content) if ta is not None else None,
                "default": _ws_collapse(_ts_text(value, content)) if value is not None else None,
            })
        elif p.type in ("identifier", "rest_pattern",                 # JS
                        "object_pattern", "array_pattern"):
            out.append({"name": _ws_collapse(_ts_text(p, content)),
                        "type": None, "default": None})
        elif p.type == "assignment_pattern":                          # JS default
            left = p.child_by_field_name("left")
            right = p.child_by_field_name("right")
            if left is not None:
                out.append({
                    "name": _ws_collapse(_ts_text(left, content)),
                    "type": None,
                    "default": _ws_collapse(_ts_text(right, content)) if right is not None else None,
                })
    return out


def _tsjs_type_params(node, content: bytes) -> list[str]:
    tp = node.child_by_field_name("type_parameters")
    if tp is None:
        return []
    return [_ws_collapse(_ts_text(c, content))
            for c in tp.children if c.is_named]


def _tsjs_callable_fields(node, content: bytes) -> dict:
    """Canonical fields for function_declaration / method_definition /
    arrow_function / function_expression nodes."""
    fields: dict = {}
    params_node = node.child_by_field_name("parameters")
    if params_node is not None:
        params = _tsjs_params(params_node, content)
        if params:
            fields["params"] = params
    rt = node.child_by_field_name("return_type")
    if rt is not None:
        returns = _tsjs_annotation_type(rt, content)
        if returns:
            fields["returns"] = returns
    type_params = _tsjs_type_params(node, content)
    if type_params:
        fields["type_params"] = type_params
    for c in node.children:
        if not c.is_named and c.type == "async":
            fields["is_async"] = True
        elif c.type == "accessibility_modifier":
            fields["visibility"] = _ts_text(c, content)
        elif c.type == "decorator":
            fields.setdefault("decorators", []).append(
                _ws_collapse(_ts_text(c, content)).lstrip("@"))
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    signature = _ws_collapse(
        content[node.start_byte:end].decode("utf-8", "replace")
    ).rstrip(";").strip()
    if signature:
        fields["signature"] = signature
    return fields


def _tsjs_class_fields(class_node, content: bytes) -> dict:
    """Canonical fields for a class_declaration: heritage → bases, generics,
    decorators, header signature (up to the class body)."""
    fields: dict = {}
    type_params = _tsjs_type_params(class_node, content)
    if type_params:
        fields["type_params"] = type_params
    bases: list[str] = []
    heritage = next((c for c in class_node.children
                     if c.type == "class_heritage"), None)
    if heritage is not None:
        for clause in heritage.children:
            if clause.type == "extends_clause":
                # TS pairs each value with optional type_arguments; slice from
                # the value start to its (possibly extended) end to keep
                # ``Base<T>`` as written.
                named = [c for c in clause.children if c.is_named]
                i = 0
                while i < len(named):
                    start = named[i].start_byte
                    end = named[i].end_byte
                    if i + 1 < len(named) and named[i + 1].type == "type_arguments":
                        end = named[i + 1].end_byte
                        i += 1
                    bases.append(_ws_collapse(
                        content[start:end].decode("utf-8", "replace")))
                    i += 1
            elif clause.type == "implements_clause":
                bases.extend(_ws_collapse(_ts_text(c, content))
                             for c in clause.children if c.is_named)
            elif clause.is_named:
                # JS grammar: class_heritage wraps the expression directly.
                bases.append(_ws_collapse(_ts_text(clause, content)))
    if bases:
        fields["bases"] = bases
    decorators = [_ws_collapse(_ts_text(c, content)).lstrip("@")
                  for c in class_node.children if c.type == "decorator"]
    if decorators:
        fields["decorators"] = decorators
    body = next((c for c in class_node.children if c.type == "class_body"), None)
    end = body.start_byte if body is not None else class_node.end_byte
    signature = _ws_collapse(
        content[class_node.start_byte:end].decode("utf-8", "replace")).strip()
    if signature:
        fields["signature"] = signature
    return fields


def _tsjs_arrow_fields(inner_decl, declarator, content: bytes) -> dict:
    """Canonical fields for a ``const f = (...) => ...`` binding: callable
    fields come from the arrow/function expression; the signature is the
    declaration keyword + declarator text up to and including the ``=>``
    (or up to the function body for ``function`` expressions)."""
    value = declarator.child_by_field_name("value")
    fields = _tsjs_callable_fields(value, content) if value is not None else {}
    keyword = inner_decl.children[0] if inner_decl.children else None
    kw = _ts_text(keyword, content) if keyword is not None and not keyword.is_named else ""
    body = value.child_by_field_name("body") if value is not None else None
    end = body.start_byte if body is not None else declarator.end_byte
    tail = _ws_collapse(
        content[declarator.start_byte:end].decode("utf-8", "replace")).strip()
    signature = f"{kw} {tail}".strip()
    if signature:
        fields["signature"] = signature
    return fields


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


# ---------------------------------------------------------------------------
# Rust signature extraction (canonical fields — see signatures.py)
# ---------------------------------------------------------------------------


def _rust_params(params_node, content: bytes) -> list[dict]:
    """Flatten a ``parameters`` node. Self params keep their exact form
    (``&self`` / ``&mut self``) with type None unless explicitly typed."""
    out: list[dict] = []
    for p in params_node.children:
        if not p.is_named:
            continue
        if p.type == "self_parameter":
            text = _ws_collapse(_ts_text(p, content))
            name, _, ptype = text.partition(":")
            out.append({"name": name.strip(),
                        "type": ptype.strip() or None, "default": None})
        elif p.type == "parameter":
            pattern = p.child_by_field_name("pattern")
            ptype = p.child_by_field_name("type")
            if pattern is None:
                continue
            out.append({
                "name": _ws_collapse(_ts_text(pattern, content)),
                "type": _ws_collapse(_ts_text(ptype, content)) if ptype is not None else None,
                "default": None,
            })
        elif p.type == "variadic_parameter":
            out.append({"name": "...", "type": None, "default": None})
    return out


def _rust_attributes(node, content: bytes) -> list[str]:
    """Preceding ``attribute_item`` siblings, with the ``#[`` ``]`` sigil
    stripped (attributes are separate sibling nodes in tree-sitter-rust)."""
    out: list[str] = []
    sib = node.prev_named_sibling
    while sib is not None and sib.type == "attribute_item":
        text = _ws_collapse(_ts_text(sib, content))
        if text.startswith("#[") and text.endswith("]"):
            text = text[2:-1]
        out.insert(0, text)
        sib = sib.prev_named_sibling
    return out


def _rust_signature_fields(node, content: bytes,
                           bases: list[str] | None = None) -> dict:
    """Canonical fields for a Rust item node (function / struct / enum /
    union / trait / impl / function_signature_item)."""
    fields: dict = {}
    for c in node.children:
        if c.type == "visibility_modifier":
            fields["visibility"] = _ws_collapse(_ts_text(c, content))
        elif c.type == "function_modifiers":
            if "async" in _ts_text(c, content).split():
                fields["is_async"] = True
        elif not c.is_named and c.type == "async":
            fields["is_async"] = True
    tp = node.child_by_field_name("type_parameters")
    if tp is not None:
        type_params = [_ws_collapse(_ts_text(c, content))
                       for c in tp.children if c.is_named]
        if type_params:
            fields["type_params"] = type_params
    params_node = node.child_by_field_name("parameters")
    if params_node is not None:
        params = _rust_params(params_node, content)
        if params:
            fields["params"] = params
    rt = node.child_by_field_name("return_type")
    if rt is not None:
        fields["returns"] = _ws_collapse(_ts_text(rt, content))
    if bases:
        fields["bases"] = list(bases)
    decorators = _rust_attributes(node, content)
    if decorators:
        fields["decorators"] = decorators
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else node.end_byte
    signature = _ws_collapse(
        content[node.start_byte:end].decode("utf-8", "replace")
    ).rstrip(";").strip()
    if signature:
        fields["signature"] = signature
    return fields


def _rust_trait_bases(node, content: bytes) -> list[str]:
    """Supertraits from a trait_item's ``bounds`` (``: Draw + Resize``)."""
    bounds = node.child_by_field_name("bounds")
    if bounds is None:
        return []
    return [_ws_collapse(_ts_text(c, content))
            for c in bounds.children if c.is_named]


def _rust_impl_bases(node, content: bytes) -> list[str]:
    """For ``impl Trait for Type``, the implemented trait; empty for
    inherent impls."""
    trait = node.child_by_field_name("trait")
    return [_ws_collapse(_ts_text(trait, content))] if trait is not None else []


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
                chunks.append(_chunk_from_ts_node(
                    node, "function", None, name, content,
                    _rust_signature_fields(node, content)))
        elif t in ("struct_item", "enum_item", "union_item"):
            name = _rust_chunk_name(node, content)
            if name:
                chunks.append(_chunk_from_ts_node(
                    node, "class", None, name, content,
                    _rust_signature_fields(node, content)))
        elif t == "trait_item":
            name = _rust_chunk_name(node, content)
            if not name:
                continue
            chunks.append(_chunk_from_ts_node(
                node, "class", None, name, content,
                _rust_signature_fields(node, content,
                                       _rust_trait_bases(node, content))))
            body = _rust_decl_list(node)
            if body is not None:
                for m in body.children:
                    if m.is_named and m.type in ("function_item", "function_signature_item"):
                        m_name = _rust_chunk_name(m, content)
                        if m_name:
                            chunks.append(_chunk_from_ts_node(
                                m, "method", name, m_name, content,
                                _rust_signature_fields(m, content)))
        elif t == "impl_item":
            impl_name = _rust_chunk_name(node, content)
            if not impl_name:
                continue
            chunks.append(_chunk_from_ts_node(
                node, "class", None, impl_name, content,
                _rust_signature_fields(node, content,
                                       _rust_impl_bases(node, content))))
            body = _rust_decl_list(node)
            if body is not None:
                for m in body.children:
                    if m.is_named and m.type == "function_item":
                        m_name = _rust_chunk_name(m, content)
                        if m_name:
                            chunks.append(_chunk_from_ts_node(
                                m, "method", impl_name, m_name, content,
                                _rust_signature_fields(m, content)))

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
        chunks.append(apply_signature_fields({
            "kind": _DART_TO_CHUNK_KIND.get(kind, "method"),
            "symbol": _dart_symbol_for(item),
            "parent_symbol": item.get("parent"),
            "byte_start": byte_start,
            "byte_end": byte_end,
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------


# SQL object kinds → canonical chunk kind (CHUNK_KINDS = class/function/
# method/file). Structural objects map to "class"; executable objects
# (functions, procedures, triggers) map to "function".
_SQL_TO_CHUNK_KIND = {
    "table": "class", "view": "class", "materialized_view": "class",
    "type": "class", "schema": "class", "sequence": "class", "index": "class",
    "function": "function", "procedure": "function", "trigger": "function",
}
_SQL_CHUNKABLE_KINDS = frozenset(_SQL_TO_CHUNK_KIND)


def _chunk_sql(content: bytes, record: FileRecord) -> list[dict]:
    """Build per-object chunks from the SQL analyzer's ``items`` array.

    Same architecture as ``_chunk_dart``: the L1 analyzer is the single
    source of declarations; this consumer only maps them to chunk records.
    Falls back to a whole-file chunk when no objects were extracted.
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
        if kind not in _SQL_CHUNKABLE_KINDS:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append(apply_signature_fields({
            "kind": _SQL_TO_CHUNK_KIND.get(kind, "class"),
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


# Structural HTML elements all map to the "class" chunk kind (CHUNK_KINDS =
# class/function/method/file); they are containers, not callables.
_HTML_CHUNKABLE_KINDS = frozenset({"element"})


def _chunk_html(content: bytes, record: FileRecord) -> list[dict]:
    """Per-element chunks from the HTML analyzer's ``items`` array (nested
    elements yield nested chunks, like class/method spans elsewhere)."""
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
        if item.get("kind") not in _HTML_CHUNKABLE_KINDS:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append(apply_signature_fields({
            "kind": "class",
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# CSS / SCSS
# ---------------------------------------------------------------------------


# Rules and at-rule blocks map to "class" (structural); an SCSS @function maps
# to "function" (CHUNK_KINDS = class/function/method/file).
_CSS_TO_CHUNK_KIND = {
    "rule": "class", "media": "class", "keyframes": "class",
    "font_face": "class", "supports": "class", "at_rule": "class",
    "mixin": "class", "function": "function",
}
_CSS_CHUNKABLE_KINDS = frozenset(_CSS_TO_CHUNK_KIND)


def _chunk_css(content: bytes, record: FileRecord) -> list[dict]:
    """Per-rule chunks from the CSS/SCSS analyzer's ``items`` array."""
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
        if kind not in _CSS_CHUNKABLE_KINDS:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append(apply_signature_fields({
            "kind": _CSS_TO_CHUNK_KIND.get(kind, "class"),
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# JSON / YAML (structural key members)
# ---------------------------------------------------------------------------


# JSON object members and YAML mapping keys are structural nodes → "class"
# (CHUNK_KINDS = class/function/method/file). The JSON and YAML analyzers emit
# the same {kind:"member", name, parent, spans} item shape, so one chunker
# serves both.
_MEMBER_CHUNKABLE_KINDS = frozenset({"member"})


def _chunk_key_members(content: bytes, record: FileRecord) -> list[dict]:
    """Per-key chunks from the JSON/YAML analyzer's ``items`` array."""
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
        if item.get("kind") not in _MEMBER_CHUNKABLE_KINDS:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append(apply_signature_fields({
            "kind": "class",
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

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
        chunks.append(apply_signature_fields({
            "kind": _JAVA_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

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
        chunks.append(apply_signature_fields({
            "kind": _GO_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# Clojure
# ---------------------------------------------------------------------------


_CLOJURE_TO_CHUNK_KIND = {
    "function": "function",
    "var": "var",
    "record": "class",
    "type": "class",
    "protocol": "class",
}


def _chunk_clojure(content: bytes, record: FileRecord) -> list[dict]:
    """Per-symbol chunks from the Clojure analyzer's ``items`` array — one chunk
    per top-level defn/def/defrecord/deftype/defprotocol. The ``ns`` form is not
    chunked. Same items-based shape as ``_chunk_java``."""
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
        if kind not in _CLOJURE_TO_CHUNK_KIND:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append(apply_signature_fields({
            "kind": _CLOJURE_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# COBOL
# ---------------------------------------------------------------------------


_COBOL_TO_CHUNK_KIND = {
    "program": "class",
    "section": "method",
    "paragraph": "method",
}


def _chunk_cobol(content: bytes, record: FileRecord) -> list[dict]:
    """Per-symbol chunks from the COBOL analyzer's ``items`` array — one chunk
    per program (class-like) and one per PROCEDURE DIVISION section/paragraph
    (method-like, ``parent_symbol`` = the enclosing program). Same items-based
    shape as ``_chunk_go``/``_chunk_clojure``; falls back to a whole-file chunk
    when nothing structural is recovered (e.g. a data-only copybook)."""
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
        if kind not in _COBOL_TO_CHUNK_KIND:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append(apply_signature_fields({
            "kind": _COBOL_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# CFML (ColdFusion)
# ---------------------------------------------------------------------------


_CFML_TO_CHUNK_KIND = {
    "component": "class",
    "interface": "class",
    "function": "function",
    "method": "method",
}


def _chunk_cfml(content: bytes, record: FileRecord) -> list[dict]:
    """Per-symbol chunks from the CFML analyzer's ``items`` array — one chunk
    per component/interface (class-like) and one per function (``method``
    when it lives in a component, ``function`` when free-standing in a
    template or script). Same items-based shape as ``_chunk_cobol``; falls
    back to a whole-file chunk when nothing structural is recovered (e.g. a
    static HTML-only ``.cfm`` template)."""
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
        if kind not in _CFML_TO_CHUNK_KIND:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append(apply_signature_fields({
            "kind": _CFML_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

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
        chunks.append(apply_signature_fields({
            "kind": _CPP_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

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
        chunks.append(apply_signature_fields({
            "kind": _OBJC_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# Ruby
# ---------------------------------------------------------------------------


_RUBY_TO_CHUNK_KIND = {
    "method": "method",
    "class": "class",
}


def _chunk_ruby(content: bytes, record: FileRecord) -> list[dict]:
    """Per-symbol chunks from the Ruby analyzer's ``items`` array — one chunk
    per top-level/nested ``def``/``class``/``module``. Same items-based shape
    as ``_chunk_go``; the analyzer already normalizes ``singleton_method`` to
    chunk kind ``method`` and ``module`` to chunk kind ``class``."""
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
        if kind not in _RUBY_TO_CHUNK_KIND:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append(apply_signature_fields({
            "kind": _RUBY_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# C
# ---------------------------------------------------------------------------


_C_TO_CHUNK_KIND = {
    "function": "function",
    "struct": "class",
    "union": "class",
    "enum": "class",
    "typedef": "class",
}


def _chunk_c(content: bytes, record: FileRecord) -> list[dict]:
    """Per-symbol chunks from the C analyzer's ``items`` array — one chunk
    per top-level function (definition or prototype), named struct/union/enum,
    and typedef. Same items-based shape as ``_chunk_go``."""
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
        if kind not in _C_TO_CHUNK_KIND:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append(apply_signature_fields({
            "kind": _C_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# Kotlin
# ---------------------------------------------------------------------------


_KOTLIN_TO_CHUNK_KIND = {
    "function": "function",
    "method": "method",
    "class": "class",
}


def _chunk_kotlin(content: bytes, record: FileRecord) -> list[dict]:
    """Per-symbol chunks from the Kotlin analyzer's ``items`` array — one
    chunk per top-level/member ``fun`` and ``class``/``interface``/``object``.
    Same items-based shape as ``_chunk_go``."""
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
        if kind not in _KOTLIN_TO_CHUNK_KIND:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append(apply_signature_fields({
            "kind": _KOTLIN_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks


# ---------------------------------------------------------------------------
# Swift
# ---------------------------------------------------------------------------


_SWIFT_TO_CHUNK_KIND = {
    "function": "function",
    "method": "method",
    "class": "class",
}


def _chunk_swift(content: bytes, record: FileRecord) -> list[dict]:
    """Per-symbol chunks from the Swift analyzer's ``items`` array — one
    chunk per top-level/member ``func`` and ``class``/``struct``/``enum``/
    ``protocol``. Same items-based shape as ``_chunk_go``."""
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
        if kind not in _SWIFT_TO_CHUNK_KIND:
            continue
        line_start = item["line_start"]
        line_end = item["line_end"]
        if line_end > n_lines:
            line_end = n_lines
        if line_start < 1 or line_start > n_lines:
            continue
        chunk_text = "".join(src_lines[line_start - 1: line_end])
        chunk_bytes = chunk_text.encode("utf-8")
        chunks.append(apply_signature_fields({
            "kind": _SWIFT_TO_CHUNK_KIND[kind],
            "symbol": item["name"],
            "parent_symbol": item.get("parent"),
            "byte_start": item["byte_start"],
            "byte_end": item["byte_end"],
            "line_start": line_start,
            "line_end": line_end,
            "text": chunk_text,
            "content_sha256": hashlib.sha256(chunk_bytes).hexdigest(),
        }, signature_fields_from_item(item)))

    if not chunks:
        return _whole_file_chunk(content, record.path)
    return chunks
