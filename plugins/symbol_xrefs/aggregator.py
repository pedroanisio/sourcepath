"""XrefAggregator — runs the registered per-language symbol resolvers and
collects their edges into a single index entry under ``ctx.indices["l3_10_xrefs"]``.

Output shape (the downstream contract for graph_writer / artifact):

    {
        "edges":       list[SymbolXrefEdge],        # sorted, deduped
        "unresolved":  list[UnresolvedSymbolRef],   # sorted
        "by_language": dict[str, dict[str, int]],   # lang -> {resolved, unresolved}
        "by_kind":     dict[str, int],              # edge kind -> count
        "by_resolution": dict[str, int],            # resolution -> count
    }

Phase 1 ships the empty shape: no resolvers are registered, so every
collection is empty. Later phases plug per-language resolvers in via the
``_RESOLVERS`` dispatch dict in ``__init__.py``.
"""
from __future__ import annotations

from codebase_mapper.extensions import PipelineCtx
from codebase_mapper.models import SymbolXrefEdge, UnresolvedSymbolRef


XREF_INDEX_KEY = "l3_10_xrefs"


class XrefAggregator:
    name = XREF_INDEX_KEY

    def __init__(self, resolvers: dict | None = None) -> None:
        # Dispatch dict: language string -> resolver callable. Phase 1
        # leaves it empty; phases 2/4/8 add entries.
        self._resolvers = resolvers or {}

    def run(self, ctx: PipelineCtx) -> dict:
        # Track per-language unique edges; dedup matters because the same
        # call expression can be visited multiple times (e.g. two identical
        # call sites in one body produce identical edges).
        per_lang_edges: dict[str, set[SymbolXrefEdge]] = {}
        per_lang_unresolved: dict[str, set[UnresolvedSymbolRef]] = {}

        for record in ctx.records:
            resolver = self._resolvers.get(record.language or "")
            if resolver is None:
                continue
            r_edges, r_unresolved = resolver(record, ctx)
            if not (r_edges or r_unresolved):
                continue
            lang = record.language
            per_lang_edges.setdefault(lang, set()).update(r_edges)
            per_lang_unresolved.setdefault(lang, set()).update(r_unresolved)

        all_edges: set[SymbolXrefEdge] = set()
        all_unresolved: set[UnresolvedSymbolRef] = set()
        for s in per_lang_edges.values():
            all_edges.update(s)
        for s in per_lang_unresolved.values():
            all_unresolved.update(s)

        edges = sorted(all_edges, key=_edge_sort_key)
        unresolved = sorted(all_unresolved, key=_unresolved_sort_key)

        by_kind: dict[str, int] = {}
        by_resolution: dict[str, int] = {}
        for e in edges:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
            by_resolution[e.resolution] = by_resolution.get(e.resolution, 0) + 1

        # Counts unique edges per language (post-dedup), matching the rest
        # of the metrics. Languages with no edges and no unresolved refs
        # don't get a bucket at all — "by_language" reports outcomes,
        # not attempts.
        by_language = {
            lang: {
                "resolved": len(per_lang_edges.get(lang, set())),
                "unresolved": len(per_lang_unresolved.get(lang, set())),
            }
            for lang in sorted(set(per_lang_edges) | set(per_lang_unresolved))
        }

        return {
            "edges": edges,
            "unresolved": unresolved,
            "by_language": by_language,
            "by_kind": by_kind,
            "by_resolution": by_resolution,
        }


def _edge_sort_key(e: SymbolXrefEdge) -> tuple[str, str, str, str, str]:
    return (e.src_chunk_id, e.dst_chunk_id, e.kind, e.resolution, e.resolver)


def _unresolved_sort_key(u: UnresolvedSymbolRef) -> tuple[str, str, str, str, str]:
    return (u.src_chunk_id, u.raw_target, u.kind, u.reason, u.resolver)
