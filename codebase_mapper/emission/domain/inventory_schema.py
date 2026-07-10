"""Canonical Pydantic schema for the inventory.ttl graph.

One model per SHACL node shape in ``build_shacl_graph()``
(emission/infrastructure/rdf/rdflib_emitter.py), which remains the
enforcement authority at emit time. This module is the *typed mirror*
of those shapes for Python consumers: same cardinalities, datatypes,
patterns, and class-of-target constraints, expressed as Pydantic
validation. ``tests/test_inventory_schema.py`` holds the two in lockstep
— a predicate shaped without a schema field here (or vice versa) fails
the drift guard.

Layering: this is emission *domain* code and must stay framework-free
(no rdflib — enforced by the import-linter contract
``domain-ports-avoid-framework-imports``). The rdflib bridge that
parses an inventory graph into these models lives in
``emission/infrastructure/rdf/inventory_reader.py``.

Edges are stored by natural key, not IRI: file→file edges hold the
target ``cbm:path``, package edges hold ``cbm:packageName``, and pin
edges hold the ``name@version`` release key.
"""
from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...shared_kernel.vocabulary import CBM_NS

# Explicit members (not built dynamically from the vocabulary tuples)
# so mypy can see them; test_vocabularies_match_constants in
# tests/test_inventory_schema.py holds them equal to TYPE_VOCABULARY /
# PHASE_VOCABULARY in shared_kernel.vocabulary, which stays the source
# of truth.
class FileType(str, Enum):
    source_code = "source_code"
    test_code = "test_code"
    configuration = "configuration"
    documentation = "documentation"
    environment = "environment"
    container = "container"
    build_script = "build_script"
    dependency_manifest = "dependency_manifest"
    lockfile = "lockfile"
    ci_cd = "ci_cd"
    data = "data"
    asset = "asset"
    binary = "binary"
    generated = "generated"
    license = "license"
    unknown = "unknown"


class Phase(str, Enum):
    build = "build"
    compile = "compile"
    runtime = "runtime"
    test = "test"
    ci = "ci"
    deploy = "deploy"
    dev = "dev"

_HEX_SHA256 = r"^[0-9a-f]{64}$"
_HEX = r"^[0-9a-f]+$"


class _Node(BaseModel):
    """Closed models: an unknown key is schema drift, not extra data."""
    model_config = ConfigDict(extra="forbid")


class CommitNode(_Node):
    """Mirror of CommitShape."""
    commit_sha: str = Field(pattern=_HEX)


class ExternalPackageNode(_Node):
    """Mirror of cbm:ExternalPackage (typed in the graph; keyed by name)."""
    package_name: str


class PackageReleaseNode(_Node):
    """Mirror of PackageReleaseShape."""
    package_name: str
    package_version: str
    # sh:class cbm:ExternalPackage — holds the target's package_name.
    release_of: str

    @property
    def key(self) -> str:
        """The natural key pin edges reference: ``name@version``."""
        return f"{self.package_name}@{self.package_version}"


class FileNode(_Node):
    """Mirror of FileShape (targetClass cbm:File)."""
    path: str
    git_blob_sha: str
    content_sha256: str = Field(pattern=_HEX_SHA256)
    size_bytes: int = Field(ge=0)
    type: FileType
    phases: list[Phase] = Field(min_length=1)
    language: str | None = None
    # Canonical JSON literal exactly as emitted (cbm:astSummary).
    ast_summary: str | None = None
    extraction_errors: list[str] = Field(default_factory=list)
    atime: dt.datetime | None = None
    mtime: dt.datetime | None = None
    ctime: dt.datetime | None = None
    git_commit_time: dt.datetime | None = None
    # Edge targets by natural key; InventoryGraph enforces sh:class.
    imports: list[str] = Field(default_factory=list)
    possible_imports: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    imports_external: list[str] = Field(default_factory=list)
    declares_dependencies: list[str] = Field(default_factory=list)
    pins_dependencies: list[str] = Field(default_factory=list)


