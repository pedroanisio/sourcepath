"""TDD spec — items→chunk signature copy-through (Tier 2, delivery 3).

The six items-based chunkers (dart/java/go/clojure/cpp/objc) must copy the
canonical signature fields (plugins/chunks_embeddings/signatures.py) from
``record.ast_summary["items"]`` onto the chunks they emit. Analyzers that
don't produce the fields yet yield chunks without them — graceful absence,
never placeholder values.

Run: python -m pytest tests/test_signature_copythrough.py
"""
from __future__ import annotations

from codebase_mapper.inspection.models import FileRecord
from plugins.chunks_embeddings.chunker import _chunk_go, _chunk_java


def _record(path: str, language: str, items: list[dict]) -> FileRecord:
    return FileRecord(
        path=path,
        git_blob_sha="0" * 40,
        content_sha256="0" * 64,
        size_bytes=0,
        language=language,
        type_="source_code",
        phases=["runtime"],
        ast_summary={"items": items},
    )


JAVA_SRC = (
    "public class Repo extends Base implements Store {\n"
    "    public String get(String key) { return null; }\n"
    "}\n"
).encode()


def test_java_item_signature_fields_copied_to_chunk():
    items = [
        {
            "kind": "class", "name": "Repo", "parent": None,
            "line_start": 1, "line_end": 3, "byte_start": 0,
            "byte_end": len(JAVA_SRC),
            "signature": "public class Repo extends Base implements Store",
            "bases": ["Base", "Store"],
            "visibility": "public",
        },
        {
            "kind": "method", "name": "get", "parent": "Repo",
            "line_start": 2, "line_end": 2, "byte_start": 54, "byte_end": 99,
            "signature": "public String get(String key)",
            "params": [{"name": "key", "type": "String", "default": None}],
            "returns": "String",
            "visibility": "public",
        },
    ]
    chunks = _chunk_java(JAVA_SRC, _record("Repo.java", "java", items))
    by = {c["symbol"]: c for c in chunks}
    assert by["Repo"]["bases"] == ["Base", "Store"]
    assert by["Repo"]["visibility"] == "public"
    assert by["Repo"]["signature"] == "public class Repo extends Base implements Store"
    assert by["get"]["params"] == [{"name": "key", "type": "String", "default": None}]
    assert by["get"]["returns"] == "String"


def test_items_without_signature_fields_yield_chunks_without_them():
    items = [{
        "kind": "class", "name": "Plain", "parent": None,
        "line_start": 1, "line_end": 3, "byte_start": 0,
        "byte_end": len(JAVA_SRC),
    }]
    chunks = _chunk_java(JAVA_SRC, _record("Plain.java", "java", items))
    (c,) = chunks
    for absent in ("signature", "params", "returns", "bases", "type_params",
                   "visibility", "is_async", "decorators"):
        assert absent not in c, f"{absent} must be absent, not a placeholder"


GO_SRC = (
    "package p\n"
    "func (s *Server) Serve(addr string) error { return nil }\n"
).encode()


def test_go_item_signature_fields_copied_to_chunk():
    items = [{
        "kind": "method", "name": "Serve", "parent": "Server",
        "line_start": 2, "line_end": 2, "byte_start": 10,
        "byte_end": len(GO_SRC) - 1,
        "signature": "func (s *Server) Serve(addr string) error",
        "params": [{"name": "addr", "type": "string", "default": None}],
        "returns": "error",
    }]
    chunks = _chunk_go(GO_SRC, _record("srv.go", "go", items))
    (c,) = chunks
    assert c["signature"] == "func (s *Server) Serve(addr string) error"
    assert c["returns"] == "error"
    assert c["parent_symbol"] == "Server"
