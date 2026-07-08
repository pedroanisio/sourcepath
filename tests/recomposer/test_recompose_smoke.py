"""End-to-end: decompose the real bundle, recompose it, validate plan invariants.

Exercises the actual Decomposer→Recomposer contract (via ``to_document``), so a
schema drift between the two packages fails here before it reaches a user.
Skips cleanly when no bundle exists.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from decomposer import decompose
from decomposer import to_yaml as decomp_to_yaml
from recomposer import recompose, to_markdown, to_yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _find_bundle() -> Path | None:
    root = REPO_ROOT / "_tmp"
    if not root.is_dir():
        return None
    for child in sorted(root.iterdir()):
        if (child / "run_manifest.json").exists():
            return child
    return None


@pytest.fixture(scope="module")
def plan_and_doc():
    bundle = _find_bundle()
    if bundle is None:
        pytest.skip("no bundle under _tmp/ to decompose")
    # Round-trip through YAML text — the real consumption contract.
    doc = yaml.safe_load(decomp_to_yaml(decompose(bundle)))
    return recompose(doc), doc


def test_all_module_parts_scheduled_exactly_once(plan_and_doc):
    plan, doc = plan_and_doc
    module_ids = sorted(
        p["id"] for p in doc["parts"] if p["kind"] in {"module", "package"})
    scheduled = sorted(
        pid for s in plan.steps for pid in s.parts if pid.startswith("module:"))
    assert scheduled == module_ids


def test_requires_are_backward_only(plan_and_doc):
    plan, _ = plan_and_doc
    for s in plan.steps:
        assert all(r < s.number for r in s.requires)


def test_module_dependencies_precede_dependents(plan_and_doc):
    plan, doc = plan_and_doc
    num = {pid: s.number for s in plan.steps for pid in s.parts}
    for p in doc["parts"]:
        if p["kind"] not in {"module", "package"}:
            continue
        for dep in p["dependencies"]["outgoing"]:
            if dep in num and num[dep] != num[p["id"]]:
                assert num[dep] < num[p["id"]], (dep, p["id"])


def test_steps_carry_required_fields(plan_and_doc):
    plan, _ = plan_and_doc
    for s in plan.steps:
        assert s.goal and s.expected_result and s.confidence
        assert s.evidence, f"step {s.number} has no evidence"


def test_step_numbers_are_dense_and_ordered(plan_and_doc):
    plan, _ = plan_and_doc
    assert [s.number for s in plan.steps] == list(range(1, len(plan.steps) + 1))
    phases = [s.phase for s in plan.steps]
    assert phases == sorted(phases), "steps must be emitted phase-monotonically"


def test_plan_is_deterministic(plan_and_doc):
    plan, doc = plan_and_doc
    assert to_yaml(recompose(doc)) == to_yaml(plan)


def test_markdown_renders_with_disclaimer_and_phases(plan_and_doc):
    plan, _ = plan_and_doc
    md = to_markdown(plan)
    assert md.startswith("---")
    assert "Construction sequence" in md
    assert "Open assumptions" in md


def test_cycles_become_joint_steps(plan_and_doc):
    """Modules the decomposer reports as a directory-granularity cycle must land
    in one joint step. The gate's *subject* lists the member module ids — an
    independently produced view of the same SCCs the scheduler derives from the
    dependency edges, so this cross-checks the two rather than testing the
    scheduler against itself."""
    plan, doc = plan_and_doc
    cycle_findings = [q for q in doc["quality_gates"]
                      if q["gate"] == "directory_aggregation_cycle"]
    if not cycle_findings:
        pytest.skip("bundle has no directory-granularity cycles")
    num = {pid: s.number for s in plan.steps for pid in s.parts}
    for q in cycle_findings:
        members = [m for m in q["subject"].split(", ") if m in num]
        assert len(members) >= 2, f"unparseable cycle subject: {q['subject']}"
        assert len({num[m] for m in members}) == 1, "cycle split across steps"
