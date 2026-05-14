"""Concept endpoint application logic."""
from __future__ import annotations

from fastapi import HTTPException

from .bundle_data import get_bundle


def get_concept_detail_response(
    name: str,
    cooccur_k: int = 30,
    chunk_k: int = 50,
    file_k: int = 100,
    bundle: str | None = None,
) -> dict[str, object]:
    b = get_bundle(bundle)
    concept = b.concepts.get("concepts", {}).get(name)
    if not concept:
        raise HTTPException(status_code=404, detail="concept not found")
    files: list[str] = []
    for path, names in (b.concepts.get("per_path_concepts") or {}).items():
        if name in names:
            files.append(path)
            if len(files) >= file_k:
                break
    return {
        "concept": concept,
        "files": files,
        "cooccurring": [{"name": n, "weight": w} for n, w in b.cooccur.get(name, [])[:cooccur_k]],
        "chunks": [
            {k: b.chunks[i].get(k) for k in ("idx", "symbol", "kind", "file", "beginLine", "endLine")}
            for i in b.concept_chunks.get(name, [])[:chunk_k]
        ],
        "components": concept.get("components", []),
        "file_count_total": len(
            [p for p, ns in (b.concepts.get("per_path_concepts") or {}).items() if name in ns]
        ),
        "chunk_count_total": len(b.concept_chunks.get(name, [])),
    }
