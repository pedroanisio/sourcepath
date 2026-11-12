"""AnthropicClient — Claude-backed drop-in for :class:`OllamaClient`.

Reached only through the ``CBM_LLM_PROVIDER=anthropic`` feature toggle
(see :mod:`plugins.llm_enrich.provider`). With the toggle unset the
module is never constructed and the ``anthropic`` package is never
imported, so the default local-Ollama path is byte-identical to before.

Why a separate class instead of a flag on ``OllamaClient``: the two
providers agree on *the call* (one system + one user message in, one
string out) and on nothing else. Ollama resolves a model by asking the
server what is installed; Anthropic resolves it by name against a
hosted catalog. Keeping them apart lets each own its own failure modes
and leaves the Ollama class untouched.

Interface contract — duck-types ``OllamaClient``:

    chat(model, system, user, *, seed=0) -> (text, wall_seconds)
    ping() -> bool
    available_models() -> list[str]
    model_catalog() -> list[dict]
    close() -> None

and, critically, it raises only the two exceptions the enricher and the
aggregator already catch (``OllamaUnreachable`` / ``OllamaModelMissing``).
Those names say "Ollama" for history's sake; here they mean "provider
unreachable" and "model not available". Raising anything else would
crash a run mid-bundle, which this pipeline never does — its documented
failure mode is "no L4 triples emitted, SHACL stays green".

Three deliberate differences from the Ollama client, each forced by the
API rather than chosen:

* **No ``temperature``.** The Messages API rejects it (HTTP 400 as of
  API version ``2026-10-21``). The Ollama client pins ``temperature=0``
  for determinism; here determinism is best-effort and the on-disk
  cache is what actually guarantees repeat runs are stable.
* **No ``seed``.** Not a parameter of the API. ``chat()`` accepts it so
  the signatures match, and ignores it. It is not part of the cache key
  either (see ``cache.get_or_compute``), so nothing downstream drifts.
* **Thinking is off by default, and how to turn it off is per-model.**
  ``{"type": "disabled"}`` is a 400 on Claude Sonnet 5.5, which wants
  ``{"type": "between_tools"}``; Claude Haiku 5.5 takes ``disabled``;
  Claude Opus 5.5 takes neither and always thinks. See
  :data:`THINKING_OFF_BY_MODEL` for why this is worth the branching —
  thinking tokens were 82% of the bill on the first real run. Depth,
  where thinking does run, is ``output_config.effort``, default ``low``.

A refusal (``stop_reason == "refusal"``) is *not* an error. Source files
touching credentials, tokens or crypto can trip a safety classifier, and
one refused file must not disable enrichment for the other 164. The call
returns ``""``, which ``validation.py`` rejects as ``empty`` and
``graph_writer`` skips — the file simply gets no annotation. Server-side
fallback is enabled so most would-be refusals are answered by another
model before reaching that path.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .client import OllamaModelMissing, OllamaUnreachable

#: Default Claude model for enrichment. Sonnet is the right tier here:
#: the task is short-form summarization of a 4000-char window, run once
#: per file, and the result is cached on disk forever after.
DEFAULT_MODEL = "claude-sonnet-5-5"

#: The prompts ask for one sentence under 30 words. 2048 is slack, not
#: a budget — it exists so a pathological generation cannot run away.
DEFAULT_MAX_TOKENS = 2048

#: ``output_config.effort``. ``low`` for summarization; raise per-run
#: with $CBM_ANTHROPIC_EFFORT if a corpus proves to need it.
DEFAULT_EFFORT = "low"

#: How to turn thinking off, per model — there is no portable way.
#: Claude Sonnet 5.5 rejects ``{"type": "disabled"}`` with a 400 and takes
#: ``{"type": "between_tools"}`` instead; Claude Haiku 5.5 takes
#: ``disabled``; Claude Opus 5.5 rejects both at every effort level, so
#: thinking simply stays on there and effort is the only lever.
#:
#: This is not a micro-optimization. Measured on a 219-call run over
#: payment-application: 72,337 input tokens against 68,230 output — 311
#: output tokens per call for a prompt that asks for one sentence under 30
#: words. The excess is thinking, billed as output at 5x the input rate, so
#: it was 82% of the bill. Enrichment is single-shot summarization with no
#: tools and a validator that rejects anything shaped unlike one sentence;
#: there is nothing for a reasoning pass to earn here.
THINKING_OFF_BY_MODEL: dict[str, dict[str, str]] = {
    "claude-sonnet-5-5": {"type": "between_tools"},
    "claude-haiku-5-5": {"type": "disabled"},
}

#: Both off-switches above are accepted only at effort ``high`` or below;
#: at ``xhigh``/``max`` they are a 400. A run that asks for deep effort has
#: asked for thinking, so the switch yields rather than erroring.
_EFFORT_FORBIDS_THINKING_OFF = frozenset({"xhigh", "max"})

#: ``off`` (default) suppresses thinking where the model allows it;
#: ``adaptive`` leaves it on. Any other value falls back to ``off``.
THINKING_ENV_VAR = "CBM_ANTHROPIC_THINKING"
DEFAULT_THINKING = "off"

DEFAULT_TIMEOUT_SECONDS = 180.0

#: Server-side refusal fallback: on a safety refusal the API retries the
#: same request on another model instead of handing back a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

MAX_TOKENS_ENV_VAR = "CBM_ANTHROPIC_MAX_TOKENS"
EFFORT_ENV_VAR = "CBM_ANTHROPIC_EFFORT"
API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"

#: USD per million tokens, for the end-of-run spend line only — never
#: for a decision. Cached constants go stale; a model missing from this
#: table reports token counts with no dollar figure rather than a guess.
#: Source: platform.claude.com pricing, read 2026-11-11.
_PRICES_USD_PER_MTOK: dict[str, tuple[float, float, float]] = {
    # model: (input, output, cache_read)
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
}

_log = logging.getLogger("cbm.llm_enrich")


class AnthropicNotInstalled(RuntimeError):
    """The ``anthropic`` package is not importable.

    Raised at construction, never mid-run: the toggle is an explicit
    operator intent, so a missing dependency is a setup error to fix,
    not something to degrade around.
    """


class AnthropicKeyMissing(RuntimeError):
    """$ANTHROPIC_API_KEY is unset (and no other credential resolved).

    Also a construction-time error, for the same reason. Degrading here
    would hand back an un-enriched bundle with no visible cause — the
    exact silent-failure shape this pipeline is built to avoid.
    """


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        _log.warning(
            "llm_enrich: %s=%r is not an integer; using %d", name, raw, default)
        return default


@dataclass
class Usage:
    """Running token/spend total for one process.

    Mutated from the enricher's parallel pass, hence the lock. Read it
    at the end of a run to print what the run cost.
    """
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    refusals: int = 0
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False)

    def add(self, *, input_tokens: int, output_tokens: int,
            cache_read_tokens: int = 0, refused: bool = False) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.cache_read_tokens += cache_read_tokens
            if refused:
                self.refusals += 1

    def cost_usd(self, model: str) -> float | None:
        """Estimated spend, or ``None`` for a model not in the price table."""
        price = _PRICES_USD_PER_MTOK.get(model)
        if price is None:
            return None
        p_in, p_out, p_cache = price
        fresh_in = max(0, self.input_tokens - self.cache_read_tokens)
        return (
            fresh_in / 1e6 * p_in
            + self.cache_read_tokens / 1e6 * p_cache
            + self.output_tokens / 1e6 * p_out
        )

    def summary(self, model: str) -> str:
        """One operator-facing line. Says tokens always, dollars when known."""
        with self._lock:
            base = (
                f"{self.calls} calls, "
                f"{self.input_tokens:,} in / {self.output_tokens:,} out tokens"
            )
            if self.cache_read_tokens:
                base += f" ({self.cache_read_tokens:,} cached)"
            if self.refusals:
                base += f", {self.refusals} refused"
            cost = self.cost_usd(model)
        if cost is None:
            return f"{base} — no price on file for {model!r}"
        return f"{base} — about ${cost:.2f}"


@dataclass
class AnthropicClient:
    """Claude-backed enrichment client.

    Construct through :func:`plugins.llm_enrich.provider.make_client`
    rather than directly, so the feature toggle stays the single place
    that decides whether code leaves this machine.

    The credential is resolved by the SDK itself (``$ANTHROPIC_API_KEY``,
    then ``$ANTHROPIC_AUTH_TOKEN``, then an ``ant auth login`` profile).
    This class never reads a key out of a file and never logs one.
    """

    model: str = DEFAULT_MODEL
    max_tokens: int = field(
        default_factory=lambda: _int_env(MAX_TOKENS_ENV_VAR, DEFAULT_MAX_TOKENS))
    effort: str = field(
        default_factory=lambda: os.environ.get(EFFORT_ENV_VAR) or DEFAULT_EFFORT)
    thinking: str = field(
        default_factory=lambda: os.environ.get(THINKING_ENV_VAR) or DEFAULT_THINKING)
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    # Test seam: an object exposing .beta.messages.create(...), so the
    # unit tests run with no network and no key.
    sdk_client: Any | None = None
    usage: Usage = field(default_factory=Usage)
    _client: Any | None = field(default=None, init=False, repr=False)
    _client_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False)

    # -- construction ------------------------------------------------

    def __post_init__(self) -> None:
        # Fail fast, at wiring time, where the operator is still looking
        # at the terminal — not on the first record, 40 files in.
        if self.sdk_client is not None:
            return
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise AnthropicNotInstalled(
                "CBM_LLM_PROVIDER=anthropic needs the 'anthropic' package: "
                "pip install 'anthropic>=1.0'"
            ) from e
        if not (os.environ.get(API_KEY_ENV_VAR)
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE")):
            # An `ant auth login` profile on disk also works and is not
            # visible from the environment, so this is a warning-grade
            # check promoted to an error only when nothing at all is set.
            raise AnthropicKeyMissing(
                f"CBM_LLM_PROVIDER=anthropic but ${API_KEY_ENV_VAR} is unset. "
                "Export it (or run `ant auth login`) before enabling the "
                "toggle."
            )

    def _sdk(self) -> Any:
        if self.sdk_client is not None:
            return self.sdk_client
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    import anthropic
                    self._client = anthropic.Anthropic(timeout=self.timeout)
        return self._client

    def close(self) -> None:
        with self._client_lock:
            self._client = None

    # -- OllamaClient-shaped surface ---------------------------------

    @property
    def host(self) -> str:
        """Stand-in for ``OllamaClient.host`` — the CLI prints it."""
        return "api.anthropic.com"

    def ping(self) -> bool:
        """Cheap reachability check. Never raises."""
        try:
            self._sdk().models.retrieve(self.model)
            return True
        except Exception:
            return False

    def model_catalog(self) -> list[dict[str, Any]]:
        """Hosted models, shaped like Ollama's ``/api/tags`` entries.

        Only ``name`` is faithful; ``capabilities`` is synthesized as
        ``["completion"]`` because every chat model here is completion-
        capable. The Ollama resolver reads that field, and claiming it
        for a model the catalog does list is a fact, not a guess.
        """
        try:
            page = self._sdk().models.list()
        except Exception as e:
            raise OllamaUnreachable(f"models.list failed: {e}") from e
        out: list[dict[str, Any]] = []
        for m in page:
            mid = getattr(m, "id", None)
            if mid:
                out.append({"name": mid, "capabilities": ["completion"]})
        return out

    def available_models(self) -> list[str]:
        return [m["name"] for m in self.model_catalog()]

    def chat(
        self, model: str, system: str, user: str,
        *, seed: int = 0,
    ) -> tuple[str, float]:
        """One Messages call. Returns (text, wall seconds).

        ``seed`` is accepted for signature compatibility and ignored —
        the API has no such parameter (see the module docstring).

        Raises only ``OllamaUnreachable`` / ``OllamaModelMissing``, the
        two signals the enricher and aggregator already degrade on.
        """
        del seed  # not an API parameter; kept so the signatures match

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": self.effort},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        off = self._thinking_off_param(model)
        if off is not None:
            kwargs["thinking"] = off

        t0 = time.time()
        try:
            resp = self._sdk().beta.messages.create(**kwargs)
        except Exception as e:
            raise self._translate(e, model) from e
        elapsed = time.time() - t0

        usage = getattr(resp, "usage", None)
        self.usage.add(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read_tokens=int(
                getattr(usage, "cache_read_input_tokens", 0) or 0),
            refused=getattr(resp, "stop_reason", None) == "refusal",
        )

        # Check the stop reason before reading content: on a refusal the
        # content blocks carry no annotation worth storing.
        if getattr(resp, "stop_reason", None) == "refusal":
            details = getattr(resp, "stop_details", None)
            _log.warning(
                "llm_enrich: Claude declined to annotate one input "
                "(category=%s) — that file gets no L4 triple; the run "
                "continues.", getattr(details, "category", None),
            )
            return "", elapsed

        return self._text_of(resp), elapsed

    # -- internals ---------------------------------------------------

    def _thinking_off_param(self, model: str) -> dict[str, str] | None:
        """The ``thinking`` value that turns reasoning off, or ``None``.

        ``None`` means "send no ``thinking`` field", which leaves the model
        on adaptive thinking. That is the answer whenever the operator
        asked for it, the effort level forbids the off-switch, or the model
        has none — Claude Opus 5.5 rejects every way of disabling it.
        """
        if self.thinking != "off":
            return None
        if self.effort in _EFFORT_FORBIDS_THINKING_OFF:
            _log.warning(
                "llm_enrich: effort=%r cannot be combined with thinking off; "
                "leaving thinking on for this run.", self.effort,
            )
            return None
        off = THINKING_OFF_BY_MODEL.get(model)
        if off is None:
            _log.info(
                "llm_enrich: %r has no way to disable thinking; running "
                "adaptive at effort=%r.", model, self.effort,
            )
        return off

    @staticmethod
    def _text_of(resp: Any) -> str:
        """Concatenate the text blocks, ignoring thinking blocks."""
        parts: list[str] = []
        for block in getattr(resp, "content", None) or []:
            if getattr(block, "type", None) == "text":
                parts.append(getattr(block, "text", "") or "")
        return "".join(parts)

    @staticmethod
    def _translate(exc: Exception, model: str) -> Exception:
        """Map an SDK exception onto the pipeline's two degradation signals.

        Everything becomes ``OllamaUnreachable`` except a 404, which is
        ``OllamaModelMissing`` — the caller's message for that case
        ("model not available, disabling for this run") is already the
        right one. 4xx that are our own bug are logged at ERROR so they
        are visible rather than lost among expected degradations.
        """
        try:
            import anthropic
        except ImportError:
            return OllamaUnreachable(f"Anthropic call failed: {exc}")

        if isinstance(exc, anthropic.NotFoundError):
            return OllamaModelMissing(f"model {model!r} not available: {exc}")
        if isinstance(exc, anthropic.AuthenticationError):
            return OllamaUnreachable(
                f"Anthropic rejected the credential (check "
                f"${API_KEY_ENV_VAR}): {exc}")
        if isinstance(exc, anthropic.RateLimitError):
            return OllamaUnreachable(
                f"Anthropic rate limit, retries exhausted: {exc}")
        if isinstance(exc, anthropic.APIConnectionError):
            return OllamaUnreachable(f"cannot reach Anthropic: {exc}")
        if isinstance(exc, anthropic.APIStatusError):
            status = getattr(exc, "status_code", 0)
            if 400 <= status < 500:
                _log.error(
                    "llm_enrich: Anthropic returned %s — this is a malformed "
                    "request, not a transient failure: %s", status, exc,
                )
            return OllamaUnreachable(f"Anthropic HTTP {status}: {exc}")
        return OllamaUnreachable(f"Anthropic call failed: {exc}")
