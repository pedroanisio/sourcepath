"""F10 — emit cost controls must be reachable via environment.

``--skip-shacl`` / ``--no-jsonld`` existed only as run_l4.py flags; the main
CLI always validated and always serialized JSON-LD, and nothing was
env-drivable. ``CBM_SKIP_SHACL`` / ``CBM_EMIT_JSONLD`` now feed emit()'s
defaults; explicit arguments still win. Skips remain disclosed in the
manifest (a skipped check never reads as a passed one).

Run from the repo root:  python -m pytest tests/test_emit_env_knobs.py
"""
from __future__ import annotations

import subprocess

import pytest

from codebase_mapper.emission.application.emit_bundle import emit
from codebase_mapper.inspection.pipeline import map_codebase
from codebase_mapper.shared_kernel.extensions import reset_registries

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "1111111111 +0000",
    "GIT_COMMITTER_DATE": "1111111111 +0000",
    "PATH": "/usr/bin:/bin",
}


@pytest.fixture()
def mapped(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "a.py").write_text("x = 1\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    return map_codebase(r, "HEAD")


def test_env_skips_shacl_with_disclosure(mapped, tmp_path, monkeypatch):
    monkeypatch.setenv("CBM_SKIP_SHACL", "1")
    manifest = emit("fixture", mapped, tmp_path / "b", emit_blobs_flag=False)
    assert manifest["shacl_self_check"]["skipped"] is True
    assert manifest["shacl_self_check"]["conforms"] is None


def test_env_skips_jsonld(mapped, tmp_path, monkeypatch):
    monkeypatch.setenv("CBM_EMIT_JSONLD", "0")
    manifest = emit("fixture", mapped, tmp_path / "b", emit_blobs_flag=False)
    assert not (tmp_path / "b" / "inventory.jsonld").exists()
    assert "inventory.jsonld" not in manifest["artifacts"]


def test_explicit_argument_beats_env(mapped, tmp_path, monkeypatch):
    monkeypatch.setenv("CBM_SKIP_SHACL", "1")
    manifest = emit("fixture", mapped, tmp_path / "b", emit_blobs_flag=False,
                    validate_shacl=True)
    assert manifest["shacl_self_check"]["conforms"] is True
    assert not manifest["shacl_self_check"].get("skipped")


def test_default_unchanged_without_env(mapped, tmp_path):
    manifest = emit("fixture", mapped, tmp_path / "b", emit_blobs_flag=False)
    assert manifest["shacl_self_check"]["conforms"] is True
    assert "inventory.jsonld" in manifest["artifacts"]
