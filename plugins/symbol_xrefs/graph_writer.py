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
from rdflib.namespace import RDF, XSD

from codebase_mapper.shared_kernel.constants import (
    CBM_NS, CBMI_NS, CBMXR, CBMXR_NS,
    XREF_KINDS, XREF_RESOLUTIONS,
)
from codebase_mapper.shared_kernel.extensions import PipelineCtx
from codebase_mapper.shared_kernel.shacl_spec import (
    NodeShapeSpec, PropertySpec, render_shapes,
)
from codebase_mapper.emission.models import SymbolXrefEdge

from .aggregator import XREF_INDEX_KEY


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


# Canonical model of the xref edge shape (rendered by shacl_spec — the
# single spec→RDF code path).
SHAPE_SPECS: tuple[NodeShapeSpec, ...] = (
    NodeShapeSpec(
        iri=f"{CBMXR_NS}EdgeShape", target_class=str(CBMXR.Edge),
        properties=(
            PropertySpec(path=str(CBMXR.src), klass=str(CBML2.Chunk),
                         min_count=1, max_count=1),
            PropertySpec(path=str(CBMXR.dst), klass=str(CBML2.Chunk),
                         min_count=1, max_count=1),
            PropertySpec(path=str(CBMXR.kind), name="_kindProp",
                         list_name="_kindList", in_literals=XREF_KINDS,
                         min_count=1, max_count=1),
            PropertySpec(path=str(CBMXR.resolution),
                         name="_resolutionProp",
                         list_name="_resolutionList",
                         in_literals=XREF_RESOLUTIONS, min_count=1, max_count=1),
            PropertySpec(path=str(CBMXR.resolver),
                         datatype=str(XSD.string), min_count=1, max_count=1),
        )),
)


class XrefShapes:
    name = "l3_10_xrefs_shapes"

    def contribute(self, shapes: Graph) -> None:
        render_shapes(shapes, SHAPE_SPECS, bind={"cbmxr": CBMXR_NS})
