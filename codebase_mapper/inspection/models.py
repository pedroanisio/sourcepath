"""Inspection-side dataclasses produced while mapping a repository."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field


@dataclass
class FileRecord:
    path: str
    git_blob_sha: str
    content_sha256: str
    size_bytes: int
    language: str | None
    type_: str
    phases: list[str]
    ast_summary: dict | None = None
    extraction_errors: list[str] = field(default_factory=list)
    # Filesystem times via os.lstat() on the working tree. None when the
    # file isn't materialized on disk (e.g. mapping a non-HEAD commit).
    atime: float | None = None
    mtime: float | None = None
    ctime: float | None = None
    # Unix timestamp of the most-recent commit that touched this path
    # (resolved via `git log --name-only`). Deterministic per commit.
    git_commit_time: int | None = None


@dataclass(frozen=True)
class ImportEdge:
    src_path: str
    dst_path: str


@dataclass(frozen=True)
class PossibleImportEdge:
    """Disclosed multi-candidate include resolution (plan E4, schema v2):
    the hard cbm:imports tier stays 100% precise; each surviving candidate
    of an ambiguous angle include becomes one of these edges."""
    src_path: str
    dst_path: str


@dataclass(frozen=True)
class ImportExternalEdge:
    src_path: str
    package_name: str


@dataclass(frozen=True)
class DeclaresDependencyEdge:
    manifest_path: str
    package_name: str


@dataclass(frozen=True)
class PinsDependencyEdge:
    lockfile_path: str
    package_name: str
    package_version: str


@dataclass(frozen=True)
class TestsEdge:
    test_path: str
    subject_path: str
