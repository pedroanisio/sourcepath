"""RED (round-2 review): recomposer-side fixes.

* #4c phase completeness: every canonical phase either has steps or an
  explicit ``skipped_phases`` entry — no silent gaps (tokio's plan omitted
  phases 4 and 6 with no explanation).
* #5 ownership reconciliation: every file carried by the decomposition is
  owned by exactly one step, or listed in ``unassigned_files`` with a reason.
* #4b validation wording: Rust modules stamped with a crate get
  ``cargo check -p <crate>`` instead of "smoke-import".
"""
from __future__ import annotations

from recomposer import recompose
from recomposer.model import PHASES, PHASE_TITLE


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


# ── #4c: phase completeness ───────────────────────────────────────────────────
def test_every_phase_has_steps_or_skip_entry():
    plan = recompose(_doc())
    present = {s.phase for s in plan.steps}
    skipped_titles = {e["phase"] for e in plan.skipped_phases}
    for n, _key, title in PHASES:
        assert n in present or title in skipped_titles, (
            f"phase {n} ({title}) is silently absent")


def test_skip_entries_are_deterministically_ordered():
    plan = recompose(_doc())
    titles = [e["phase"] for e in plan.skipped_phases]
    order = {t: n for n, _k, t in PHASES}
    assert titles == sorted(titles, key=lambda t: order.get(t, 99))


# ── #5: ownership reconciliation ──────────────────────────────────────────────
def test_unscheduled_part_files_are_reported_not_dropped():
    doc = _doc(parts=[
        _part("module:lib", files=["lib/a.py"]),
        {  # a promoted generated artifact in a dir with no code module
            "id": "file:gen/bundle.js", "name": "gen/bundle.js",
            "kind": "generated_artifact", "layer": None,
            "responsibility": "generated", "responsibility_confidence": "certain",
            "evidence": {"files": ["gen/bundle.js"], "symbols": [],
                         "graph_nodes": [], "graph_edges": [], "signals": [],
                         "llm_summaries": []},
            "dependencies": {"incoming": [], "outgoing": []},
            "classification": {"role": "generated", "role_confidence": "certain",
                               "reusability": "internal", "risk": "low",
                               "risk_reasons": []},
            "metrics": {}, "interface_symbols": [], "notes": [],
            "overall_confidence": "certain",
        },
    ])
    plan = recompose(doc)
    owned = {f for s in plan.steps for f in list(s.creates) + list(s.modifies)}
    assert "gen/bundle.js" not in owned
    ua = {u["path"]: u for u in plan.unassigned_files}
    assert "gen/bundle.js" in ua
    assert "regenerat" in ua["gen/bundle.js"]["reason"]   # regenerate, not author


def test_full_reconciliation_no_silent_leftovers():
    plan = recompose(_doc())
    universe = {"lib/a.py"}
    owned = {f for s in plan.steps for f in list(s.creates) + list(s.modifies)
             if not f.endswith("/")}
    unassigned = {u["path"] for u in plan.unassigned_files}
    assert universe <= owned | unassigned
    assert not (owned & unassigned), "a file cannot be both owned and unassigned"


# ── #4b: cargo check wording ──────────────────────────────────────────────────
def test_rust_module_with_crate_gets_cargo_check():
    doc = _doc(parts=[_part(
        "module:core/src", files=["core/src/lib.rs"],
        languages=("rust",), metrics_extra={"crate": "core"},
    )], build_order=[["module:core/src"]])
    plan = recompose(doc)
    step = next(s for s in plan.steps if "module:core/src" in s.parts)
    assert any("cargo check -p core" in t for t in step.tests_required)
    assert not any("smoke-import" in t for t in step.tests_required)
