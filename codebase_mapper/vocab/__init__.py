"""codebase_mapper.vocab — controlled vocabulary for L3 concepts.

Stage 1 of the vocabulary absorption: package skeleton + loader API.
No host code consumes this yet; Stage 4 wires it into the concept-graph
plugin. The bundled YAML is filled in at Stage 2.
"""
from __future__ import annotations

from .loader import (
    VOCAB_SCHEMA_VERSION,
    ConceptKind,
    VocabTerm,
    Vocabulary,
    builtin_vocabulary,
    builtin_vocabulary_path,
    load_vocabulary,
)

__all__ = [
    "VOCAB_SCHEMA_VERSION",
    "ConceptKind",
    "VocabTerm",
    "Vocabulary",
    "builtin_vocabulary",
    "builtin_vocabulary_path",
    "load_vocabulary",
]
