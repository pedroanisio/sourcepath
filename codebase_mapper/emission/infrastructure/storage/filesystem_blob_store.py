"""codebase_mapper.blobs."""
from __future__ import annotations

from pathlib import Path

from ....inspection.git_plumbing import read_blob
from ....inspection.models import FileRecord


def emit_blobs(records: list[FileRecord], repo: Path, blob_by_path: dict[str, str],
               blobs_dir: Path) -> int:
    """Write one file per unique content-SHA-256. Returns count written."""
    blobs_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    count = 0
    for r in records:
        if r.content_sha256 in seen:
            continue
        seen.add(r.content_sha256)
        content = read_blob(repo, blob_by_path[r.path])
        out = blobs_dir / r.content_sha256
        out.write_bytes(content)
        count += 1
    return count
