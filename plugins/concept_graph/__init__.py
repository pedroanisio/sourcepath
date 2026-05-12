"""L3 — lexical/concept-graph companion to codebase_mapper.

Public surface:
    IdentifierSplitter      — RecordEnricher
    ConceptAggregator       — Aggregator
    ConceptGraphWriter      — GraphContributor
    ConceptShapes           — ShapeContributor
    ConceptsArtifact        — ArtifactEmitter

Convenience:
    register_all() — registers every L3 plugin with the host in one call.
"""
from __future__ import annotations

from .splitter import IdentifierSplitter, split_identifier
from .concepts import ConceptAggregator, canonicalize
from .graph_writer import ConceptGraphWriter, ConceptShapes
from .artifact import ConceptsArtifact

__all__ = [
    "IdentifierSplitter",
    "ConceptAggregator",
    "ConceptGraphWriter",
    "ConceptShapes",
    "ConceptsArtifact",
    "split_identifier",
    "canonicalize",
    "register_all",
]


def register_all() -> None:
    """Register every L3 plugin with the host's extension registries."""
    from codebase_mapper.extensions import (
        register_record_enricher, register_aggregator,
        register_graph_contributor, register_shape_contributor,
        register_artifact_emitter,
    )
    register_record_enricher(IdentifierSplitter())
    register_aggregator(ConceptAggregator())
    register_graph_contributor(ConceptGraphWriter())
    register_shape_contributor(ConceptShapes())
    register_artifact_emitter(ConceptsArtifact())
