"""The L4 provider feature toggle.

One question, one place to answer it: *does source code leave this
machine?*

    CBM_LLM_PROVIDER unset | "ollama"   → local Ollama (the default)
    CBM_LLM_PROVIDER = "anthropic"      → Claude, over the network

Default-off is the whole point. Enrichment reads up to 4000 characters
of every source file and sends it to the model; with the local provider
that stays on localhost, with the hosted one it goes to a third party.
That is a decision for whoever owns the code, so it is an explicit,
greppable opt-in rather than an inferred one — the toggle never turns
itself on because a key happens to be exported, and it never silently
falls back to the other provider when the chosen one is misconfigured.

Resolution order for the model, same shape for both providers:

    explicit argument  →  $CBM_LLM_MODEL  →  the provider's default

So ``--llm-model`` still wins, ``$CBM_LLM_MODEL`` still works, and
flipping the provider moves the default from ``qwen2.5-coder:7b`` to
``claude-sonnet-5-5`` without either name being hard-coded at a call
site.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from . import model_resolver
from .client import OllamaClient

#: The toggle.
PROVIDER_ENV_VAR = "CBM_LLM_PROVIDER"

OLLAMA = "ollama"
ANTHROPIC = "anthropic"
PROVIDERS = (OLLAMA, ANTHROPIC)

_log = logging.getLogger("cbm.llm_enrich")


class UnknownProvider(RuntimeError):
    """``$CBM_LLM_PROVIDER`` names a provider that does not exist.

    Strict on purpose: a typo (``antropic``) must not quietly resolve to
    the default and send the operator a local-only bundle they think
    came from Claude.
    """


def resolve_provider(explicit: str | None = None) -> str:
    """Which provider to use. ``explicit`` → ``$CBM_LLM_PROVIDER`` → ollama."""
    name = (explicit or os.environ.get(PROVIDER_ENV_VAR) or OLLAMA).strip().lower()
    if name not in PROVIDERS:
        raise UnknownProvider(
            f"{PROVIDER_ENV_VAR}={name!r} is not one of {list(PROVIDERS)}"
        )
    return name


def is_remote(provider: str) -> bool:
    """True when using this provider sends source code off the machine."""
    return provider == ANTHROPIC


def default_model(provider: str) -> str:
    if provider == ANTHROPIC:
        from .anthropic_client import DEFAULT_MODEL as ANTHROPIC_DEFAULT
        return ANTHROPIC_DEFAULT
    return model_resolver.DEFAULT_MODEL


def resolve_preferred_model(
    provider: str, explicit: str | None = None,
) -> str:
    """explicit → ``$CBM_LLM_MODEL`` → the provider's default."""
    return (
        explicit
        or os.environ.get(model_resolver.MODEL_ENV_VAR)
        or default_model(provider)
    )


def supports_auto_resolve(provider: str) -> bool:
    """Whether :func:`model_resolver.resolve_model` means anything here.

    It does not for Anthropic: that resolver's fallback chain is the
    qwen2.5-coder family, and "the tag is not installed on this server"
    is not a failure mode a hosted catalog has. Letting it run would at
    best waste a round-trip and at worst substitute a model nobody asked
    for.
    """
    return provider == OLLAMA


def make_client(
    *,
    provider: str | None = None,
    host: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> tuple[Any, str, str]:
    """Build the enrichment client for the selected provider.

    Returns ``(client, provider, preferred_model)``.

    ``host`` applies to Ollama only and is ignored by Anthropic (whose
    endpoint is not ours to pick). Misconfiguration raises here, at
    wiring time, rather than degrading — see the module docstring.
    """
    name = resolve_provider(provider)
    preferred = resolve_preferred_model(name, model)

    if name == ANTHROPIC:
        from .anthropic_client import AnthropicClient
        kwargs: dict[str, Any] = {"model": preferred}
        if timeout is not None:
            kwargs["timeout"] = timeout
        client = AnthropicClient(**kwargs)
        _log.warning(
            "llm_enrich: provider=anthropic — file contents (up to 4000 "
            "chars each) will be sent to the Anthropic API using model %r. "
            "Unset $%s to keep enrichment local.",
            preferred, PROVIDER_ENV_VAR,
        )
        return client, name, preferred

    kwargs = {}
    if host:
        kwargs["host"] = host
    if timeout is not None:
        kwargs["timeout"] = timeout
    return OllamaClient(**kwargs), name, preferred
