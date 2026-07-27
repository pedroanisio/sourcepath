"""L2 — chunk + embedding companion to codebase_mapper.

Public surface:
    ChunkExtractor          — RecordEnricher
    EmbeddingComputer       — Aggregator
    ChunkGraphWriter        — GraphContributor
    ChunkShapes             — ShapeContributor
    EmbeddingsArtifact      — ArtifactEmitter

Plus backends:
    SentenceTransformerBackend
    OllamaEmbeddingBackend
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
    OllamaEmbeddingBackend, OllamaEmbeddingUnreachable,
    OllamaEmbeddingModelMissing, OllamaEmbeddingRequestFailed,
    DEFAULT_OLLAMA_EMBED_MODEL,
)

__all__ = [
    "ChunkExtractor",
    "EmbeddingComputer",
    "ChunkGraphWriter",
    "ChunkShapes",
    "EmbeddingsArtifact",
    "EmbeddingBackend",
    "SentenceTransformerBackend",
    "OllamaEmbeddingBackend",
    "OllamaEmbeddingUnreachable",
    "OllamaEmbeddingModelMissing",
    "OllamaEmbeddingRequestFailed",
    "DEFAULT_OLLAMA_EMBED_MODEL",
    "DeterministicHashBackend",
    "build_backend",
    "register_all",
]


def build_backend(
    kind: str,
    *,
    hash_dim: int = 256,
    sbert_model: str = "sentence-transformers/all-MiniLM-L6-v2",
    ollama_model: str = DEFAULT_OLLAMA_EMBED_MODEL,
) -> EmbeddingBackend:
    """Construct an embedding backend from a CLI choice.

    Single source of truth for the ``--backend {sbert,hash,ollama}``
    dispatch shared by every run script — keeps the four CLIs from
    drifting apart. The Ollama server address comes from ``$OLLAMA_HOST``
    (default http://localhost:11434), same as the L4 enrichment client.
    """
    if kind == "sbert":
        return SentenceTransformerBackend(sbert_model)
    if kind == "ollama":
        return OllamaEmbeddingBackend(ollama_model)
    if kind == "hash":
        return DeterministicHashBackend(hash_dim)
    raise ValueError(f"unknown embedding backend {kind!r}")


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
