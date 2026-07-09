"""F15/F16 — the X-ray report must derive mechanical caveats from the bundle.

linux.html printed "91,736 import edges" as a bare FACT while the manifest
in the same bundle recorded 407,936 extracted directives (~22% resolution),
and listed 13,537 objective-c files with no anomaly framing. A reader of the
HTML alone inherited the bundle's known distortions as clean facts. The
caveat layer computes disclosures from run_manifest.json itself — no
hardcoded knowledge of any particular repo — and discloses companion inputs
(ABox, decomposition, build plan) that were not wired into the render.

Run from the repo root:  python -m pytest tests/test_report_caveats.py
"""
from __future__ import annotations

from scripts.cbm_report import caveats_html, mechanical_caveats

LINUX_LIKE = {
    "counts": {"files": 94_841, "import_edges": 91_736, "tests_edges": 139},
    "ast_coverage": {
        "n_source_files": 65_340,
        "totals": {
            "imports_extracted": 407_936,
            "files_with_parse_errors": 31_943,
            "files": 65_340,
        },
    },
    "files_by_language": {"c": 50_012, "(none)": 29_012},
    "extensions": {
        "l3_40_concepts_artifact": {
            "n_concepts": 776_716,
            "n_concepts_without_embedding": 7_418,
        },
    },
    # no "degradations" key: bundle predates the disclosure wiring
}

HEALTHY = {
    "counts": {"files": 100, "import_edges": 95, "tests_edges": 12},
    "ast_coverage": {
        "n_source_files": 80,
        "totals": {
            "imports_extracted": 100,
            "files_with_parse_errors": 2,
            "files": 80,
        },
    },
    "files_by_language": {"python": 95, "(none)": 5},
    "extensions": {
        "l3_40_concepts_artifact": {
            "n_concepts": 500,
            "n_concepts_without_embedding": 0,
        },
    },
    "degradations": [],
}


def _ids(caveats):
    return {c["id"] for c in caveats}


def test_linux_like_manifest_produces_the_known_caveats():
    caveats = mechanical_caveats(LINUX_LIKE)
    ids = _ids(caveats)
    assert "import_resolution" in ids
    assert "parse_errors" in ids
    assert "unlanguaged_files" in ids
    assert "concept_embedding_gap" in ids
    assert "degradations_unknown" in ids  # key absent = health unknown
    imp = next(c for c in caveats if c["id"] == "import_resolution")
    assert "22" in imp["text"] and "91,736" in imp["text"] \
        and "407,936" in imp["text"]
    gap = next(c for c in caveats if c["id"] == "concept_embedding_gap")
    assert "7,418" in gap["text"]


def test_healthy_manifest_produces_no_caveats():
    assert mechanical_caveats(HEALTHY) == []


def test_recorded_degradations_become_caveats():
    man = dict(HEALTHY)
    man["degradations"] = [{
        "component": "git_provenance",
        "reason": "shallow_clone_no_history",
        "affected_files": 100,
    }]
    caveats = mechanical_caveats(man)
    assert _ids(caveats) == {"degradation"}
    assert "shallow_clone_no_history" in caveats[0]["text"]


def test_missing_companions_are_disclosed():
    found = {"abox": None, "decomposition": "x.yaml", "buildplan": None}
    caveats = mechanical_caveats(HEALTHY, found=found)
    ids = _ids(caveats)
    assert "companion_missing_abox" in ids
    assert "companion_missing_buildplan" in ids
    assert "companion_missing_decomposition" not in ids


def test_caveats_render_and_escape():
    caveats = [{"id": "x", "severity": "warning",
                "text": "resolution <50% & counted"}]
    html = caveats_html(caveats)
    assert "&lt;50% &amp; counted" in html
    assert caveats_html([]) != ""  # healthy state is stated, not omitted
    assert "no mechanical caveats" in caveats_html([]).lower()
