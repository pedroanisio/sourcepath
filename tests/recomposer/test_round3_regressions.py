"""RED (round-3 review: 2,528-step / 13,445-file scale run against Airflow).

* #2 explicit schema_version: two schema-different tool runs must not carry
  the same version stamp (v0.1.0 was hardcoded across a commit that added
  `modifies`/`creates_ordered`).
* #5 ownership reconciliation at scale: every file the decomposition carries
  is owned by a step or listed in `unassigned_files` with a reason — this
  round adds the "large repo, many unassigned" shape on top of round-2's
  single-file case.
* #4 phase bookkeeping: a fixed-step "skip" must never coexist with a
  misleading "phase skipped" label when unit steps still populate that phase.
"""
from __future__ import annotations

from recomposer import recompose
from recomposer.model import SCHEMA_VERSION


def _part(pid, kind="module", role="core", outgoing=(), files=(), iface=(),
          conf="strong", languages=("python",), metrics_extra=None):
    name = pid.split(":", 1)[1]
    metrics = {"ca": 0, "ce": 0, "instability": None, "n_symbols": 3,
               "languages": list(languages)}
    metrics.update(metrics_extra or {})
    return {
        "id": pid, "name": name, "kind": kind, "layer": None,
        "responsibility": f"{role} responsibilities of {name}.",
        "responsibility_confidence": conf,
        "evidence": {"files": list(files), "symbols": [], "graph_nodes": [],
                     "graph_edges": [], "signals": [], "llm_summaries": []},
        "dependencies": {"incoming": [], "outgoing": list(outgoing)},
        "classification": {"role": role, "role_confidence": conf,
                           "reusability": "internal", "risk": "low",
                           "risk_reasons": []},
        "metrics": metrics,
        "interface_symbols": list(iface),
        "notes": [], "overall_confidence": conf,
    }


def _doc(**overrides):
    base = {
        "repository": {"name": "synthetic", "files": 3, "n_parts": 1,
                       "purpose": "fixture", "purpose_confidence": "certain",
                       "generated_at": "2026-01-01T00:00:00Z", "commit_sha": "cafe"},
        "parts": [_part("module:lib", files=["lib/a.py"])],
        "relationships": [],
        "detected_architecture": {"style": "x", "confidence": "strong",
                                  "evidence": [], "violations": [],
                                  "hypotheses": []},
        "quality_gates": [],
        "build_order": [["module:lib"]],
        "cycle_resolutions": [],
        "provenance": {"tool": "decomposer-test", "bundle_dir": "synthetic"},
    }
    base.update(overrides)
    return base


def test_schema_version_is_explicit_and_not_the_bare_tool_string():
    plan = recompose(_doc())
    assert plan.provenance.get("schema_version") == SCHEMA_VERSION
    # Must be a dotted version, independent of the free-text tool name.
    assert SCHEMA_VERSION.count(".") == 2


def test_domain_data_skip_reason_never_implies_an_empty_phase():
    # No data_schema/domain-*kind* parts, but a module classified layer=domain
    # still nominally builds in phase 3 -- the skip note must say so, and the
    # word "skipped" must not appear unqualified against an active phase.
    doc = _doc(parts=[_part("module:core/domain", files=["core/domain/order.py"])],
               build_order=[["module:core/domain"]])
    doc["parts"][0]["layer"] = "domain"
    plan = recompose(doc)
    present = {s.phase for s in plan.steps}
    assert 3 in present, "the domain-layer module must still schedule in phase 3"
    skip = next(s for s in plan.skipped_phases if "domain" in s["phase"].lower())
    assert "still build" in skip["reason"] or "module" in skip["reason"]


# ── #6: per-toolchain validation beyond Rust ──────────────────────────────────
def test_go_module_gets_go_build_not_smoke_import():
    doc = _doc(parts=[_part("module:cmd/api", files=["cmd/api/main.go"],
                            languages=("go",))],
               build_order=[["module:cmd/api"]])
    plan = recompose(doc)
    step = next(s for s in plan.steps if "module:cmd/api" in s.parts)
    assert any("go build ./cmd/api/..." in t for t in step.tests_required)
    assert not any("smoke-import" in t for t in step.tests_required)


