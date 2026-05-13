"""Symbol-xref layer — call/subclass/override/reference edges between L2 chunks.

Public surface:
    XrefAggregator    — Aggregator (l3_10_xrefs)
    XrefGraphWriter   — GraphContributor (l3_10_xrefs_graph)
    XrefShapes        — ShapeContributor (l3_10_xrefs_shapes)
    XrefsArtifact     — ArtifactEmitter (l3_50_xrefs_artifact)

Convenience:
    register_all()    — registers every component with the host in one call.

The aggregator's dispatch dict ``_RESOLVERS`` is empty in Phase 1. Each
later phase adds one entry: language-string → ``resolve(record, ctx) ->
(edges, unresolved)``. Adding a new language is one dict entry plus a
resolver module — same registry shape as ``codebase_mapper.regenerate._REGENERATORS``.
"""
from __future__ import annotations

from .aggregator import XrefAggregator, XREF_INDEX_KEY
from .artifact import XrefsArtifact, SIDECAR_FILENAME
from .graph_writer import XrefGraphWriter, XrefShapes, chunk_iri, edge_iri
from .python_resolver import resolve_python_intra_file
from .tsjs_resolver import resolve_tsjs_calls


__all__ = [
    "XrefAggregator",
    "XrefGraphWriter",
    "XrefShapes",
    "XrefsArtifact",
    "XREF_INDEX_KEY",
    "SIDECAR_FILENAME",
    "chunk_iri",
    "edge_iri",
    "resolve_python_intra_file",
    "resolve_tsjs_calls",
    "register_all",
]


# Per-language resolvers. Phase N adds one entry. Each entry:
# language string (matches FileRecord.language) -> callable
# (record, ctx) -> (list[SymbolXrefEdge], list[UnresolvedSymbolRef]).
_RESOLVERS: dict = {
    "python": resolve_python_intra_file,
    "typescript": resolve_tsjs_calls,
    "javascript": resolve_tsjs_calls,
}


def register_all() -> None:
    """Register every xref-layer component with the host's registries."""
    from codebase_mapper.extensions import (
        register_aggregator, register_artifact_emitter,
        register_graph_contributor, register_shape_contributor,
    )
    register_aggregator(XrefAggregator(resolvers=dict(_RESOLVERS)))
    register_graph_contributor(XrefGraphWriter())
    register_shape_contributor(XrefShapes())
    register_artifact_emitter(XrefsArtifact())
