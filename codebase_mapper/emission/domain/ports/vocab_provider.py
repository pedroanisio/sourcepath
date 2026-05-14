"""Port for loading the bundled controlled vocabulary."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class VocabularyProvider(Protocol):
    def builtin_vocabulary_path(self) -> Path: ...
    def builtin_vocabulary(self): ...
    def load_vocabulary(self, path: Path): ...
