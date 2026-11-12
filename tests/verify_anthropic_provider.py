"""Offline contract tests for the CBM_LLM_PROVIDER toggle.

No network, no API key, no `anthropic` package required: every test
drives :class:`AnthropicClient` through its ``sdk_client`` seam with a
fake that records what it was sent.

What is asserted, in order of what would hurt most if it broke:

1. Default-off. An unset toggle still builds an OllamaClient, so the
   back-compat anchor (``tests/verify_llm_enrich.py``: a default run is
   byte-identical to a no-plugin run) is untouched by this feature.
2. A typo in the toggle raises instead of silently running locally.
3. The remote client raises only the two exceptions the enricher
   catches — anything else would crash a run mid-bundle.
4. A refusal returns "" rather than raising, so one declined file
   cannot disable enrichment for the rest of the repo.
5. The request never carries `temperature` or `seed` (both rejected by
   the API; the second simply does not exist there).
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from plugins.llm_enrich import provider as prov  # noqa: E402
from plugins.llm_enrich.anthropic_client import AnthropicClient  # noqa: E402
from plugins.llm_enrich.client import (  # noqa: E402
    OllamaClient,
    OllamaModelMissing,
    OllamaUnreachable,
)

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL {name}: {detail}")


class FakeResponse:
    def __init__(self, text="ok", stop_reason="end_turn", category=None):
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.stop_reason = stop_reason
        self.stop_details = (
            types.SimpleNamespace(type="refusal", category=category)
            if stop_reason == "refusal" else None
        )
        self.usage = types.SimpleNamespace(
            input_tokens=1200, output_tokens=40, cache_read_input_tokens=0)


class FakeSdk:
    """Minimal stand-in for `anthropic.Anthropic`."""

    def __init__(self, response=None, raises=None):
        self.calls: list[dict] = []
        self._response = response or FakeResponse()
        self._raises = raises
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if outer._raises is not None:
                    raise outer._raises
                return outer._response

        self.beta = types.SimpleNamespace(messages=_Messages())
        self.models = types.SimpleNamespace(
            retrieve=lambda _m: types.SimpleNamespace(id=_m),
            list=lambda: [types.SimpleNamespace(id="claude-sonnet-5-5")],
        )


def _client(**kw) -> AnthropicClient:
    return AnthropicClient(sdk_client=kw.pop("sdk"), **kw)


def test_default_is_local() -> None:
    print("\n[1] default-off")
    saved = os.environ.pop(prov.PROVIDER_ENV_VAR, None)
    try:
        check("unset toggle resolves to ollama",
              prov.resolve_provider() == prov.OLLAMA)
        client, name, _model = prov.make_client()
        check("unset toggle builds an OllamaClient",
              isinstance(client, OllamaClient), type(client).__name__)
        check("ollama is not remote", not prov.is_remote(name))
        check("ollama keeps its default model",
              prov.default_model(prov.OLLAMA).startswith("qwen"),
              prov.default_model(prov.OLLAMA))
        check("anthropic default is Sonnet 5.5",
              prov.default_model(prov.ANTHROPIC) == "claude-sonnet-5-5",
              prov.default_model(prov.ANTHROPIC))
        check("auto-resolve only for ollama",
              prov.supports_auto_resolve(prov.OLLAMA)
              and not prov.supports_auto_resolve(prov.ANTHROPIC))
    finally:
        if saved is not None:
            os.environ[prov.PROVIDER_ENV_VAR] = saved


def test_typo_raises() -> None:
    print("\n[2] a typo is an error, not a silent local run")
    try:
        prov.resolve_provider("antropic")
        check("unknown provider raises", False, "no exception")
    except prov.UnknownProvider as e:
        check("unknown provider raises", True)
        check("message names the bad value", "antropic" in str(e), str(e))


def test_model_precedence() -> None:
    print("\n[3] model precedence: explicit > $CBM_LLM_MODEL > default")
    saved = os.environ.pop("CBM_LLM_MODEL", None)
    try:
        check("default when nothing set",
              prov.resolve_preferred_model(prov.ANTHROPIC) == "claude-sonnet-5-5")
        os.environ["CBM_LLM_MODEL"] = "claude-haiku-5-5"
        check("env var wins over default",
              prov.resolve_preferred_model(prov.ANTHROPIC) == "claude-haiku-5-5")
        check("explicit wins over env var",
              prov.resolve_preferred_model(prov.ANTHROPIC, "claude-opus-5-5")
              == "claude-opus-5-5")
    finally:
        os.environ.pop("CBM_LLM_MODEL", None)
        if saved is not None:
            os.environ["CBM_LLM_MODEL"] = saved


def test_happy_path_request_shape() -> None:
    print("\n[4] request shape")
    sdk = FakeSdk()
    c = _client(sdk=sdk)
    text, dt = c.chat(model="claude-sonnet-5-5", system="S", user="U", seed=42)
    check("returns the text", text == "ok", repr(text))
    check("returns wall seconds", isinstance(dt, float) and dt >= 0)
    sent = sdk.calls[0]
    check("no temperature (API rejects it)", "temperature" not in sent)
    check("no seed (not an API parameter)", "seed" not in sent)
    check("thinking off for Sonnet 5.5 via between_tools",
          sent.get("thinking") == {"type": "between_tools"},
          str(sent.get("thinking")))
    check("effort is set", sent.get("output_config", {}).get("effort") == "low",
          str(sent.get("output_config")))
    check("system sent as a top-level field", sent.get("system") == "S")
    check("refusal fallback enabled", sent.get("fallbacks") == "default")
    check("fallback beta sent",
          "server-side-fallback-2026-07-01" in (sent.get("betas") or []))
    check("usage accumulated", c.usage.input_tokens == 1200 and c.usage.calls == 1)
    cost = c.usage.cost_usd("claude-sonnet-5-5")
    check("cost computed for a priced model",
          cost is not None and abs(cost - (1200 / 1e6 * 2 + 40 / 1e6 * 10)) < 1e-9,
          str(cost))
    check("no cost invented for an unpriced model",
          c.usage.cost_usd("some-future-model") is None)


def test_refusal_is_not_an_error() -> None:
    print("\n[5] a refusal skips one file, it does not stop the run")
    sdk = FakeSdk(FakeResponse(text="I can't help", stop_reason="refusal",
                               category="cyber"))
    c = _client(sdk=sdk)
    text, _dt = c.chat(model="claude-sonnet-5-5", system="S", user="U")
    check("returns empty string", text == "", repr(text))
    check("counted as a refusal", c.usage.refusals == 1)


def test_errors_map_to_degradation_signals() -> None:
    print("\n[6] only the two exceptions the enricher catches")
    try:
        import anthropic
    except ImportError:
        print("  SKIP anthropic package not installed; "
              "exception mapping needs its classes")
        return

    req = types.SimpleNamespace()
    cases = [
        ("404 -> model missing",
         anthropic.NotFoundError("nope", response=_resp(404), body=None),
         OllamaModelMissing),
        ("401 -> unreachable",
         anthropic.AuthenticationError("bad key", response=_resp(401), body=None),
         OllamaUnreachable),
        ("429 -> unreachable",
         anthropic.RateLimitError("slow down", response=_resp(429), body=None),
         OllamaUnreachable),
        ("connection -> unreachable",
         anthropic.APIConnectionError(request=req), OllamaUnreachable),
        ("plain error -> unreachable", ValueError("boom"), OllamaUnreachable),
    ]
    for name, exc, expected in cases:
        c = _client(sdk=FakeSdk(raises=exc))
        try:
            c.chat(model="m", system="S", user="U")
            check(name, False, "no exception raised")
        except (OllamaUnreachable, OllamaModelMissing) as got:
            check(name, isinstance(got, expected), type(got).__name__)
        except Exception as got:  # noqa: BLE001
            check(name, False, f"leaked {type(got).__name__}: {got}")


def _resp(status: int):
    """Build a response object of whatever HTTP lib the SDK is built on.

    anthropic 1.x moved to `httpx2`; the Ollama client in this same
    package still uses `httpx`. Both can be installed at once, so the
    test picks the one the SDK's exception classes will accept rather
    than assuming either.
    """
    try:
        import httpx2 as h
    except ImportError:
        import httpx as h
    return h.Response(status, request=h.Request("POST", "https://x"))


def test_catalog_shape() -> None:
    print("\n[7] catalog duck-types Ollama's /api/tags")
    c = _client(sdk=FakeSdk())
    cat = c.model_catalog()
    check("entries have a name", all("name" in e for e in cat), str(cat))
    check("entries claim completion",
          all("completion" in e["capabilities"] for e in cat), str(cat))
    check("available_models lists ids",
          c.available_models() == ["claude-sonnet-5-5"], str(c.available_models()))
    check("ping is true when the model resolves", c.ping() is True)


def test_thinking_is_per_model() -> None:
    print("\n[8] thinking off is per-model, and yields when it cannot apply")
    cases = [
        ("sonnet 5.5 -> between_tools", "claude-sonnet-5-5", "low",
         {"type": "between_tools"}),
        ("haiku 5.5 -> disabled", "claude-haiku-5-5", "low",
         {"type": "disabled"}),
        ("opus 5.5 -> no field (cannot be disabled)", "claude-opus-5-5",
         "low", None),
        ("xhigh effort -> no field (400 if combined)", "claude-sonnet-5-5",
         "xhigh", None),
    ]
    for name, model, effort, expected in cases:
        sdk = FakeSdk()
        c = _client(sdk=sdk, effort=effort)
        c.chat(model=model, system="S", user="U")
        check(name, sdk.calls[0].get("thinking") == expected,
              str(sdk.calls[0].get("thinking")))

    sdk = FakeSdk()
    c = _client(sdk=sdk, thinking="adaptive")
    c.chat(model="claude-sonnet-5-5", system="S", user="U")
    check("thinking=adaptive opts back in", "thinking" not in sdk.calls[0])


def test_instructed_decline_survives() -> None:
    print("\n[9] the prompt's own decline is not a refusal")
    from plugins.llm_enrich.validation import validate

    accepted = [
        "insufficient context to characterize this concept",
        "I cannot characterize this concept from the data provided.",
        "There is insufficient context here; I can not draw a conclusion.",
        "The data is too sparse. I am unable to characterize this concept.",
        "Not enough information is available to describe this concept.",
    ]
    canonical = "insufficient context to characterize this concept"
    for text in accepted:
        r = validate("concept_description", text)
        check(f"accepted: {text[:44]!r}", r.ok, str(r.reason))
        if r.ok:
            check("  normalized to the canonical sentinel",
                  r.text == canonical, r.text)

    still_refusals = [
        "I'm sorry, I can't help with that.",
        "As an AI language model, I do not analyze source code.",
        "I am unable to assist with this request.",
        "I don't have access to that information.",
    ]
    for text in still_refusals:
        r = validate("concept_description", text)
        check(f"still rejected: {text[:44]!r}",
              (not r.ok) and r.reason == "refusal", f"ok={r.ok} {r.reason}")

    # A bare inability with no insufficiency marker stays a refusal: it is
    # the model declining without reaching the prompt's escape hatch.
    r = validate("concept_description", "I cannot do this.")
    check("bare 'I cannot' with no marker is still a refusal",
          (not r.ok) and r.reason == "refusal", f"ok={r.ok} {r.reason}")

    # Caveats. These are verbatim tails from the first real run, where a
    # good anchored description ended by marking the limit of what the
    # data showed. Scanning the whole text called every one a refusal and
    # threw the annotation away; the answer opens with content, so it must
    # survive — and survive *whole*, not normalized to the sentinel.
    caveats = [
        'In this codebase, "order_response" appears 21 times under the forms '
        "`OrderResponse` and `orderResponse`. The data does not show the fields "
        "of `OrderResponse`, so I cannot say how the QR code data is carried "
        "within it.",
        'In this codebase, "changelog" refers to versioned migration files under '
        "`src/main/resources/db/changelog/`. That naming matches Liquibase "
        "conventions, but the data does not name the tool, so I can't confirm it.",
        'The concept "method" is declared across three service classes and a '
        "guard condition that branch on it, though without the method bodies I "
        "can't say what the guard enforces.",
        'In this codebase, "page" appears seven times in `PageableDto.java`. How '
        "it is consumed cannot be determined from this context.",
    ]
    for text in caveats:
        r = validate("concept_description", text)
        check(f"caveat survives: ...{text[-46:]!r}", r.ok, str(r.reason))
        if r.ok:
            check("  kept whole, not collapsed to the sentinel",
                  r.text != canonical and len(r.text) > 60, r.text[:70])

    # The kind without that sentinel must not gain the escape hatch.
    r = validate("file_summary", "I cannot summarize: insufficient context.")
    check("file_summary does not inherit the concept sentinel",
          (not r.ok) and r.reason == "refusal", f"ok={r.ok} {r.reason}")


def test_java_is_summarized() -> None:
    print("\n[10] the JVM/legacy languages are in file_summary scope")
    from plugins.llm_enrich.enricher import SUPPORTED_LANGUAGES

    for lang in ("java", "clojure", "cobol"):
        check(f"{lang} in scope", lang in SUPPORTED_LANGUAGES)
    # The exclusions the existing contract test pins must survive.
    for lang in ("json", "yaml"):
        check(f"{lang} still out of scope", lang not in SUPPORTED_LANGUAGES)


def main() -> int:
    print("verify_anthropic_provider: offline contract for CBM_LLM_PROVIDER")
    test_default_is_local()
    test_typo_raises()
    test_model_precedence()
    test_happy_path_request_shape()
    test_refusal_is_not_an_error()
    test_errors_map_to_degradation_signals()
    test_catalog_shape()
    test_thinking_is_per_model()
    test_instructed_decline_survives()
    test_java_is_summarized()
    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
