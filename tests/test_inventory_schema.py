"""TDD spec — canonical Pydantic schema for the inventory.ttl graph.

Four contracts:

  1. ``inventory_schema`` field constraints mirror the SHACL FileShape /
     CommitShape / PackageReleaseShape declarations in
     ``build_shacl_graph()``: seeded violations that SHACL would reject
     must raise ``ValidationError``.
  2. Referential integrity mirrors the shapes' ``sh:class`` constraints:
     every edge target (imports, possibleImport, tests, importsExternal,
     declaresDependency, pinsDependency, releaseOf) must resolve to a
     node present in the model.
  3. Roundtrip — ``build_inventory_graph()`` → turtle → parse →
     ``read_inventory()`` produces a validated ``InventoryGraph`` whose
     fields match the source records exactly.
  4. Drift guard — every ``sh:path`` predicate declared by the live
     shapes graph is consumed by the schema's ``PREDICATE_FIELDS``
     registry, and vice versa. Adding a shaped predicate without a
     schema field (or the reverse) fails this spec.

Run: python -m pytest tests/test_inventory_schema.py
"""
from __future__ import annotations

import datetime as dt
import json

import pytest
from pydantic import ValidationError
from rdflib import Graph, URIRef

from codebase_mapper.emission.domain.inventory_schema import (
    CommitNode,
    ExternalPackageNode,
    FileNode,
    FileType,
    InventoryGraph,
    PackageReleaseNode,
    Phase,
    PREDICATE_FIELDS,
)
from codebase_mapper.emission.infrastructure.rdf.inventory_reader import (
    read_inventory,
)
from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import (
    build_inventory_graph,
    build_shacl_graph,
)
# Module import (not `from ... import TestsEdge`) keeps pytest from
# trying to collect the Test*-named dataclass as a test class.
from codebase_mapper.inspection import models
from codebase_mapper.shared_kernel.constants import (
    CBM_NS,
    PHASE_VOCABULARY,
    TYPE_VOCABULARY,
)

SHA64 = "a" * 64
REPO_IRI = "https://codebase-mapper.example.org/cbm/instance#repo/fixture"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"


def file_node(**over) -> FileNode:
    base = dict(
        path="src/app.py",
        git_blob_sha="b" * 40,
        content_sha256=SHA64,
        size_bytes=10,
        type="source_code",
        phases=["runtime"],
    )
    base.update(over)
    return FileNode(**base)


def graph_kwargs(**over) -> dict:
    base = dict(
        repository_iri=REPO_IRI,
        commit=CommitNode(commit_sha=COMMIT_SHA),
        files=[file_node()],
        external_packages=[],
        package_releases=[],
    )
    base.update(over)
    return base


# --- Contract 1: field constraints mirror the SHACL shapes ---------------

def test_valid_nodes_pass() -> None:
    inv = InventoryGraph(**graph_kwargs())
    assert inv.files[0].type is FileType.source_code
    assert inv.files[0].phases == [Phase.runtime]


def test_vocabularies_match_constants() -> None:
    assert tuple(m.value for m in FileType) == TYPE_VOCABULARY
    assert tuple(m.value for m in Phase) == PHASE_VOCABULARY


@pytest.mark.parametrize("over", [
    {"content_sha256": "zz" * 32},          # pattern ^[0-9a-f]{64}$
    {"content_sha256": "ab" * 8},           # too short
    {"size_bytes": -1},                     # minInclusive 0
    {"type": "not_a_type"},                 # sh:in TYPE_VOCABULARY
    {"phases": []},                         # minCount 1
    {"phases": ["not_a_phase"]},            # sh:in PHASE_VOCABULARY
    {"language": 3},                        # datatype xsd:string
])
def test_file_violations_rejected(over: dict) -> None:
    with pytest.raises(ValidationError):
        file_node(**over)


def test_commit_sha_pattern_rejected() -> None:
    with pytest.raises(ValidationError):
        CommitNode(commit_sha="NOT-HEX")


def test_unknown_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        file_node(unknown_predicate="x")


# --- Contract 2: referential integrity mirrors sh:class ------------------

@pytest.mark.parametrize("over", [
    {"files": [file_node(imports=["missing.py"])]},
    {"files": [file_node(possible_imports=["missing.py"])]},
    {"files": [file_node(type="test_code", phases=["test"],
                         tests=["missing.py"])]},
    {"files": [file_node(imports_external=["ghost-pkg"])]},
    {"files": [file_node(declares_dependencies=["ghost-pkg"])]},
    {"files": [file_node(pins_dependencies=["ghost@1.0"])]},
])
def test_dangling_edges_rejected(over: dict) -> None:
    with pytest.raises(ValidationError):
        InventoryGraph(**graph_kwargs(**over))


def test_release_of_unknown_package_rejected() -> None:
    rel = PackageReleaseNode(
        package_name="rich", package_version="13.0.0", release_of="rich")
    with pytest.raises(ValidationError):
        InventoryGraph(**graph_kwargs(package_releases=[rel]))


def test_tests_subject_must_be_test_code() -> None:
    # TestsSubjectShape: any subject of cbm:tests carries type test_code.
    lib = file_node(path="lib.py")
    tester = file_node(path="test_lib.py", tests=["lib.py"])  # source_code
    with pytest.raises(ValidationError):
        InventoryGraph(**graph_kwargs(files=[lib, tester]))