class InventoryGraph(_Node):
    """Mirror of RepositoryShape plus whole-graph integrity.

    The per-edge ``sh:class`` constraints and TestsSubjectShape only
    make sense with the full node set in hand, so they are enforced
    here rather than on FileNode.
    """
    repository_iri: str
    commit: CommitNode
    files: list[FileNode]
    external_packages: list[ExternalPackageNode] = Field(default_factory=list)
    package_releases: list[PackageReleaseNode] = Field(default_factory=list)

    @model_validator(mode="after")
    def _referential_integrity(self) -> "InventoryGraph":
        paths: set[str] = set()
        for f in self.files:
            if f.path in paths:
                raise ValueError(f"duplicate cbm:File path {f.path!r}")
            paths.add(f.path)
        packages = {p.package_name for p in self.external_packages}
        releases = {r.key for r in self.package_releases}

        for r in self.package_releases:
            if r.release_of not in packages:
                raise ValueError(
                    f"release {r.key!r}: cbm:releaseOf target "
                    f"{r.release_of!r} is not an ExternalPackage node")

        for f in self.files:
            for pred, targets, known, kind in (
                ("imports", f.imports, paths, "File"),
                ("possibleImport", f.possible_imports, paths, "File"),
                ("tests", f.tests, paths, "File"),
                ("importsExternal", f.imports_external, packages,
                 "ExternalPackage"),
                ("declaresDependency", f.declares_dependencies, packages,
                 "ExternalPackage"),
                ("pinsDependency", f.pins_dependencies, releases,
                 "PackageRelease"),
            ):
                for t in targets:
                    if t not in known:
                        raise ValueError(
                            f"{f.path}: cbm:{pred} target {t!r} is not a "
                            f"{kind} node in this graph")
            # TestsSubjectShape: subjects of cbm:tests carry type test_code.
            if f.tests and f.type is not FileType.test_code:
                raise ValueError(
                    f"{f.path}: has cbm:tests edges but cbm:type is "
                    f"{f.type.value!r}, not 'test_code'")
        return self


#: cbm: predicate IRI → FileNode/CommitNode/... field it lands in.
#: Consumed by the infrastructure reader and by the drift guard in
#: tests/test_inventory_schema.py, which keeps this registry equal to
#: the set of sh:path predicates in the live shapes graph.
PREDICATE_FIELDS: dict[str, str] = {
    f"{CBM_NS}path": "path",
    f"{CBM_NS}gitBlobSha": "git_blob_sha",
    f"{CBM_NS}contentSha256": "content_sha256",
    f"{CBM_NS}sizeBytes": "size_bytes",
    f"{CBM_NS}type": "type",
    f"{CBM_NS}hasPhase": "phases",
    f"{CBM_NS}language": "language",
    f"{CBM_NS}astSummary": "ast_summary",
    f"{CBM_NS}extractionError": "extraction_errors",
    f"{CBM_NS}atime": "atime",
    f"{CBM_NS}mtime": "mtime",
    f"{CBM_NS}ctime": "ctime",
    f"{CBM_NS}gitCommitTime": "git_commit_time",
    f"{CBM_NS}imports": "imports",
    f"{CBM_NS}possibleImport": "possible_imports",
    f"{CBM_NS}tests": "tests",
    f"{CBM_NS}importsExternal": "imports_external",
    f"{CBM_NS}declaresDependency": "declares_dependencies",
    f"{CBM_NS}pinsDependency": "pins_dependencies",
    f"{CBM_NS}atCommit": "commit",
    f"{CBM_NS}hasFile": "files",
    f"{CBM_NS}commitSha": "commit_sha",
    f"{CBM_NS}packageName": "package_name",
    f"{CBM_NS}packageVersion": "package_version",
    f"{CBM_NS}releaseOf": "release_of",
}
