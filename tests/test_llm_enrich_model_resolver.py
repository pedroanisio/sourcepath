"""Regression tests for LLM-enrichment model resolution.

Root cause guarded here: the pipeline was hard-locked to
``qwen2.5-coder:7b``. On a host with only a smaller same-family tag
installed (e.g. ``qwen2.5-coder:1.5b``), every generate call 404'd, the
enricher/aggregator silently disabled themselves, and bundles emitted
with zero L4 enrichments — while the connectivity-only skip guard kept
the contract tests running, so they failed instead of skipping. See
``plugins/llm_enrich/model_resolver.py`` and the failure analysis in
``frontend/mcp_server/tests/test_llm_enrich_surface.py``.

These tests are pure unit tests — no live Ollama. A fake client stubs
``available_models()`` so every branch of ``resolve_model`` and the
``register_all`` auto-resolution wiring is exercised deterministically.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from plugins.llm_enrich import register_all  # noqa: E402
from plugins.llm_enrich.client import OllamaUnreachable  # noqa: E402
from plugins.llm_enrich.model_resolver import (  # noqa: E402
    DEFAULT_MODEL,
    MODEL_ENV_VAR,
    candidate_models,
    preferred_model,
    resolve_model,
)
from codebase_mapper.shared_kernel.extensions import (  # noqa: E402
    iter_aggregators,
    iter_record_enrichers,
    reset_registries,
)


class _FakeClient:
    """Stubs the one method ``resolve_model`` calls."""

    def __init__(self, installed, *, unreachable: bool = False):
        self._installed = list(installed)
        self._unreachable = unreachable

    def available_models(self):
        if self._unreachable:
            raise OllamaUnreachable("simulated: server down")
        return list(self._installed)


# --------------------------------------------------------------------------
# preferred_model — override precedence
# --------------------------------------------------------------------------


def test_preferred_model_defaults(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    assert preferred_model() == DEFAULT_MODEL


def test_preferred_model_env_override(monkeypatch):
    monkeypatch.setenv(MODEL_ENV_VAR, "qwen2.5-coder:3b")
    assert preferred_model() == "qwen2.5-coder:3b"


def test_preferred_model_explicit_beats_env(monkeypatch):
    monkeypatch.setenv(MODEL_ENV_VAR, "qwen2.5-coder:3b")
    assert preferred_model("qwen2.5-coder:0.5b") == "qwen2.5-coder:0.5b"


# --------------------------------------------------------------------------
# candidate_models — ordering + dedup
# --------------------------------------------------------------------------


def test_candidate_models_preferred_first_no_duplicates(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    cands = candidate_models()
    assert cands[0] == DEFAULT_MODEL
    assert len(cands) == len(set(cands)), f"duplicate candidate: {cands}"
    # The preferred tag must not appear a second time via the fallback chain.
    assert cands.count(DEFAULT_MODEL) == 1


def test_candidate_models_explicit_prepended(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    cands = candidate_models("some-other:model")
    assert cands[0] == "some-other:model"
    assert "qwen2.5-coder:7b" in cands  # fallback chain still appended


# --------------------------------------------------------------------------
# resolve_model — the core auto-solve
# --------------------------------------------------------------------------


def test_resolve_exact_preferred_installed(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _FakeClient(["qwen2.5-coder:7b", "qwen2.5-coder:1.5b"])
    assert resolve_model(client) == "qwen2.5-coder:7b"


def test_resolve_auto_solves_when_preferred_missing(monkeypatch):
    """THE regression: prefer 7b, only 1.5b installed → resolve to 1.5b
    instead of failing / emitting an un-enriched bundle."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _FakeClient(["qwen2.5-coder:1.5b"])
    assert resolve_model(client) == "qwen2.5-coder:1.5b"


