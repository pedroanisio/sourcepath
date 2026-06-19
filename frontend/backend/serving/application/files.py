"""File endpoint application logic."""
from __future__ import annotations

from fastapi import HTTPException

from .bundle_data import get_bundle, xref_row


def get_file_detail_response(path: str, bundle: str | None = None) -> dict[str, object]:
    b = get_bundle(bundle)
    rec = b.file_by_path.get(path)
    if not rec:
        raise HTTPException(status_code=404, detail="file not found")
    chunk_idxs = b.chunks_by_file.get(path, [])
    chunks = [
        {k: b.chunks[i].get(k) for k in ("idx", "symbol", "kind", "beginLine", "endLine", "embeddingRow")}
        for i in chunk_idxs
    ]
    concepts = list((b.concepts.get("per_path_concepts") or {}).get(path, []))

    xrefs_out: list[dict[str, object]] = []
    xrefs_in: list[dict[str, object]] = []
    seen_out: set[int] = set()
    seen_in: set[int] = set()
    for ci in chunk_idxs:
        for e_idx in b.xrefs_by_src_idx.get(ci, []):
            edge = b.xrefs[e_idx]
            if edge["dst_idx"] in seen_out:
                continue
            seen_out.add(edge["dst_idx"])
            xrefs_out.append(xref_row(b, edge["dst_idx"], edge))
        for e_idx in b.xrefs_by_dst_idx.get(ci, []):
            edge = b.xrefs[e_idx]
            if edge["src_idx"] in seen_in:
                continue
            seen_in.add(edge["src_idx"])
            xrefs_in.append(xref_row(b, edge["src_idx"], edge))
    xrefs_out.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0, r["symbol"] or ""))
    xrefs_in.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0, r["symbol"] or ""))

    return {
        "file": rec,
        "imports_out": sorted(b.imports_out.get(path, [])),
        "imports_in": sorted(b.imports_in.get(path, [])),
        "external_imports": sorted(b.external_imports.get(path, [])),
        "chunks": chunks,
        "concepts": concepts,
        "xrefs_out": xrefs_out,
        "xrefs_in": xrefs_in,
    }
