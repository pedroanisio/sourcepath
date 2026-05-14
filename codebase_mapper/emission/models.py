"""Emission-side types consumed by xref/graph artifact surfaces."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


XrefKind = Literal["calls", "subclassOf", "overrides", "references"]
XrefResolution = Literal["exact", "heuristic", "ambiguous"]
XrefUnresolvedReason = Literal[
    "module_not_in_repo",
    "symbol_not_exported",
    "ambiguous",
    "dynamic_dispatch",
    "language_unsupported",
]


@dataclass(frozen=True)
class SymbolXrefEdge:
    """Symbol-level edge between two L2 chunks (function/class/method)."""

    src_chunk_id: str
    dst_chunk_id: str
    kind: XrefKind
    resolution: XrefResolution
    resolver: str


@dataclass(frozen=True)
class UnresolvedSymbolRef:
    """A symbol reference the resolver could not bind to a chunk."""

    src_chunk_id: str
    raw_target: str
    kind: XrefKind
    reason: XrefUnresolvedReason
    resolver: str
