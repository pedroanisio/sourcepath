"""Performance feature F4 — concurrent L4 enrichment.

During the Linux-kernel benchmark the H200 sat at 0% while
``OllamaClient.chat`` issued one blocking HTTP call at a time from the
host's serial enricher loop. The feature, pinned here:

- ``OllamaClient`` is thread-safe (single lazily-built httpx.Client even
  under a first-call race; injectable transport for offline tests);
- ``Cache.put`` survives concurrent writers on the same key (writer-
  unique tmp files, atomic replace);
- ``LlmEnricher`` declares ``parallel_safe`` and tolerates concurrent
  ``enrich`` calls on distinct records;
- the host pipeline runs parallel-safe enrichers concurrently
  (``$CBM_ENRICH_WORKERS``, default 4) and falls back to the exact
  serial path whenever hoisting would change per-record enricher order.

All tests run offline; concurrency proofs use barriers (deterministic
rendezvous), never wall-clock timing.

Run from the repo root:  python -m pytest tests/test_perf_llm_concurrency.py
"""
from __future__ import annotations

import json
import subprocess
import threading

import httpx
import pytest

from codebase_mapper.inspection import pipeline as pl
from codebase_mapper.shared_kernel.extensions import (
    register_record_enricher, reset_registries,
)
from plugins.llm_enrich import cache as cache_mod
from plugins.llm_enrich import client as client_mod
from plugins.llm_enrich.cache import Cache
from plugins.llm_enrich.client import OllamaClient
from plugins.llm_enrich.enricher import LlmEnricher


# ---------------------------------------------------------------------------
# worker-count resolution
# ---------------------------------------------------------------------------


def test_enrich_workers_default_is_four(monkeypatch):
    monkeypatch.delenv("CBM_ENRICH_WORKERS", raising=False)
    assert pl._enrich_workers() == 4


def test_enrich_workers_env_override(monkeypatch):
    monkeypatch.setenv("CBM_ENRICH_WORKERS", "9")
    assert pl._enrich_workers() == 9


@pytest.mark.parametrize("bad", ["nope", "0", "-1"])
def test_enrich_workers_degrades_to_serial_on_garbage(monkeypatch, bad):
    monkeypatch.setenv("CBM_ENRICH_WORKERS", bad)
    assert pl._enrich_workers() == 1


# ---------------------------------------------------------------------------
# OllamaClient thread safety
# ---------------------------------------------------------------------------


def _ok_transport():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "hi"}})
    return httpx.MockTransport(handler)


def test_client_accepts_injected_transport():
    client = OllamaClient(host="http://test", transport=_ok_transport())
    text, dt = client.chat(model="m", system="s", user="u", seed=1)
    assert text == "hi" and dt >= 0


def test_client_builds_exactly_one_http_client_under_race(monkeypatch):
    created = []
    real_client = httpx.Client

    class Counting(real_client):
        def __init__(self, *a, **kw):
            created.append(1)
            super().__init__(*a, **kw)

    monkeypatch.setattr(client_mod.httpx, "Client", Counting)
    client = OllamaClient(host="http://test", transport=_ok_transport())

    start = threading.Barrier(8, timeout=10)
    results, errors = [], []

    def worker():
        start.wait()
        try:
            results.append(client.chat(model="m", system="s", user="u")[0])
        except Exception as e:  # pragma: no cover - failure reporting
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert results == ["hi"] * 8
    assert len(created) == 1


# ---------------------------------------------------------------------------
# Cache under concurrent writers
# ---------------------------------------------------------------------------


def test_cache_put_uses_writer_unique_tmp(tmp_path, monkeypatch):
    """Two writes of the same key must never share a tmp path — a shared
    name lets one thread atomically publish another thread's half-written
    bytes."""
    seen = []
    real_replace = cache_mod.os.replace

    def spy(src, dst):
        seen.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(cache_mod.os, "replace", spy)
    c = Cache(cache_dir=tmp_path)
    c.put("kk", {"text": "1"})
    c.put("kk", {"text": "2"})
    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_cache_concurrent_same_key_stays_consistent(tmp_path):
    c = Cache(cache_dir=tmp_path)
    start = threading.Barrier(8, timeout=10)
    results, errors = [], []

    def worker():
        start.wait()
        try:
            rec, _hit = c.get_or_compute(
                kind="k", model="m", prompt_sha="p", target_sha="t",
                compute=lambda: {"text": "x", "generated_at": "now"},
            )
            results.append(rec)
        except Exception as e:  # pragma: no cover - failure reporting
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert all(r["text"] == "x" for r in results)
    entries = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert entries, "cache entry must exist"
    for p in entries:
        json.loads(p.read_text())  # no torn writes


