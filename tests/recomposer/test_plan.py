"""Unit tests for the recomposition scheduler on a synthetic decomposition.

The synthetic document exercises the scheduler's load-bearing behaviors without
needing a real bundle: cycle merging, phase relaxation, dependency-safe
ordering, and evidence propagation.
"""
from __future__ import annotations

import pytest

from recomposer import recompose, to_markdown, to_yaml


def _part(pid, kind="module", role="core", layer=None, outgoing=(), files=(),
          iface=(), conf="strong", ca=0, ce=0):
    name = pid.split(":", 1)[1]
    return {
        "id": pid, "name": name, "kind": kind, "layer": layer,
        "responsibility": f"{role} responsibilities of {name}.",
        "responsibility_confidence": conf,
        "evidence": {"files": list(files), "symbols": [], "graph_nodes": [],
                     "graph_edges": [], "signals": [], "llm_summaries": []},
        "dependencies": {"incoming": [], "outgoing": list(outgoing)},
        "classification": {"role": role, "role_confidence": conf,
                           "reusability": "internal", "risk": "low",
                           "risk_reasons": []},
        "metrics": {"ca": ca, "ce": ce, "instability": None, "n_symbols": 3},
        "interface_symbols": list(iface),
        "notes": [], "overall_confidence": conf,
    }


@pytest.fixture()
def doc():
    """core_a <-> infra_b form a cycle; adapter_c depends on the cycle;
    tests_d covers core_a. domain_e is a leaf domain module."""
    return {
        "repository": {"name": "synthetic", "files": 10, "n_parts": 5,
                       "purpose": "test fixture", "purpose_confidence": "certain",
                       "generated_at": "2026-01-01T00:00:00Z", "commit_sha": "cafe"},
        "parts": [
            _part("module:core_a", role="core",
                  outgoing=["module:infra_b", "module:domain_e"],
                  files=["core_a/x.py"], iface=["run"]),
            _part("module:infra_b", role="infrastructure",
                  outgoing=["module:core_a"], files=["infra_b/y.py"]),
            _part("module:adapter_c", role="adapter",
                  outgoing=["module:core_a"], files=["adapter_c/z.py"]),
            _part("module:tests_d", role="test",
                  outgoing=["module:core_a"], files=["tests_d/t.py"]),
            _part("module:domain_e", role="core", layer="domain",
                  files=["domain_e/m.py"]),
            {
                "id": "ext:numpy", "name": "numpy", "kind": "external_dependency",
                "layer": "external", "responsibility": "dep",
                "responsibility_confidence": "certain",
                "evidence": {"files": [], "symbols": [], "graph_nodes": [],
                             "graph_edges": [], "signals": [], "llm_summaries": []},
                "dependencies": {"incoming": ["module:core_a"], "outgoing": []},
                "classification": {"role": "infrastructure",
                                   "role_confidence": "strong",
                                   "reusability": "external", "risk": "low",
                                   "risk_reasons": []},
                "metrics": {"importer_modules": 1},
                "interface_symbols": [], "notes": [],
                "overall_confidence": "certain",
            },
        ],
        "relationships": [
            {"from": "module:core_a", "to": "ext:numpy",
             "type": "imports_external", "strength": 1,
             "confidence": "certain", "evidence": ""},
            {"from": "module:tests_d", "to": "module:core_a", "type": "tests",
             "strength": 1, "confidence": "certain", "evidence": ""},
        ],
        "detected_architecture": {
            "style": "layered", "confidence": "strong", "evidence": [],
            "violations": [], "hypotheses": [],
        },
        "quality_gates": [
            {"gate": "circular_dependencies", "severity": "error",
             "subject": "module:core_a, module:infra_b",
             "description": "cycle", "confidence": "certain",
             "evidence": ["module:core_a", "module:infra_b"]},
        ],
        # depends-on layering: cycle group + domain_e at 0; c and d above.
        "build_order": [
            ["module:core_a", "module:infra_b", "module:domain_e"],
            ["module:adapter_c", "module:tests_d"],
        ],
        "provenance": {"tool": "decomposer-test", "bundle_dir": "synthetic"},
    }


