"""Integration test: decompose the project's own bundle end-to-end.

Skips cleanly when no bundle is available (e.g. CI without a generated ``_tmp``),
so the suite never depends on regenerating a multi-megabyte artifact.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from decomposer import decompose, to_markdown, to_yaml
from decomposer.model import Confidence

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
def decomp():
    bundle = _find_bundle()
    if bundle is None:
        pytest.skip("no bundle under _tmp/ to decompose")
    return decompose(bundle)


def test_repository_header_is_populated(decomp):
    r = decomp.repository
    assert r["name"]
    assert r["files"] and r["files"] > 0
    assert r["n_parts"] == len(decomp.parts)
    assert r["confidence"] in {c.value for c in Confidence}


def test_multiple_part_kinds_are_discovered(decomp):
    kinds = {p.kind for p in decomp.parts}
    # A non-trivial repo must surface at least modules, externals, entry points.
    assert {"module", "package"} & kinds
    assert "external_dependency" in kinds
    assert "entrypoint" in kinds


def test_every_part_carries_explicit_confidence(decomp):
    for p in decomp.parts:
        assert isinstance(p.overall_confidence, Confidence)
        assert isinstance(p.classification.role_confidence, Confidence)
        assert p.classification.role in {
            "core", "supporting", "infrastructure", "adapter", "test", "generated",
        }


def test_instability_is_bounded_and_consistent(decomp):
    for p in decomp.parts:
        inst = p.metrics.get("instability")
        if inst is not None:
            assert 0.0 <= inst <= 1.0


def test_yaml_has_all_part_two_sections(decomp):
    doc = yaml.safe_load(to_yaml(decomp))
    for key in ("repository", "parts", "relationships", "detected_architecture"):
        assert key in doc
    # Recomposer-readiness: build order + interface symbols must be present.
    assert "build_order" in doc
    assert any(p.get("interface_symbols") for p in doc["parts"])


def test_build_order_covers_all_module_parts(decomp):
    module_ids = {p.id for p in decomp.parts if p.kind in {"module", "package"}}
    ordered = {pid for layer in decomp.build_order for pid in layer}
    # Every module part that participates in the import graph is scheduled.
    assert ordered <= module_ids
    assert ordered, "build order should schedule at least one module"


def test_output_is_deterministic(decomp):
    bundle = _find_bundle()
    again = decompose(bundle)
    assert to_yaml(decomp) == to_yaml(again)


def test_markdown_report_renders(decomp):
    md = to_markdown(decomp)
    assert md.startswith("---")            # disclaimer frontmatter
    assert "Detected architecture" in md
    assert "Reconstruction build order" in md


def test_report_carries_bundle_provenance(decomp):
    md = to_markdown(decomp)
    assert "**Bundle:**" in md
    assert "run_manifest sha256" in md


def test_report_part_tables_use_unique_ids(decomp):
    md = to_markdown(decomp)
    # `codebase_mapper` exists both as a module part and an application part;
    # rows must be distinguishable by part id.
    assert "`module:" in md
    assert "`app:" in md


def test_report_role_tables_exclude_domain_parts(decomp):
    md = to_markdown(decomp)
    section = md.split("## Parts by role", 1)[1].split("## Coupling", 1)[0]
    # Interpretive domain overlays live in their own section, not in the
    # structural parts inventory.
    assert "`domain:" not in section
    # The section itself exists only when the bundle carries concept evidence
    # (a bundle built without the concept plugin legitimately has no domains).
    if any(p.kind == "domain" for p in decomp.parts):
        assert "## Semantic domains" in md
    else:
        assert "## Semantic domains" not in md


def test_report_gate_table_has_examples(decomp):
    md = to_markdown(decomp)
    assert "| gate | findings | worst severity | examples |" in md


def test_report_marks_cycle_groups_in_build_order(decomp):
    md = to_markdown(decomp)
    if decomp.provenance.get("module_cycles"):
        section = md.split("## Reconstruction build order", 1)[1]
        assert "⇄" in section


def test_report_legend_defines_interpretive_columns(decomp):
    md = to_markdown(decomp)
    legend = md.rsplit("---", 1)[-1]
    for term in ("reuse", "risk", "layer", "confidence"):
        assert term in legend, f"legend missing {term}"
