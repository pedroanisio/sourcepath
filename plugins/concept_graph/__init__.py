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
from .concepts import (
    USE_BUILTIN, ConceptAggregator, _UseBuiltin, canonicalize,
)
from .graph_writer import ConceptGraphWriter, ConceptShapes
from .artifact import ConceptsArtifact
from codebase_mapper.emission.infrastructure.vocab import Vocabulary

__all__ = [
    "IdentifierSplitter",
    "ConceptAggregator",
    "ConceptGraphWriter",
    "ConceptShapes",
    "ConceptsArtifact",
    "USE_BUILTIN",
    "Vocabulary",
    "split_identifier",
    "canonicalize",
    "register_all",
]


def register_all(vocab: Vocabulary | _UseBuiltin | None = USE_BUILTIN) -> None:
    """Register every L3 plugin with the host's extension registries.

    `vocab` controls L3's typed-concept behavior:
      - USE_BUILTIN (default): load and apply software_primitives.yaml
      - a Vocabulary instance: apply the given vocab
      - None: disable typed concepts (pre-vocab behavior; bundles
        contain no cbml3:conceptKind / skos:Collection nodes)
    """
    from codebase_mapper.shared_kernel.extensions import (
        register_record_enricher, register_aggregator,
        register_graph_contributor, register_shape_contributor,
        register_artifact_emitter,
    )
    register_record_enricher(IdentifierSplitter())
    register_aggregator(ConceptAggregator(vocab))
    register_graph_contributor(ConceptGraphWriter())
    register_shape_contributor(ConceptShapes())
    register_artifact_emitter(ConceptsArtifact())
