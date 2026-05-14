"""Inspection port for resolving local or remote repository inputs."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from ...repo_source import ResolvedRepo


class RepoSource(Protocol):
    def resolve_repo_source(
        self, source: str | Path, state: str = "HEAD"
    ) -> AbstractContextManager[ResolvedRepo]: ...
