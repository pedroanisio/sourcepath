"""Regression tests for the executability review findings.

Each test pins one accepted fix:
  #1 single-owner ``creates`` (duplicates become ``modifies``)
  #2 file-level order inside SCC joint steps (``cycle_resolutions`` consumption)
  #3 language-aware validation wording
  #4 fixture manifests excluded from the environment step
  #5 no silent truncation of ``contracts``
"""
from __future__ import annotations

import pytest

from recomposer import recompose


def _part(pid, kind="module", role="core", layer=None, outgoing=(), files=(),
          iface=(), conf="strong", languages=("python",)):
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
        "metrics": {"ca": 0, "ce": 0, "instability": None, "n_symbols": 3,
                    "languages": list(languages)},
        "interface_symbols": list(iface),
        "notes": [], "overall_confidence": conf,
    }


def _doc(**overrides):
    base = {
        "repository": {"name": "synthetic", "files": 6, "n_parts": 4,
                       "purpose": "fixture", "purpose_confidence": "certain",
                       "generated_at": "2026-01-01T00:00:00Z", "commit_sha": "cafe"},
        "parts": [],
        "relationships": [],
        "detected_architecture": {"style": "x", "confidence": "strong",
                                  "evidence": [], "violations": [],
                                  "hypotheses": []},
        "quality_gates": [],
        "build_order": [],
        "cycle_resolutions": [],
        "provenance": {"tool": "decomposer-test", "bundle_dir": "synthetic"},
    }
    base.update(overrides)
    return base


# ── #1: single ownership ──────────────────────────────────────────────────────
def test_entry_file_owned_once_wiring_step_modifies():
    doc = _doc(
        parts=[
            _part("module:app", role="adapter",
                  files=["app/main.py", "app/util.py"]),
            {"id": "app:app", "name": "app", "kind": "application",
             "layer": None, "responsibility": "the app",
             "responsibility_confidence": "strong",
             "evidence": {"files": ["app/main.py"], "symbols": [],
                          "graph_nodes": [], "graph_edges": [], "signals": [],
                          "llm_summaries": []},
             "dependencies": {"incoming": [], "outgoing": []},
             "classification": {"role": "core", "role_confidence": "strong",
                                "reusability": "internal", "risk": "low",
                                "risk_reasons": []},
             "metrics": {}, "interface_symbols": [], "notes": [],
             "overall_confidence": "strong"},
            {"id": "entry:app/main.py", "name": "app/main.py",
             "kind": "entrypoint", "layer": None,
             "responsibility": "entry", "responsibility_confidence": "strong",
             "evidence": {"files": ["app/main.py"], "symbols": ["main"],
                          "graph_nodes": [], "graph_edges": [], "signals": [],
                          "llm_summaries": []},
             "dependencies": {"incoming": [], "outgoing": ["module:app"]},
             "classification": {"role": "core", "role_confidence": "strong",
                                "reusability": "internal", "risk": "low",
                                "risk_reasons": []},
             "metrics": {}, "interface_symbols": [], "notes": [],
             "overall_confidence": "strong"},
        ],
        build_order=[["module:app"]],
    )
    plan = recompose(doc)
    owners = {}
    for s in plan.steps:
        for f in s.creates:
            assert f not in owners, f"{f} created twice (steps {owners[f]}, {s.number})"
            owners[f] = s.number
    wiring = next(s for s in plan.steps if s.phase == 7)
    assert "app/main.py" in wiring.modifies
    assert "app/main.py" not in wiring.creates
    module_step = next(s for s in plan.steps if "module:app" in s.parts)
    assert "app/main.py" in module_step.creates


# ── #2: cycle resolution consumption ─────────────────────────────────────────
def _cycle_doc(with_resolution: bool):
    return _doc(
        parts=[
            _part("module:a", outgoing=["module:b"], files=["a/x.py", "a/y.py"]),
            _part("module:b", outgoing=["module:a"], files=["b/z.py"]),
        ],
        build_order=[["module:a", "module:b"]],
        cycle_resolutions=([{
            "members": ["module:a", "module:b"],
            "file_order": ["a/y.py", "b/z.py", "a/x.py"],
            "note": "topological over the group's internal file imports; "
                    "the directory-level cycle dissolves at file granularity",
        }] if with_resolution else []),
    )