def test_cycle_members_share_one_joint_step(doc):
    plan = recompose(doc)
    steps_a = [s for s in plan.steps if "module:core_a" in s.parts]
    steps_b = [s for s in plan.steps if "module:infra_b" in s.parts]
    assert len(steps_a) == len(steps_b) == 1
    assert steps_a[0].number == steps_b[0].number
    assert any("cycle" in a for a in steps_a[0].assumptions)


def test_requires_always_point_backward(doc):
    plan = recompose(doc)
    for s in plan.steps:
        assert all(r < s.number for r in s.requires), (s.number, s.requires)


def test_dependency_before_dependent(doc):
    plan = recompose(doc)
    num = {pid: s.number for s in plan.steps for pid in s.parts}
    # adapter_c depends on core_a: cycle step must come first.
    assert num["module:core_a"] < num["module:adapter_c"]
    # tests after core.
    assert num["module:core_a"] < num["module:tests_d"]
    # domain module scheduled no later than the cycle that depends on it.
    assert num["module:domain_e"] < num["module:adapter_c"]


def test_phase_assignment(doc):
    plan = recompose(doc)
    phase = {pid: s.phase for s in plan.steps for pid in s.parts}
    assert phase["module:domain_e"] == 3          # layer=domain -> phase 3
    assert phase["module:adapter_c"] == 6         # adapter -> phase 6
    assert phase["module:tests_d"] == 10          # test role -> phase 10
    # cycle: min(core=5, infra=6) = 5
    assert phase["module:core_a"] == 5


def test_external_dependency_flows_into_step(doc):
    plan = recompose(doc)
    cycle_step = next(s for s in plan.steps if "module:core_a" in s.parts)
    assert any("numpy" in d for d in cycle_step.dependencies_introduced)
    # env step (declares numpy) must be required.
    env = next(s for s in plan.steps if s.phase == 2)
    assert env.number in cycle_step.requires


def test_every_module_part_is_scheduled_exactly_once(doc):
    plan = recompose(doc)
    scheduled = [pid for s in plan.steps for pid in s.parts
                 if pid.startswith("module:")]
    assert sorted(scheduled) == sorted(
        p["id"] for p in doc["parts"] if p["kind"] == "module")


def test_tests_required_names_test_module(doc):
    plan = recompose(doc)
    cycle_step = next(s for s in plan.steps if "module:core_a" in s.parts)
    assert any("tests_d" in t for t in cycle_step.tests_required)


def test_determinism(doc):
    a, b = recompose(doc), recompose(doc)
    assert to_yaml(a) == to_yaml(b)
    assert to_markdown(a) == to_markdown(b)


def test_persistence_phase_skipped_with_reason(doc):
    plan = recompose(doc)
    assert any("persistence" in s["phase"].lower() or "migration" in s["reason"]
               for s in plan.skipped_phases)


def test_relaxation_pulls_dependency_earlier():
    """A core module (phase 5) depending on an adapter module (nominal 6)
    forces the adapter into phase <= 5 — dependency evidence beats canon."""
    doc = {
        "repository": {"name": "r", "files": 2, "n_parts": 2,
                       "purpose": "x", "purpose_confidence": "certain",
                       "generated_at": "2026-01-01T00:00:00Z"},
        "parts": [
            _part("module:core", role="core", outgoing=["module:helper"]),
            _part("module:helper", role="adapter"),
        ],
        "relationships": [], "quality_gates": [],
        "detected_architecture": {"style": "x", "confidence": "weak",
                                  "evidence": [], "violations": [],
                                  "hypotheses": []},
        "build_order": [["module:helper"], ["module:core"]],
        "provenance": {},
    }
    plan = recompose(doc)
    phase = {pid: s.phase for s in plan.steps for pid in s.parts}
    assert phase["module:helper"] <= phase["module:core"]
    num = {pid: s.number for s in plan.steps for pid in s.parts}
    assert num["module:helper"] < num["module:core"]
