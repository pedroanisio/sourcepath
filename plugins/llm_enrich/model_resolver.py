"""Self-correcting model resolution for the LLM enrichment pipeline.

Root-cause fix for the "Ollama up, required model absent" failure mode.

The pipeline used to be hard-locked to ``qwen2.5-coder:7b``. On a host
that only had a smaller tag pulled (e.g. ``qwen2.5-coder:1.5b``), every
``/api/generate`` call 404'd, the enricher and aggregator caught the
resulting :class:`OllamaModelMissing`, silently disabled themselves for
the run, and the bundle emitted with *zero* L4 enrichments. The
connectivity-only skip guard (``OllamaClient.ping()`` — checks the
server is up, not that the model exists) could not see this, so
downstream contract tests failed instead of skipping or succeeding.

That is a textbook PALS's-Law silent failure: unverified pre-condition
(model presence) masked by a coarse health check.

``resolve_model`` makes the pipeline auto-solve the mismatch: it selects
the best model that is *actually installed*, honoring an explicit
override, then the ``CBM_LLM_MODEL`` env var, then the configured
default, then a descending same-family fallback chain. If the server is
reachable but no candidate is installed — or the server is unreachable —
it returns ``None`` so callers degrade *knowingly* (skip the test / wire
the runtime degradation path) rather than failing blind.
"""
from __future__ import annotations

import logging
import os

from .client import OllamaClient, OllamaUnreachable

#: Preferred enrichment model when nothing else is specified. Chosen per
#: the benchmark in ``docs/llm-baseline-results.md``.
DEFAULT_MODEL = "qwen2.5-coder:7b"

#: Environment override for the preferred model tag.
MODEL_ENV_VAR = "CBM_LLM_MODEL"

#: Same-family tags, largest → smallest. Used as the auto-solve fallback
#: chain when the preferred tag is not installed: quality tracks size, so
#: the largest *installed* tag wins. Extend this list to admit new
#: same-family sizes; keep it descending.
FALLBACK_MODELS: tuple[str, ...] = (
    "qwen2.5-coder:7b",
    "qwen2.5-coder:3b",
    "qwen2.5-coder:1.5b",
    "qwen2.5-coder:0.5b",
)

_log = logging.getLogger("cbm.llm_enrich")


def preferred_model(explicit: str | None = None) -> str:
    """Top model preference, with no availability check.

    Resolution order (first truthy wins): ``explicit`` argument →
    ``$CBM_LLM_MODEL`` → :data:`DEFAULT_MODEL`.
    """
    return explicit or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL


def candidate_models(explicit: str | None = None) -> list[str]:
    """Ordered, de-duplicated resolution candidates.

    The preferred model (see :func:`preferred_model`) first, then the
    same-family :data:`FALLBACK_MODELS` chain with the preferred tag
    removed from its later position so it is never tried twice.
    """
    top = preferred_model(explicit)
    ordered: list[str] = [top]
    for tag in FALLBACK_MODELS:
        if tag not in ordered:
            ordered.append(tag)
    return ordered


def resolve_model(
    client: OllamaClient | None,
    explicit: str | None = None,
) -> str | None:
    """Return the best *installed* enrichment model, or ``None``.

    Semantics — never raises:

    ==========================================  ==================================
    Condition                                   Result
    ==========================================  ==================================
    ``client is None``                          ``None``  (no-op wiring)
    reachable, a candidate installed            that tag  (auto-solved)
    reachable, no candidate installed           ``None``  (degrade cleanly)
    unreachable                                 ``None``  (degrade cleanly)
    ==========================================  ==================================

    A substitution away from the preferred tag is logged at WARNING so an
    operator can see the pipeline auto-solved a model mismatch rather than
    silently using a different (and lower-quality) model.
    """
    if client is None:
        return None

    # Probe what's installed. resolve_model's contract is "never raises —
    # degrade to None", so *any* probe failure (server unreachable, or a
    # stub/offline client that does not implement available_models) means
    # "cannot auto-resolve": the caller keeps its preferred model and the
    # runtime degradation path takes over. This is deliberate — a
    # cache-only client (e.g. the CI-determinism fixture stub) must not be
    # silently switched to a different model, which would change cache
    # keys and break the byte-identical guarantee.
    try:
        installed = set(client.available_models())
    except OllamaUnreachable as exc:
        _log.warning(
            "llm_enrich: cannot resolve model — Ollama unreachable: %s", exc,
        )
        return None
    except Exception as exc:  # noqa: BLE001 — contract: never raise
        _log.warning(
            "llm_enrich: cannot resolve model — availability probe failed "
            "(%s: %s); keeping preferred model", type(exc).__name__, exc,
        )
        return None

    candidates = candidate_models(explicit)
    for cand in candidates:
        if cand in installed:
            if cand != candidates[0]:
                _log.warning(
                    "llm_enrich: preferred model %r not installed; "
                    "auto-resolved to %r (installed: %s)",
                    candidates[0], cand, sorted(installed),
                )
            return cand

    _log.warning(
        "llm_enrich: no suitable model installed "
        "(tried %s; installed: %s) — enrichment disabled for this run",
        candidates, sorted(installed),
    )
    return None
