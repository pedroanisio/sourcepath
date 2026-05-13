"""Resolve local and remote repository inputs for CLI entry points."""
from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import tempfile

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


_GITHUB_SHORTHAND_RE = re.compile(
    r"^(?:www\.)?github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s#?]+?)(?:\.git)?/?$"
)
_SCP_LIKE_RE = re.compile(
    r"^(?P<user>[^@\s]+@)?(?P<host>[^:\s]+):(?P<path>[^\\\s]+)$"
)


@dataclass(frozen=True)
class ResolvedRepo:
    path: Path
    name: str
    source: str
    state: str
    cloned: bool = False


def normalize_git_source(source: str) -> str | None:
    """Return a git-cloneable URL when ``source`` looks remote.

    Supported GitHub conveniences:
    - https://github.com/owner/repo(.git)
    - git@github.com:owner/repo.git
    - ssh://git@github.com/owner/repo.git
    - github.com/owner/repo

    ``file://`` URLs are also accepted so tests and air-gapped users can run
    the same code path against local bare repositories.
    """
    value = source.strip()
    if not value:
        return None

    shorthand = _GITHUB_SHORTHAND_RE.match(value)
    if shorthand:
        owner = shorthand.group("owner")
        repo = shorthand.group("repo").removesuffix(".git")
        return f"https://github.com/{owner}/{repo}.git"

    parsed = urlparse(value)
    if parsed.scheme == "file" and parsed.path:
        return value
    if parsed.scheme in {"http", "https", "ssh", "git"} and parsed.netloc:
        return value

    scp_like = _SCP_LIKE_RE.match(value)
    if scp_like and "/" in scp_like.group("path"):
        return value

    return None


def repo_name_from_source(source: str) -> str:
    url = normalize_git_source(source)
    if not url:
        return Path(source).expanduser().resolve().name

    parsed = urlparse(url)
    if parsed.scheme and parsed.path:
        name = Path(parsed.path.rstrip("/")).name
    else:
        scp_like = _SCP_LIKE_RE.match(url)
        name = Path(scp_like.group("path").rstrip("/")).name if scp_like else Path(url).name
    return name.removesuffix(".git") or "repo"


def _run_git(args: list[str], cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, check=True)


def _checkout_state(repo: Path, state: str) -> None:
    candidates = [state]
    if not state.startswith("origin/"):
        candidates.append(f"origin/{state}")
    errors: list[str] = []
    for candidate in candidates:
        result = subprocess.run(
            ["git", "checkout", "--detach", candidate],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        errors.append((result.stderr or result.stdout).strip())
    raise subprocess.CalledProcessError(
        returncode=1,
        cmd=["git", "checkout", "--detach", state],
        stderr="\n".join(e for e in errors if e),
    )


@contextlib.contextmanager
def resolve_repo_source(source: str | Path, state: str = "HEAD"):
    """Yield a local repository path, cloning remote sources into a temp dir."""
    source_str = str(source)
    git_url = normalize_git_source(source_str)
    if not git_url:
        path = Path(source_str).expanduser().resolve()
        yield ResolvedRepo(path=path, name=path.name, source=source_str, state=state, cloned=False)
        return

    tmp = Path(tempfile.mkdtemp(prefix="cbm-repo-"))
    clone_dir = tmp / repo_name_from_source(source_str)
    try:
        _run_git(["clone", "--", git_url, str(clone_dir)])
        if state != "HEAD":
            _checkout_state(clone_dir, state)
        yield ResolvedRepo(
            path=clone_dir.resolve(),
            name=repo_name_from_source(source_str),
            source=source_str,
            state="HEAD",
            cloned=True,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
