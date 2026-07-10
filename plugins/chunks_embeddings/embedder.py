"""EmbeddingComputer — Aggregator that batches all chunks through an
embedding backend, in sorted (path, line_start, kind, symbol) order so the
row index assignment is deterministic across runs.

Reads from:  ctx.scratch["chunks"]      (dict[path, list[chunk_dict]])
Writes to:   ctx.indices["l2_10_chunks"] (the same chunks, with a stable
                                          chunk_id and row index attached)
             ctx.indices["l2_20_embeddings"] = {
                "vectors": np.ndarray (N, D) float32,
                "row_to_chunk_id": list[str],
                "backend": {"name": ..., "dimension": ..., "normalized": bool},
             }

The chunk_id is
`<file_path>#<kind>:<symbol>:L<line_start>-L<line_end>:b<byte_start>-<byte_end>`.
For nested symbols (methods), parent is included: `...#method:UserService.foo:...`.
The trailing byte span makes the id injective: two symbols that share a
(kind, symbol, line range) — common in minified single-line files — differ in
their byte span and so get distinct ids instead of colliding into one node.
"""
from __future__ import annotations

import logging
from typing import cast

import numpy as np

from codebase_mapper.shared_kernel.extensions import PipelineCtx
from .backends import EmbeddingBackend


logger = logging.getLogger("cbm.l2.embedder")


# Embedding truncation — most code-aware models choke past their context
# window. We truncate the text we send to the model. The chunk metadata in
# the graph still records the *full* byte range; only the embedded text
# is truncated.
MAX_EMBED_CHARS = 8000


class EmbeddingComputer:
    name = "l2_20_embeddings"

    def __init__(self, backend: EmbeddingBackend) -> None:
        self.backend = backend

    def run(self, ctx: PipelineCtx) -> dict:
        chunks_map = cast(dict, ctx.scratch.get("chunks", {}))
        # Build a deterministic flat list, deduplicating by chunk_id. After D1
        # an injective chunk_id (D2) only collides when two chunks are the same
        # span — i.e. byte-identical content — so keeping the first is correct.
        # PALS's Law (no silent caps): every drop is logged, not swallowed.
        flat: list[tuple[str, dict]] = []
        seen: set[str] = set()
        dropped = 0
        for path in sorted(chunks_map.keys()):
            for c in sorted(chunks_map[path],
                            key=lambda x: (x["line_start"], x["kind"], x["symbol"],
                                           x.get("byte_start", 0), x.get("byte_end", 0))):
                chunk_id = _chunk_id(path, c)
                if chunk_id in seen:
                    dropped += 1
                    logger.warning("dropping duplicate chunk_id %s", chunk_id)
                    continue
                seen.add(chunk_id)
                flat.append((chunk_id, dict(c, path=path, chunk_id=chunk_id)))
        if dropped:
            logger.warning(
                "embedder dropped %d duplicate chunk(s) of %d total",
                dropped, dropped + len(flat),
            )

        if not flat:
            ctx.indices["l2_10_chunks"] = []
            ctx.indices["l2_20_embeddings"] = {
                "vectors": np.zeros((0, self.backend.dimension), dtype=np.float32),
                "row_to_chunk_id": [],
                "backend": {
                    "name": self.backend.name,
                    "dimension": self.backend.dimension,
                    "normalized": self.backend.normalized,
                },
            }
            return ctx.indices["l2_20_embeddings"]

        # Assign row indices in flat order.
        texts = []
        for i, (cid, c) in enumerate(flat):
            c["row"] = i
            text = c["text"]
            if len(text) > MAX_EMBED_CHARS:
                text = text[:MAX_EMBED_CHARS]
                c["truncated_for_embedding"] = True
            else:
                c["truncated_for_embedding"] = False
            texts.append(text)

        vectors = self.backend.encode(texts)
        assert vectors.shape == (len(flat), self.backend.dimension), (
            f"backend returned {vectors.shape}, expected ({len(flat)}, {self.backend.dimension})"
        )

        ctx.indices["l2_10_chunks"] = [c for _cid, c in flat]
        ctx.indices["l2_20_embeddings"] = {
            "vectors": vectors,
            "row_to_chunk_id": [cid for cid, _c in flat],
            "backend": {
                "name": self.backend.name,
                "dimension": self.backend.dimension,
                "normalized": self.backend.normalized,
            },
            # Downstream layers may embed their own short texts through the
            # same backend (L3 concept label fallback — plan E7), keeping
            # every vector in one space.
            "encode_texts": self.backend.encode,
        }
        return ctx.indices["l2_20_embeddings"]


def _chunk_id(path: str, c: dict) -> str:
    sym = c["symbol"]
    if c.get("parent_symbol"):
        sym = f"{c['parent_symbol']}.{sym}"
    return (
        f"{path}#{c['kind']}:{sym}"
        f":L{c['line_start']}-L{c['line_end']}"
        f":b{c['byte_start']}-{c['byte_end']}"
    )
