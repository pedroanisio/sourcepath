"""codebase_mapper.blobs."""
from __future__ import annotations

from pathlib import Path

from ....inspection.git_plumbing import BlobReader
from ....inspection.models import FileRecord


def emit_blobs(records: list[FileRecord], repo: Path, blob_by_path: dict[str, str],
               blobs_dir: Path) -> int:
    """Write one file per unique content-SHA-256. Returns count written.

    All reads ride one persistent ``git cat-file --batch`` process
    (BlobReader) instead of spawning one subprocess per unique blob.
    """
    blobs_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    count = 0
    with BlobReader(repo) as reader:
        for r in records:
            if r.content_sha256 in seen:
                continue
            seen.add(r.content_sha256)
            content = reader.read(blob_by_path[r.path])
            out = blobs_dir / r.content_sha256
            out.write_bytes(content)
            count += 1
    return count
