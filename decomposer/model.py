"""Decomposition data model.

Plain dataclasses describing the decomposed repository. This module is the
contract the Recomposer (second delivery) consumes: it must be serializable to
the Part II YAML schema and carry enough structure (dependency DAG, build
order, interface symbols) that a reconstruction plan can be produced without
ever re-reading the raw CBM bundle.

ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs will always produce some form of
error. LLM-authored evidence (file summaries, concept descriptions) is carried
here only inside ``Evidence.llm_summaries`` and is always labeled unverified.
No classification or metric in this model is derived from unverified text; every
such field is graph-derived and confidence-tagged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Confidence(str, Enum):
    """Confidence ladder mandated by Part IV.

    Ordered strongest→weakest so callers can compare / take a minimum.
    """

    CERTAIN = "certain"      # directly proven by graph evidence
    STRONG = "strong"        # supported by multiple signals
    PROBABLE = "probable"    # supported by naming/location/dependencies
    WEAK = "weak"            # plausible but under-evidenced
    UNKNOWN = "unknown"      # cannot be determined from the bundle

    @property
    def rank(self) -> int:
        return _CONF_RANK[self]

    @classmethod
    def weakest(cls, *values: "Confidence") -> "Confidence":
        """Return the least-confident of the given values (for combining signals)."""
        if not values:
            return cls.UNKNOWN
        return max(values, key=lambda c: c.rank)


_CONF_RANK = {
    Confidence.CERTAIN: 0,
    Confidence.STRONG: 1,
    Confidence.PROBABLE: 2,
    Confidence.WEAK: 3,
    Confidence.UNKNOWN: 4,
}


# ── part / role vocabularies (closed sets; documented in the design) ──────────
PART_KINDS = frozenset({
    "file", "module", "package", "application", "service", "library",
    "external_dependency", "entrypoint", "domain", "data_schema",
    "generated_artifact", "operational", "documentation",
})
ROLES = frozenset({
    "core", "supporting", "infrastructure", "adapter", "test", "generated",
})
REUSABILITY = frozenset({
    "reusable", "replaceable", "internal", "external", "unknown",
})
RISK = frozenset({"low", "elevated", "high", "unknown"})


@dataclass
class Evidence:
    """Everything the decomposer relied on to assert a part exists.

    ``graph_nodes`` / ``graph_edges`` are opaque bundle identifiers (file URIs,
    ``kind:src->dst`` edge descriptors) so a reviewer can trace any claim back to
    the RDF graph. ``signals`` are human-readable justifications. ``llm_summaries``
    is the only channel for unverified LLM text and is never used to compute a
    classification.
    """

    files: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    graph_nodes: list[str] = field(default_factory=list)
    graph_edges: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    llm_summaries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": list(self.files),
            "symbols": list(self.symbols),
            "graph_nodes": list(self.graph_nodes),
            "graph_edges": list(self.graph_edges),
            "signals": list(self.signals),
            "llm_summaries": list(self.llm_summaries),
        }


@dataclass
class Classification:
    role: str = "supporting"
    role_confidence: Confidence = Confidence.PROBABLE
    layer: str | None = None
    layer_confidence: Confidence = Confidence.UNKNOWN
    instability: float | None = None       # Martin I = Ce/(Ca+Ce)
    stability: float | None = None         # 1 - I
    stability_confidence: Confidence = Confidence.UNKNOWN
    reusability: str = "unknown"
    risk: str = "unknown"
    risk_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "role_confidence": self.role_confidence.value,
            "layer": self.layer,
            "layer_confidence": self.layer_confidence.value,
            "instability": _round(self.instability),
            "stability": _round(self.stability),
            "stability_confidence": self.stability_confidence.value,
            "reusability": self.reusability,
            "risk": self.risk,
            "risk_reasons": list(self.risk_reasons),
        }


@dataclass
class DepRef:
    incoming: list[str] = field(default_factory=list)   # part ids that depend on this
    outgoing: list[str] = field(default_factory=list)   # part ids this depends on
    # Edges that exist only through a dev/test-scoped dependency (e.g. a Cargo
    # dev-dependency cycle). Legal, but excluded from SCC/build-order math —
    # a builder needs them only when building the tests.
    test_only_outgoing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"incoming": sorted(self.incoming),
                             "outgoing": sorted(self.outgoing)}
        if self.test_only_outgoing:
            d["test_only_outgoing"] = sorted(self.test_only_outgoing)
        return d


@dataclass
class Part:
    id: str
    name: str
    kind: str
    responsibility: str = ""
    responsibility_confidence: Confidence = Confidence.WEAK
    layer: str | None = None
    evidence: Evidence = field(default_factory=Evidence)
    dependencies: DepRef = field(default_factory=DepRef)
    classification: Classification = field(default_factory=Classification)
    metrics: dict[str, Any] = field(default_factory=dict)
    interface_symbols: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    overall_confidence: Confidence = Confidence.PROBABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "layer": self.layer,
            "responsibility": self.responsibility,
            "responsibility_confidence": self.responsibility_confidence.value,
            "evidence": self.evidence.to_dict(),
            "dependencies": self.dependencies.to_dict(),
            "classification": self.classification.to_dict(),
            "metrics": self.metrics,
            "interface_symbols": list(self.interface_symbols),
            "notes": list(self.notes),
            "overall_confidence": self.overall_confidence.value,
        }


@dataclass
class SymbolRecord:
    """One symbol chunk, projected for the symbol map (Tier 1).

    Every record is proven by a ``cbml2:Chunk`` node in the bundle graph, so
    ``confidence`` defaults to CERTAIN. Signature fields mirror the canonical
    chunk contract (plugins/chunks_embeddings/signatures.py) and follow the
    same omission rule: ``to_dict`` drops empty/unknown values so absence in
    the YAML means "not extracted", never "empty placeholder".
    """

    name: str
    kind: str                                  # class | function | method
    file: str
    line_start: int | None = None
    line_end: int | None = None
    parent: str | None = None
    signature: str | None = None
    params: list[dict[str, Any]] | None = None
    returns: str | None = None
    bases: list[str] | None = None
    type_params: list[str] | None = None
    visibility: str | None = None
    is_async: bool = False
    decorators: list[str] | None = None
    is_interface: bool = False                 # cross-module xref target
    confidence: Confidence = Confidence.CERTAIN

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "confidence": self.confidence.value,
        }
        if self.parent:
            out["parent"] = self.parent
        if self.signature:
            out["signature"] = self.signature
        if self.params:
            out["params"] = [dict(p) for p in self.params]
        if self.returns:
            out["returns"] = self.returns
        if self.bases:
            out["bases"] = list(self.bases)
        if self.type_params:
            out["type_params"] = list(self.type_params)
        if self.visibility:
            out["visibility"] = self.visibility
        if self.is_async:
            out["is_async"] = True
        if self.decorators:
            out["decorators"] = list(self.decorators)
        if self.is_interface:
            out["is_interface"] = True
        return out


@dataclass
class Relationship:
    source: str      # part id (serialized as ``from``)
    target: str      # part id (serialized as ``to``)
    type: str        # imports | imports_external | tests | calls | subclassOf | overrides | contains
    strength: int = 1
    confidence: Confidence = Confidence.CERTAIN
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.source,
            "to": self.target,
            "type": self.type,
            "strength": self.strength,
            "confidence": self.confidence.value,
            "evidence": self.evidence,
        }


@dataclass
class Violation:
    kind: str
    description: str
    confidence: Confidence
    subjects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "confidence": self.confidence.value,
            "subjects": list(self.subjects),
        }


@dataclass
class Hypothesis:
    statement: str
    confidence: Confidence
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "confidence": self.confidence.value,
            "evidence": list(self.evidence),
        }


@dataclass
class Architecture:
    style: str = "unknown"
    confidence: Confidence = Confidence.UNKNOWN
    evidence: list[str] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "style": self.style,
            "confidence": self.confidence.value,
            "evidence": list(self.evidence),
            "violations": [v.to_dict() for v in self.violations],
            "hypotheses": [h.to_dict() for h in self.hypotheses],
        }


@dataclass
class QualityFinding:
    gate: str            # e.g. "circular_dependencies", "god_module", "test_gap"
    severity: str        # info | warning | error
    subject: str         # part id or path or edge
    description: str
    confidence: Confidence
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "severity": self.severity,
            "subject": self.subject,
            "description": self.description,
            "confidence": self.confidence.value,
            "evidence": list(self.evidence),
        }


@dataclass
class Decomposition:
    repository: dict[str, Any]
    parts: list[Part] = field(default_factory=list)
    # part id -> full symbol inventory (Tier 1). Serialized to a sidecar via
    # serialize.to_symbols_yaml, NOT into the main YAML: the main document
    # stays "meaningful parts", the sidecar holds the exhaustive map.
    symbol_map: dict[str, list[SymbolRecord]] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    detected_architecture: Architecture = field(default_factory=Architecture)
    quality_gates: list[QualityFinding] = field(default_factory=list)
    build_order: list[list[str]] = field(default_factory=list)
    # One entry per directory-granularity cycle group: the *file-level*
    # construction order inside it. Module-level SCCs often dissolve at file
    # granularity (this repo's file graph is a DAG); when they do, a consumer
    # can build the group file-by-file instead of "all at once". Empty
    # ``file_order`` + a ``note`` means the files are genuinely cyclic too.
    cycle_resolutions: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "parts": [p.to_dict() for p in self.parts],
            "relationships": [r.to_dict() for r in self.relationships],
            "detected_architecture": self.detected_architecture.to_dict(),
            "quality_gates": [q.to_dict() for q in self.quality_gates],
            "build_order": [list(layer) for layer in self.build_order],
            "cycle_resolutions": [dict(c) for c in self.cycle_resolutions],
            "provenance": self.provenance,
        }


def _round(value: float | None, ndigits: int = 3) -> float | None:
    return round(value, ndigits) if isinstance(value, (int, float)) else None
