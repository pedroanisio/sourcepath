"""Serialize a :class:`Decomposition` to the Part II YAML schema.

The emitted document is the Recomposer's sole input: it carries the parts,
relationships, detected architecture, quality gates, and — critically for
reconstruction — the module ``build_order`` and each module's
``interface_symbols``. A ``disclaimer`` block (operator-approved evidence-basis
banner) heads the file so the mechanical/interpretive split travels with the data.
"""
from __future__ import annotations

from typing import Any

import yaml

from .model import Decomposition


def to_yaml(decomp: Decomposition) -> str:
    doc = _document(decomp)
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def to_document(decomp: Decomposition) -> dict[str, Any]:
    return _document(decomp)


def _document(decomp: Decomposition) -> dict[str, Any]:
    d = decomp.to_dict()
    return {
        "disclaimer": {
            "notice": (
                "Evidence basis & confidence — structural parts, dependency "
                "edges, and coupling metrics are mechanically derived from the "
                "codebase-mapper graph and are evidence-backed. Roles, layers, "
                "domains, responsibilities, and architecture style are "
                "interpretive and confidence-tagged (certain > strong > "
                "probable > weak > unknown). LLM-authored text appears only in "
                "evidence.llm_summaries and is unverified. Validate before "
                "high-stakes decisions."
            ),
            "generated_by": decomp.provenance.get("tool"),
        },
        "repository": d["repository"],
        "parts": d["parts"],
        "relationships": d["relationships"],
        "detected_architecture": d["detected_architecture"],
        "quality_gates": d["quality_gates"],
        "build_order": d["build_order"],
        "provenance": d["provenance"],
    }