def test_typescript_module_gets_tsc_not_smoke_import():
    doc = _doc(parts=[_part("module:src/ui", files=["src/ui/app.tsx"],
                            languages=("typescript",))],
               build_order=[["module:src/ui"]])
    plan = recompose(doc)
    step = next(s for s in plan.steps if "module:src/ui" in s.parts)
    assert any("tsc --noEmit" in t for t in step.tests_required)
    assert not any("smoke-import" in t for t in step.tests_required)


def test_java_module_without_build_tool_signal_keeps_generic_wording():
    # No per-module Maven/Gradle signal exists in the decomposition today --
    # inventing one would be an unverifiable claim (PALS's Law). Java stays on
    # the honest generic path until that evidence exists.
    doc = _doc(parts=[_part("module:src/main/java/app", files=["src/main/java/app/App.java"],
                            languages=("java",))],
               build_order=[["module:src/main/java/app"]])
    plan = recompose(doc)
    step = next(s for s in plan.steps if "module:src/main/java/app" in s.parts)
    assert any("smoke-import/compile" in t and "java" in t for t in step.tests_required)


def test_rust_module_without_crate_metadata_keeps_generic_wording():
    doc = _doc(parts=[_part("module:src", files=["src/lib.rs"], languages=("rust",))],
               build_order=[["module:src"]])
    plan = recompose(doc)
    step = next(s for s in plan.steps if "module:src" in s.parts)
    assert any("smoke-import/compile" in t and "rust" in t for t in step.tests_required)
    assert not any("cargo check" in t for t in step.tests_required)


def test_unassigned_files_scales_to_many_generated_artifacts():
    doc = _doc(parts=[
        _part("module:lib", files=["lib/a.py"]),
        *[{
            "id": f"file:gen/{i}.js", "name": f"gen/{i}.js",
            "kind": "generated_artifact", "layer": None,
            "responsibility": "generated", "responsibility_confidence": "certain",
            "evidence": {"files": [f"gen/{i}.js"], "symbols": [], "graph_nodes": [],
                         "graph_edges": [], "signals": [], "llm_summaries": []},
            "dependencies": {"incoming": [], "outgoing": []},
            "classification": {"role": "generated", "role_confidence": "certain",
                               "reusability": "internal", "risk": "low",
                               "risk_reasons": []},
            "metrics": {}, "interface_symbols": [], "notes": [],
            "overall_confidence": "certain",
        } for i in range(25)],
    ])
    plan = recompose(doc)
    assert len(plan.unassigned_files) == 25
    assert all("regenerat" in u["reason"] for u in plan.unassigned_files)
    owned = {f for s in plan.steps for f in list(s.creates) + list(s.modifies)}
    assert not (owned & {u["path"] for u in plan.unassigned_files})


# ── #4b: non-joint units honor decomposer-supplied file_orderings ─────────────
def test_non_joint_unit_honors_file_orderings():
    doc = _doc(
        parts=[_part("module:migrations/versions", files=[
            "migrations/versions/0002_add_col.py",   # deliberately out of order
            "migrations/versions/0001_init.py",
        ])],
        build_order=[["module:migrations/versions"]],
        file_orderings=[{
            "part": "module:migrations/versions",
            "file_order": ["migrations/versions/0001_init.py",
                           "migrations/versions/0002_add_col.py"],
            "note": "topological order from Alembic revision/down_revision markers",
        }],
    )
    plan = recompose(doc)
    step = next(s for s in plan.steps if "module:migrations/versions" in s.parts)
    assert step.creates_ordered is True
    assert step.creates == ["migrations/versions/0001_init.py",
                            "migrations/versions/0002_add_col.py"]
    assert any("outside the import graph" in a for a in step.assumptions)


def test_non_joint_unit_without_ordering_stays_lexicographic():
    doc = _doc(
        parts=[_part("module:lib", files=["b.py", "a.py"])],
        build_order=[["module:lib"]],
    )
    plan = recompose(doc)
    step = next(s for s in plan.steps if "module:lib" in s.parts)
    assert step.creates_ordered is False
    assert step.creates == ["a.py", "b.py"]
