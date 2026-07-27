"""OllamaEmbeddingBackend — offline contract tests (httpx.MockTransport).

The backend is the third L2 embedding implementation next to
``SentenceTransformerBackend`` and ``DeterministicHashBackend``. Its
contract (shared with both, enforced by ``EmbeddingComputer`` and
``verify_l2.py``):

- ``name`` / ``dimension`` / ``normalized`` attributes present after
  construction (dimension is probed with one ``/api/embed`` call);
- ``encode(texts)`` returns a float32 ``(N, D)`` array with every row
  L2-normalized client-side — server normalization is never assumed;
- typed errors: ``OllamaEmbeddingUnreachable`` when the server is down,
  ``OllamaEmbeddingModelMissing`` on a 404 for the model tag;
- server responses are untrusted input: a row-count or dimension
  mismatch raises ``ValueError`` instead of propagating garbage.

All tests run offline. Run from the repo root:
    python -m pytest tests/test_ollama_embedding_backend.py
"""
from __future__ import annotations

import json

import httpx
import numpy as np
import pytest

from plugins.chunks_embeddings.backends import (
    OllamaEmbeddingBackend,
    OllamaEmbeddingModelMissing,
    OllamaEmbeddingRequestFailed,
    OllamaEmbeddingUnreachable,
)


DIM = 8


def _fake_rows(inputs: list[str], dim: int = DIM) -> list[list[float]]:
    # Deterministic, intentionally NOT normalized — the backend must
    # normalize client-side for the test to pass.
    return [
        [float(len(t) + j + 1) for j in range(dim)]
        for t in inputs
    ]


def _embed_transport(calls: list[dict] | None = None, dim: int = DIM):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        body = json.loads(request.content)
        if calls is not None:
            calls.append(body)
        return httpx.Response(
            200, json={"model": body["model"],
                       "embeddings": _fake_rows(body["input"], dim)},
        )
    return httpx.MockTransport(handler)


def _backend(**kw) -> OllamaEmbeddingBackend:
    kw.setdefault("model", "fake-embed")
    kw.setdefault("host", "http://test")
    kw.setdefault("transport", _embed_transport())
    return OllamaEmbeddingBackend(**kw)


# ---------------------------------------------------------------------------
# construction / metadata
# ---------------------------------------------------------------------------


def test_construction_probes_dimension_and_sets_metadata():
    calls: list[dict] = []
    b = OllamaEmbeddingBackend(
        model="fake-embed", host="http://test",
        transport=_embed_transport(calls))
    assert b.dimension == DIM
    assert b.name == "ollama:fake-embed"
    assert b.normalized is True
    assert len(calls) == 1  # exactly one probe call
    assert calls[0]["model"] == "fake-embed"


def test_explicit_host_wins_over_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://from-env:1234")
    b = _backend(host="http://explicit:5678")
    assert b.host == "http://explicit:5678"


def test_env_host_used_when_no_explicit_host(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://from-env:1234")
    b = OllamaEmbeddingBackend(
        model="fake-embed", transport=_embed_transport())
    assert b.host == "http://from-env:1234"


def test_default_host_when_nothing_set(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    b = OllamaEmbeddingBackend(
        model="fake-embed", transport=_embed_transport())
    assert b.host == "http://localhost:11434"


# ---------------------------------------------------------------------------
# encode contract
# ---------------------------------------------------------------------------


def test_encode_returns_normalized_float32_rows():
    b = _backend()
    out = b.encode(["def foo(): pass", "class Bar: ..."])
    assert out.shape == (2, DIM)
    assert out.dtype == np.float32
    norms = np.linalg.norm(out, axis=1)
    assert np.all(np.abs(norms - 1.0) < 1e-5)


def test_encode_empty_list_makes_no_http_call():
    calls: list[dict] = []
    b = OllamaEmbeddingBackend(
        model="fake-embed", host="http://test",
        transport=_embed_transport(calls))
    out = b.encode([])
    assert out.shape == (0, DIM)
    assert out.dtype == np.float32
    assert len(calls) == 1  # probe only, nothing more


def test_encode_batches_requests():
    calls: list[dict] = []
    b = OllamaEmbeddingBackend(
        model="fake-embed", host="http://test", batch_size=2,
        transport=_embed_transport(calls))
    out = b.encode(["a", "bb", "ccc", "dddd", "eeeee"])
    assert out.shape == (5, DIM)
    # 1 probe + ceil(5/2) = 3 encode batches
    assert len(calls) == 4
    assert [len(c["input"]) for c in calls[1:]] == [2, 2, 1]


def test_empty_text_is_replaced_before_sending():
    calls: list[dict] = []
    b = OllamaEmbeddingBackend(
        model="fake-embed", host="http://test",
        transport=_embed_transport(calls))
    b.encode(["", "real text"])
    sent = calls[-1]["input"]
    assert sent[0] != ""  # empty input rejected by some models — replaced
    assert sent[1] == "real text"


# ---------------------------------------------------------------------------
# typed errors
# ---------------------------------------------------------------------------


def test_model_missing_raises_typed_error_at_probe():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"error": "model 'fake-embed' not found"})
    with pytest.raises(OllamaEmbeddingModelMissing, match="fake-embed"):
        OllamaEmbeddingBackend(
            model="fake-embed", host="http://test",
            transport=httpx.MockTransport(handler))


def test_unreachable_raises_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")
    with pytest.raises(OllamaEmbeddingUnreachable):
        OllamaEmbeddingBackend(
            model="fake-embed", host="http://test",
            transport=httpx.MockTransport(handler))


def test_non_404_server_error_surfaces_servers_own_message():
    """Observed live on Ollama 0.32.1: /api/embed answers 501 with a hint
    when the tag is a generation-only model. The hint must reach the
    operator, not a bare status code."""
    hint = "This server does not support embeddings. Start it with `--embeddings`"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(501, json={"error": hint})

    with pytest.raises(OllamaEmbeddingRequestFailed, match="--embeddings"):
        OllamaEmbeddingBackend(
            model="qwen2.5:14b-instruct", host="http://test",
            transport=httpx.MockTransport(handler))


def test_timeout_raises_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")
    with pytest.raises(OllamaEmbeddingUnreachable):
        OllamaEmbeddingBackend(
            model="fake-embed", host="http://test",
            transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# untrusted-response validation
# ---------------------------------------------------------------------------


def test_row_count_mismatch_raises_value_error():
    n_call = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        n_call["n"] += 1
        if n_call["n"] == 1:  # healthy probe
            return httpx.Response(
                200, json={"embeddings": _fake_rows(body["input"])})
        return httpx.Response(200, json={"embeddings": []})  # wrong count

    b = OllamaEmbeddingBackend(
        model="fake-embed", host="http://test",
        transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="embedding"):
        b.encode(["x", "y"])


def test_dimension_drift_between_batches_raises_value_error():
    n_call = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        n_call["n"] += 1
        dim = DIM if n_call["n"] == 1 else DIM + 3
        return httpx.Response(
            200, json={"embeddings": _fake_rows(body["input"], dim)})

    b = OllamaEmbeddingBackend(
        model="fake-embed", host="http://test",
        transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="dimension"):
        b.encode(["x"])


def test_missing_embeddings_key_raises_value_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})
    with pytest.raises(ValueError):
        OllamaEmbeddingBackend(
            model="fake-embed", host="http://test",
            transport=httpx.MockTransport(handler))