# ---------------------------------------------------------------------------
# LlmEnricher parallel safety
# ---------------------------------------------------------------------------


class _StubChat:
    """Duck-typed client: two concurrent chats rendezvous, proving the
    enricher tolerates overlap; content is deterministic per path."""

    def __init__(self, parties):
        self.barrier = threading.Barrier(parties, timeout=10)

    def chat(self, model, system, user, *, seed=0):
        self.barrier.wait()
        return f"summary::{hash(user) & 0xffff}", 0.0


def _mkrec(path):
    from codebase_mapper.inspection.models import FileRecord
    return FileRecord(
        path=path, git_blob_sha="0" * 40, content_sha256="0" * 64,
        size_bytes=1, language="python", type_="source_code",
        phases=["runtime"], atime=None, mtime=None, ctime=None,
        git_commit_time=None,
    )


def test_llm_enricher_declares_parallel_safe():
    assert LlmEnricher.parallel_safe is True


def test_llm_enricher_concurrent_records(tmp_path):
    from codebase_mapper.shared_kernel.extensions import PipelineCtx

    records = [_mkrec("a.py"), _mkrec("b.py")]
    ctx = PipelineCtx(
        repo=None, commit="c", records=records, blob_by_path={},
        mode_by_path={}, paths_set={"a.py", "b.py"},
        read_path=lambda p: b"",
    )
    enricher = LlmEnricher(
        client=_StubChat(parties=2), cache=Cache(enabled=False),
        model="m", scopes=("files",),
    )
    errors = []

    def worker(rec):
        try:
            enricher.enrich(rec, f"# {rec.path}\n".encode(), ctx)
        except Exception as e:  # pragma: no cover - failure reporting
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(r,)) for r in records]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    bucket = ctx.scratch["llm:file_summary"]
    assert set(bucket) == {"a.py", "b.py"}
    assert all(v["text"].startswith("summary::") for v in bucket.values())


# ---------------------------------------------------------------------------
# host pipeline: parallel-safe enrichers run concurrently
# ---------------------------------------------------------------------------

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
    r.mkdir()
    (r / "a.py").write_text("x = 1\n")
    (r / "b.py").write_text("y = 2\n")
    (r / "c.txt").write_text("prose\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True,
                       capture_output=True, env=_ENV)
    return r


class _Rendezvous:
    """Parallel-safe enricher: the two .py records must be in flight at
    the same time. Serial execution breaks the barrier (timeout) and
    fails the run."""
    name = "l4_probe"
    parallel_safe = True

    def __init__(self):
        self.barrier = threading.Barrier(2, timeout=10)
        self.seen: list[str] = []
        self.lock = threading.Lock()

    def enrich(self, record, content, ctx):
        if not record.path.endswith(".py"):
            return
        self.barrier.wait()
        with self.lock:
            self.seen.append(record.path)


class _MaxConcurrency:
    """Records the peak number of overlapping enrich() calls."""
    parallel_safe = True

    def __init__(self, name):
        self.name = name
        self.lock = threading.Lock()
        self.cur = 0
        self.max_seen = 0

    def enrich(self, record, content, ctx):
        with self.lock:
            self.cur += 1
            self.max_seen = max(self.max_seen, self.cur)
        with self.lock:
            self.cur -= 1


class _SerialMarker:
    """An enricher without parallel_safe — its presence after a
    parallel-safe one in sort order must force the whole loop serial."""
    name = "z_serial"

    def __init__(self):
        self.calls = 0

    def enrich(self, record, content, ctx):
        self.calls += 1


def test_pipeline_runs_parallel_safe_enrichers_concurrently(repo, monkeypatch):
    reset_registries()
    probe = _Rendezvous()
    register_record_enricher(probe)
    monkeypatch.setenv("CBM_ENRICH_WORKERS", "2")
    monkeypatch.setenv("CBM_EXTRACT_WORKERS", "1")
    pl.map_codebase(repo, "HEAD")
    assert sorted(probe.seen) == ["a.py", "b.py"]


def test_pipeline_forces_serial_when_order_would_change(repo, monkeypatch):
    """parallel-safe 'a_par' sorts before non-parallel 'z_serial'; hoisting
    the parallel group would run a_par after z_serial for early records —
    an observable ordering change. The host must detect this and run the
    exact serial path."""
    reset_registries()
    par = _MaxConcurrency("a_par")
    ser = _SerialMarker()
    register_record_enricher(ser)
    register_record_enricher(par)
    monkeypatch.setenv("CBM_ENRICH_WORKERS", "4")
    monkeypatch.setenv("CBM_EXTRACT_WORKERS", "1")
    pl.map_codebase(repo, "HEAD")
    assert ser.calls == 3
    assert par.max_seen == 1
