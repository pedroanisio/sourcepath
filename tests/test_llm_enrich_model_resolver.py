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
    """Name-only client: no capability metadata, like an older server."""

    def __init__(self, installed, *, unreachable: bool = False):
        self._installed = list(installed)
        self._unreachable = unreachable

    def available_models(self):
        if self._unreachable:
            raise OllamaUnreachable("simulated: server down")
        return list(self._installed)


class _CatalogClient(_FakeClient):
    """Client whose server reports per-model ``capabilities`` in /api/tags
    (Ollama >= 0.32 does). ``entries`` mirrors the real payload shape."""

    def __init__(self, entries, *, unreachable: bool = False):
        super().__init__([e["name"] for e in entries], unreachable=unreachable)
        self._entries = list(entries)

    def model_catalog(self):
        if self._unreachable:
            raise OllamaUnreachable("simulated: server down")
        return [dict(e) for e in self._entries]


def _entry(name, caps, size="7B"):
    return {"name": name, "capabilities": list(caps),
            "details": {"parameter_size": size}}


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
    """Name-only evidence: a tag outside the known family is NOT assumed
    usable. Guessing from a name is exactly the unverified premise this
    project forbids — degrade instead."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _FakeClient(["llama3:8b", "mistral:latest"])
    assert resolve_model(client) is None


# --------------------------------------------------------------------------
# capability-based last resort — cross-family, but only on server evidence
# --------------------------------------------------------------------------


def test_resolve_uses_completion_capable_model_when_family_chain_misses(monkeypatch):
    """THE second regression: a host with no qwen2.5-coder tag but a
    perfectly capable instruct model emitted zero L4 enrichments. When the
    server *reports* the completion capability, use it."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _CatalogClient([
        _entry("nomic-embed-text:latest", ["embedding"], "137M"),
        _entry("qwen2.5:14b-instruct", ["completion", "tools"], "14.8B"),
    ])
    assert resolve_model(client) == "qwen2.5:14b-instruct"


def test_resolve_never_selects_an_embedding_only_model(monkeypatch):
    """An embedding model cannot answer /api/chat. Selecting one would
    turn a clean degradation into a run-long stream of failures."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _CatalogClient([
        _entry("nomic-embed-text:latest", ["embedding"], "137M"),
        _entry("mxbai-embed-large:latest", ["embedding"], "335M"),
    ])
    assert resolve_model(client) is None


def test_resolve_prefers_code_specialized_over_larger_general(monkeypatch):
    """Task is code annotation: a code-specialized tag wins over a bigger
    general one, even though the general one has more parameters."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _CatalogClient([
        _entry("qwen3-vl:32b-instruct", ["vision", "completion"], "33.4B"),
        _entry("some-coder:8b", ["completion"], "8B"),
    ])
    assert resolve_model(client) == "some-coder:8b"


def test_resolve_prefers_larger_when_neither_is_code_specialized(monkeypatch):
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _CatalogClient([
        _entry("small:latest", ["completion"], "3B"),
        _entry("big:latest", ["completion"], "33.4B"),
        _entry("tiny:latest", ["completion"], "500M"),
    ])
    assert resolve_model(client) == "big:latest"


def test_family_chain_still_wins_over_capability_fallback(monkeypatch):
    """The curated chain is benchmark-backed (docs/llm-baseline-results.md);
    the capability sweep is a last resort, not a competitor."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _CatalogClient([
        _entry("qwen2.5-coder:1.5b", ["completion"], "1.5B"),
        _entry("huge-general:70b", ["completion"], "70B"),
    ])
    assert resolve_model(client) == "qwen2.5-coder:1.5b"


def test_capability_fallback_ignores_entries_without_capability_data(monkeypatch):
    """An older server omits ``capabilities``. Absent evidence is not
    evidence — such entries are skipped, not assumed usable."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _CatalogClient([
        {"name": "mystery:latest", "details": {"parameter_size": "70B"}},
    ])
    assert resolve_model(client) is None


def test_capability_fallback_survives_catalog_probe_failure(monkeypatch):
    """model_catalog() raising must not break the never-raises contract."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)

    class _AngryCatalog(_FakeClient):
        def model_catalog(self):
            raise ValueError("kaboom")

    assert resolve_model(_AngryCatalog(["llama3:8b"])) is None


def test_capability_fallback_handles_unparsable_parameter_size(monkeypatch):
    """A malformed size must not crash the ranking — it sorts last."""
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)
    client = _CatalogClient([
        {"name": "weird:latest", "capabilities": ["completion"],
         "details": {"parameter_size": "not-a-size"}},
        _entry("normal:latest", ["completion"], "8B"),
    ])
    assert resolve_model(client) == "normal:latest"


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


def test_register_all_honors_env_var_when_model_arg_is_none(monkeypatch):
    """``model=None`` must defer to $CBM_LLM_MODEL. Guards the CLI
    contract: an argparse default of DEFAULT_MODEL (rather than None)
    passes a non-None explicit value on every invocation and silently
    kills the env var that .env.example documents as working."""
    monkeypatch.setenv(MODEL_ENV_VAR, "qwen2.5-coder:3b")
    register_all(client=None, cache=None, scopes=("files",))
    enr_model, agg_model = _registered_models()
    assert enr_model == "qwen2.5-coder:3b"
    assert agg_model == "qwen2.5-coder:3b"


# --------------------------------------------------------------------------
# CLI wiring — run_l4.py must not defeat $CBM_LLM_MODEL with its default
# --------------------------------------------------------------------------


def test_run_l4_llm_model_flag_defaults_to_none(monkeypatch):
    """The regression this guards: ``--llm-model`` defaulted to the
    DEFAULT_MODEL literal, so ``preferred_model(explicit)`` always got a
    truthy value and $CBM_LLM_MODEL was dead on the CLI path — while
    .env.example advertised it as live. The flag must default to None and
    let the documented precedence chain resolve it."""
    import importlib

    run_l4 = importlib.import_module("scripts.run_l4")
    parser_defaults = {}

    class _CapturingParser:
        def __init__(self, *a, **kw):
            pass

        def add_argument(self, *names, **kw):
            for n in names:
                if n.startswith("--"):
                    parser_defaults[n] = kw.get("default", "<required>")

        def add_mutually_exclusive_group(self, **kw):
            return self

        def error(self, msg):
            raise SystemExit(msg)

        def parse_args(self, argv=None):
            raise SystemExit(0)

    monkeypatch.setattr(run_l4.argparse, "ArgumentParser", _CapturingParser)
    with pytest.raises(SystemExit):
        run_l4.main([])

    assert parser_defaults["--llm-model"] is None, (
        "--llm-model must default to None so $CBM_LLM_MODEL is honored; "
        f"got {parser_defaults['--llm-model']!r}"
    )
