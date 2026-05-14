"""LLM enrichment layer — local Ollama-driven semantic annotations on cbm bundles.

Adds `cbml4:fileSummary` / `cbml4:conceptDescription` / `cbml4:schemaPurpose`
triples plus provenance (model, prompt SHA, timestamp) on top of the existing
L1/L2/L3 layers. Default off; opt in via ``register_all(client=…)`` or one of
the ``scripts/run_l*.py --llm-enrich`` flags.

Public surface:
    LlmEnricher       — RecordEnricher (l4_10_enrich)
    LlmAggregator     — Aggregator    (l4_20_enrich)   [Step 5+]
    LlmGraphWriter    — GraphContributor (l4_30_graph)
    LlmShapes         — ShapeContributor (l4_30_shapes)
    LlmArtifact       — ArtifactEmitter  (l4_50_artifact)

Convenience:
    register_all()    — register every component with the host in one call.

Step 1 status: skeleton only. The components register and run but emit zero
triples, write no sidecar, and skip every record. The verifier
``tests/verify_llm_enrich.py`` asserts that a default-config run produces a
bundle byte-identical to a no-plugin run — the back-compat anchor for every
later step.
"""
from __future__ import annotations

from .aggregator import (
    ALL_SCOPES,
    LlmAggregator,
    SCOPE_CONCEPTS,
    SCOPE_FILES,
    SCOPE_SCHEMAS,
)
from .artifact import LlmArtifact, SIDECAR_FILENAME
from .cache import Cache
from .client import OllamaClient
from .enricher import LlmEnricher
from .graph_writer import LlmGraphWriter, LlmShapes

__all__ = [
    "ALL_SCOPES",
    "Cache",
    "LlmAggregator",
    "LlmArtifact",
    "LlmEnricher",
    "LlmGraphWriter",
    "LlmShapes",
    "OllamaClient",
    "SCOPE_CONCEPTS",
    "SCOPE_FILES",
    "SCOPE_SCHEMAS",
    "SIDECAR_FILENAME",
    "register_all",
]


def register_all(
    *,
    client: "OllamaClient | None" = None,
    cache: "Cache | None" = None,
    model: str = "qwen2.5-coder:7b",
    scopes: "tuple[str, ...] | None" = None,
) -> None:
    """Register every L4 component with the host's extension registries.

    Parameters mirror the CLI flags on ``scripts/run_l4.py`` (Step 8). All
    are optional in Step 1 — the skeleton ignores them. Each parameter
    becomes load-bearing in a later step:

      ``client``   Step 2 wires it into the enricher + aggregator.
      ``cache``    Step 2 wires it into the enricher + aggregator.
      ``model``    Step 3 uses it in the cache key.
      ``scopes``   Step 5 uses it to opt in/out of each enrichment kind.
    """
    from codebase_mapper.shared_kernel.extensions import (
        register_aggregator,
        register_artifact_emitter,
        register_graph_contributor,
        register_record_enricher,
        register_shape_contributor,
    )
    register_record_enricher(LlmEnricher(client=client, cache=cache,
                                         model=model, scopes=scopes))
    register_aggregator(LlmAggregator(client=client, cache=cache,
                                      model=model, scopes=scopes))
    register_graph_contributor(LlmGraphWriter())
    register_shape_contributor(LlmShapes())
    register_artifact_emitter(LlmArtifact())
