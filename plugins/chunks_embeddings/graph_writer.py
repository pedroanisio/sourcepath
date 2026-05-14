"""ChunkGraphWriter and ChunkShapes — emit RDF triples and SHACL shapes for
the L2 chunk layer.

Triple shape (one chunk):
    cbmi:chunk/<safe_id> a cbml2:Chunk, nif:Context ;
        cbml2:inFile cbmi:file/<file_safe> ;
        cbml2:kind "function" ;
        cbml2:symbol "calculate_score" ;
        cbml2:parentSymbol "UserService" ;   # optional
        nif:beginIndex 1024 ;
        nif:endIndex 1456 ;
        cbml2:beginLine 42 ;
        cbml2:endLine 58 ;
        cbml2:contentSha256 "..."^^xsd:hexBinary ;
        cbml2:embeddingRow 42 ;
        cbml2:embeddingArtifact "embeddings.npz" .

The embedding row index and artifact filename let downstream readers find
the vector. The artifact filename is a relative pointer — the artifact
emitter is responsible for actually writing the file at that path.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import cast

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from codebase_mapper.shared_kernel.constants import CBM_NS, CBMI_NS
from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import file_iri

from codebase_mapper.shared_kernel.extensions import PipelineCtx


CBML2_NS = "https://codebase-mapper.example.org/cbml2#"
NIF_NS = "http://persistence.uni-leipzig.org/nlp2rdf/ontologies/nif-core#"
SH_NS = "http://www.w3.org/ns/shacl#"

CBML2 = Namespace(CBML2_NS)
NIF = Namespace(NIF_NS)
SH = Namespace(SH_NS)
CBM = Namespace(CBM_NS)

CHUNK_KINDS = ("function", "class", "method", "file")
EMBEDDINGS_ARTIFACT_FILENAME = "embeddings.npz"


def chunk_iri(chunk_id: str) -> URIRef:
    # chunk_id contains '#', ':', '/' — quote them all.
    safe = urllib.parse.quote(chunk_id, safe="")
    return URIRef(f"{CBMI_NS}chunk/{safe}")


class ChunkGraphWriter:
    name = "l2_30_graph"

    def contribute(self, g: Graph, ctx: PipelineCtx) -> None:
        g.bind("cbml2", CBML2)
        g.bind("nif", NIF)
        chunks = cast(list, ctx.indices.get("l2_10_chunks", []))
        embeddings_meta = cast(dict, ctx.indices.get("l2_20_embeddings", {}))
        has_embeddings = bool(embeddings_meta.get("row_to_chunk_id"))

        for c in chunks:
            ciri = chunk_iri(c["chunk_id"])
            g.add((ciri, RDF.type, CBML2.Chunk))
            g.add((ciri, RDF.type, NIF.Context))
            g.add((ciri, CBML2.inFile, file_iri(c["path"])))
            g.add((ciri, CBML2.kind, Literal(c["kind"])))
            g.add((ciri, CBML2.symbol, Literal(c["symbol"])))
            if c.get("parent_symbol"):
                g.add((ciri, CBML2.parentSymbol, Literal(c["parent_symbol"])))
            g.add((ciri, NIF.beginIndex,
                   Literal(c["byte_start"], datatype=XSD.integer)))
            g.add((ciri, NIF.endIndex,
                   Literal(c["byte_end"], datatype=XSD.integer)))
            g.add((ciri, CBML2.beginLine,
                   Literal(c["line_start"], datatype=XSD.integer)))
            g.add((ciri, CBML2.endLine,
                   Literal(c["line_end"], datatype=XSD.integer)))
            g.add((ciri, CBML2.contentSha256,
                   Literal(c["content_sha256"], datatype=XSD.hexBinary)))
            if c.get("truncated_for_embedding"):
                g.add((ciri, CBML2.truncatedForEmbedding,
                       Literal(True, datatype=XSD.boolean)))
            if has_embeddings:
                g.add((ciri, CBML2.embeddingRow,
                       Literal(c["row"], datatype=XSD.integer)))
                g.add((ciri, CBML2.embeddingArtifact,
                       Literal(EMBEDDINGS_ARTIFACT_FILENAME)))


class ChunkShapes:
    name = "l2_30_shapes"

    def contribute(self, shapes: Graph) -> None:
        shapes.bind("cbml2", CBML2)
        shapes.bind("nif", NIF)
        shapes.bind("sh", SH)

        from rdflib.collection import Collection
        chunk_shape = URIRef(f"{CBML2_NS}ChunkShape")
        shapes.add((chunk_shape, RDF.type, SH.NodeShape))
        shapes.add((chunk_shape, SH.targetClass, CBML2.Chunk))

        # inFile -> cbm:File, exactly one
        _add_prop(shapes, chunk_shape, CBML2.inFile, klass=CBM.File, min_count=1, max_count=1)
        # kind in enum
        kinds_list = URIRef(f"{CBML2_NS}_kindList")
        Collection(shapes, kinds_list, [Literal(k) for k in CHUNK_KINDS])
        kind_prop = URIRef(f"{CBML2_NS}_kindProp")
        shapes.add((chunk_shape, SH.property, kind_prop))
        shapes.add((kind_prop, SH.path, CBML2.kind))
        shapes.add((kind_prop, SH.minCount, Literal(1)))
        shapes.add((kind_prop, SH.maxCount, Literal(1)))
        shapes.add((kind_prop, URIRef(SH_NS + "in"), kinds_list))

        _add_prop(shapes, chunk_shape, CBML2.symbol,
                  datatype=XSD.string, min_count=1, max_count=1)
        _add_prop(shapes, chunk_shape, CBML2.parentSymbol,
                  datatype=XSD.string, max_count=1)
        _add_prop(shapes, chunk_shape, NIF.beginIndex,
                  datatype=XSD.integer, min_count=1, max_count=1, min_inclusive=0)
        _add_prop(shapes, chunk_shape, NIF.endIndex,
                  datatype=XSD.integer, min_count=1, max_count=1, min_inclusive=0)
        _add_prop(shapes, chunk_shape, CBML2.beginLine,
                  datatype=XSD.integer, min_count=1, max_count=1, min_inclusive=1)
        _add_prop(shapes, chunk_shape, CBML2.endLine,
                  datatype=XSD.integer, min_count=1, max_count=1, min_inclusive=1)
        _add_prop(shapes, chunk_shape, CBML2.contentSha256,
                  datatype=XSD.hexBinary, min_count=1, max_count=1,
                  pattern="^[0-9a-f]{64}$")
        _add_prop(shapes, chunk_shape, CBML2.embeddingRow,
                  datatype=XSD.integer, max_count=1, min_inclusive=0)
        _add_prop(shapes, chunk_shape, CBML2.embeddingArtifact,
                  datatype=XSD.string, max_count=1)
        _add_prop(shapes, chunk_shape, CBML2.truncatedForEmbedding,
                  datatype=XSD.boolean, max_count=1)


def _add_prop(g: Graph, parent: URIRef, path: URIRef, *,
              datatype: URIRef | None = None,
              klass: URIRef | None = None,
              min_count: int | None = None,
              max_count: int | None = None,
              min_inclusive: int | None = None,
              pattern: str | None = None) -> None:
    import hashlib
    key = f"{parent}|{path}|{datatype}|{klass}|{min_count}|{max_count}|{min_inclusive}|{pattern}"
    p_iri = URIRef(f"{CBML2_NS}_ps_{hashlib.sha1(key.encode()).hexdigest()[:16]}")
    g.add((parent, SH.property, p_iri))
    g.add((p_iri, SH.path, path))
    if datatype is not None:
        g.add((p_iri, SH.datatype, datatype))
    if klass is not None:
        g.add((p_iri, SH["class"], klass))
    if min_count is not None:
        g.add((p_iri, SH.minCount, Literal(min_count)))
    if max_count is not None:
        g.add((p_iri, SH.maxCount, Literal(max_count)))
    if min_inclusive is not None:
        g.add((p_iri, SH.minInclusive, Literal(min_inclusive)))
    if pattern is not None:
        g.add((p_iri, SH.pattern, Literal(pattern)))
