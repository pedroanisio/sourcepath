"""ChunkExtractor — RecordEnricher that splits source files into chunks.

Strategy:
  - Python: re-parse with `ast`, emit one chunk per top-level FunctionDef /
    AsyncFunctionDef / ClassDef, and one chunk per method inside classes
    (one nesting level). Bytes ranges come from `ast.get_source_segment`-
    equivalent line-based slicing.
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
