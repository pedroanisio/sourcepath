"""Port for writing content-addressed blobs."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ....inspection.models import FileRecord


class BlobStore(Protocol):
    def emit_blobs(
        self,
        records: list[FileRecord],
        repo: Path,
        blob_by_path: dict[str, str],
        blobs_dir: Path,
    ) -> int: ...
