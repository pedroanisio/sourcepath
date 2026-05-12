"""codebase_mapper.models."""
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

@dataclass(frozen=True)
class ImportEdge:
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
