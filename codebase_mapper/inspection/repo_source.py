"""Resolve local and remote repository inputs for CLI entry points."""
from __future__ import annotations

import contextlib
import os
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


def _resolve_work_root(work_dir: str | Path | None) -> Path | None:
    """Pick the directory that will host the temporary clone.

    Precedence: explicit ``work_dir`` argument > ``CBM_WORK_DIR`` env var >
    ``None`` (let :func:`tempfile.mkdtemp` fall back to ``$TMPDIR`` / ``/tmp``).

    Cloning large repositories into the default ``/tmp`` is a footgun on hosts
    where ``/tmp`` is a small ``tmpfs`` — the clone can exhaust RAM-backed
    storage while a multi-terabyte data volume sits idle. Callers should point
    this at the same filesystem as their output directory.
    """
    candidate: Path | None = None
    if work_dir is not None:
        candidate = Path(work_dir).expanduser()
    else:
        env = os.environ.get("CBM_WORK_DIR")
        if env:
            candidate = Path(env).expanduser()
    if candidate is None:
        return None
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _unshallow_enabled(unshallow: bool | None) -> bool:
    """Explicit argument wins; otherwise ``CBM_UNSHALLOW`` opts OUT.

    E5 (docs/plan/error-free-mapping.md): correct provenance is the
    default — an unset variable attempts the blob-free history deepen;
    0/false/no forces the shallow clone. Omission remains only as the
    disclosed fallback when the deepen fetch fails (``_try_unshallow``).
    """
    if unshallow is not None:
        return unshallow
    return os.environ.get("CBM_UNSHALLOW", "").strip().lower() not in {"0", "false", "no"}


def _try_unshallow(clone_dir: Path) -> None:
    """Best-effort: deepen a shallow clone to full commit history, blob-free.

    ``--filter=blob:none`` fetches all commits and trees but no historical
    blobs, so per-file commit-time provenance becomes derivable while the
    transfer stays a fraction of a full clone; HEAD's blobs already arrived
    with the initial ``--depth 1`` fetch, which is what the working-tree
    plumbing (``git cat-file``) reads. Failure is tolerated — servers may
    refuse filters or ``--unshallow`` (e.g. the clone already holds full
    history) — and the pipeline then omits ``git_commit_time`` and records
    the ``git_provenance`` degradation instead.
    """
    try:
        _run_git(["fetch", "--unshallow", "--filter=blob:none", "origin"], cwd=clone_dir)
    except subprocess.CalledProcessError:
        pass


def _clone(git_url: str, clone_dir: Path, state: str, *, unshallow: bool = False) -> str:
    """Clone ``git_url`` at ``state`` into ``clone_dir``; return the checked-out state.

    Remotes are fetched ``--depth 1`` by default. This is the decisive
    difference for very large repositories (e.g. the Linux kernel is ~11.7M
    objects at full history but a single-commit working tree is a small
    fraction of that). The trade-off: a shallow clone has no commit history,
    so per-file ``git_commit_time`` provenance cannot be derived from it —
    the pipeline omits that fact and records a ``git_provenance``
    degradation (fabricating times from the lone tip commit is worse than
    omitting them). Pass ``unshallow=True`` (or set ``CBM_UNSHALLOW=1``) to
    attempt a blob-free history deepen after the shallow clone.

    - ``state == "HEAD"``: shallow-clone the remote's default branch tip.
    - a branch or tag name: shallow-clone that ref directly (``--branch`` names
      both), leaving its tip checked out.
    - a commit SHA (which ``--branch`` cannot name): fall back to a full clone
      plus an explicit checkout, since most servers refuse to serve an arbitrary
      SHA to a shallow fetch.

    In every case the returned working tree is at the requested state and HEAD is
    detached there, so the effective state reported to callers is ``"HEAD"``.
    """
    if state == "HEAD":
        _run_git(["clone", "--depth", "1", "--single-branch", "--", git_url, str(clone_dir)])
        if unshallow:
            _try_unshallow(clone_dir)
        return "HEAD"
    try:
        _run_git(
            ["clone", "--depth", "1", "--single-branch", "--branch", state, "--", git_url, str(clone_dir)]
        )
        if unshallow:
            _try_unshallow(clone_dir)
        return "HEAD"
    except subprocess.CalledProcessError:
        shutil.rmtree(clone_dir, ignore_errors=True)
    _run_git(["clone", "--", git_url, str(clone_dir)])
    _checkout_state(clone_dir, state)
    return "HEAD"


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
def resolve_repo_source(
    source: str | Path,
    state: str = "HEAD",
    *,
    work_dir: str | Path | None = None,
    unshallow: bool | None = None,
):
    """Yield a local repository path, cloning remote sources into a temp dir.

    ``work_dir`` (or the ``CBM_WORK_DIR`` env var) selects the filesystem that
    hosts the temporary clone; when both are unset the system temp dir is used.
    Point it at the same volume as your output directory to avoid exhausting a
    small ``/tmp`` ``tmpfs`` on large clones.

    ``unshallow`` (or the ``CBM_UNSHALLOW`` env var; default off) opts into a
    best-effort ``git fetch --unshallow --filter=blob:none`` after the default
    ``--depth 1`` clone, recovering commit history — and with it per-file
    ``git_commit_time`` provenance — without downloading historical blobs.
    """
    source_str = str(source)
    git_url = normalize_git_source(source_str)
    if not git_url:
        path = Path(source_str).expanduser().resolve()
        yield ResolvedRepo(path=path, name=path.name, source=source_str, state=state, cloned=False)
        return

    root = _resolve_work_root(work_dir)
    tmp = Path(tempfile.mkdtemp(prefix="cbm-repo-", dir=str(root) if root else None))
    clone_dir = tmp / repo_name_from_source(source_str)
    try:
        try:
            effective_state = _clone(
                git_url, clone_dir, state, unshallow=_unshallow_enabled(unshallow),
            )
        except subprocess.CalledProcessError as exc:
            free_gib = shutil.disk_usage(tmp).free / 1024**3
            raise RuntimeError(
                f"git clone of {git_url!r} into {tmp} failed "
                f"({free_gib:.2f} GiB free on that filesystem). If this is "
                f"'No space left on device', clone onto a larger volume by "
                f"setting CBM_WORK_DIR=/path/with/space (or passing "
                f"work_dir=...)."
            ) from exc
        yield ResolvedRepo(
            path=clone_dir.resolve(),
            name=repo_name_from_source(source_str),
            source=source_str,
            state=effective_state,
            cloned=True,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