def test_resolve_prefers_largest_available(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _FakeClient(["qwen2.5-coder:0.5b", "qwen2.5-coder:3b"])
    # 7b missing → next largest in the descending chain that is installed.
    assert resolve_model(client) == "qwen2.5-coder:3b"


def test_resolve_honors_env_preference(monkeypatch):
    monkeypatch.setenv(MODEL_ENV_VAR, "qwen2.5-coder:1.5b")
    # Both installed, but env pins 1.5b as the top preference.
    client = _FakeClient(["qwen2.5-coder:7b", "qwen2.5-coder:1.5b"])
    assert resolve_model(client) == "qwen2.5-coder:1.5b"


def test_resolve_none_when_no_suitable_model(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _FakeClient(["llama3:8b", "mistral:latest"])
    assert resolve_model(client) is None


def test_resolve_none_when_unreachable(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _FakeClient([], unreachable=True)
    assert resolve_model(client) is None


def test_resolve_none_when_client_lacks_available_models(monkeypatch):
    """A cache-only stub (no available_models method) must not crash the
    probe — resolve returns None so the caller keeps its preferred model.
    Guards the CI-determinism fixture stub regression."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)

    class _CacheOnlyStub:
        host = "stub://no-ollama"

        def ping(self):
            return True

    assert resolve_model(_CacheOnlyStub()) is None


def test_resolve_none_when_probe_raises_arbitrary_exception(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)

    class _AngryClient:
        def available_models(self):
            raise ValueError("kaboom")

    assert resolve_model(_AngryClient()) is None


def test_resolve_none_when_client_none(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    assert resolve_model(None) is None


def test_resolve_explicit_arg_used_as_top_preference(monkeypatch):
    monkeypatch.setenv(MODEL_ENV_VAR, "qwen2.5-coder:3b")
    client = _FakeClient(["qwen2.5-coder:7b", "qwen2.5-coder:0.5b"])
    # explicit beats env; 0.5b installed and requested → picked.
    assert resolve_model(client, "qwen2.5-coder:0.5b") == "qwen2.5-coder:0.5b"


# --------------------------------------------------------------------------
# register_all — the pipeline wires the auto-solved model
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registries():
    reset_registries()
    yield
    reset_registries()


def _registered_models():
    enr = next(e for e in iter_record_enrichers() if e.name == "l4_10_enrich")
    agg = next(a for a in iter_aggregators() if a.name == "l4_20_enrich")
    return enr.model, agg.model


def test_register_all_wires_auto_solved_model(monkeypatch):
    """register_all with only 1.5b installed must wire 1.5b into BOTH the
    enricher and aggregator — proving the bundle would carry provenance
    for the model actually used, not the missing default."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _FakeClient(["qwen2.5-coder:1.5b"])
    register_all(client=client, cache=None, scopes=("files", "concepts"))
    enr_model, agg_model = _registered_models()
    assert enr_model == "qwen2.5-coder:1.5b"
    assert agg_model == "qwen2.5-coder:1.5b"


def test_register_all_keeps_preferred_when_installed(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _FakeClient(["qwen2.5-coder:7b"])
    register_all(client=client, cache=None, scopes=("files",))
    enr_model, _ = _registered_models()
    assert enr_model == "qwen2.5-coder:7b"


def test_register_all_no_suitable_model_falls_back_to_preferred(monkeypatch):
    """No qwen tag installed → wire the preferred literal so the runtime
    degradation path (log + skip, SHACL stays green) takes over, exactly
    as before this fix."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _FakeClient(["llama3:8b"])
    register_all(client=client, cache=None, scopes=("files",))
    enr_model, _ = _registered_models()
    assert enr_model == DEFAULT_MODEL


def test_register_all_auto_resolve_disabled(monkeypatch):
    """auto_resolve=False bypasses the availability probe entirely."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _FakeClient(["qwen2.5-coder:1.5b"])
    register_all(client=client, cache=None, scopes=("files",),
                 auto_resolve=False)
    enr_model, _ = _registered_models()
    assert enr_model == DEFAULT_MODEL


def test_register_all_none_client_uses_preferred(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    register_all(client=None, cache=None, scopes=("files",))
    enr_model, _ = _registered_models()
    assert enr_model == DEFAULT_MODEL
