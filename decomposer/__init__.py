"""Repository Decomposer — first delivery.

Consumes a codebase-mapper bundle (RDF/JSON artifacts under ``_tmp/<name>/``) and
produces an evidence-grounded, confidence-tagged decomposition of the repository
into structural, behavioral, semantic, dependency, data, and operational parts.

The output (``decomposer.serialize.to_yaml``) is designed to be consumed by the
Recomposer (second delivery) with no further access to the raw bundle: it embeds
the module dependency graph, a topological ``build_order``, and each module's
public ``interface_symbols``.

Public API:
    decompose(bundle_dir) -> Decomposition
    to_yaml(decomp) -> str
    to_markdown(decomp) -> str
"""
from __future__ import annotations

from .decompose import decompose
from .model import (
    Architecture, Classification, Confidence, Decomposition, DepRef, Evidence,
    Part, QualityFinding, Relationship,
)
from .report import to_markdown
from .serialize import to_document, to_yaml

__all__ = [
    "decompose",
    "to_yaml",
    "to_document",
    "to_markdown",
    "Decomposition",
    "Part",
    "Relationship",
    "Evidence",
    "Classification",
    "DepRef",
    "Architecture",
    "QualityFinding",
    "Confidence",
]

__version__ = "0.1.0"
