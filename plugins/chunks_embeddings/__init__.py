"""L2 — chunk + embedding companion to codebase_mapper.

Public surface:
    ChunkExtractor          — RecordEnricher
    EmbeddingComputer       — Aggregator
    ChunkGraphWriter        — GraphContributor
    ChunkShapes             — ShapeContributor
    EmbeddingsArtifact      — ArtifactEmitter

Plus backends:
    SentenceTransformerBackend
    DeterministicHashBackend

Convenience:
    register_all(backend) — registers every L2 plugin with the host in one call.
"""
from __future__ import annotations

from .chunker import ChunkExtractor
from .embedder import EmbeddingComputer
from .graph_writer import ChunkGraphWriter, ChunkShapes
from .artifact import EmbeddingsArtifact
from .backends import (
    EmbeddingBackend, SentenceTransformerBackend, DeterministicHashBackend,
)

__all__ = [
    "ChunkExtractor",
    "EmbeddingComputer",
    "ChunkGraphWriter",
    "ChunkShapes",
    "EmbeddingsArtifact",
    "EmbeddingBackend",
    "SentenceTransformerBackend",
    "DeterministicHashBackend",
    "register_all",
]


def register_all(backend: EmbeddingBackend) -> None:
    """Register every L2 plugin with the host's extension registries."""
    from codebase_mapper.shared_kernel.extensions import (
        register_record_enricher, register_aggregator,
        register_graph_contributor, register_shape_contributor,
        register_artifact_emitter,
    )
    register_record_enricher(ChunkExtractor())
    register_aggregator(EmbeddingComputer(backend))
    register_graph_contributor(ChunkGraphWriter())
    register_shape_contributor(ChunkShapes())
    register_artifact_emitter(EmbeddingsArtifact())
