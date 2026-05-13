"""XrefGraphWriter + XrefShapes — emit RDF triples and SHACL shapes for
the symbol-xref layer.

Triple shape (one edge, reified):

    cbmi:xref/<safe_id> a cbmxr:Edge ;
        cbmxr:src         cbmi:chunk/<safe_src> ;
        cbmxr:dst         cbmi:chunk/<safe_dst> ;
        cbmxr:kind        "calls" ;
        cbmxr:resolution  "exact" ;
        cbmxr:resolver    "python_intra_file" .

Edges are reified (a node, not a direct predicate) so future provenance
(call-site byte offset, confidence) can attach without changing the
chunk vocabulary. The edge IRI is derived from a stable hash of
(src, dst, kind, resolver) so two runs of the same input produce
byte-identical TTL.
"""
from __future__ import annotations

import hashlib
import urllib.parse
from typing import cast

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, XSD

from codebase_mapper.constants import (
    CBM_NS, CBMI_NS, CBMXR, CBMXR_NS,
    XREF_KINDS, XREF_RESOLUTIONS,
)
from codebase_mapper.extensions import PipelineCtx
from codebase_mapper.models import SymbolXrefEdge

from .aggregator import XREF_INDEX_KEY


SH_NS = "http://www.w3.org/ns/shacl#"
SH = Namespace(SH_NS)
CBM = Namespace(CBM_NS)

# Chunk class is owned by the L2 plugin; we reference the IRI by name
# rather than importing the plugin (loose coupling — see chunk_iri).
CBML2_NS = "https://codebase-mapper.example.org/cbml2#"
CBML2 = Namespace(CBML2_NS)


def chunk_iri(chunk_id: str) -> URIRef:
    """Mirror of plugins.chunks_embeddings.graph_writer.chunk_iri.

    Duplicated (not imported) so the L3 xref plugin talks to L2 only
    through ``ctx.indices`` keys, never via cross-plugin imports.
    """
    safe = urllib.parse.quote(chunk_id, safe="")
    return URIRef(f"{CBMI_NS}chunk/{safe}")


def edge_iri(edge: SymbolXrefEdge) -> URIRef:
    """Stable per-edge IRI. Hash of the fields that define identity."""
    key = f"{edge.src_chunk_id}|{edge.dst_chunk_id}|{edge.kind}|{edge.resolver}"
    return URIRef(f"{CBMI_NS}xref/{hashlib.sha1(key.encode()).hexdigest()[:16]}")


class XrefGraphWriter:
    name = "l3_10_xrefs_graph"

    def contribute(self, g: Graph, ctx: PipelineCtx) -> None:
        g.bind("cbmxr", CBMXR)
        index = cast(dict, ctx.indices.get(XREF_INDEX_KEY, {}))
        for edge in index.get("edges", ()):
            eiri = edge_iri(edge)
            g.add((eiri, RDF.type, CBMXR.Edge))
            g.add((eiri, CBMXR.src, chunk_iri(edge.src_chunk_id)))
            g.add((eiri, CBMXR.dst, chunk_iri(edge.dst_chunk_id)))
            g.add((eiri, CBMXR.kind, Literal(edge.kind)))
            g.add((eiri, CBMXR.resolution, Literal(edge.resolution)))
            g.add((eiri, CBMXR.resolver, Literal(edge.resolver)))


class XrefShapes:
    name = "l3_10_xrefs_shapes"

    def contribute(self, shapes: Graph) -> None:
        shapes.bind("cbmxr", CBMXR)

        edge_shape = URIRef(f"{CBMXR_NS}EdgeShape")
        shapes.add((edge_shape, RDF.type, SH.NodeShape))
        shapes.add((edge_shape, SH.targetClass, CBMXR.Edge))

        _add_prop(shapes, edge_shape, CBMXR.src,
                  klass=CBML2.Chunk, min_count=1, max_count=1)
        _add_prop(shapes, edge_shape, CBMXR.dst,
                  klass=CBML2.Chunk, min_count=1, max_count=1)
        _add_enum_prop(shapes, edge_shape, CBMXR.kind, XREF_KINDS, "kind")
        _add_enum_prop(shapes, edge_shape, CBMXR.resolution,
                       XREF_RESOLUTIONS, "resolution")
        _add_prop(shapes, edge_shape, CBMXR.resolver,
                  datatype=XSD.string, min_count=1, max_count=1)


def _add_prop(g: Graph, parent: URIRef, path: URIRef, *,
              datatype: URIRef | None = None,
              klass: URIRef | None = None,
              min_count: int | None = None,
              max_count: int | None = None) -> None:
    key = f"{parent}|{path}|{datatype}|{klass}|{min_count}|{max_count}"
    p_iri = URIRef(f"{CBMXR_NS}_ps_{hashlib.sha1(key.encode()).hexdigest()[:16]}")
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


def _add_enum_prop(g: Graph, parent: URIRef, path: URIRef,
                   values: tuple[str, ...], slug: str) -> None:
    list_iri = URIRef(f"{CBMXR_NS}_{slug}List")
    Collection(g, list_iri, [Literal(v) for v in values])
    p_iri = URIRef(f"{CBMXR_NS}_{slug}Prop")
    g.add((parent, SH.property, p_iri))
    g.add((p_iri, SH.path, path))
    g.add((p_iri, SH.minCount, Literal(1)))
    g.add((p_iri, SH.maxCount, Literal(1)))
    g.add((p_iri, URIRef(SH_NS + "in"), list_iri))
