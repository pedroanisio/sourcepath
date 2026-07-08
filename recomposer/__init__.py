"""Repository Recomposer — second delivery.

Consumes a Decomposer YAML document (never the raw bundle or repository) and
generates a **Natural Description Build Plan**: an ordered, dependency-aware,
evidence-grounded sequence of natural-language construction steps that could
recreate the system from scratch, executable by a human engineer or an AI
coding agent.

Public API:
    recompose(doc: dict) -> BuildPlan
    to_markdown(plan) -> str
    to_yaml(plan) -> str
"""
from __future__ import annotations

from .model import PHASES, BuildPlan, BuildStep
from .plan import recompose
from .render import to_markdown
from .serialize import to_document, to_yaml

__all__ = [
    "recompose",
    "to_markdown",
    "to_yaml",
    "to_document",
    "BuildPlan",
    "BuildStep",
    "PHASES",
]

__version__ = "0.1.0"
