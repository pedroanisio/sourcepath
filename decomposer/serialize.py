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


def to_symbols_yaml(decomp: Decomposition) -> str:
    """Serialize the full symbol map (Tier 1) as a sidecar document.

    Kept out of the main YAML deliberately: the main document stays
    "meaningful parts", while this sidecar carries the exhaustive, per-part
    symbol inventory (one record per graph-proven chunk, with signature
    evidence where the bundle provides it).
    """
    doc = {
        "disclaimer": {
            "notice": (
                "Full symbol inventory — every record is mechanically derived "
                "from a chunk node in the codebase-mapper graph (confidence: "
                "certain). Signature fields are parsed from source at bundle "
                "time; their absence means 'not extracted', never 'empty'."
            ),
            "generated_by": decomp.provenance.get("tool"),
        },
        "repository": {
            "name": decomp.repository.get("name"),
            "commit_sha": decomp.repository.get("commit_sha"),
        },
        "provenance": {
            "tool": decomp.provenance.get("tool"),
            "run_manifest_sha256": decomp.provenance.get("run_manifest_sha256"),
        },
        "symbols": {
            part_id: [s.to_dict() for s in records]
            for part_id, records in sorted(decomp.symbol_map.items())
        },
    }
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
        "cycle_resolutions": d["cycle_resolutions"],
        "provenance": d["provenance"],
    }
