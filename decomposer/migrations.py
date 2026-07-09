"""Alembic-shaped revision-chain ordering (round-3 review #4).

`migrations/versions` directories are strictly ordered, but not by anything
the import graph sees: Alembic chains one revision to the previous one by a
string id (``revision``/``down_revision``) recorded in each file, never by a
Python import. Left alone, the recomposer would list these files in plain
lexicographic order with no ordering guarantee — wrong for a directory whose
entire value is "apply in this exact sequence".

Evidence: ``EvidenceGraph.revision_chains`` (parsed from bundle blobs by
``decomposer.evidence._read_revision_markers``); no re-extraction needed here.
Scoped to the Alembic shape specifically, per the review's own example — other
frameworks (Django, Flyway, ...) chain migrations differently and are not
guessed at.
"""
from __future__ import annotations

from typing import Any

from .evidence import EvidenceGraph
from .parts import ModuleGraph


def _build_chain(markers: dict[str, dict[str, Any]]) -> list[str] | None:
    """A topological file order from revision/down_revision markers, or
    ``None`` if the chain doesn't resolve to one unambiguous linear order —
    never a fabricated guess (PALS's Law): multiple heads, a branch point, a
    dangling reference, a duplicate revision id, or leftover disconnected
    files all abstain rather than assert a wrong order.
    """
    by_revision: dict[str, str] = {}
    for path, m in markers.items():
        rev = m.get("revision")
        if not rev or rev in by_revision:
            return None
        by_revision[rev] = path

    down_to_path: dict[str, str] = {}
    roots: list[str] = []
    for path, m in markers.items():
        down = m.get("down_revision")
        if down is None:
            roots.append(path)
            continue
        if down not in by_revision or down in down_to_path:
            return None
        down_to_path[down] = path
    if len(roots) != 1:
        return None

    chain = [roots[0]]
    seen = {roots[0]}
    cur_revision = markers[roots[0]]["revision"]
    while cur_revision in down_to_path:
        nxt = down_to_path[cur_revision]
        if nxt in seen:
            return None
        chain.append(nxt)
        seen.add(nxt)
        cur_revision = markers[nxt]["revision"]
    if len(chain) != len(markers):
        return None
    return chain


def revision_orderings(ev: EvidenceGraph, mg: ModuleGraph) -> list[dict]:
    """One ``{part, file_order, note}`` entry per module whose revision
    markers resolve to a clean chain. Modules with zero or one marker, or an
    unresolvable chain, are silently omitted — the recomposer falls back to
    its default (lexicographic, unordered) file listing for those, which is
    correct behavior, not a degraded one: no order was proven.
    """
    by_module: dict[str, dict[str, dict[str, Any]]] = {}
    for path, marker in ev.revision_chains.items():
        mod = mg.module_of_file.get(path)
        if mod is None:
            continue
        by_module.setdefault(mod, {})[path] = marker

    out: list[dict] = []
    for mod in sorted(by_module):
        markers = by_module[mod]
        if len(markers) < 2:
            continue
        chain = _build_chain(markers)
        if chain is None:
            continue
        out.append({
            "part": f"module:{mod}",
            "file_order": chain,
            "note": "topological order from Alembic revision/down_revision markers",
        })
    return out
