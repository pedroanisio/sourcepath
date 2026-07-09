"""OllamaClient — single-call wrapper over Ollama's /api/chat endpoint.

Step 2 fills in the stub from Step 1. The client makes one HTTP call
per ``.chat()`` invocation, returns ``(content, wall_seconds)``, and
does no retries — the cache (``plugins.llm_enrich.cache.Cache``)
absorbs transient failures on the next run.

Configuration order (first non-None wins):
  1. constructor ``host`` argument
  2. ``OLLAMA_HOST`` env var
  3. ``DEFAULT_HOST`` = http://localhost:11434

The client honors ``temperature=0`` and a fixed ``seed`` per call.
Determinism is best-effort at the call layer — the spike showed
that Ollama produces byte-identical output for repeated identical
calls *after* the model is warm, but a cold first call may drift.
This is fine because the cache eliminates the call entirely on hit;
on miss, the first call's output is the source of truth and gets
cached.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


DEFAULT_HOST = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 180.0


def resolve_host(explicit: str | None = None) -> str:
    """First non-None of (explicit, $OLLAMA_HOST, DEFAULT_HOST)."""
    if explicit:
        return explicit
    env = os.environ.get("OLLAMA_HOST")
    return env or DEFAULT_HOST


class OllamaUnreachable(RuntimeError):
    """Raised when the Ollama server cannot be reached.

    Callers should treat this as a degradation signal, not a crash —
    the L4 layer's failure mode is "no triples emitted, SHACL stays
    green" (plan Commitment #7)."""


class OllamaModelMissing(RuntimeError):
    """Raised when the requested model is not present on the server."""


@dataclass
class OllamaClient:
    """Thin wrapper over the Ollama HTTP API.

    Caller pattern:

        client = OllamaClient()                # honors $OLLAMA_HOST
        text, dt = client.chat(
            model="qwen2.5-coder:7b",
            system="…", user="…", seed=42,
        )

    The host is resolved at construction time (so swapping
    ``$OLLAMA_HOST`` mid-process has no effect, which is the
    operationally sane choice). The underlying ``httpx.Client`` is
    constructed lazily on first call so importing this module is free.
    """

    host: str = field(default_factory=lambda: resolve_host())
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    # Test seam: an httpx transport (e.g. MockTransport) for offline tests.
    transport: Any | None = None
    _client: httpx.Client | None = field(default=None, init=False, repr=False)
    # chat() may be called from many threads at once (the host's parallel
    # enricher pass); httpx.Client is thread-safe for requests, but the
    # lazy construction below needs the lock so a first-call race can't
    # build (and leak) multiple clients.
    _client_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False)

    def _http(self) -> httpx.Client:
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    kwargs: dict[str, Any] = {
                        "base_url": self.host, "timeout": self.timeout,
                    }
                    if self.transport is not None:
                        kwargs["transport"] = self.transport
                    self._client = httpx.Client(**kwargs)
        return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def ping(self) -> bool:
        """Cheap reachability check (used by the offline-degradation
        path). Returns True if /api/tags responds 2xx, False otherwise.
        Never raises."""
        try:
            r = self._http().get("/api/tags", timeout=5.0)
            return r.is_success
        except Exception:
            return False

    def available_models(self) -> list[str]:
        """List installed model tags. Raises OllamaUnreachable if the
        server is down."""
        try:
            r = self._http().get("/api/tags", timeout=10.0)
            r.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            raise OllamaUnreachable(f"GET /api/tags failed: {e}") from e
        data = r.json()
        return [m["name"] for m in data.get("models", [])]

    def chat(
        self, model: str, system: str, user: str,
        *, seed: int = 0,
    ) -> tuple[str, float]:
        """Single chat turn at ``temperature=0`` with a fixed seed.

        Returns the assistant content + wall-clock seconds. Raises
        ``OllamaUnreachable`` if the server is down, ``OllamaModelMissing``
        if the model isn't installed, or ``httpx.HTTPError`` for any
        other transport failure. No retries — the cache layer is
        responsible for absorbing transient failures across runs.
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0, "seed": int(seed)},
        }
        t0 = time.time()
        try:
            r = self._http().post("/api/chat", json=body)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise OllamaUnreachable(
                f"POST /api/chat failed (connect): {e}"
            ) from e
        except httpx.TimeoutException as e:
            # A timeout is a real failure, not a degradation — the
            # caller should decide whether to skip or back off. The
            # cache won't have an entry for this prompt either way.
            raise OllamaUnreachable(
                f"POST /api/chat timed out after {self.timeout}s"
            ) from e
        if r.status_code == 404:
            # Ollama returns 404 with {"error": "model 'x' not found, ..."}
            try:
                msg = r.json().get("error", r.text)
            except Exception:
                msg = r.text
            raise OllamaModelMissing(
                f"model {model!r} not found on server: {msg}"
            )
        r.raise_for_status()
        payload = r.json()
        content = payload.get("message", {}).get("content", "")
        return content, time.time() - t0
