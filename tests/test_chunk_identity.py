"""Regression tests for chunk identity & span accuracy (defects D1/D2/D3).

D1 — byte-accurate chunk spans: a chunk's text/byte-span must reflect the
     parser node's real byte range, not the whole source line(s). Minified
     single-line files previously produced N byte-identical whole-file chunks.
D2 — injective chunk_id: the id embeds the byte span so two symbols sharing
     (kind, symbol, line range) but differing in bytes get distinct ids.
D3 — embedder dedup: truly-identical chunk_ids collapse to one embedding row,
     and every drop is logged (PALS's Law: no silent caps).

Root cause, traced from the ``octavia`` bundle: ``public/pdf.worker.min.mjs``
produced 2,690 byte-identical chunks (the whole 1.37 MB minified blob, sliced
by line), 4 of which collided on chunk_id and yielded a list-valued
``cbml2:embeddingRow`` that crashed every bundle-reading MCP tool.

Run from the repo root:  python -m pytest tests/test_chunk_identity.py
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import numpy as np
import pytest

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from codebase_mapper.ts_setup import TS_AVAILABLE
from plugins.chunks_embeddings.chunker import _chunk_python, _chunk_tsjs
from plugins.chunks_embeddings.embedder import EmbeddingComputer, _chunk_id


# ---------------------------------------------------------------------------
# D1 — byte-accurate spans (TypeScript / JavaScript)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")
def test_tsjs_minified_single_line_yields_distinct_chunks():
    """Two functions on ONE physical line (a minified file) must produce two
    chunks with *distinct* content — not two copies of the whole line."""
    src = b"function alpha(){return 1};function beta(){return 22}\n"
    chunks = _chunk_tsjs(src, "min.js")
    by_sym = {c["symbol"]: c for c in chunks}
    assert {"alpha", "beta"} <= set(by_sym), f"got symbols {set(by_sym)}"
    a, b = by_sym["alpha"], by_sym["beta"]

    # Core D1 guarantee: distinct content despite sharing line 1.
    assert a["content_sha256"] != b["content_sha256"]
    assert (a["byte_start"], a["byte_end"]) != (b["byte_start"], b["byte_end"])
    # Each chunk's text is its own symbol's source, not the whole line.
    assert a["text"] == "function alpha(){return 1}"
    assert b["text"] == "function beta(){return 22}"
    # Byte span round-trips against the raw content.
    assert src[a["byte_start"]:a["byte_end"]].decode() == a["text"]


# ---------------------------------------------------------------------------
# D1 — byte-accurate spans (Python)
# ---------------------------------------------------------------------------
def test_python_method_chunk_excludes_line_indentation():
    """A method chunk's text is the node's source, not the whole indented
    line(s) — so a single-line method does not capture leading whitespace."""
    src = b"class C:\n    def m(self): return 1\n"
    chunks = _chunk_python(src, "c.py")
    m = next(c for c in chunks if c["symbol"] == "m")
    assert m["text"] == "def m(self): return 1"
    assert not m["text"].startswith(" ")
    assert src[m["byte_start"]:m["byte_end"]].decode() == m["text"]


def test_python_decorator_is_included_in_chunk():
    """Decorators stay part of the chunk (unchanged behavior under D1)."""
    src = b"@deco\ndef f():\n    return 1\n"
    f = next(c for c in _chunk_python(src, "d.py") if c["symbol"] == "f")
    assert f["text"].startswith("@deco")
    assert "def f()" in f["text"]
    assert src[f["byte_start"]:f["byte_end"]].decode() == f["text"]


def test_python_byte_span_correct_with_multibyte_chars():
    """Byte offsets (ast col_offset is a UTF-8 byte offset) must be exact even
    when the source contains multibyte characters."""
    src = "def f():\n    s = 'ααα'\n    return s\n".encode("utf-8")
    f = next(c for c in _chunk_python(src, "u.py") if c["symbol"] == "f")
    assert "ααα" in f["text"]
    # content_sha256 is over the exact node bytes and round-trips with text.
    assert hashlib.sha256(f["text"].encode("utf-8")).hexdigest() == f["content_sha256"]
    assert src[f["byte_start"]:f["byte_end"]] == f["text"].encode("utf-8")


# ---------------------------------------------------------------------------
# D2 — injective chunk_id
# ---------------------------------------------------------------------------
def test_chunk_id_embeds_byte_span_for_injectivity():
    """Two chunks sharing (kind, symbol, line range) but differing in byte span
    must get distinct ids — otherwise they collide into a list-valued row."""
    base = dict(kind="method", symbol="m", parent_symbol="C",
                line_start=21, line_end=21)
    a = _chunk_id("f.js", {**base, "byte_start": 100, "byte_end": 150})
    b = _chunk_id("f.js", {**base, "byte_start": 200, "byte_end": 250})
    assert a != b
    # Human-readable prefix preserved; byte span present.
    assert a.startswith("f.js#method:C.m:L21-L21")
    assert "100" in a and "150" in a


# ---------------------------------------------------------------------------
# D3 — embedder dedups identical chunk_ids, logging every drop
# ---------------------------------------------------------------------------
class _StubBackend:
    name = "stub"
    dimension = 4
    normalized = True

    def encode(self, texts: list[str]) -> np.ndarray:
        v = np.ones((len(texts), self.dimension), dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def _ctx_with_chunks(chunks_by_path: dict) -> PipelineCtx:
    return PipelineCtx(
        repo=Path("/x"), commit="0" * 40, records=[], blob_by_path={},
        mode_by_path={}, paths_set=set(), read_path=lambda _p: b"",
        scratch={"chunks": chunks_by_path},
    )


def test_embedder_dedups_identical_chunk_ids_and_logs(caplog):
    chunk = dict(kind="method", symbol="m", parent_symbol="C",
                 line_start=21, line_end=21, byte_start=100, byte_end=150,
                 text="x", content_sha256="a" * 64)
    ctx = _ctx_with_chunks({"f.js": [dict(chunk), dict(chunk)]})

    with caplog.at_level(logging.WARNING):
        EmbeddingComputer(_StubBackend()).run(ctx)

    rows = ctx.indices["l2_10_chunks"]
    ids = ctx.indices["l2_20_embeddings"]["row_to_chunk_id"]
    assert len(rows) == 1, "identical duplicate chunk must collapse to one row"
    assert len(ids) == 1
    assert len(set(ids)) == len(ids), "every embedding row id must be unique"
    # PALS's Law: a dropped chunk is logged, never silent.
    assert any("duplicate" in r.getMessage().lower() for r in caplog.records)


def test_embedder_keeps_distinct_chunks_on_same_line():
    """Distinct byte spans on the same line are kept (D1+D2 together): no
    false dedup."""
    common = dict(kind="function", parent_symbol=None,
                  line_start=1, line_end=1, text="x", content_sha256="b" * 64)
    a = dict(common, symbol="alpha", byte_start=0, byte_end=26)
    b = dict(common, symbol="beta", byte_start=27, byte_end=53)
    ctx = _ctx_with_chunks({"min.js": [a, b]})
    EmbeddingComputer(_StubBackend()).run(ctx)
    ids = ctx.indices["l2_20_embeddings"]["row_to_chunk_id"]
    assert len(ids) == 2
    assert len(set(ids)) == 2
