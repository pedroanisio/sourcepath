"""codebase_mapper.fast_serializer — Rust-backed inventory Turtle emission.

rdflib's Turtle serializer dominates emit() wall-clock on large graphs
(it re-groups subjects and computes prefix compressions in Python).
Measured on real artifacts (Xeon 8592+, 2026-07-09):

- 255,343 triples (fastapi): rdflib Turtle 8.5 s · rdflib N-Triples
  1.0 s · oxigraph Turtle 0.25 s.
- 67,382,898 triples (torvalds/linux): oxigraph parsed the 5.4 GB
  Turtle in 157 s and re-serialized Turtle in 38 s at 23.6 GB peak;
  the rdflib path spent tens of minutes and >100 GB on the same stage.

Strategy: emit the graph once through rdflib's cheapest serializer
(N-Triples — a flat line-per-triple walk), bulk-load into an oxigraph
store (Rust), and dump prefixed Turtle. Falls back to rdflib when:

- pyoxigraph is not installed, or
- the graph contains blank nodes: rdflib labels bnodes with
  process-random ids, so the N-Triples intermediate would break the
  project's byte-determinism commitment. (Host + shipped plugins keep
  the inventory graph bnode-free; the guard makes that an enforced
  invariant instead of an assumption.)
"""
from __future__ import annotations

import logging
import os
import subprocess

from pathlib import Path

from rdflib import BNode, Graph

_log = logging.getLogger(__name__)


def _load_pyoxigraph():
    """Import seam (patched in tests). Returns the module or None."""
    try:
        import pyoxigraph
        return pyoxigraph
    except ImportError:
        return None


def _has_bnodes(g: Graph) -> bool:
    """Full-graph scan with early exit. Costs one Python pass in the
    clean case — small next to the Turtle serialization it unlocks."""
    return any(
        isinstance(s, BNode) or isinstance(o, BNode) for s, _p, o in g
    )


def serialize_inventory(g: Graph, dest: Path) -> str:
    """Write ``g`` to ``dest`` as prefixed Turtle. Returns the engine
    used: ``"oxigraph"`` (fast path) or ``"rdflib"`` (fallback). The
    output is byte-deterministic per engine; the manifest records which
    engine produced the artifact.
    """
    ox = _load_pyoxigraph()
    if ox is None:
        _log.info("pyoxigraph unavailable — inventory Turtle via rdflib "
                  "(slow on large graphs)")
        g.serialize(destination=str(dest), format="turtle")
        return "rdflib"
    if _has_bnodes(g):
        _log.warning(
            "inventory graph contains blank nodes; rdflib bnode labels are "
            "process-random and would break byte-determinism through the "
            "N-Triples intermediate — falling back to rdflib Turtle")
        g.serialize(destination=str(dest), format="turtle")
        return "rdflib"
    nt_tmp = dest.with_name(dest.name + ".tmp.nt")
    nt_sorted = dest.with_name(dest.name + ".tmp.sorted.nt")
    try:
        g.serialize(destination=str(nt_tmp), format="nt", encoding="utf-8")
        # Canonicalize: byte-sort the triple lines (LC_ALL=C, external
        # merge sort — memory-bounded) so the Turtle bytes are a pure
        # function of the triple SET, independent of contributor
        # insertion order and of any loader parallelism. A Store-based
        # round trip is NOT order-stable (bulk_load loads in parallel),
        # which the CI determinism verifier caught; the streaming
        # parse→serialize below preserves the sorted order by
        # construction.
        subprocess.run(
            ["sort", "-o", str(nt_sorted), "-T", str(dest.parent),
             str(nt_tmp)],
            check=True, env={**os.environ, "LC_ALL": "C"},
        )
        ox.serialize(
            ox.parse(path=str(nt_sorted), format=ox.RdfFormat.N_TRIPLES),
            output=str(dest), format=ox.RdfFormat.TURTLE,
            prefixes={p: str(ns) for p, ns in g.namespaces()},
        )
    finally:
        nt_tmp.unlink(missing_ok=True)
        nt_sorted.unlink(missing_ok=True)
    return "oxigraph"
