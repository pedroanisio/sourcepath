"""Embedding backends.

Three implementations:
  - SentenceTransformerBackend: a real in-process model. Default
    "sentence-transformers/all-MiniLM-L6-v2" (384-dim, ~80MB). Honest
    starting choice for a prototype; production code should swap for a
    code-specialized model (voyage-code, CodeRankEmbed, Nomic Embed Code).
  - OllamaEmbeddingBackend: a real model served by an Ollama instance
    over HTTP (``POST /api/embed``). Lets the pipeline use embedding
    models Ollama hosts (nomic-embed-text, mxbai-embed-large, ...)
    without adding the sentence-transformers/torch stack.
  - DeterministicHashBackend: SHA-256-derived pseudo-vectors. Not a real
    embedding — semantics are zero. Useful for unit tests and for
    reproducibility checks where you want byte-identical output without
    depending on a model download.

All backends produce float32 arrays with rows L2-normalized (so cosine
similarity = dot product). This is a contract: the artifact emitter writes
the normalization choice into embeddings_meta.json so downstream code
doesn't have to guess.

Determinism honesty: sbert and hash are deterministic per machine. The
Ollama backend is a plain forward pass (no sampling), so repeated runs
against the same warm server/model produce stable vectors, but
bit-identity across machines or GPU builds is not guaranteed — same
caveat as any served model.
"""
from __future__ import annotations

import hashlib
import os
import struct
from typing import Any, Protocol

import numpy as np


class EmbeddingBackend(Protocol):
    name: str           # appears in embeddings_meta.json
    dimension: int      # vector length
    normalized: bool    # whether rows are L2-normalized

    def encode(self, texts: list[str]) -> np.ndarray: ...


class SentenceTransformerBackend:
    """Real embedding backend, used when sentence-transformers is available."""
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
                 batch_size: int = 32) -> None:
        from sentence_transformers import SentenceTransformer
        self.name = model_name
        self._model = SentenceTransformer(model_name)
        # MiniLM dim is 384; query the model to be sure. Newer sentence-
        # transformers (>=3.0) renamed the method; keep the fallback for older
        # releases.
        get_dim = getattr(
            self._model, "get_embedding_dimension",
            self._model.get_sentence_embedding_dimension,
        )
        self.dimension = int(get_dim() or 0)
        self.normalized = True
        self.batch_size = batch_size

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        out = self._model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return out.astype(np.float32, copy=False)


DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_EMBED_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 120.0


class OllamaEmbeddingUnreachable(RuntimeError):
    """Raised when the Ollama server cannot be reached (or times out)."""


class OllamaEmbeddingModelMissing(RuntimeError):
    """Raised when the requested embedding model is not on the server."""


class OllamaEmbeddingRequestFailed(RuntimeError):
    """Raised when the server answers /api/embed with a non-404 error.

    Carries the server's own ``error`` message — observed live: Ollama
    0.32.1 answers 501 "This server does not support embeddings. Start
    it with `--embeddings`" when the tag is a generation-only model, and
    that hint must reach the operator instead of a bare status code."""


