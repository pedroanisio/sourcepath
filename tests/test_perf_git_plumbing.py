"""Performance features F1/F2 — batched blob reads and streamed git log.

F1 — ``BlobReader``: at Linux-kernel scale the pipeline spawned one
``git cat-file`` subprocess per blob, twice per run (classify pass +
enricher re-read). ``BlobReader`` holds one persistent
``git cat-file --batch`` process and answers every read through it.

F2 — ``list_commit_times``: previously buffered the entire
``git log --name-only`` history as a single Python string (hundreds of
MB on torvalds/linux). It must stream the log incrementally instead —
same results, bounded memory.

Both features preserve exact observable behavior; these tests pin the
equivalence and the no-per-call-subprocess / no-full-buffer properties.

Run from the repo root:  python -m pytest tests/test_perf_git_plumbing.py
"""
from __future__ import annotations

import subprocess

import pytest

from codebase_mapper.inspection import git_plumbing as gp


# ---------------------------------------------------------------------------
# fixture: a tiny deterministic git repo
# ---------------------------------------------------------------------------

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(repo, *args, ts=None):
    env = dict(_ENV)
    if ts is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{ts} +0000"
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, env={**env, "PATH": "/usr/bin:/bin"})


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    (r / "a.py").write_text("print('a')\n")
    (r / "b.bin").write_bytes(bytes(range(256)))
    (r / "sub").mkdir()
    (r / "sub" / "c.txt").write_text("c\n" * 1000)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "one", ts=1111111111)
    (r / "a.py").write_text("print('a2')\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "two", ts=1222222222)
    return r


def _tree(repo):
    commit = gp.resolve_commit(repo, "HEAD")
    return commit, gp.list_tree(repo, commit)


# ---------------------------------------------------------------------------
# F1 — BlobReader
# ---------------------------------------------------------------------------


def test_blob_reader_matches_per_call_reads(repo):
    _, entries = _tree(repo)
    assert len(entries) == 3
    with gp.BlobReader(repo) as reader:
        for path, sha, _mode in entries:
            assert reader.read(sha) == gp.read_blob(repo, sha), path


def test_blob_reader_uses_one_subprocess_for_many_reads(repo, monkeypatch):
    _, entries = _tree(repo)
    spawned = []
    real_popen = subprocess.Popen

    def counting_popen(*args, **kwargs):
        spawned.append(args[0] if args else kwargs.get("args"))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", counting_popen)
    with gp.BlobReader(repo) as reader:
        for _ in range(3):  # repeated sweeps — still one process
            for _path, sha, _mode in entries:
                reader.read(sha)
    assert len(spawned) == 1


def test_blob_reader_missing_sha_raises(repo):
    with gp.BlobReader(repo) as reader:
        with pytest.raises(KeyError):
            reader.read("0" * 40)
        # the reader must survive a miss and keep serving hits
        _, entries = _tree(repo)
        assert reader.read(entries[0][1]) == gp.read_blob(repo, entries[0][1])


def test_blob_reader_close_is_idempotent(repo):
    reader = gp.BlobReader(repo)
    _, entries = _tree(repo)
    reader.read(entries[0][1])
    reader.close()
    reader.close()  # no-op, no raise
    # closed reader restarts transparently on next read
    assert reader.read(entries[0][1]) == gp.read_blob(repo, entries[0][1])


def test_blob_reader_is_thread_safe(repo):
    from concurrent.futures import ThreadPoolExecutor

    _, entries = _tree(repo)
    expected = {sha: gp.read_blob(repo, sha) for _p, sha, _m in entries}
    shas = [sha for _p, sha, _m in entries] * 20
    with gp.BlobReader(repo) as reader:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda s: (s, reader.read(s)), shas))
    for sha, data in results:
        assert data == expected[sha]


# ---------------------------------------------------------------------------
# F2 — streamed list_commit_times
# ---------------------------------------------------------------------------


def test_list_commit_times_last_touch_semantics(repo):
    commit = gp.resolve_commit(repo, "HEAD")
    times = gp.list_commit_times(repo, commit)
    # a.py was touched by the second commit; the others only by the first
    assert times["a.py"] == 1222222222
    assert times["b.bin"] == 1111111111
    assert times["sub/c.txt"] == 1111111111


def test_list_commit_times_streams_instead_of_buffering(repo, monkeypatch):
    """The buffered implementation went through ``gp.git`` (one big
    stdout string). The streamed one must not — it iterates a pipe."""
    commit = gp.resolve_commit(repo, "HEAD")

    def forbid_buffered_git(*args, **kwargs):
        raise AssertionError(
            "list_commit_times must stream `git log`, not buffer it via git()"
        )

    monkeypatch.setattr(gp, "git", forbid_buffered_git)
    times = gp.list_commit_times(repo, commit)
    assert times["a.py"] == 1222222222
