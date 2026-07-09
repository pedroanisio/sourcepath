"""codebase_mapper.git_plumbing."""
from __future__ import annotations

import subprocess
import threading

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


class BlobReader:
    """Persistent ``git cat-file --batch`` process serving blob reads.

    One subprocess per reader regardless of how many blobs are read —
    at repository scale this replaces one ``git cat-file`` spawn per
    blob (twice per run) with a single long-lived pipe. Results are
    byte-identical to :func:`read_blob`.

    Thread-safe: the batch protocol is strictly request/response, so
    each round trip is serialized under a lock. ``close()`` is
    idempotent; a read after close transparently restarts the process.
    """

    def __init__(self, repo: Path):
        self.repo = repo
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> subprocess.Popen[bytes]:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = subprocess.Popen(
                ["git", "-C", str(self.repo), "cat-file", "--batch"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            )
        return self._proc

    def _read_exact(self, stream, n: int) -> bytes:
        chunks: list[bytes] = []
        remaining = n
        while remaining:
            chunk = stream.read(remaining)
            if not chunk:
                raise RuntimeError(
                    f"git cat-file --batch stream truncated in {self.repo}")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def read(self, blob_sha: str) -> bytes:
        with self._lock:
            p = self._ensure()
            assert p.stdin is not None and p.stdout is not None
            p.stdin.write(blob_sha.encode("ascii") + b"\n")
            p.stdin.flush()
            header = p.stdout.readline()
            if not header:
                raise RuntimeError(
                    f"git cat-file --batch terminated unexpectedly in {self.repo}")
            parts = header.split()
            if len(parts) < 3:
                # "<sha> missing" / "<sha> ambiguous" — the process stays
                # usable for subsequent reads.
                raise KeyError(
                    f"object {blob_sha!r} not found in {self.repo}: "
                    f"{header.decode(errors='replace').strip()}")
            data = self._read_exact(p.stdout, int(parts[2]))
            self._read_exact(p.stdout, 1)  # protocol's trailing newline
            return data

    def close(self) -> None:
        with self._lock:
            p, self._proc = self._proc, None
            if p is None:
                return
            for stream in (p.stdin, p.stdout):
                if stream is not None:
                    stream.close()
            p.wait()

    def __enter__(self) -> "BlobReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def is_shallow_repository(repo: Path) -> bool:
    """True when ``repo`` has truncated (shallow-cloned) history.

    Calls subprocess directly rather than the ``git()`` helper:
    ``list_commit_times`` invokes this probe, and its streaming
    contract (tests/test_perf_git_plumbing.py) forbids ``git()``
    inside that call path. The probe's output is five bytes — the
    buffering concern the contract guards against does not apply.
    """
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-shallow-repository"],
        check=True, capture_output=True, text=True,
    ).stdout
    return out.strip() == "true"


def list_commit_times(repo: Path, commit: str) -> dict[str, int]:
    """Map each path to the Unix timestamp of its most-recent commit.

    Single ``git log`` invocation that walks all ancestors of ``commit``
    newest-first; the first time we see a path is its last-modified time.
    Cost is O(commits + total touched paths). Renames are not followed —
    a renamed file's recorded time is the time of the rename, which is
    the same "last touched at this path" semantics that callers want.

    The log is streamed line-by-line off the pipe rather than buffered
    as one string: on torvalds/linux the full ``--name-only`` history is
    hundreds of MB, and this function's memory must stay bounded by the
    result dict, not by history size.

    Shallow repositories return ``{}``: a depth-1 clone has a parentless
    tip, so ``git log`` reports every path as added by that lone commit
    and every file would receive the tip's author time — a fabricated
    fact (observed as 94,841 identical ``gitCommitTime`` stamps on a
    kernel run). Absent facts beat fabricated facts, so no times are
    reported when the history needed to derive them is not present.
    """
    if is_shallow_repository(repo):
        return {}
    sentinel = "__cbm_commit__"
    proc = subprocess.Popen(
        ["git", "-C", str(repo), "log", commit,
         f"--format={sentinel}%at", "--name-only", "--no-merges"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    last: dict[str, int] = {}
    current_ts: int | None = None
    assert proc.stdout is not None and proc.stderr is not None
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
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
        stderr = proc.stderr.read()
    finally:
        proc.stdout.close()
        proc.stderr.close()
        returncode = proc.wait()
    if returncode != 0:
        raise subprocess.CalledProcessError(
            returncode, ["git", "log", commit], stderr=stderr)
    return last
