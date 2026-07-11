"""BL-002 — member containment edges and qualified chunk identity.

Contract under test (L2 chunk layer):

  1. ``cbml2:memberOf`` — every method chunk whose parent class chunk exists
     in the same file gets exactly one edge to that class chunk's IRI.
     Resolution is mechanical: same file, ``symbol == parent_symbol``, and
     the class's byte span must enclose the method's byte span. When two
     same-named classes in one file both enclose the span (impossible for
     siblings, possible for a redefinition that nests), the tightest
     enclosing span wins. No candidate → no edge (omission contract, no
     guessing).
  2. ``cbml2:qualifiedSymbol`` — every symbol-level chunk (function/class/
     method) carries a file-local qualified name: ``Parent.symbol`` for
     members, ``symbol`` otherwise. File-kind chunks carry none (their
     symbol is a sentinel, not a declaration).
  3. Same-named classes in different files stay fully disjoint: distinct
     IRIs, distinct members, no cross-file memberOf edges.
  4. Shapes: ``ChunkShapes`` declares both predicates, and an emitted graph
     with the new triples conforms to the shapes.

Run: uv run python -m pytest tests/test_chunk_containment.py
"""
from __future__ import annotations

import pytest
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from plugins.chunks_embeddings.graph_writer import (
    CBML2_NS,
    ChunkGraphWriter,
    ChunkShapes,
    chunk_iri,
)

CBML2 = Namespace(CBML2_NS)
CBM = Namespace("https://codebase-mapper.example.org/cbm#")


def _chunk(path, kind, symbol, parent, byte_start, byte_end, line_start, line_end):
    return {
        "chunk_id": f"{path}#{kind}:{(parent + '.') if parent else ''}{symbol}"
                    f":L{line_start}-L{line_end}:b{byte_start}-{byte_end}",
        "path": path,
        "kind": kind,
        "symbol": symbol,
        "parent_symbol": parent,
        "byte_start": byte_start,
        "byte_end": byte_end,
        "line_start": line_start,
        "line_end": line_end,
        "content_sha256": "ab" * 32,
    }


# One file: a class with two methods, a top-level function, a whole-file
# chunk, and an orphan method whose parent class chunk does not exist.
CLS = _chunk("src/a.py", "class", "Repo", None, 0, 400, 1, 30)
METH_GET = _chunk("src/a.py", "method", "get", "Repo", 40, 200, 3, 12)
METH_PUT = _chunk("src/a.py", "method", "put", "Repo", 210, 390, 14, 28)
FUNC = _chunk("src/a.py", "function", "helper", None, 410, 500, 32, 40)
ORPHAN = _chunk("src/a.py", "method", "lost", "Ghost", 510, 560, 42, 45)
FILE_CHUNK = _chunk("src/a.py", "file", "<file>", None, 0, 600, 1, 50)

# A same-named class in a second file, with a same-named method.
CLS_B = _chunk("src/b.py", "class", "Repo", None, 0, 300, 1, 20)
METH_GET_B = _chunk("src/b.py", "method", "get", "Repo", 30, 280, 3, 18)

# Same file, two same-named classes, one nested inside the other's span —
# the tightest enclosing span must win.
OUTER = _chunk("src/c.py", "class", "Widget", None, 0, 500, 1, 40)
INNER = _chunk("src/c.py", "class", "Widget", None, 100, 300, 8, 24)
METH_NESTED = _chunk("src/c.py", "method", "render", "Widget", 150, 250, 12, 20)

ALL_CHUNKS = [CLS, METH_GET, METH_PUT, FUNC, ORPHAN, FILE_CHUNK,
              CLS_B, METH_GET_B, OUTER, INNER, METH_NESTED]


def _ctx() -> PipelineCtx:
    from pathlib import Path
    return PipelineCtx(
        repo=Path("."), commit="0" * 40, records=[], blob_by_path={},
        mode_by_path={}, paths_set=set(), read_path=lambda p: b"",
    )


def _emit() -> Graph:
    g = Graph()
    ctx = _ctx()
    ctx.indices["l2_10_chunks"] = list(ALL_CHUNKS)
    ctx.indices["l2_20_embeddings"] = {}
    from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import file_iri
    for path in ("src/a.py", "src/b.py", "src/c.py"):
        f = file_iri(path)
        g.add((f, RDF.type, CBM.File))
        g.add((f, CBM.path, Literal(path)))
    ChunkGraphWriter().contribute(g, ctx)
    return g


