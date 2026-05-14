"""Graph endpoints application logic."""
from __future__ import annotations

from .bundle_data import get_bundle


def build_file_graph_response(limit: int, bundle: str | None = None) -> dict[str, object]:
    b = get_bundle(bundle)
    deg: dict[str, int] = {}
    for a, b_ in b.imports:
        deg[a] = deg.get(a, 0) + 1
        deg[b_] = deg.get(b_, 0) + 1
    ranked = sorted(b.files, key=lambda r: deg.get(r["path"], 0), reverse=True)
    selected = ranked[:limit]
    selected_paths = {r["path"] for r in selected}
    return {
        "nodes": [
            {
                "id": r["path"],
                "label": r["path"].rsplit("/", 1)[-1],
                "group": r["language"] or r["type"] or "unknown",
                "weight": float(deg.get(r["path"], 0)),
                "meta": {
                    "path": r["path"],
                    "type": r["type"],
                    "language": r["language"],
                    "size": r["size"],
                },
            }
            for r in selected
        ],
        "edges": [
            {"source": a, "target": b_, "weight": None}
            for a, b_ in b.imports
            if a in selected_paths and b_ in selected_paths
        ],
        "truncated": len(b.files) > len(selected),
        "total_nodes_available": len(b.files),
    }


def build_symbol_graph_response(
    limit: int,
    kind: str = "calls",
    bundle: str | None = None,
) -> dict[str, object]:
    b = get_bundle(bundle)
    selected_edges = [e for e in b.xrefs if kind == "all" or e["kind"] == kind]
    deg: dict[int, int] = {}
    for e in selected_edges:
        deg[e["src_idx"]] = deg.get(e["src_idx"], 0) + 1
        deg[e["dst_idx"]] = deg.get(e["dst_idx"], 0) + 1
    selected_idxs = sorted(deg.keys(), key=lambda i: (-deg[i], i))[:limit]
    selected_set = set(selected_idxs)
    return {
        "nodes": [
            {
                "id": str(i),
                "label": b.chunks[i].get("symbol") or "—",
                "group": b.chunks[i].get("kind") or "unknown",
                "weight": float(deg.get(i, 0)),
                "meta": {
                    "idx": i,
                    "file": b.chunks[i].get("file"),
                    "kind": b.chunks[i].get("kind"),
                    "beginLine": b.chunks[i].get("beginLine"),
                    "endLine": b.chunks[i].get("endLine"),
                },
            }
            for i in selected_idxs
        ],
        "edges": [
            {"source": str(e["src_idx"]), "target": str(e["dst_idx"]), "weight": None}
            for e in selected_edges
            if e["src_idx"] in selected_set and e["dst_idx"] in selected_set
        ],
        "truncated": len(deg) > len(selected_idxs),
        "total_nodes_available": len(deg),
    }


def build_concept_graph_response(
    limit: int,
    min_edge: int,
    bundle: str | None = None,
) -> dict[str, object]:
    b = get_bundle(bundle)
    concepts = b.concepts.get("concepts", {})
    ranked = sorted(
        concepts.items(),
        key=lambda kv: kv[1].get("frequency", 0),
        reverse=True,
    )
    selected = ranked[:limit]
    selected_set = {k for k, _ in selected}
    edges = []
    for entry in b.concepts.get("cooccurrence", []):
        if len(entry) != 3:
            continue
        a, b_, w = entry
        if w < min_edge:
            continue
        if a in selected_set and b_ in selected_set:
            edges.append({"source": a, "target": b_, "weight": float(w)})
    return {
        "nodes": [
            {
                "id": k,
                "label": v.get("label") or k,
                "group": None,
                "weight": float(v.get("frequency", 0)),
                "meta": {
                    "alt_labels": v.get("alt_labels", []),
                    "frequency": v.get("frequency", 0),
                    "file_count": v.get("file_count", 0),
                    "components": v.get("components", []),
                },
            }
            for k, v in selected
        ],
        "edges": edges,
        "truncated": len(concepts) > len(selected),
        "total_nodes_available": len(concepts),
    }
