"""LlmGraphWriter + LlmShapes — emit cbml4: triples + SHACL shapes.

Step 1 status: skeleton. ``LlmGraphWriter.contribute`` binds the
``cbml4`` prefix on first call (so future SHACL violations and
SPARQL queries have a friendly prefix) but emits no triples.
``LlmShapes.contribute`` is empty in Step 1 — Step 4 adds the
optional-cardinality shape entries.

Triple shape (Step 4+):

    cbmi:file/<safe>
        cbml4:fileSummary       "…" ;
        cbml4:fileSummaryModel  "qwen2.5-coder:7b" ;
        cbml4:fileSummaryPromptSha "<hex>" ;
        cbml4:fileSummaryGeneratedAt "2026-05-14T..."^^xsd:dateTime .

The predicate-name convention is ``<kind><Field>`` rather than reified
edges: enrichments are *per-target attributes*, not relationships
between two nodes, so a flat predicate set is the right shape (the
xrefs layer uses reified edges precisely because *its* data points
between nodes).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from rdflib import Graph, Namespace

from codebase_mapper.constants import CBML4, CBML4_NS

if TYPE_CHECKING:
    from codebase_mapper.extensions import PipelineCtx


SH_NS = "http://www.w3.org/ns/shacl#"
SH = Namespace(SH_NS)


GRAPH_WRITER_NAME = "l4_30_graph"
SHAPES_NAME = "l4_30_shapes"


class LlmGraphWriter:
    """Step-1 skeleton. Emits no triples and binds no prefix — back-compat
    requires the inventory.ttl with this writer registered to be
    byte-identical to one without it. Step 4 fills in the prefix
    binding (only needed when triples land) and the per-kind emission."""

    name = GRAPH_WRITER_NAME

    def contribute(self, g: Graph, ctx: "PipelineCtx") -> None:
        # Step 4 calls g.bind("cbml4", CBML4) and emits the triples.
        # Step 1 is intentionally a no-op so the back-compat verifier
        # can assert byte equality.
        return None


class LlmShapes:
    """Step-1 skeleton. Empty body so the shapes file is byte-identical
    to a no-plugin run. Step 4 adds the optional-cardinality shape
    entries (``maxCount 1``, no ``minCount``)."""

    name = SHAPES_NAME

    def contribute(self, shapes: Graph) -> None:
        # Step 4: add the per-predicate shapes on cbm:File. Until then,
        # no contribution — the L4 layer is non-existent from SHACL's
        # point of view, which matches reality.
        return None