class OllamaEmbeddingBackend:
    """Real embedding backend served by an Ollama instance.

    Speaks ``POST /api/embed`` (batch form: ``{"model": ..., "input":
    [texts]}`` -> ``{"embeddings": [[...], ...]}``) per the Ollama API
    reference:
    https://github.com/ollama/ollama/blob/main/docs/api.md#generate-embeddings

    Host resolution (first non-None wins), matching the L4 enrichment
    client: constructor ``host`` -> ``$OLLAMA_HOST`` -> localhost:11434.

    Construction requires a reachable server: one probe call resolves
    ``dimension`` and fails fast with a typed error when the server is
    down or the model tag is absent — the same eager-failure shape as
    SentenceTransformerBackend, which loads its model in ``__init__``.

    Server responses are untrusted input: row counts and dimensions are
    validated, and rows are re-normalized client-side, so the L2 contract
    (float32, L2-normalized) holds regardless of server behavior.
    """

    def __init__(
        self,
        model: str = DEFAULT_OLLAMA_EMBED_MODEL,
        host: str | None = None,
        timeout: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
        batch_size: int = 64,
        transport: Any | None = None,
    ) -> None:
        # Lazy: httpx ships with the [frontend] extra, so importing this
        # module stays free for sbert/hash users who never touch Ollama.
        try:
            import httpx
        except ModuleNotFoundError as e:  # pragma: no cover - install hint
            raise ModuleNotFoundError(
                "the 'ollama' embedding backend needs httpx: "
                "pip install 'httpx>=0.28,<1.0' (or install the "
                "[frontend] extra)"
            ) from e

        self._httpx = httpx
        self.model = model
        self.name = f"ollama:{model}"
        self.normalized = True
        self.timeout = float(timeout)
        self.batch_size = int(batch_size)
        self.host = host or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST
        kwargs: dict[str, Any] = {"base_url": self.host, "timeout": self.timeout}
        if transport is not None:  # test seam (httpx.MockTransport)
            kwargs["transport"] = transport
        self._client = httpx.Client(**kwargs)
        probe = self._embed_batch(["dimension probe"])
        self.dimension = int(len(probe[0]))

    def close(self) -> None:
        self._client.close()

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        httpx = self._httpx
        try:
            r = self._client.post(
                "/api/embed", json={"model": self.model, "input": batch})
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise OllamaEmbeddingUnreachable(
                f"POST /api/embed failed (connect to {self.host}): {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise OllamaEmbeddingUnreachable(
                f"POST /api/embed timed out after {self.timeout}s"
            ) from e
        if r.is_error:
            # Ollama puts the reason in {"error": ...} — surface it.
            try:
                msg = r.json().get("error", r.text)
            except Exception:
                msg = r.text
            if r.status_code == 404:
                raise OllamaEmbeddingModelMissing(
                    f"model {self.model!r} not found on server: {msg}"
                )
            raise OllamaEmbeddingRequestFailed(
                f"/api/embed HTTP {r.status_code} for model "
                f"{self.model!r}: {msg}"
            )
        rows = r.json().get("embeddings")
        if not isinstance(rows, list) or len(rows) != len(batch):
            got = len(rows) if isinstance(rows, list) else type(rows).__name__
            raise ValueError(
                f"/api/embed returned {got} embeddings for {len(batch)} inputs"
            )
        return rows

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        out = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for start in range(0, len(texts), self.batch_size):
            # Some embedding models reject empty input; a single space
            # keeps the row (and its chunk_id) instead of dropping it.
            batch = [t if t else " " for t in texts[start:start + self.batch_size]]
            for j, row in enumerate(self._embed_batch(batch)):
                if len(row) != self.dimension:
                    raise ValueError(
                        f"/api/embed row dimension {len(row)} != probed "
                        f"dimension {self.dimension} (model {self.model!r})"
                    )
                vec = np.asarray(row, dtype=np.float32)
                norm = float(np.linalg.norm(vec))
                if norm > 0:
                    vec = vec / norm
                out[start + j] = vec
        return out


class DeterministicHashBackend:
    """Lies about semantics but is fully deterministic and dependency-free.

    Uses SHA-256 of the input string to derive `dimension/8` 64-bit integers,
    casts to int8, then L2-normalizes. Same string -> same vector across
    machines, runs, and Python versions.
    """
    name = "deterministic-hash-sha256-v1"
    normalized = True

    def __init__(self, dimension: int = 256) -> None:
        if dimension % 8 != 0:
            raise ValueError("dimension must be a multiple of 8")
        self.dimension = dimension

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        n_blocks = self.dimension // 8  # each SHA-256 chunk gives 8 int8 values
        out = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for i, t in enumerate(texts):
            buf = []
            # Repeat SHA-256 with a counter to fill the needed dimension.
            for k in range(n_blocks // 4 + 1):
                h = hashlib.sha256(f"{k}\x00{t}".encode("utf-8")).digest()
                buf.append(h)
            raw = b"".join(buf)[: self.dimension]
            arr = np.frombuffer(raw, dtype=np.int8).astype(np.float32)
            n = np.linalg.norm(arr)
            if n > 0:
                arr = arr / n
            out[i] = arr
        return out
