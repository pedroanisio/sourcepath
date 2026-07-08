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
from .model_resolver import (
    DEFAULT_MODEL,
    MODEL_ENV_VAR,
    preferred_model,
    resolve_model,
)

__all__ = [
    "ALL_SCOPES",
    "Cache",
    "DEFAULT_MODEL",
    "LlmAggregator",
    "LlmArtifact",
    "LlmEnricher",
    "LlmGraphWriter",
    "LlmShapes",
    "MODEL_ENV_VAR",
    "OllamaClient",
    "SCOPE_CONCEPTS",
    "SCOPE_FILES",
    "SCOPE_SCHEMAS",
    "SIDECAR_FILENAME",
    "preferred_model",
    "register_all",
    "resolve_model",
]


def register_all(
    *,
    client: "OllamaClient | None" = None,
    cache: "Cache | None" = None,
    model: "str | None" = None,
    scopes: "tuple[str, ...] | None" = None,
    auto_resolve: bool = True,
) -> None:
    """Register every L4 component with the host's extension registries.

    Parameters mirror the CLI flags on ``scripts/run_l4.py``:

      ``client``   Wired into the enricher + aggregator (``None`` → no-op).
      ``cache``    Wired into the enricher + aggregator.
      ``model``    Preferred Ollama tag. ``None`` → ``$CBM_LLM_MODEL`` →
                   :data:`DEFAULT_MODEL`. Used in the cache key and
                   provenance.
      ``scopes``   Which enrichment kinds to opt in to.
      ``auto_resolve``  When True (default) and a ``client`` is given,
                   the *preferred* tag is resolved against the models
                   actually installed on the server via
                   :func:`resolve_model` — if the preferred tag is
                   missing but a smaller same-family tag is present, the
                   pipeline auto-solves to it instead of silently
                   emitting an un-enriched bundle. When the server is
                   unreachable or has no suitable model, the preferred
                   tag is wired as-is and the runtime degradation path
                   (log + skip, SHACL stays green) takes over.
    """
    from codebase_mapper.shared_kernel.extensions import (
        register_aggregator,
        register_artifact_emitter,
        register_graph_contributor,
        register_record_enricher,
        register_shape_contributor,
    )
    preferred = preferred_model(model)
    effective = preferred
    if auto_resolve and client is not None:
        effective = resolve_model(client, model) or preferred
    register_record_enricher(LlmEnricher(client=client, cache=cache,
                                         model=effective, scopes=scopes))
    register_aggregator(LlmAggregator(client=client, cache=cache,
                                      model=effective, scopes=scopes))
    register_graph_contributor(LlmGraphWriter())
    register_shape_contributor(LlmShapes())
    register_artifact_emitter(LlmArtifact())
