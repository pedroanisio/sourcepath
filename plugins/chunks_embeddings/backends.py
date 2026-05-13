"""Embedding backends.

Two implementations, both deterministic:
  - SentenceTransformerBackend: a real model. Default
    "sentence-transformers/all-MiniLM-L6-v2" (384-dim, ~80MB). Honest
    starting choice for a prototype; production code should swap for a
    code-specialized model (voyage-code, CodeRankEmbed, Nomic Embed Code).
  - DeterministicHashBackend: SHA-256-derived pseudo-vectors. Not a real
    embedding — semantics are zero. Useful for unit tests and for
    reproducibility checks where you want byte-identical output without
    depending on a model download.

Both backends produce float32 arrays with rows L2-normalized (so cosine
similarity = dot product). This is a contract: the artifact emitter writes
the normalization choice into embeddings_meta.json so downstream code
doesn't have to guess.
"""
from __future__ import annotations

import hashlib
import struct
from typing import Protocol

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
