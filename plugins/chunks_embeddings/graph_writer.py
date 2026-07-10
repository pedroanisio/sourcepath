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

import json
import re
import urllib.parse
from typing import cast

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from codebase_mapper.shared_kernel.constants import CBM_NS, CBMI_NS
from codebase_mapper.shared_kernel.shacl_spec import (
    NodeShapeSpec, PropertySpec, render_shapes,
)
from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import file_iri

from codebase_mapper.shared_kernel.extensions import PipelineCtx


CBML2_NS = "https://codebase-mapper.example.org/cbml2#"
NIF_NS = "http://persistence.uni-leipzig.org/nlp2rdf/ontologies/nif-core#"

CBML2 = Namespace(CBML2_NS)
NIF = Namespace(NIF_NS)
CBM = Namespace(CBM_NS)

CHUNK_KINDS = ("function", "class", "method", "file")
EMBEDDINGS_ARTIFACT_FILENAME = "embeddings.npz"

# Signature/type fields (plugins/chunks_embeddings/signatures.py) → predicates.
# Emitted only when present on the chunk (omission contract); ``params`` is a
# JSON literal — its inner structure is validated at extraction time, and a
# JSON string keeps the graph compact and SPARQL-safe.
SIGNATURE_SCALAR_PREDICATES = (
    ("signature", "signature"),
    ("returns", "returnsType"),
    ("visibility", "visibility"),
)
SIGNATURE_REPEATED_PREDICATES = (
    ("bases", "baseType"),
    ("type_params", "typeParam"),
    ("decorators", "decorator"),
)


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
            for field, pred in SIGNATURE_SCALAR_PREDICATES:
                value = c.get(field)
                if value:
                    g.add((ciri, CBML2[pred], Literal(value)))
            for field, pred in SIGNATURE_REPEATED_PREDICATES:
                for value in c.get(field) or []:
                    g.add((ciri, CBML2[pred], Literal(value)))
            if c.get("params"):
                g.add((ciri, CBML2.paramsJson,
                       Literal(json.dumps(c["params"], sort_keys=False))))
            if c.get("is_async"):
                g.add((ciri, CBML2.isAsync,
                       Literal(True, datatype=XSD.boolean)))
            if has_embeddings:
                g.add((ciri, CBML2.embeddingRow,
                       Literal(c["row"], datatype=XSD.integer)))
                g.add((ciri, CBML2.embeddingArtifact,
                       Literal(EMBEDDINGS_ARTIFACT_FILENAME)))


# Canonical model of the L2 chunk shape (rendered by shacl_spec — the
# single spec→RDF code path). One spec per predicate the writer emits.
SHAPE_SPECS: tuple[NodeShapeSpec, ...] = (
    NodeShapeSpec(
        iri=f"{CBML2_NS}ChunkShape", target_class=str(CBML2.Chunk),
        properties=(
            PropertySpec(path=str(CBML2.inFile), klass=str(CBM.File),
                         min_count=1, max_count=1),
            PropertySpec(path=str(CBML2.kind), name="_kindProp",
                         list_name="_kindList", in_literals=CHUNK_KINDS,
                         min_count=1, max_count=1),
            PropertySpec(path=str(CBML2.symbol), datatype=str(XSD.string),
                         min_count=1, max_count=1),
            PropertySpec(path=str(CBML2.parentSymbol),
                         datatype=str(XSD.string), max_count=1),
            PropertySpec(path=str(NIF.beginIndex), min_inclusive=0,
                         datatype=str(XSD.integer), min_count=1, max_count=1),
            PropertySpec(path=str(NIF.endIndex), min_inclusive=0,
                         datatype=str(XSD.integer), min_count=1, max_count=1),
            PropertySpec(path=str(CBML2.beginLine), min_inclusive=1,
                         datatype=str(XSD.integer), min_count=1, max_count=1),
            PropertySpec(path=str(CBML2.endLine), min_inclusive=1,
                         datatype=str(XSD.integer), min_count=1, max_count=1),
            PropertySpec(path=str(CBML2.contentSha256),
                         datatype=str(XSD.hexBinary),
                         pattern="^[0-9a-f]{64}$", min_count=1, max_count=1),
            PropertySpec(path=str(CBML2.embeddingRow), min_inclusive=0,
                         datatype=str(XSD.integer), max_count=1),
            PropertySpec(path=str(CBML2.embeddingArtifact),
                         datatype=str(XSD.string), max_count=1),
            PropertySpec(path=str(CBML2.truncatedForEmbedding),
                         datatype=str(XSD.boolean), max_count=1),
            # signature/type fields — all optional (omission contract)
            PropertySpec(path=str(CBML2.signature),
                         datatype=str(XSD.string), max_count=1),
            PropertySpec(path=str(CBML2.returnsType),
                         datatype=str(XSD.string), max_count=1),
            PropertySpec(path=str(CBML2.visibility),
                         datatype=str(XSD.string), max_count=1),
            PropertySpec(path=str(CBML2.paramsJson),
                         datatype=str(XSD.string), max_count=1),
            PropertySpec(path=str(CBML2.isAsync),
                         datatype=str(XSD.boolean), max_count=1),
            PropertySpec(path=str(CBML2.baseType),
                         datatype=str(XSD.string)),
            PropertySpec(path=str(CBML2.typeParam),
                         datatype=str(XSD.string)),
            PropertySpec(path=str(CBML2.decorator),
                         datatype=str(XSD.string)),
        )),
)


class ChunkShapes:
    name = "l2_30_shapes"

    def contribute(self, shapes: Graph) -> None:
        render_shapes(shapes, SHAPE_SPECS,
                      bind={"cbml2": CBML2_NS, "nif": NIF_NS})
