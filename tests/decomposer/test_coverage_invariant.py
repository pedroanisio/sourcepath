"""RED (round-2 review #5): every repository file must belong to at least one
part — files with no structural home (LICENSE, unknown types) fall through all
part builders today and become silently unowned in the build plan.
"""
from __future__ import annotations

from pathlib import Path

from decomposer.evidence import EvidenceGraph
from decomposer.parts import build_cross_cutting_parts, build_module_graph, build_module_parts


def _ev(file_specs, imports_out=None):
    files = [
        {"path": p, "type": t, "language": lang, "size": 1, "uri": f"urn:{p}"}
        for (p, t, lang) in file_specs
    ]
    imports_out = imports_out or {}
    imports_in: dict[str, list[str]] = {}
    for src, tgts in imports_out.items():
        for t in tgts:
            imports_in.setdefault(t, []).append(src)
    return EvidenceGraph(
        bundle_dir=Path("."), manifest={}, files=files,
        file_by_path={f["path"]: f for f in files},
        imports_out=imports_out, imports_in=imports_in,
        external_imports={}, tests_for_subject={}, subjects_for_test={},
        chunks=[], chunks_by_file={}, xrefs=[],
        concepts={}, per_path_concepts={}, collections={},
        file_summaries={}, schema_purposes={}, phases={},
    )


SPECS = [
    ("pkg/a.py", "source_code", "python"),
    ("LICENSE", "license", None),
    ("pkg/LICENSE", "license", None),
    ("spellcheck.dic", "unknown", None),
    ("notes.md", "documentation", None),
]


def _all_part_files(ev):
    mg = build_module_graph(ev)
    parts = build_module_parts(ev, mg, set()) + build_cross_cutting_parts(ev, mg)
    return {f for p in parts for f in p.evidence.files}, parts


def test_every_file_belongs_to_a_part():
    ev = _ev(SPECS)
    covered, _ = _all_part_files(ev)
    assert {p for p, _, _ in SPECS} <= covered, (
        f"unowned files: {sorted({p for p, _, _ in SPECS} - covered)}")


def test_license_files_get_a_certain_operational_part():
    ev = _ev(SPECS)
    _, parts = _all_part_files(ev)
    lic = next((p for p in parts if "licensing" in p.id), None)
    assert lic is not None
    assert sorted(lic.evidence.files) == ["LICENSE", "pkg/LICENSE"]
    assert lic.overall_confidence.value == "certain"


def test_unknown_files_get_an_explicit_catchall_part():
    ev = _ev(SPECS)
    _, parts = _all_part_files(ev)
    catch = next((p for p in parts if "unclassified" in p.id), None)
    assert catch is not None
    assert "spellcheck.dic" in catch.evidence.files
