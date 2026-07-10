"""RED: shallow clones must not fabricate per-file git commit times.

Observed on a real Linux-kernel run: repo_source clones remotes with
``--depth 1``, so ``git log --name-only`` sees a single parentless tip and
attributes every one of 94,841 files to that lone commit — every
``cbm:gitCommitTime`` came out identical (and wrong). Fabricated facts are
worse than absent facts (PURPOSE.md), so on shallow history the pipeline
must omit ``git_commit_time`` entirely and record the degradation:

    ctx.scratch["degradations"] += [{
        "component": "git_provenance",
        "reason": "shallow_clone_no_history",
        "affected_files": <count>,
    }]

Full-history repos keep today's behavior byte-identically.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from rdflib import URIRef

from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import (
    build_inventory_graph,
)
from codebase_mapper.inspection.git_plumbing import list_commit_times
from codebase_mapper.inspection.pipeline import map_codebase
from codebase_mapper.shared_kernel.constants import CBM, CBMI_NS

TS1 = 1_700_000_000
TS2 = 1_700_086_400  # TS1 + 1 day


def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, env={**os.environ, **(env or {})},
    )


@pytest.fixture
def full_repo(tmp_path: Path) -> Path:
    """Two commits at distinct, known author times.

    Commit 1 (TS1): app.py + lib.py.  Commit 2 (TS2): re-touches app.py.
    """
    repo = tmp_path / "src"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text("def main(): pass\n")
    (repo / "lib.py").write_text("X = 1\n")
    _git(repo, "add", "-A", env=None)
    _git(repo, "commit", "-q", "-m", "init", env={
        "GIT_AUTHOR_DATE": f"{TS1} +0000", "GIT_COMMITTER_DATE": f"{TS1} +0000",
    })
    (repo / "app.py").write_text("def main(): return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "update app", env={
        "GIT_AUTHOR_DATE": f"{TS2} +0000", "GIT_COMMITTER_DATE": f"{TS2} +0000",
    })
    return repo


@pytest.fixture
def shallow_clone(full_repo: Path, tmp_path: Path) -> Path:
    """``git clone --depth 1 file://...`` of full_repo — the repo_source path."""
    clone = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", full_repo.as_uri(), str(clone)],
        check=True, capture_output=True,
    )
    # Fixture sanity: the clone really is shallow with a lone parentless tip.
    out = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "--is-shallow-repository"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert out == "true", "fixture must produce a shallow repository"
    return clone


# ── plumbing layer ──────────────────────────────────────────────────────────

def test_is_shallow_repository_detects_both_cases(full_repo, shallow_clone):
    from codebase_mapper.inspection.git_plumbing import is_shallow_repository

    assert is_shallow_repository(full_repo) is False
    assert is_shallow_repository(shallow_clone) is True


def test_list_commit_times_full_history_returns_distinct_real_times(full_repo):
    times = list_commit_times(full_repo, "HEAD")
    assert times["app.py"] == TS2
    assert times["lib.py"] == TS1
    assert times["app.py"] != times["lib.py"], "real history has distinct times"


def test_list_commit_times_shallow_returns_nothing_not_fabrications(shallow_clone):
    # On a depth-1 clone every path would appear "added" by the lone tip —
    # a fabricated uniform stamp. The contract is: no history, no facts.
    times = list_commit_times(shallow_clone, "HEAD")
    assert times == {}, (
        f"shallow history must yield no commit times, got fabricated {times}"
    )


# ── pipeline wiring ─────────────────────────────────────────────────────────

def test_map_codebase_shallow_omits_times_and_registers_degradation(shallow_clone):
    mapped = map_codebase(shallow_clone, "HEAD")
    records = mapped["records"]
    assert records, "fixture repo maps to at least one record"

    stamped = {r.path: r.git_commit_time for r in records if r.git_commit_time is not None}
    assert stamped == {}, f"shallow clone must not stamp git_commit_time: {stamped}"

    degradations = mapped["ctx"].scratch.get("degradations")
    assert degradations == [{
        "component": "git_provenance",
        "reason": "shallow_clone_no_history",
        "affected_files": len(records),
    }]


