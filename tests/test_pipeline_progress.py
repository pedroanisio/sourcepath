"""RED: progress reporting for map_codebase()'s host-side passes.

L4 got a progress indicator first (plugins/llm_enrich/), but the silence a
large repo actually spends most of its wall-clock time in is upstream of
that: classify+build records and AST extraction run once per file with zero
output today. This pins the throttled progress helper (no print-storm on a
94K-file repo) and its wiring into map_codebase().
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codebase_mapper.inspection.pipeline import _progress, map_codebase


# ── _progress: pure throttling logic ───────────────────────────────────────

def test_progress_always_shows_first_and_last(capsys):
    for i in range(1, 1001):
        _progress("classify", i, 1000, f"file{i}.py")
    out = capsys.readouterr().err
    assert "[host] classify  1/1000  file1.py" in out
    assert "[host] classify  1000/1000  file1000.py" in out


def test_progress_throttles_large_totals(capsys):
    for i in range(1, 1001):
        _progress("classify", i, 1000, f"file{i}.py")
    out = capsys.readouterr().err
    lines = [l for l in out.splitlines() if l]
    # Capped at ~_PROGRESS_LINES regardless of total, not one line per file.
    assert 30 <= len(lines) <= 60, f"expected ~50 throttled lines, got {len(lines)}"


def test_progress_throttles_mid_size_totals_visibly():
    # A few hundred files must look obviously throttled (skipped indices),
    # not "every other item" -- the boundary case that motivated capping by
    # target line count rather than a flat percentage.
    from codebase_mapper.inspection.pipeline import _PROGRESS_LINES
    interval = max(1, 250 // _PROGRESS_LINES)
    assert interval >= 4, (
        f"interval={interval} barely skips anything for a 250-item pass")


def test_progress_small_totals_show_every_item(capsys):
    for i in range(1, 4):
        _progress("classify", i, 3, f"file{i}.py")
    out = capsys.readouterr().err
    lines = [l for l in out.splitlines() if l]
    assert len(lines) == 3


def test_progress_zero_total_prints_nothing(capsys):
    _progress("classify", 0, 0, "n/a")
    assert capsys.readouterr().err == ""


# ── wiring: map_codebase() actually emits it ───────────────────────────────

def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "a.py").write_text("def f():\n    return 1\n")
    (repo / "b.py").write_text("def g():\n    return 2\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)
    return repo


def test_map_codebase_emits_host_progress_on_stderr(tiny_repo, capsys):
    map_codebase(tiny_repo, "HEAD")
    out = capsys.readouterr().err
    assert "[host] classify" in out
    assert "[host] extract" in out
