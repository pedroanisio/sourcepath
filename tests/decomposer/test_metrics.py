"""Unit tests for the pure graph metrics (the numeric backbone of the report)."""
from __future__ import annotations

from decomposer.metrics import build_order, cycles, instability, tarjan_scc


def test_instability_bounds_and_formula():
    assert instability(0, 0) is None          # isolated node: undefined, not 0
    assert instability(3, 1) == 0.25          # Ce/(Ca+Ce)
    assert instability(0, 5) == 1.0           # pure consumer: maximally unstable
    assert instability(5, 0) == 0.0           # pure dependency: maximally stable


def test_tarjan_finds_scc_and_singletons():
    adj = {"a": ["b"], "b": ["c"], "c": ["a"], "d": ["c"], "e": []}
    comps = tarjan_scc("abcde", adj)
    sizes = sorted(len(c) for c in comps)
    assert sizes == [1, 1, 3]
    big = [sorted(c) for c in comps if len(c) == 3][0]
    assert big == ["a", "b", "c"]


def test_cycles_include_self_loops_and_exclude_dags():
    assert cycles("abc", {"a": ["b"], "b": ["c"], "c": []}) == []   # DAG: no cycles
    assert cycles(["x", "y"], {"x": ["x"], "y": ["x"]}) == [["x"]]  # self-loop counts


def test_build_order_layers_dependencies_before_dependents():
    # a->b means "a imports b"; b must be built first.
    adj = {"a": ["b"], "b": ["c"], "c": []}
    layers = build_order("abc", adj)
    flat = [n for layer in layers for n in layer]
    assert flat.index("c") < flat.index("b") < flat.index("a")


def test_build_order_condenses_cycles_into_one_layer():
    adj = {"a": ["b"], "b": ["c"], "c": ["a"], "d": ["a"]}
    layers = build_order("abcd", adj)
    # a, b, c form a cycle -> same (earliest) layer; d depends on it -> later.
    layer_of = {n: i for i, layer in enumerate(layers) for n in layer}
    assert layer_of["a"] == layer_of["b"] == layer_of["c"]
    assert layer_of["d"] > layer_of["a"]


def test_metrics_are_deterministic_across_runs():
    adj = {"a": ["b", "c"], "b": ["c"], "c": [], "d": ["a"]}
    assert tarjan_scc("abcd", adj) == tarjan_scc("abcd", adj)
    assert build_order("abcd", adj) == build_order("abcd", adj)
