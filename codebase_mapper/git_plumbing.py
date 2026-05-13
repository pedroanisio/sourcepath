"""codebase_mapper.git_plumbing."""
from __future__ import annotations

import subprocess

from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout

def git_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True,
    ).stdout

def resolve_commit(repo: Path, state: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{state}^{{commit}}").strip()

def list_tree(repo: Path, commit: str) -> list[tuple[str, str, str]]:
    """Return (path, blob_sha, mode) for each blob entry. Mode is the
    six-digit git mode string: '100644' regular, '100755' executable,
    '120000' symlink, '160000' gitlink (skipped above)."""
    out = git(repo, "ls-tree", "-r", "--full-tree", "-z", commit)
    entries: list[tuple[str, str, str]] = []
    for entry in out.split("\x00"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, otype, sha = meta.split(" ")
        if otype != "blob":
            continue
        entries.append((path, sha, mode))
    entries.sort(key=lambda x: x[0])
    return entries

def read_blob(repo: Path, blob_sha: str) -> bytes:
    return git_bytes(repo, "cat-file", "blob", blob_sha)


def list_commit_times(repo: Path, commit: str) -> dict[str, int]:
    """Map each path to the Unix timestamp of its most-recent commit.

    Single ``git log`` invocation that walks all ancestors of ``commit``
    newest-first; the first time we see a path is its last-modified time.
    Cost is O(commits + total touched paths). Renames are not followed —
    a renamed file's recorded time is the time of the rename, which is
    the same "last touched at this path" semantics that callers want.
    """
    sentinel = "__cbm_commit__"
    out = git(
        repo, "log", commit,
        f"--format={sentinel}%at", "--name-only", "--no-merges",
    )
    last: dict[str, int] = {}
    current_ts: int | None = None
    for line in out.splitlines():
        if not line:
            continue
        if line.startswith(sentinel):
            try:
                current_ts = int(line[len(sentinel):])
            except ValueError:
                current_ts = None
            continue
        if current_ts is None:
            continue
        if line not in last:
            last[line] = current_ts
    return last
