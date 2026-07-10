"""E8 (error-free-mapping plan) — fast structural gate, pyshacl-equivalent.

pySHACL costs ~2 h at 67 M triples. The fast engine interprets exactly the
SHACL subset our bundled shapes use (targetClass / targetSubjectsOf,
minCount, maxCount, datatype, class, in, hasValue) and must agree with
pyshacl on every fixture — healthy AND seeded-violation — or this suite
fails. An unsupported SHACL feature in the shapes raises instead of
guessing, so shape evolution can never silently outrun the engine.

Run from the repo root:  python -m pytest tests/test_fast_shacl.py
"""
from __future__ import annotations

import subprocess

import pytest
from rdflib import Literal, URIRef

from codebase_mapper.shared_kernel.constants import CBM, CBMI_NS
from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import (
    build_shacl_graph,
)
from codebase_mapper.verification.fast_shacl import (
    UnsupportedShaclFeature,
    validate_fast,
)

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "1111111111 +0000",
    "GIT_COMMITTER_DATE": "1111111111 +0000",
    "PATH": "/usr/bin:/bin",
}


@pytest.fixture(scope="module")
def healthy_graph(tmp_path_factory):
    from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import (
        build_inventory_graph,
    )
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries

    root = tmp_path_factory.mktemp("fastshacl")
    repo = root / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("import os\n")
    (repo / "pkg" / "tests" / "test_a.py").parent.mkdir(exist_ok=True)
    (repo / "pkg" / "tests" / "test_a.py").write_text("from pkg.a import *\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    mapped = map_codebase(repo, "HEAD")
    return build_inventory_graph(
        repo_iri=URIRef(f"{CBMI_NS}repo/fixture"), commit_sha=mapped["commit"],
        records=mapped["records"], import_edges=mapped["import_edges"],
        import_ext_edges=mapped["import_ext_edges"],
        dep_edges=mapped["dep_edges"], pin_edges=mapped["pin_edges"],
        tests_edges=mapped["tests_edges"],
        possible_import_edges=mapped["possible_import_edges"],
    )


def _pyshacl(data, shapes) -> bool:
    from pyshacl import validate
    conforms, _, _ = validate(data_graph=data, shacl_graph=shapes,
                              inference="none")
    return bool(conforms)


def _seed_violations(graph):
    """(mutator, description) pairs — each breaks one shape constraint."""
    f = URIRef(f"{CBMI_NS}file/pkg%2Fa.py")

    def missing_path(g):
        g.remove((f, CBM.path, None))

    def double_path(g):
        g.add((f, CBM.path, Literal("second/path.py")))

    def bogus_type(g):
        g.remove((f, CBM.type, None))
        g.add((f, CBM.type, URIRef("https://example.org/not-a-type")))

    def no_phase(g):
        g.remove((f, CBM.hasPhase, None))

    def import_to_nonfile(g):
        g.add((f, CBM.imports, URIRef("https://example.org/ghost")))

    def tests_nontest_subject(g):
        # pkg/a.py is source_code, not test_code → TestsSubjectShape breaks
        g.add((f, CBM.tests, f))

    return [
        (missing_path, "path minCount"),
        (double_path, "path maxCount"),
        (bogus_type, "type sh:in"),
        (no_phase, "phase minCount"),
        (import_to_nonfile, "imports sh:class"),
        (tests_nontest_subject, "tests hasValue"),
    ]


def test_healthy_graph_conforms_and_agrees(healthy_graph):
    shapes = build_shacl_graph()
    fast_conforms, violations = validate_fast(healthy_graph, shapes)
    assert fast_conforms, violations
    assert _pyshacl(healthy_graph, shapes) is True


@pytest.mark.parametrize("idx", range(6))
def test_seeded_violations_agree_with_pyshacl(healthy_graph, idx):
    import copy
    shapes = build_shacl_graph()
    mutator, desc = _seed_violations(healthy_graph)[idx]
    broken = copy.deepcopy(healthy_graph)
    mutator(broken)
    fast_conforms, violations = validate_fast(broken, shapes)
    ref = _pyshacl(broken, shapes)
    assert fast_conforms == ref, (desc, violations)
    assert fast_conforms is False, f"seed did not break anything: {desc}"
    assert violations, "a violation must name what failed"


def test_min_length_agrees_with_pyshacl():
    # sh:minLength is used by the L4 plugin's contributed shapes; the two
    # engines must agree on it or a with-L4 emit silently falls back to
    # pyshacl and the run manifests diverge (verify_llm_enrich check 2).
    from rdflib import Graph, RDF
    from rdflib.namespace import SH
    shapes = Graph()
    shape = URIRef("https://example.org/S")
    shapes.add((shape, RDF.type, SH.NodeShape))
    shapes.add((shape, SH.targetClass, CBM.File))
    prop = URIRef("https://example.org/p")
    shapes.add((shape, SH.property, prop))
    shapes.add((prop, SH.path, CBM.path))
    shapes.add((prop, SH.minLength, Literal(1)))

    for path_value, expect in ((Literal("pkg/a.py"), True),
                               (Literal(""), False)):
        data = Graph()
        f = URIRef(f"{CBMI_NS}file/pkg%2Fa.py")
        data.add((f, RDF.type, CBM.File))
        data.add((f, CBM.path, path_value))
        fast_conforms, violations = validate_fast(data, shapes)
        assert fast_conforms == _pyshacl(data, shapes)
        assert fast_conforms is expect, violations


def test_unsupported_shacl_feature_raises():
    from rdflib import Graph, RDF
    from rdflib.namespace import SH
    shapes = Graph()
    shape = URIRef("https://example.org/S")
    shapes.add((shape, RDF.type, SH.NodeShape))
    shapes.add((shape, SH.targetClass, CBM.File))
    prop = URIRef("https://example.org/p")
    shapes.add((shape, SH.property, prop))
    shapes.add((prop, SH.path, CBM.path))
    shapes.add((prop, SH.maxLength, Literal(3)))  # not in our subset
    with pytest.raises(UnsupportedShaclFeature):
        validate_fast(Graph(), shapes)


def test_emit_uses_fast_engine_and_discloses_it(tmp_path):
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    mapped = map_codebase(repo, "HEAD")
    manifest = emit("fixture", mapped, tmp_path / "b", emit_blobs_flag=False)
    sc = manifest["shacl_self_check"]
    assert sc["conforms"] is True
    assert sc["engine"] == "fast-structural"


def test_emit_pyshacl_engine_via_env(tmp_path, monkeypatch):
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries

    monkeypatch.setenv("CBM_SHACL_ENGINE", "pyshacl")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    mapped = map_codebase(repo, "HEAD")
    manifest = emit("fixture", mapped, tmp_path / "b", emit_blobs_flag=False)
    sc = manifest["shacl_self_check"]
    assert sc["conforms"] is True
    assert sc["engine"] == "pyshacl"