# ---------------------------------------------------------------------------
# 1. memberOf containment edges
# ---------------------------------------------------------------------------
def test_method_chunks_link_to_their_class_chunk():
    g = _emit()
    cls = chunk_iri(CLS["chunk_id"])
    assert (chunk_iri(METH_GET["chunk_id"]), CBML2.memberOf, cls) in g
    assert (chunk_iri(METH_PUT["chunk_id"]), CBML2.memberOf, cls) in g


def test_class_members_are_queryable_with_one_edge():
    g = _emit()
    members = set(g.subjects(CBML2.memberOf, chunk_iri(CLS["chunk_id"])))
    assert members == {chunk_iri(METH_GET["chunk_id"]),
                       chunk_iri(METH_PUT["chunk_id"])}


def test_top_level_and_file_chunks_get_no_member_of():
    g = _emit()
    for c in (FUNC, FILE_CHUNK, CLS, CLS_B, OUTER, INNER):
        assert list(g.objects(chunk_iri(c["chunk_id"]), CBML2.memberOf)) == []


def test_orphan_parent_symbol_emits_no_edge():
    """parent_symbol names a class with no chunk in the file → omission,
    never a dangling or guessed edge."""
    g = _emit()
    assert list(g.objects(chunk_iri(ORPHAN["chunk_id"]), CBML2.memberOf)) == []


def test_same_named_classes_in_different_files_stay_disjoint():
    g = _emit()
    a_members = set(g.subjects(CBML2.memberOf, chunk_iri(CLS["chunk_id"])))
    b_members = set(g.subjects(CBML2.memberOf, chunk_iri(CLS_B["chunk_id"])))
    assert chunk_iri(METH_GET_B["chunk_id"]) in b_members
    assert a_members.isdisjoint(b_members)


def test_tightest_enclosing_span_wins_for_duplicate_names():
    g = _emit()
    targets = list(g.objects(chunk_iri(METH_NESTED["chunk_id"]), CBML2.memberOf))
    assert targets == [chunk_iri(INNER["chunk_id"])]


def test_every_member_of_edge_is_unique():
    g = _emit()
    subjects = list(g.subjects(CBML2.memberOf, None))
    assert len(subjects) == len(set(subjects)), "a chunk carries >1 memberOf"


# ---------------------------------------------------------------------------
# 2. qualifiedSymbol
# ---------------------------------------------------------------------------
def test_qualified_symbol_for_members_and_top_level():
    g = _emit()
    assert (chunk_iri(METH_GET["chunk_id"]), CBML2.qualifiedSymbol,
            Literal("Repo.get")) in g
    assert (chunk_iri(CLS["chunk_id"]), CBML2.qualifiedSymbol,
            Literal("Repo")) in g
    assert (chunk_iri(FUNC["chunk_id"]), CBML2.qualifiedSymbol,
            Literal("helper")) in g
    # orphan still gets its declared qualification — the parent NAME is
    # source truth even when the parent chunk is missing
    assert (chunk_iri(ORPHAN["chunk_id"]), CBML2.qualifiedSymbol,
            Literal("Ghost.lost")) in g


def test_file_chunks_carry_no_qualified_symbol():
    g = _emit()
    assert list(g.objects(chunk_iri(FILE_CHUNK["chunk_id"]),
                          CBML2.qualifiedSymbol)) == []


# ---------------------------------------------------------------------------
# 3. shapes
# ---------------------------------------------------------------------------
def test_shapes_cover_new_predicates():
    shapes = Graph()
    ChunkShapes().contribute(shapes)
    SH = Namespace("http://www.w3.org/ns/shacl#")
    covered = {str(o) for o in shapes.objects(None, SH.path)}
    assert f"{CBML2_NS}memberOf" in covered
    assert f"{CBML2_NS}qualifiedSymbol" in covered


def test_emitted_graph_conforms_to_shapes():
    pyshacl = pytest.importorskip("pyshacl")
    g = _emit()
    shapes = Graph()
    ChunkShapes().contribute(shapes)
    conforms, _, report = pyshacl.validate(
        g, shacl_graph=shapes, inference="none", abort_on_first=False)
    assert conforms, report
