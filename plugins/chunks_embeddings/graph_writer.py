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
        cbml2:qualifiedSymbol "UserService.calculate_score" ;
        cbml2:memberOf cbmi:chunk/<class_safe_id> ;   # members only
        cbml2:embeddingRow 42 ;
        cbml2:embeddingArtifact "embeddings.npz" .

The embedding row index and artifact filename let downstream readers find
the vector. The artifact filename is a relative pointer — the artifact
emitter is responsible for actually writing the file at that path.

Containment (BL-002): ``cbml2:memberOf`` links a member chunk to its parent
class chunk in the same file. Resolution is mechanical, never guessed:
prefer the class chunk with the same symbol whose byte span encloses the
member (tightest span wins — disambiguates duplicate class names); when no
enclosing chunk exists (e.g. C++ out-of-line definitions) fall back to the
unique same-file class of that name; ambiguity or absence emits nothing
(omission contract). ``cbml2:qualifiedSymbol`` is the file-local qualified
name (``Parent.symbol`` for members) on every symbol-level chunk; file-kind
chunks carry none — their symbol is a sentinel, not a declaration.
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
    # BL-005: generalization vs realization, as written; present only where
    # the language syntactically distinguishes them. baseType stays merged.
    ("extends", "extendsType"),
    ("implements", "implementsType"),
)


def chunk_iri(chunk_id: str) -> URIRef:
    # chunk_id contains '#', ':', '/' — quote them all.
    safe = urllib.parse.quote(chunk_id, safe="")
    return URIRef(f"{CBMI_NS}chunk/{safe}")


def _parent_chunk_id(member: dict, classes_by_symbol: dict) -> str | None:
    """chunk_id of the class chunk that contains ``member``, or None.

    Candidates share the member's file and ``symbol == parent_symbol``.
    A candidate whose byte span encloses the member wins; among several
    (duplicate class names), the tightest span. With no enclosing span
    (out-of-line definitions), a unique same-file candidate still binds;
    anything ambiguous binds nothing.
    """
    candidates = classes_by_symbol.get(member.get("parent_symbol") or "", [])
    if not candidates:
        return None
    enclosing = [k for k in candidates
                 if k["byte_start"] <= member["byte_start"]
                 and member["byte_end"] <= k["byte_end"]]
    if enclosing:
        tightest = min(enclosing, key=lambda k: (
            k["byte_end"] - k["byte_start"], k["byte_start"], k["chunk_id"]))
        return tightest["chunk_id"]
    if len(candidates) == 1:
        return candidates[0]["chunk_id"]
    return None


class ChunkGraphWriter:
    name = "l2_30_graph"

    def contribute(self, g: Graph, ctx: PipelineCtx) -> None:
        g.bind("cbml2", CBML2)
        g.bind("nif", NIF)
        chunks = cast(list, ctx.indices.get("l2_10_chunks", []))
        embeddings_meta = cast(dict, ctx.indices.get("l2_20_embeddings", {}))
        has_embeddings = bool(embeddings_meta.get("row_to_chunk_id"))

        # Per-file index of class chunks by symbol, for memberOf resolution.
        classes_by_file: dict[str, dict[str, list[dict]]] = {}
        for c in chunks:
            if c["kind"] == "class":
                classes_by_file.setdefault(c["path"], {}) \
                    .setdefault(c["symbol"], []).append(c)

        for c in chunks:
            ciri = chunk_iri(c["chunk_id"])
            g.add((ciri, RDF.type, CBML2.Chunk))
            g.add((ciri, RDF.type, NIF.Context))
            g.add((ciri, CBML2.inFile, file_iri(c["path"])))
            g.add((ciri, CBML2.kind, Literal(c["kind"])))
            g.add((ciri, CBML2.symbol, Literal(c["symbol"])))
            if c["kind"] != "file":
                qualified = (f"{c['parent_symbol']}.{c['symbol']}"
                             if c.get("parent_symbol") else c["symbol"])
                g.add((ciri, CBML2.qualifiedSymbol, Literal(qualified)))
            if c.get("parent_symbol"):
                g.add((ciri, CBML2.parentSymbol, Literal(c["parent_symbol"])))
                parent_id = _parent_chunk_id(
                    c, classes_by_file.get(c["path"], {}))
                if parent_id is not None and parent_id != c["chunk_id"]:
                    g.add((ciri, CBML2.memberOf, chunk_iri(parent_id)))
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
            PropertySpec(path=str(CBML2.qualifiedSymbol),
                         datatype=str(XSD.string), max_count=1),
            PropertySpec(path=str(CBML2.memberOf), klass=str(CBML2.Chunk),
                         max_count=1),
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
            PropertySpec(path=str(CBML2.extendsType),
                         datatype=str(XSD.string)),
            PropertySpec(path=str(CBML2.implementsType),
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
