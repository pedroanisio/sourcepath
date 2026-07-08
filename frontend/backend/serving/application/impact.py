"""Impact endpoint application logic."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .bundle_data import chunk_payload, get_bundle, walk_paths, walk_xref_chunks


def get_impact_response(
    path: str,
    depth: int = 2,
    limit: int = 100,
    bundle: str | None = None,
) -> dict[str, Any]:
    b = get_bundle(bundle)
    if path not in b.file_by_path:
        raise HTTPException(status_code=404, detail="file not found")

    dependencies, dep_truncated = walk_paths(path, b.imports_out, depth, limit)
    dependents, rev_truncated = walk_paths(path, b.imports_in, depth, limit)
    chunk_idxs = b.chunks_by_file.get(path, [])[:25]
    chunks = [chunk_payload(b, i, include_file=True) for i in chunk_idxs]

    related_tests = set(b.tests_for_subject.get(path, []))
    tested_subjects = set(b.subjects_for_test.get(path, []))
    for impacted in dependents:
        related_tests.update(b.tests_for_subject.get(impacted, []))

    file_chunk_seeds = list(b.chunks_by_file.get(path, []))
    callee_idxs, callees_trunc = walk_xref_chunks(
        file_chunk_seeds, b.xrefs_by_src_idx, b.xrefs, "dst_idx", depth, limit
    )
    caller_idxs, callers_trunc = walk_xref_chunks(
        file_chunk_seeds, b.xrefs_by_dst_idx, b.xrefs, "src_idx", depth, limit
    )

    symbol_callees = [chunk_payload(b, i, include_file=True) for i in callee_idxs]
    symbol_callers = [chunk_payload(b, i, include_file=True) for i in caller_idxs]
    symbol_callees.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0, r["symbol"] or ""))
    symbol_callers.sort(key=lambda r: (r["file"] or "", r["beginLine"] or 0, r["symbol"] or ""))

    return {
        "file": path,
        "depth": depth,
        "direct_dependencies": sorted(b.imports_out.get(path, [])),
        "direct_dependents": sorted(b.imports_in.get(path, [])),
        "transitive_dependencies": dependencies,
        "transitive_dependents": dependents,
        "related_tests": sorted(related_tests),
        "tested_subjects": sorted(tested_subjects),
        "concepts": list((b.concepts.get("per_path_concepts") or {}).get(path, [])),
        "chunks": chunks,
        "symbol_callers": symbol_callers,
        "symbol_callees": symbol_callees,
        "truncated": dep_truncated or rev_truncated or callees_trunc or callers_trunc,
    }
