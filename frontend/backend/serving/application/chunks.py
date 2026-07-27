"""Chunk endpoints application logic."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

import numpy as np
from fastapi import HTTPException

from .bundle_data import chunk_payload, get_bundle, xref_row

logger = logging.getLogger("cbm.backend.chunks")

# Must stay comfortably under the MCP server's 10 s dispatch budget for
# semantic_neighbors (frontend/mcp_server/observability.py::TIMEOUTS) —
# a query embed that outlives the budget kills the whole call instead of
# degrading to lexical. 6 s covers a cold embedding-model load with room
# for the cosine pass and the fallback.
_OLLAMA_QUERY_TIMEOUT_SECONDS = 6.0


def list_chunks_response(
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    bundle: str | None = None,
) -> dict[str, Any]:
    b = get_bundle(bundle)
    rows = b.chunks
    if q:
        ql = q.lower()
        rows = [
            r
            for r in rows
            if (r["symbol"] or "").lower().find(ql) >= 0
            or (r["file"] or "").lower().find(ql) >= 0
        ]
    total = len(rows)
    return {
        "chunks": [chunk_payload_from_row(r, include_file=True) for r in rows[offset : offset + limit]],
        "total": total,
        "backend": (b.embeddings_meta.get("backend") or {}).get("name"),
        "mode": "lexical",
    }


def search_chunks_response(q: str, k: int, bundle: str | None = None) -> dict[str, Any]:
    b = get_bundle(bundle)
    backend_name = (b.embeddings_meta.get("backend") or {}).get("name") or ""
    lowered = backend_name.lower()
    is_sbert = (
        "sentence-transformer" in lowered
        or "sbert" in lowered
        or "minilm" in lowered
    )
    is_ollama = lowered.startswith("ollama:")

    q_vec: np.ndarray | None = None
    if b.chunk_vectors is not None:
        if is_sbert:
            # Embed the query with the model the bundle was built with —
            # a different model would rank in the wrong vector space. The
            # name is a loadable model id only when it has an org prefix.
            name = (backend_name if "/" in backend_name
                    else "sentence-transformers/all-MiniLM-L6-v2")
            model = _get_model(name)
            q_vec = model.encode([q], normalize_embeddings=True)[0].astype("float32")
        elif is_ollama:
            # "ollama:<model>" — embed via the Ollama server. On any
            # failure (server down, model gone, dimension drift) this
            # returns None and the endpoint degrades to lexical mode.
            q_vec = _embed_query_ollama(backend_name.split(":", 1)[1], q)
            if q_vec is not None and q_vec.shape[0] != b.chunk_vectors.shape[1]:
                logger.warning(
                    "ollama query dim %d != bundle dim %d — lexical fallback",
                    q_vec.shape[0], b.chunk_vectors.shape[1])
                q_vec = None

    if q_vec is None:
        ql = q.lower()
        scored = [
            (
                r,
                1.0
                if (r["symbol"] or "").lower().find(ql) >= 0
                else (0.5 if (r["file"] or "").lower().find(ql) >= 0 else 0.0),
            )
            for r in b.chunks
        ]
        scored = [s for s in scored if s[1] > 0]
        scored.sort(key=lambda s: s[1], reverse=True)
        return {
            "chunks": [chunk_payload_from_row(r, include_file=True, score=score) for r, score in scored[:k]],
            "total": len(scored),
            "backend": backend_name,
            "mode": "lexical",
        }

    sims = b.chunk_vectors @ q_vec
    top_idx = np.argsort(-sims)[:k]
    chunk_by_row = {
        r["embeddingRow"]: r for r in b.chunks if r["embeddingRow"] is not None
    }
    out = []
    for i in top_idx:
        row = int(i)
        r = chunk_by_row.get(row)
        if r:
            out.append(chunk_payload_from_row(r, include_file=True, score=float(sims[row])))
    return {
        "chunks": out,
        "total": len(out),
        "backend": backend_name,
        "mode": "semantic",
    }


@lru_cache(maxsize=1)
def _get_model(name: str):
    from sentence_transformers import SentenceTransformer  # type: ignore

    return SentenceTransformer(name)


def _embed_query_ollama(model: str, q: str) -> np.ndarray | None:
    """Embed one query through the bundle's recorded Ollama model.

    POST /api/embed on $OLLAMA_HOST (default localhost:11434). Returns a
    L2-normalized float32 vector, or None on any failure — the serving
    layer is an application, so it degrades to lexical search instead of
    surfacing a 500 when the embedding server is unavailable.
    """
    host = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
    try:
        import httpx

        r = httpx.post(
            f"{host}/api/embed",
            json={"model": model, "input": [q]},
            timeout=_OLLAMA_QUERY_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        rows = r.json().get("embeddings") or []
        if len(rows) != 1:
            logger.warning("ollama /api/embed returned %d rows for 1 input", len(rows))
            return None
        vec = np.asarray(rows[0], dtype="float32")
        norm = float(np.linalg.norm(vec))
        if norm <= 0:
            return None
        return vec / norm
    except Exception as e:
        logger.warning("ollama query embedding failed (%s) — lexical fallback", e)
        return None


def get_chunk_blob_response(sha: str, bundle: str | None = None) -> dict[str, str]:
    b = get_bundle(bundle)
    if not all(c in "0123456789abcdef" for c in sha) or len(sha) != 64:
        raise HTTPException(status_code=400, detail="invalid sha")
    p = b.output_dir / "blobs" / sha
    if not p.exists():
        raise HTTPException(status_code=404, detail="blob not found")
    try:
        text = p.read_text(errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"sha256": sha, "text": text[:20000]}


def get_chunk_detail_response(idx: int, bundle: str | None = None) -> dict[str, Any]:
    b = get_bundle(bundle)
    if idx < 0 or idx >= len(b.chunks):
        raise HTTPException(status_code=404, detail="chunk idx out of range")
    rec = b.chunks[idx]
    concepts = list(b.chunk_concepts.get(idx, []))
    blob_preview: str | None = None
    sha = rec.get("contentSha256")
    if sha and len(sha) == 64 and all(ch in "0123456789abcdef" for ch in sha):
        p = b.output_dir / "blobs" / sha
        if p.exists():
            try:
                blob_preview = p.read_text(errors="replace")[:8000]
            except Exception:
                blob_preview = None
    callers = [xref_row(b, b.xrefs[e]["src_idx"], b.xrefs[e]) for e in b.xrefs_by_dst_idx.get(idx, [])]
    callees = [xref_row(b, b.xrefs[e]["dst_idx"], b.xrefs[e]) for e in b.xrefs_by_src_idx.get(idx, [])]
    callers.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0, r["symbol"] or ""))
    callees.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0, r["symbol"] or ""))
    return {
        "chunk": rec,
        "concepts": concepts,
        "blob_preview": blob_preview,
        "callers": callers,
        "callees": callees,
    }


def chunk_payload_from_row(
    row: dict[str, Any],
    include_file: bool,
    score: float | None = None,
) -> dict[str, Any]:
    keys = ["idx", "symbol", "kind", "beginLine", "endLine", "embeddingRow"]
    if include_file:
        keys.insert(3, "file")
    payload = {key: row.get(key) for key in keys}
    if score is not None:
        payload["score"] = score
    return payload
