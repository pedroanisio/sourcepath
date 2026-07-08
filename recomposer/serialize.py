"""Serialize a BuildPlan to YAML (the machine-readable companion of the
Markdown guide — full file lists, no prose clipping)."""
from __future__ import annotations

from typing import Any

import yaml

from .model import BuildPlan


def to_yaml(plan: BuildPlan) -> str:
    return yaml.safe_dump(_document(plan), sort_keys=False, allow_unicode=True,
                          width=100)


def to_document(plan: BuildPlan) -> dict[str, Any]:
    return _document(plan)


def _document(plan: BuildPlan) -> dict[str, Any]:
    d = plan.to_dict()
    return {
        "disclaimer": {
            "notice": (
                "Evidence basis & confidence — file inventories, dependencies, "
                "and build order are mechanically extracted from the original "
                "repository's graph; step goals, rationale, and "
                "responsibilities are interpretive, confidence-tagged (certain "
                "> strong > probable > weak > unknown), and to be validated "
                "against the original sources. LLM-authored text is marked "
                "unverified."
            ),
            "generated_by": plan.provenance.get("tool"),
        },
        "repository": d["repository"],
        "architecture_intent": d["architecture_intent"],
        "skipped_phases": d["skipped_phases"],
        "steps": d["steps"],
        "open_assumptions": d["open_assumptions"],
        "provenance": d["provenance"],
    }
