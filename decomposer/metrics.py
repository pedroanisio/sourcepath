"""Graph metrics — pure, deterministic, evidence-grade.

Every function here operates on plain node sets and adjacency maps and returns
values that are *directly derived* from the import graph, hence ``certain``-grade
under the Part IV confidence ladder. No naming heuristics, no LLM signal.

References:
  * Robert C. Martin, "OO Design Quality Metrics: An Analysis of Dependencies"
    (1994) — afferent/efferent coupling and the Instability metric I = Ce/(Ca+Ce).
  * Robert C. Martin, "Agile Software Development: Principles, Patterns, and
    Practices" (Prentice Hall, 2002), ch. 20 — Stable-Dependencies Principle.
  * Tarjan, R. E. "Depth-first search and linear graph algorithms."
    SIAM J. Comput. 1(2), 146–160 (1972) — strongly connected components.
"""
from __future__ import annotations

from typing import Hashable, Iterable, TypeVar

N = TypeVar("N", bound=Hashable)


def instability(ca: int, ce: int) -> float | None:
    """Martin's Instability I = Ce / (Ca + Ce), in [0, 1].

    Ca = afferent coupling (incoming dependencies / fan-in),
    Ce = efferent coupling (outgoing dependencies / fan-out).
    Returns ``None`` for an isolated node (Ca+Ce == 0): instability is undefined,
    not zero — an unconnected node is neither stable nor unstable.
    """
    total = ca + ce
    if total == 0:
        return None
    return ce / total


def tarjan_scc(nodes: Iterable[N], adjacency: dict[N, list[N]]) -> list[list[N]]:
    """Strongly connected components via iterative Tarjan (no recursion limit).

    ``adjacency[n]`` lists the nodes ``n`` points to (for an import graph:
    the modules ``n`` imports). Components are returned in reverse-topological
    order (sinks first), the natural order Tarjan emits. Node iteration is
    sorted so the output is deterministic for a given graph.
    """
    index_of: dict[N, int] = {}
    lowlink: dict[N, int] = {}
    on_stack: set[N] = set()
    stack: list[N] = []
    result: list[list[N]] = []
    counter = 0

    node_list = sorted(nodes, key=_sort_key)

    for root in node_list:
        if root in index_of:
            continue
        # Iterative DFS. Each work item is (node, neighbor-iterator-position).
        work: list[tuple[N, int]] = [(root, 0)]
        while work:
            node, pi = work[-1]
            if pi == 0:
                index_of[node] = counter
                lowlink[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            neighbors = adjacency.get(node, [])
            recursed = False
            while pi < len(neighbors):
                nxt = neighbors[pi]
                pi += 1
                if nxt not in index_of:
                    work[-1] = (node, pi)
                    work.append((nxt, 0))
                    recursed = True
                    break
                if nxt in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[nxt])
            if recursed:
                continue
            work[-1] = (node, pi)
            # All neighbors processed: settle this node.
            if lowlink[node] == index_of[node]:
                component: list[N] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == node:
                        break
                result.append(sorted(component, key=_sort_key))
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
    return result


def cycles(nodes: Iterable[N], adjacency: dict[N, list[N]]) -> list[list[N]]:
    """SCCs of size > 1, or singletons with a self-loop — i.e. real cycles."""
    out: list[list[N]] = []
    for comp in tarjan_scc(nodes, adjacency):
        if len(comp) > 1:
            out.append(comp)
        elif len(comp) == 1 and comp[0] in adjacency.get(comp[0], []):
            out.append(comp)
    return out


def build_order(nodes: Iterable[N], adjacency: dict[N, list[N]]) -> list[list[N]]:
    """Topologically layered reconstruction order over the dependency graph.

    ``adjacency`` is the *depends-on* graph (``a -> b`` means "a imports b", so b
    must exist before a). Cyclic groups (SCCs) are condensed to a single vertex
    and emitted together in one layer with all their members — you cannot build
    one before another, so the Recomposer builds them jointly and breaks the
    cycle afterward.

    Layer 0 holds nodes with no dependencies (build first); each later layer
    depends only on earlier ones. Within a layer, nodes are sorted for
    determinism.
    """
    node_list = list(nodes)
    comps = tarjan_scc(node_list, adjacency)
    comp_of: dict[N, int] = {}
    for i, comp in enumerate(comps):
        for n in comp:
            comp_of[n] = i

    # Condensation edges: comp -> set of comps it depends on (excluding self).
    dep: dict[int, set[int]] = {i: set() for i in range(len(comps))}
    for n in node_list:
        ci = comp_of.get(n)
        if ci is None:
            continue
        for m in adjacency.get(n, []):
            cj = comp_of.get(m)
            if cj is not None and cj != ci:
                dep[ci].add(cj)

    # Longest-path level over the depends-on DAG (memoized DFS; DAG => safe).
    level: dict[int, int] = {}

    def depth(ci: int, seen: frozenset[int]) -> int:
        if ci in level:
            return level[ci]
        if not dep[ci]:
            level[ci] = 0
            return 0
        # ``seen`` guards against any residual self-reference; condensation is
        # acyclic so this is belt-and-suspenders, not load-bearing.
        d = 1 + max(
            depth(cj, seen | {ci}) for cj in dep[ci] if cj not in seen
        ) if any(cj not in seen for cj in dep[ci]) else 0
        level[ci] = d
        return d

    for ci in range(len(comps)):
        depth(ci, frozenset())

    max_level = max(level.values(), default=-1)
    layers: list[list[N]] = [[] for _ in range(max_level + 1)]
    for ci, lvl in level.items():
        layers[lvl].extend(comps[ci])
    for layer in layers:
        layer.sort(key=_sort_key)
    return layers


def _sort_key(n: N):
    """Deterministic ordering across heterogeneous node types."""
    return (type(n).__name__, str(n))
