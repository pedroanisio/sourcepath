"""BL-005 — generalization vs realization must survive into the graph.

Java/C++/ObjC analyzers extract ``extends`` and ``implements`` as separate
item fields, but the canonical L2 copy-through (SIGNATURE_FIELDS) carried
only the merged ``bases``, and the TS/JS chunker folded both heritage
clauses into one list — so UML's generalization (solid triangle) vs
realization (dashed triangle) distinction was parsed and then destroyed at
the chunk boundary.

Contract under test:
  1. ``SIGNATURE_FIELDS`` includes ``extends``/``implements``; the
     items-based copy-through propagates them.
  2. The TS/JS chunker splits the heritage clauses into ``extends`` /
     ``implements`` while keeping the merged ``bases`` for back-compat.
  3. ``ChunkGraphWriter`` emits ``cbml2:extendsType`` / ``cbml2:implementsType``
     (repeated, as written; omission contract), alongside the unchanged
     ``cbml2:baseType``.
  4. ``ChunkShapes`` covers both new predicates and the emitted graph
     conforms.

Run: uv run python -m pytest tests/test_heritage_split.py
"""
from __future__ import annotations

import pytest
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from codebase_mapper.ts_setup import TS_AVAILABLE
from plugins.chunks_embeddings.signatures import (
    SIGNATURE_FIELDS,
    signature_fields_from_item,
)
from plugins.chunks_embeddings.graph_writer import (
    CBML2_NS,
    ChunkGraphWriter,
    ChunkShapes,
    chunk_iri,
)

CBML2 = Namespace(CBML2_NS)
CBM = Namespace("https://codebase-mapper.example.org/cbm#")


# ---------------------------------------------------------------------------
# 1. contract + items-based copy-through (the Java/C++/ObjC path)
# ---------------------------------------------------------------------------
def test_signature_fields_include_heritage_split():
    assert "extends" in SIGNATURE_FIELDS
    assert "implements" in SIGNATURE_FIELDS


def test_items_copy_through_propagates_extends_and_implements():
    item = {
        "kind": "class", "name": "X",
        "bases": ["A", "B", "C"],
        "extends": ["A"],
        "implements": ["B", "C"],
    }
    fields = signature_fields_from_item(item)
    assert fields.get("extends") == ["A"]
    assert fields.get("implements") == ["B", "C"]
    assert fields.get("bases") == ["A", "B", "C"]


def test_items_copy_through_omits_absent_heritage():
    fields = signature_fields_from_item({"kind": "class", "name": "X",
                                         "bases": ["A"]})
    assert "extends" not in fields
    assert "implements" not in fields


# ---------------------------------------------------------------------------
# 2. TS/JS chunker splits the heritage clauses
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")
def test_tsjs_class_heritage_is_split():
    from plugins.chunks_embeddings.chunker import _chunk_tsjs
    src = b"class Repo<T> extends Base<T> implements Store, Closeable {}\n"
    chunks = _chunk_tsjs(src, "repo.ts")
    cls = next(c for c in chunks if c["kind"] == "class")
    assert cls.get("extends") == ["Base<T>"]
    assert cls.get("implements") == ["Store", "Closeable"]
    # merged view stays for back-compat
    assert cls.get("bases") == ["Base<T>", "Store", "Closeable"]


@pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")
def test_js_expression_heritage_stays_bases_only():
    from plugins.chunks_embeddings.chunker import _chunk_tsjs
    src = b"class Sub extends mixin(Base) {}\n"
    chunks = _chunk_tsjs(src, "sub.js")
    cls = next(c for c in chunks if c["kind"] == "class")
    assert cls.get("extends") == ["mixin(Base)"]
    assert "implements" not in cls


# ---------------------------------------------------------------------------
# 3+4. writer emission + shapes
# ---------------------------------------------------------------------------
def _chunk(**over):
    base = {
        "chunk_id": "src/x.java#class:X:L1-L9:b0-200",
        "path": "src/x.java", "kind": "class", "symbol": "X",
        "parent_symbol": None, "byte_start": 0, "byte_end": 200,
        "line_start": 1, "line_end": 9, "content_sha256": "ab" * 32,
    }
    base.update(over)
    return base


def _emit(chunks) -> Graph:
    from pathlib import Path
    from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import file_iri
    g = Graph()
    ctx = PipelineCtx(repo=Path("."), commit="0" * 40, records=[],
                      blob_by_path={}, mode_by_path={}, paths_set=set(),
                      read_path=lambda p: b"")
    ctx.indices["l2_10_chunks"] = chunks
    ctx.indices["l2_20_embeddings"] = {}
    f = file_iri("src/x.java")
    g.add((f, RDF.type, CBM.File))
    ChunkGraphWriter().contribute(g, ctx)
    return g


def test_writer_emits_split_heritage_predicates():
    c = _chunk(bases=["A", "B", "C"], extends=["A"], implements=["B", "C"])
    g = _emit([c])
    iri = chunk_iri(c["chunk_id"])
    assert set(g.objects(iri, CBML2.extendsType)) == {Literal("A")}
    assert set(g.objects(iri, CBML2.implementsType)) == {Literal("B"), Literal("C")}
    # merged baseType unchanged
    assert set(g.objects(iri, CBML2.baseType)) == {Literal("A"), Literal("B"), Literal("C")}


def test_writer_omits_absent_heritage():
    c = _chunk(bases=["A"])
    g = _emit([c])
    iri = chunk_iri(c["chunk_id"])
    assert list(g.objects(iri, CBML2.extendsType)) == []
    assert list(g.objects(iri, CBML2.implementsType)) == []


def test_shapes_cover_heritage_predicates_and_conform():
    shapes = Graph()
    ChunkShapes().contribute(shapes)
    SH = Namespace("http://www.w3.org/ns/shacl#")
    covered = {str(o) for o in shapes.objects(None, SH.path)}
    assert f"{CBML2_NS}extendsType" in covered
    assert f"{CBML2_NS}implementsType" in covered

    pyshacl = pytest.importorskip("pyshacl")
    g = _emit([_chunk(bases=["A", "B"], extends=["A"], implements=["B"])])
    conforms, _, report = pyshacl.validate(
        g, shacl_graph=shapes, inference="none", abort_on_first=False)
    assert conforms, report
