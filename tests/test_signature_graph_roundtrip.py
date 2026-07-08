"""TDD spec — signature fields flow chunk → RDF graph → SHACL → serving projection.

Three contracts (Tier 2, delivery 3):

  1. ``ChunkGraphWriter`` emits one ``cbml2:`` property per present signature
     field (params as a JSON literal); absent fields emit no triple.
  2. ``ChunkShapes`` declares a shape for every new predicate, and the emitted
     graph conforms (mirrors verify_l2's mutation-suite discipline).
  3. Both serving projections (JSON-LD fast path and rdflib fallback) surface
     the fields — plus the previously-dropped ``parentSymbol`` — identically
     on the chunk records consumed by the decomposer.

Run: python -m pytest tests/test_signature_graph_roundtrip.py
"""
from __future__ import annotations

import json

import pytest
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from codebase_mapper.shared_kernel.constants import CBM_NS, CBMI_NS
from codebase_mapper.shared_kernel.extensions import PipelineCtx
from plugins.chunks_embeddings.graph_writer import (
    CBML2_NS,
    ChunkGraphWriter,
    ChunkShapes,
    chunk_iri,
)

CBML2 = Namespace(CBML2_NS)
CBM = Namespace(CBM_NS)

CHUNK = {
    "chunk_id": "src/repo.py#L1-L4:class:Repo:0:120",
    "path": "src/repo.py",
    "kind": "class",
    "symbol": "Repo",
    "parent_symbol": None,
    "byte_start": 0,
    "byte_end": 120,
    "line_start": 1,
    "line_end": 4,
    "content_sha256": "ab" * 32,
    # signature fields under test
    "signature": "class Repo(Base, Generic[T])",
    "bases": ["Base", "Generic[T]"],
    "type_params": ["T"],
    "decorators": ["register"],
}
METHOD_CHUNK = {
    "chunk_id": "src/repo.py#L2-L3:method:get:20:80",
    "path": "src/repo.py",
    "kind": "method",
    "symbol": "get",
    "parent_symbol": "Repo",
    "byte_start": 20,
    "byte_end": 80,
    "line_start": 2,
    "line_end": 3,
    "content_sha256": "cd" * 32,
    "signature": "async def get(self, key: str) -> T | None",
    "params": [
        {"name": "self", "type": None, "default": None},
        {"name": "key", "type": "str", "default": None},
    ],
    "returns": "T | None",
    "is_async": True,
    "visibility": None,          # never emitted for Python
}
PLAIN_CHUNK = {
    "chunk_id": "src/repo.py#L6-L6:function:noop:130:150",
    "path": "src/repo.py",
    "kind": "function",
    "symbol": "noop",
    "parent_symbol": None,
    "byte_start": 130,
    "byte_end": 150,
    "line_start": 6,
    "line_end": 6,
    "content_sha256": "ef" * 32,
}


def _ctx() -> PipelineCtx:
    from pathlib import Path
    return PipelineCtx(
        repo=Path("."), commit="0" * 40, records=[], blob_by_path={},
        mode_by_path={}, paths_set=set(), read_path=lambda p: b"",
    )


def _emit() -> Graph:
    g = Graph()
    ctx = _ctx()
    ctx.indices["l2_10_chunks"] = [CHUNK, METHOD_CHUNK, PLAIN_CHUNK]
    ctx.indices["l2_20_embeddings"] = {}
    # the writer links chunks to their file node; add the file so SHACL's
    # class constraint on cbml2:inFile has its target
    from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import file_iri
    f = file_iri("src/repo.py")
    g.add((f, RDF.type, CBM.File))
    g.add((f, CBM.path, Literal("src/repo.py")))
    ChunkGraphWriter().contribute(g, ctx)
    return g


