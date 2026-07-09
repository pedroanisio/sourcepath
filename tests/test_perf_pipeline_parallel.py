"""Performance features F3/F6 — parallel AST extraction, bounded memory.

At Linux-kernel scale the pipeline's AST-extraction pass ran on one
core while 23 idled, and ``content_by_path`` held the whole repo's
bytes for the entire run. These tests pin:

- ``_extract_workers()`` — worker count from ``$CBM_EXTRACT_WORKERS``
  (default: all cores; garbage / non-positive values degrade to 1);
- ``_run_extraction()`` — the extraction pass extracted for testability:
  parallel output is byte-equivalent to serial, genuinely concurrent
  when workers > 1, and releases each file's content as it is consumed;
- ``map_codebase()`` end-to-end equivalence serial vs parallel, and the
  no-per-blob-subprocess property (F1 wiring: a run must not call
  ``read_blob`` once per file).

Run from the repo root:  python -m pytest tests/test_perf_pipeline_parallel.py
"""
from __future__ import annotations

import subprocess
import threading

import pytest

from codebase_mapper.inspection import pipeline as pl
from codebase_mapper.inspection.models import FileRecord
from codebase_mapper.shared_kernel.extensions import (
    PipelineCtx, reset_registries,
)

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "1111111111 +0000",
    "GIT_COMMITTER_DATE": "1111111111 +0000",
    "PATH": "/usr/bin:/bin",
}


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "__init__.py").write_text("from .a import f\n")
    (r / "pkg" / "a.py").write_text("import os\n\ndef f():\n    return os.sep\n")
    (r / "pkg" / "b.py").write_text("from pkg.a import f\n\ndef g():\n    return f()\n")
    (r / "web.js").write_text("import './pkg/a';\nexport const x = 1;\n")
    (r / "native.c").write_text("#include <stdio.h>\nint main(void){return 0;}\n")
    (r / "data.bin").write_bytes(bytes(range(256)) * 4)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True,
                       capture_output=True, env=_ENV)
    return r


def _snapshot(mapped):
    return [
        (r.path, r.language, r.type_, r.phases, r.extraction_errors,
         r.ast_summary)
        for r in mapped["records"]
    ], mapped["import_edges"], mapped["import_ext_edges"]


# ---------------------------------------------------------------------------
# worker-count resolution
# ---------------------------------------------------------------------------


def test_extract_workers_env_override(monkeypatch):
    monkeypatch.setenv("CBM_EXTRACT_WORKERS", "8")
    assert pl._extract_workers() == 8


def test_extract_workers_default_is_cpu_count(monkeypatch):
    import os
    monkeypatch.delenv("CBM_EXTRACT_WORKERS", raising=False)
    assert pl._extract_workers() == (os.cpu_count() or 1)


@pytest.mark.parametrize("bad", ["banana", "0", "-3", ""])
def test_extract_workers_degrades_to_serial_on_garbage(monkeypatch, bad):
    monkeypatch.setenv("CBM_EXTRACT_WORKERS", bad)
    if bad == "":
        import os
        assert pl._extract_workers() == (os.cpu_count() or 1)
    else:
        assert pl._extract_workers() == 1


# ---------------------------------------------------------------------------
# _run_extraction unit behavior
# ---------------------------------------------------------------------------


def _mkrec(path, language="python", type_="source_code"):
    return FileRecord(
        path=path, git_blob_sha="0" * 40, content_sha256="0" * 64,
        size_bytes=1, language=language, type_=type_,
        phases=["runtime"], atime=None, mtime=None, ctime=None,
        git_commit_time=None,
    )


def _mkctx(records):
    return PipelineCtx(
        repo=None, commit="c", records=records, blob_by_path={},
        mode_by_path={}, paths_set={r.path for r in records},
        read_path=lambda p: b"",
    )


class _MatchAll:
    name = "stub"

    def matches(self, record, ctx):
        return True

    def extract(self, record, content, ctx):
        return {"echo": content.decode()}, []


def test_run_extraction_consumes_contents(tmp_path):
    records = [_mkrec("a.py"), _mkrec("b.bin", None, "binary"), _mkrec("c.py")]
    contents = {"a.py": b"a", "b.bin": b"\x00", "c.py": b"c"}
    pl._run_extraction(records, contents, [_MatchAll()], _mkctx(records),
                       skip_extraction=set(), workers=1)
    assert contents == {}, "every file's bytes must be released as consumed"
    assert records[0].ast_summary == {"echo": "a"}
    assert records[1].ast_summary is None  # binary skipped, still released
    assert records[2].ast_summary == {"echo": "c"}