def test_map_codebase_full_history_unchanged_and_no_degradation(full_repo):
    mapped = map_codebase(full_repo, "HEAD")
    by_path = {r.path: r for r in mapped["records"]}

    assert by_path["app.py"].git_commit_time == TS2
    assert by_path["lib.py"].git_commit_time == TS1
    # Byte-identical behavior for full clones: no degradations key at all.
    assert "degradations" not in mapped["ctx"].scratch


# ── emitter: absent value → absent triple ───────────────────────────────────

def test_emitter_omits_gitCommitTime_triple_when_value_absent(shallow_clone):
    mapped = map_codebase(shallow_clone, "HEAD")
    graph = build_inventory_graph(
        repo_iri=URIRef(f"{CBMI_NS}repo/shallow"),
        commit_sha=mapped["commit"],
        records=mapped["records"],
        import_edges=mapped["import_edges"],
        import_ext_edges=mapped["import_ext_edges"],
        dep_edges=mapped["dep_edges"],
        pin_edges=mapped["pin_edges"],
        tests_edges=mapped["tests_edges"],
    )
    fabricated = list(graph.triples((None, CBM.gitCommitTime, None)))
    assert fabricated == [], f"no gitCommitTime triples may be emitted: {fabricated}"
    # Filesystem times are still real facts on a shallow working tree.
    assert list(graph.triples((None, CBM.mtime, None))), "mtime triples must remain"


# ── opt-in deepening (default OFF) ──────────────────────────────────────────

def _make_bare(src: Path, tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(src), str(bare)],
        check=True, capture_output=True,
    )
    # Local file:// upload-pack refuses --filter unless the server allows it.
    subprocess.run(
        ["git", "-C", str(bare), "config", "uploadpack.allowfilter", "true"],
        check=True, capture_output=True,
    )
    return bare


def test_resolve_repo_source_default_recovers_history(full_repo, tmp_path, monkeypatch):
    """E5 (error-free-mapping plan): correct provenance is the default.

    An unset CBM_UNSHALLOW attempts the blob-free history deepen, so
    per-file commit times exist without opting in; omission remains only
    as the disclosed fallback when the fetch fails."""
    from codebase_mapper.inspection.git_plumbing import is_shallow_repository
    from codebase_mapper.inspection.repo_source import resolve_repo_source

    monkeypatch.delenv("CBM_UNSHALLOW", raising=False)
    bare = _make_bare(full_repo, tmp_path)
    with resolve_repo_source(bare.as_uri(), "HEAD", work_dir=tmp_path / "w1") as c:
        assert is_shallow_repository(c.path) is False
        times = list_commit_times(c.path, "HEAD")
        assert times["app.py"] == TS2


def test_resolve_repo_source_optout_forces_shallow(full_repo, tmp_path, monkeypatch):
    from codebase_mapper.inspection.git_plumbing import is_shallow_repository
    from codebase_mapper.inspection.repo_source import resolve_repo_source

    monkeypatch.setenv("CBM_UNSHALLOW", "0")
    bare = _make_bare(full_repo, tmp_path)
    with resolve_repo_source(bare.as_uri(), "HEAD", work_dir=tmp_path / "w0") as c:
        assert is_shallow_repository(c.path) is True


def test_resolve_repo_source_unshallow_env_var_recovers_history(
    full_repo, tmp_path, monkeypatch,
):
    from codebase_mapper.inspection.git_plumbing import is_shallow_repository
    from codebase_mapper.inspection.repo_source import resolve_repo_source

    monkeypatch.setenv("CBM_UNSHALLOW", "1")
    bare = _make_bare(full_repo, tmp_path)
    with resolve_repo_source(bare.as_uri(), "HEAD", work_dir=tmp_path / "w2") as c:
        assert is_shallow_repository(c.path) is False
        times = list_commit_times(c.path, "HEAD")
        assert times["app.py"] == TS2
        assert times["lib.py"] == TS1