def test_duplicate_paths_rejected() -> None:
    with pytest.raises(ValidationError):
        InventoryGraph(**graph_kwargs(files=[file_node(), file_node()]))


def test_valid_edges_pass() -> None:
    lib = file_node(path="lib.py")
    app = file_node(path="app.py", imports=["lib.py"],
                    imports_external=["rich"], possible_imports=["lib.py"])
    tester = file_node(path="test_app.py", type="test_code",
                       phases=["test"], tests=["app.py"])
    manifest = file_node(path="pyproject.toml", type="dependency_manifest",
                         phases=["build", "runtime"],
                         declares_dependencies=["rich"])
    lock = file_node(path="uv.lock", type="lockfile", phases=["build"],
                     pins_dependencies=["rich@13.0.0"])
    inv = InventoryGraph(**graph_kwargs(
        files=[lib, app, tester, manifest, lock],
        external_packages=[ExternalPackageNode(package_name="rich")],
        package_releases=[PackageReleaseNode(
            package_name="rich", package_version="13.0.0",
            release_of="rich")],
    ))
    assert inv.package_releases[0].key == "rich@13.0.0"


# --- Contract 3: roundtrip through the real emitter -----------------------

def _records() -> list[models.FileRecord]:
    return [
        models.FileRecord(
            path="app.py", git_blob_sha="c" * 40, content_sha256="d" * 64,
            size_bytes=120, language="python", type_="source_code",
            phases=["runtime"], ast_summary={"defs": ["main"]},
            atime=1700000000.25, mtime=1700000001.0, ctime=1700000002.0,
            git_commit_time=1700000003),
        models.FileRecord(
            path="lib.py", git_blob_sha="e" * 40, content_sha256="f" * 64,
            size_bytes=40, language="python", type_="source_code",
            phases=["runtime"],
            extraction_errors=["parser hiccup at byte 3"]),
        models.FileRecord(
            path="test_app.py", git_blob_sha="1" * 40,
            content_sha256="2" * 64, size_bytes=60, language="python",
            type_="test_code", phases=["test"]),
        models.FileRecord(
            path="pyproject.toml", git_blob_sha="3" * 40,
            content_sha256="4" * 64, size_bytes=80, language=None,
            type_="dependency_manifest", phases=["build", "runtime"]),
        models.FileRecord(
            path="uv.lock", git_blob_sha="5" * 40, content_sha256="6" * 64,
            size_bytes=90, language=None, type_="lockfile",
            phases=["build"]),
    ]


def _build_graph() -> Graph:
    return build_inventory_graph(
        URIRef(REPO_IRI), COMMIT_SHA, _records(),
        import_edges=[models.ImportEdge("app.py", "lib.py")],
        import_ext_edges=[models.ImportExternalEdge("app.py", "rich")],
        dep_edges=[models.DeclaresDependencyEdge("pyproject.toml", "rich")],
        pin_edges=[models.PinsDependencyEdge("uv.lock", "rich", "13.0.0")],
        tests_edges=[models.TestsEdge("test_app.py", "app.py")],
        possible_import_edges=[models.PossibleImportEdge("app.py", "lib.py")],
    )


def test_roundtrip_matches_records() -> None:
    ttl = _build_graph().serialize(format="turtle")
    parsed = Graph()
    parsed.parse(data=ttl, format="turtle")

    inv = read_inventory(parsed)

    assert inv.repository_iri == REPO_IRI
    assert inv.commit.commit_sha == COMMIT_SHA
    by_path = {f.path: f for f in inv.files}
    assert set(by_path) == {r.path for r in _records()}

    app = by_path["app.py"]
    assert app.content_sha256 == "d" * 64
    assert app.size_bytes == 120
    assert app.language == "python"
    assert app.type is FileType.source_code
    assert app.imports == ["lib.py"]
    assert app.possible_imports == ["lib.py"]
    assert app.imports_external == ["rich"]
    assert json.loads(app.ast_summary) == {"defs": ["main"]}
    assert app.atime == dt.datetime(
        2023, 11, 14, 22, 13, 20, 250000, tzinfo=dt.timezone.utc)
    assert app.git_commit_time == dt.datetime(
        2023, 11, 14, 22, 13, 23, tzinfo=dt.timezone.utc)

    assert by_path["lib.py"].extraction_errors == [
        "parser hiccup at byte 3"]
    assert by_path["test_app.py"].tests == ["app.py"]
    assert by_path["pyproject.toml"].declares_dependencies == ["rich"]
    assert by_path["uv.lock"].pins_dependencies == ["rich@13.0.0"]
    assert [p.package_name for p in inv.external_packages] == ["rich"]
    assert inv.package_releases[0].key == "rich@13.0.0"


# --- Contract 4: drift guard against the live shapes graph ---------------

SH_PATH = URIRef("http://www.w3.org/ns/shacl#path")


def test_every_shaped_cbm_predicate_has_a_schema_field() -> None:
    shapes = build_shacl_graph()
    shaped = {
        str(o) for _s, _p, o in shapes.triples((None, SH_PATH, None))
        if str(o).startswith(CBM_NS)
    }
    # targetSubjectsOf predicates are enforced by a validator, list them too.
    shaped.add(f"{CBM_NS}tests")
    missing = shaped - set(PREDICATE_FIELDS)
    stale = set(PREDICATE_FIELDS) - shaped
    assert not missing, f"shaped but not in schema: {sorted(missing)}"
    assert not stale, f"in schema but no longer shaped: {sorted(stale)}"
