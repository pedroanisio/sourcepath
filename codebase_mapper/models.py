"""codebase_mapper.models.

XrefKind / XrefResolution / XrefUnresolvedReason are type-level mirrors of the
runtime tuples in ``codebase_mapper.constants``. They MUST stay in sync; the
contract test ``test_runtime_tuples_match_type_literals`` in
``tests/verify_xrefs.py`` enforces this. When you add a new value to one,
add it to the other in the same commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Literal


XrefKind = Literal["calls", "subclassOf", "overrides", "references"]
XrefResolution = Literal["exact", "heuristic", "ambiguous"]
XrefUnresolvedReason = Literal[
    "module_not_in_repo",
    "symbol_not_exported",
    "ambiguous",
    "dynamic_dispatch",
    "language_unsupported",
]


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

@dataclass(frozen=True)
class SymbolXrefEdge:
    """Symbol-level edge between two L2 chunks (function/class/method).

    `src_chunk_id` and `dst_chunk_id` are L2 chunk_id strings (the same
    identifier the chunks plugin uses to derive `cbmi:chunk/<safe_id>`).
    `kind` and `resolution` are required; `resolver` names the producing
    plugin for provenance.
    """
    src_chunk_id: str
    dst_chunk_id: str
    kind: XrefKind
    resolution: XrefResolution
    resolver: str

@dataclass(frozen=True)
class UnresolvedSymbolRef:
    """A symbol reference the resolver could not bind to a chunk.

    Recorded as data (not as a log line) so coverage is measurable and
    sortable. `raw_target` is whatever the resolver saw at the call site
    (e.g. `"foo.bar"`); `reason` is a small enum.
    """
    src_chunk_id: str
    raw_target: str
    kind: XrefKind
    reason: XrefUnresolvedReason
    resolver: str
