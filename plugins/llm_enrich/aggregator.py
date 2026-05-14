"""LlmAggregator — produces enrichments that need cross-record context.

Step 1 status: skeleton. The aggregator runs after every RecordEnricher
(including LlmEnricher) and is the right place for enrichments that
need the *full bundle*, not just a single file:

  - ``concept_description`` — needs the L3 index (typed concepts +
    cooccurrence + per-path lexicalization), only available after
    ConceptAggregator runs.
  - ``schema_purpose`` — strictly per-file but tracked here so all
    LLM-driven output flows through one index entry
    ``ctx.indices[AGGREGATOR_NAME]``, simplifying the graph writer.

Step 5 fills this in. Step 1 returns an empty index payload so the
writer + artifact have a stable shape to consume from day one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codebase_mapper.extensions import PipelineCtx
    from .cache import Cache
    from .client import OllamaClient


# Run after L3's concept aggregator (``l3_20_concepts``) and after the
# xref aggregator (``l3_10_xrefs``). ``l4_20_*`` puts us in the right
# slot under sort-order semantics.
AGGREGATOR_NAME = "l4_20_enrich"


# Stable shape of the index entry the writer consumes. Keys are present
# even on a default (no-op) run so downstream contributors don't need
# to check for missing keys.
EMPTY_INDEX: dict = {
    "file_summary": {},          # path -> {"text": str, "model": str, "prompt_sha": str, "generated_at": str}
    "concept_description": {},   # concept_canonical -> same shape
    "schema_purpose": {},        # path -> same shape
}


@dataclass
class LlmAggregator:
    """Step-1 skeleton. Constructor signature is final; ``run`` returns
    a copy of EMPTY_INDEX until Step 5 fills it in."""

    client: "OllamaClient | None" = None
    cache: "Cache | None" = None
    model: str = "qwen2.5-coder:7b"
    scopes: tuple[str, ...] | None = None

    name: str = AGGREGATOR_NAME

    def run(self, ctx: "PipelineCtx") -> dict:
        # Step 1: deliberate no-op. Return a fresh copy of EMPTY_INDEX
        # so downstream consumers can dict.get(...) without surprise.
        return {k: {} for k in EMPTY_INDEX}