def test_joint_step_lists_files_in_resolution_order():
    plan = recompose(_cycle_doc(with_resolution=True))
    joint = next(s for s in plan.steps if len(s.parts) == 2)
    assert joint.creates_ordered is True
    assert joint.creates == ["a/y.py", "b/z.py", "a/x.py"]
    assert any("dissolves at file granularity" in a for a in joint.assumptions)
    assert "file order" in joint.goal


def test_joint_step_without_resolution_says_build_together():
    plan = recompose(_cycle_doc(with_resolution=False))
    joint = next(s for s in plan.steps if len(s.parts) == 2)
    assert joint.creates_ordered is False
    assert sorted(joint.creates) == joint.creates   # plain sorted listing
    assert any("no file-level order is available" in a for a in joint.assumptions)


# ── #3: language-aware validation ─────────────────────────────────────────────
def test_non_importable_language_gets_parse_validation():
    doc = _doc(
        parts=[_part("module:styles", role="supporting",
                     files=["styles/site.css"], languages=("css",))],
        build_order=[["module:styles"]],
    )
    plan = recompose(doc)
    step = next(s for s in plan.steps if "module:styles" in s.parts)
    joined = " ".join(step.tests_required)
    assert "smoke-import" not in joined
    assert "parse" in joined and "css" in joined


def test_importable_language_gets_import_validation():
    doc = _doc(
        parts=[_part("module:lib", files=["lib/a.py"])],
        build_order=[["module:lib"]],
    )
    plan = recompose(doc)
    step = next(s for s in plan.steps if "module:lib" in s.parts)
    assert any("smoke-import/compile" in t and "python" in t
               for t in step.tests_required)


# ── #4: fixture manifests ─────────────────────────────────────────────────────
def test_fixture_manifests_move_to_test_phase():
    doc = _doc(
        parts=[
            _part("module:src", files=["src/a.py"]),
            _part("module:tests/fixtures/pkg/lib", role="test",
                  files=["tests/fixtures/pkg/lib/t.py"]),
            {"id": "ops:dependency_management", "name": "dependency_management",
             "kind": "operational", "layer": "operational",
             "responsibility": "deps", "responsibility_confidence": "strong",
             "evidence": {"files": ["pyproject.toml",
                                    "tests/fixtures/pkg/pubspec.yaml"],
                          "symbols": [], "graph_nodes": [], "graph_edges": [],
                          "signals": [], "llm_summaries": []},
             "dependencies": {"incoming": [], "outgoing": []},
             "classification": {"role": "infrastructure",
                                "role_confidence": "certain",
                                "reusability": "internal", "risk": "low",
                                "risk_reasons": []},
             "metrics": {}, "interface_symbols": [], "notes": [],
             "overall_confidence": "certain"},
        ],
        build_order=[["module:src", "module:tests/fixtures/pkg/lib"]],
    )
    plan = recompose(doc)
    env = next(s for s in plan.steps if s.phase == 2)
    assert "pyproject.toml" in env.creates
    assert "tests/fixtures/pkg/pubspec.yaml" not in env.creates
    fixture = next(s for s in plan.steps
                   if "plan:fixture-manifests" in s.parts)
    assert fixture.phase == 10
    assert "tests/fixtures/pkg/pubspec.yaml" in fixture.creates
    # The fixture step precedes the test modules that read the manifests.
    test_mod = next(s for s in plan.steps
                    if "module:tests/fixtures/pkg/lib" in s.parts)
    assert fixture.number < test_mod.number


# ── #5: no silent truncation ──────────────────────────────────────────────────
def test_contracts_are_not_truncated():
    iface = [f"symbol_{i:03d}" for i in range(40)]   # > the old cap of 25
    doc = _doc(
        parts=[_part("module:big", files=["big/a.py"], iface=iface)],
        build_order=[["module:big"]],
    )
    plan = recompose(doc)
    step = next(s for s in plan.steps if "module:big" in s.parts)
    assert len(step.contracts) == 40
    assert step.contracts == sorted(iface)
