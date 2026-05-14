"""Chunk endpoints application logic."""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np
from fastapi import HTTPException

from .bundle_data import chunk_payload, get_bundle, xref_row


def list_chunks_response(
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    bundle: str | None = None,
) -> dict[str, object]:
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


def search_chunks_response(q: str, k: int, bundle: str | None = None) -> dict[str, object]:
    b = get_bundle(bundle)
    backend_name = (b.embeddings_meta.get("backend") or {}).get("name") or ""
    lowered = backend_name.lower()
    is_sbert = (
        "sentence-transformer" in lowered
        or "sbert" in lowered
        or "minilm" in lowered
    )
    if not is_sbert or b.chunk_vectors is None:
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

    model = _get_model("sentence-transformers/all-MiniLM-L6-v2")
    q_vec = model.encode([q], normalize_embeddings=True)[0].astype("float32")
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
