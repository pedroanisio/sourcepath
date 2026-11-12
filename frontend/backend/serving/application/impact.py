"""Impact endpoint application logic."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .bundle_data import chunk_payload, get_bundle, walk_paths, walk_xref_chunks

#: Xref kinds that mean "this file is an implementation of that one".
#: ``subclassOf`` covers `implements` and `extends`; ``overrides`` catches
#: the method-level case where the class-level edge was not resolved.
SUPERTYPE_KINDS = frozenset({"subclassOf", "overrides"})


def supertype_files(b: Any, path: str) -> list[str]:
    """Files whose types ``path`` implements or extends.

    Reads the symbol xrefs already in the bundle and lifts them from chunk
    level to file level, dropping self-edges (a class overriding its own
    method says nothing about other files).
    """
    out: set[str] = set()
    for idx in b.chunks_by_file.get(path, []):
        for e_idx in b.xrefs_by_src_idx.get(idx, []):
            edge = b.xrefs[e_idx]
            if edge.get("kind") not in SUPERTYPE_KINDS:
                continue
            dst_file = b.chunks[edge["dst_idx"]].get("file")
            if dst_file and dst_file != path:
                out.add(dst_file)
    return sorted(out)


def dependents_via_supertype(b: Any, path: str) -> dict[str, list[str]]:
    """Files that reach ``path`` through an interface it implements.

    Import graphs under-report coupling in any codebase that injects by
    interface — Spring, and every other DI container. Nothing imports
    ``PurchaseServiceImpl``; the controllers, the scheduler and its test
    all import ``PurchaseService``. ``imports_in`` for the implementation
    is therefore empty, which reads as "nothing depends on this file" and
    is the worst kind of wrong answer: a confident one.

    Returned keyed by the supertype that justified each set, because this
    is a *derived* relation, not an import edge. A caller that imports the
    interface depends on *some* implementation of it, which is this one
    only when this one is the binding in play — with two implementations
    behind one interface, some of these callers reach the other. Naming
    the supertype lets the reader make that judgement instead of
    inheriting ours.
    """
    out: dict[str, list[str]] = {}
    for supertype in supertype_files(b, path):
        callers = sorted(set(b.imports_in.get(supertype, [])) - {path})
        if callers:
            out[supertype] = callers
    return out


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

    via_supertype = dependents_via_supertype(b, path)
    supertype_dependents = sorted(
        {caller for callers in via_supertype.values() for caller in callers})

    related_tests = set(b.tests_for_subject.get(path, []))
    tested_subjects = set(b.subjects_for_test.get(path, []))
    for impacted in dependents:
        related_tests.update(b.tests_for_subject.get(impacted, []))
    # Tests reach an implementation through its interface too, so the same
    # blind spot hid them: a test that imports PurchaseService exercises
    # PurchaseServiceImpl and never named it.
    for impacted in supertype_dependents:
        related_tests.update(b.tests_for_subject.get(impacted, []))
        if impacted.startswith("src/test/") or "/test/" in impacted:
            related_tests.add(impacted)

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
        # Kept beside direct_dependents, never merged into it: these are
        # derived from interface implementation, not from an import edge,
        # and collapsing the two would state an import that is not there.
        "supertypes": supertype_files(b, path),
        "dependents_via_supertype": via_supertype,
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
