"""Recomposition data model — the Natural Description Build Plan.

A :class:`BuildPlan` is an ordered sequence of :class:`BuildStep` records that,
followed in order, reconstruct the system described by a Decomposer output.
Every step is evidence-grounded (it cites the decomposition part ids and files
it derives from) and confidence-tagged; steps resting on unresolved assumptions
carry them explicitly (Part IV: "reconstruction steps that depend on unresolved
assumptions" must be reported, never silently embedded).

The Recomposer consumes ONLY the decomposition document — never the raw bundle
or repository — so this model mirrors what that document can prove.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Canonical construction phases (Part III). The scheduler may pull a step to an
# earlier phase than its nominal one when dependency evidence forces it (e.g. a
# cycle group spanning core and infrastructure must be built together); it never
# pushes a step later than its dependents.
PHASES: list[tuple[int, str, str]] = [
    (1, "skeleton", "Establish project skeleton"),
    (2, "environment", "Configure package/build/runtime environment"),
    (3, "domain_data", "Define core domain/data model"),
    (4, "contracts", "Define internal contracts/interfaces"),
    (5, "core_logic", "Implement core logic"),
    (6, "adapters_infrastructure", "Implement adapters/infrastructure"),
    (7, "delivery_surfaces", "Implement APIs/CLIs/jobs/events"),
    (8, "persistence", "Implement persistence and migrations"),
    (9, "configuration_deployment", "Implement configuration and deployment"),
    (10, "tests_fixtures", "Implement tests and fixtures"),
    (11, "validation", "Validate full-system behavior"),
    (12, "documentation", "Document usage and extension points"),
]
PHASE_TITLE: dict[int, str] = {n: title for n, _, title in PHASES}
PHASE_KEY: dict[int, str] = {n: key for n, key, _ in PHASES}


@dataclass
class BuildStep:
    number: int
    phase: int                              # 1..12, index into PHASES
    goal: str
    rationale: str                          # construction intent: why now, why this shape
    requires: list[int] = field(default_factory=list)       # earlier step numbers
    creates: list[str] = field(default_factory=list)        # files/components to create
    contracts: list[str] = field(default_factory=list)      # interfaces/symbols to define
    dependencies_introduced: list[str] = field(default_factory=list)
    tests_required: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)       # part ids + signals
    expected_result: str = ""
    confidence: str = "probable"
    assumptions: list[str] = field(default_factory=list)    # unresolved assumptions
    parts: list[str] = field(default_factory=list)          # decomposition part ids realized

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.number,
            "phase": self.phase,
            "phase_title": PHASE_TITLE.get(self.phase, "?"),
            "goal": self.goal,
            "rationale": self.rationale,
            "requires_steps": list(self.requires),
            "creates": list(self.creates),
            "contracts": list(self.contracts),
            "dependencies_introduced": list(self.dependencies_introduced),
            "tests_required": list(self.tests_required),
            "evidence": list(self.evidence),
            "expected_result": self.expected_result,
            "confidence": self.confidence,
            "assumptions": list(self.assumptions),
            "parts": list(self.parts),
        }


@dataclass
class BuildPlan:
    repository: dict[str, Any]              # copied from the decomposition header
    architecture_intent: dict[str, Any]     # style + hypotheses the rebuild should honor
    steps: list[BuildStep] = field(default_factory=list)
    skipped_phases: list[dict[str, str]] = field(default_factory=list)
    open_assumptions: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "architecture_intent": self.architecture_intent,
            "steps": [s.to_dict() for s in self.steps],
            "skipped_phases": list(self.skipped_phases),
            "open_assumptions": list(self.open_assumptions),
            "provenance": self.provenance,
        }