# ---------------------------------------------------------------------------
# 1. emission
# ---------------------------------------------------------------------------
def test_signature_fields_emitted_as_cbml2_properties():
    g = _emit()
    cls = chunk_iri(CHUNK["chunk_id"])
    m = chunk_iri(METHOD_CHUNK["chunk_id"])
    assert (cls, CBML2.signature, Literal("class Repo(Base, Generic[T])")) in g
    assert set(g.objects(cls, CBML2.baseType)) == {Literal("Base"), Literal("Generic[T]")}
    assert (cls, CBML2.typeParam, Literal("T")) in g
    assert (cls, CBML2.decorator, Literal("register")) in g
    assert (m, CBML2.returnsType, Literal("T | None")) in g
    assert (m, CBML2.isAsync, Literal(True, datatype=XSD.boolean)) in g
    params = list(g.objects(m, CBML2.paramsJson))
    assert len(params) == 1
    assert json.loads(str(params[0])) == METHOD_CHUNK["params"]


def test_absent_fields_emit_no_triples():
    g = _emit()
    plain = chunk_iri(PLAIN_CHUNK["chunk_id"])
    m = chunk_iri(METHOD_CHUNK["chunk_id"])
    for pred in ("signature", "paramsJson", "returnsType", "baseType",
                 "typeParam", "visibility", "isAsync", "decorator"):
        assert list(g.objects(plain, CBML2[pred])) == []
    # visibility None on the method chunk must not become a triple
    assert list(g.objects(m, CBML2.visibility)) == []


# ---------------------------------------------------------------------------
# 2. shapes
# ---------------------------------------------------------------------------
def test_shapes_cover_every_new_predicate():
    shapes = Graph()
    ChunkShapes().contribute(shapes)
    SH = Namespace("http://www.w3.org/ns/shacl#")
    covered = {str(o) for o in shapes.objects(None, SH.path)}
    for pred in ("signature", "paramsJson", "returnsType", "baseType",
                 "typeParam", "visibility", "isAsync", "decorator"):
        assert f"{CBML2_NS}{pred}" in covered, f"no shape for cbml2:{pred}"


def test_emitted_graph_conforms_to_shapes():
    pyshacl = pytest.importorskip("pyshacl")
    g = _emit()
    shapes = Graph()
    ChunkShapes().contribute(shapes)
    conforms, _, report = pyshacl.validate(
        g, shacl_graph=shapes, inference="none", abort_on_first=False)
    assert conforms, report


# ---------------------------------------------------------------------------
# 3. serving projection (both parser paths, identical output)
# ---------------------------------------------------------------------------
def _projected_chunks_via_rdflib(tmp_path):
    from frontend.backend.serving.application.bundle_data import _project_from_rdflib
    g = _emit()
    ttl = tmp_path / "inventory.ttl"
    g.serialize(destination=str(ttl), format="turtle")
    projection = _project_from_rdflib(ttl)
    return projection[7]  # chunks list position in the Projection tuple


def _projected_chunks_via_jsonld(tmp_path):
    from frontend.backend.serving.application.bundle_data import _project_from_jsonld
    g = _emit()
    doc = json.loads(g.serialize(format="json-ld", auto_compact=True,
                                 context={"cbm": CBM_NS, "cbml2": CBML2_NS,
                                          "cbmi": CBMI_NS}))
    if "@graph" not in doc:
        doc = {"@context": doc.get("@context", {}), "@graph": [
            {k: v for k, v in doc.items() if k != "@context"}]}
    path = tmp_path / "inventory.jsonld"
    path.write_text(json.dumps(doc))
    projection = _project_from_jsonld(path)
    return projection[7]


@pytest.mark.parametrize("project", [_projected_chunks_via_rdflib,
                                     _projected_chunks_via_jsonld])
def test_projection_surfaces_signature_fields_and_parent(project, tmp_path):
    chunks = project(tmp_path)
    by = {c["symbol"]: c for c in chunks}
    m = by["get"]
    assert m["parentSymbol"] == "Repo"
    assert m["signature"] == "async def get(self, key: str) -> T | None"
    assert m["returns"] == "T | None"
    assert m["isAsync"] is True
    assert m["params"] == METHOD_CHUNK["params"]
    cls = by["Repo"]
    assert sorted(cls["bases"]) == ["Base", "Generic[T]"]
    assert cls["typeParams"] == ["T"]
    assert cls["decorators"] == ["register"]
    # absence contract survives projection
    plain = by["noop"]
    for absent in ("signature", "params", "returns", "bases", "typeParams",
                   "visibility", "isAsync", "decorators"):
        assert plain.get(absent) in (None, [],), \
            f"{absent} must be absent/None on plain chunks"