def test_run_extraction_parallelism_is_real():
    """Two extract calls must be in flight at once when workers=2: each
    blocks on a shared barrier that only opens when both arrive. A serial
    implementation deadlocks the barrier and the test times out the wait."""
    barrier = threading.Barrier(2, timeout=10)

    class Rendezvous(_MatchAll):
        def extract(self, record, content, ctx):
            barrier.wait()
            return {"ok": True}, []

    records = [_mkrec("a.py"), _mkrec("b.py")]
    contents = {"a.py": b"", "b.py": b""}
    pl._run_extraction(records, contents, [Rendezvous()], _mkctx(records),
                       skip_extraction=set(), workers=2)
    assert all(r.ast_summary == {"ok": True} for r in records)


def test_run_extraction_error_isolation_under_parallelism():
    class Boom(_MatchAll):
        def extract(self, record, content, ctx):
            if record.path == "bad.py":
                raise ValueError("kaput")
            return {"ok": True}, []

    records = [_mkrec("a.py"), _mkrec("bad.py"), _mkrec("c.py")]
    contents = {r.path: b"" for r in records}
    pl._run_extraction(records, contents, [Boom()], _mkctx(records),
                       skip_extraction=set(), workers=4)
    assert records[0].ast_summary == {"ok": True}
    assert records[1].ast_summary is None
    assert records[1].extraction_errors == ["extract_failed: ValueError: kaput"]
    assert records[2].ast_summary == {"ok": True}


# ---------------------------------------------------------------------------
# map_codebase end-to-end
# ---------------------------------------------------------------------------


def test_map_codebase_parallel_equals_serial(repo, monkeypatch):
    reset_registries()
    monkeypatch.setenv("CBM_EXTRACT_WORKERS", "1")
    serial = _snapshot(pl.map_codebase(repo, "HEAD"))
    monkeypatch.setenv("CBM_EXTRACT_WORKERS", "4")
    parallel = _snapshot(pl.map_codebase(repo, "HEAD"))
    assert serial == parallel


def test_ts_setup_is_atomic_under_concurrent_first_call(monkeypatch):
    """Found live: under machine load, a mapping run dropped 3 import
    edges because ``_ts_setup()``'s guard is dict truthiness — a thread
    arriving during another thread's key-by-key population saw a
    non-empty dict, returned early, and KeyError'd on a grammar that
    wasn't loaded yet (swallowed by _safe_extract as a silent per-file
    degradation). Every caller must observe fully-populated tables when
    its _ts_setup() call returns, no matter who initializes."""
    from codebase_mapper import ts_setup

    if not ts_setup.TS_AVAILABLE:
        pytest.skip("tree-sitter grammars unavailable")

    saved_langs = dict(ts_setup._TS_LANGS)
    saved_queries = dict(ts_setup._TS_QUERIES)
    ts_setup._TS_LANGS.clear()
    ts_setup._TS_QUERIES.clear()
    if hasattr(ts_setup, "_TS_READY"):
        monkeypatch.setattr(ts_setup, "_TS_READY", False)

    mid_population = threading.Event()  # first grammar assigned, more to go
    gate = threading.Event()
    real_second = ts_setup.tst.language_tsx

    def slow_second():
        # Runs after _TS_LANGS["typescript"] is already assigned: the
        # initializer is now provably mid-population. Hold it there.
        mid_population.set()
        gate.wait(timeout=10)
        return real_second()

    monkeypatch.setattr(ts_setup.tst, "language_tsx", slow_second)

    complete = []
    lock = threading.Lock()

    def call_and_observe():
        ts_setup._ts_setup()
        with lock:
            complete.append(
                "cpp" in ts_setup._TS_LANGS
                and "typescript" in ts_setup._TS_QUERIES)

    try:
        initializer = threading.Thread(target=call_and_observe)
        initializer.start()
        assert mid_population.wait(timeout=10), "initializer never started"
        # These calls arrive while the tables are half-built. Each must
        # block until population completes — returning early is the bug.
        observers = [threading.Thread(target=call_and_observe)
                     for _ in range(3)]
        for t in observers:
            t.start()
        gate.set()
        initializer.join(timeout=15)
        for t in observers:
            t.join(timeout=15)
        assert complete == [True] * 4, (
            "a caller returned from _ts_setup() before the grammar tables "
            f"were fully populated: {complete}")
    finally:
        gate.set()
        ts_setup._TS_LANGS.clear()
        ts_setup._TS_LANGS.update(saved_langs)
        ts_setup._TS_QUERIES.clear()
        ts_setup._TS_QUERIES.update(saved_queries)


def test_map_codebase_never_spawns_per_blob_subprocess(repo, monkeypatch):
    """F1 wiring: the run must go through one persistent BlobReader, not
    one `git cat-file blob` subprocess per file read."""
    reset_registries()

    def bomb(*a, **kw):
        raise AssertionError("per-blob read_blob() call — use BlobReader")

    monkeypatch.setattr(pl, "read_blob", bomb)
    mapped = pl.map_codebase(repo, "HEAD")
    assert len(mapped["records"]) == 6
    # read_path (used by enrichers/indices) must also ride the reader
    assert mapped["ctx"].read_path("pkg/a.py").startswith(b"import os")
