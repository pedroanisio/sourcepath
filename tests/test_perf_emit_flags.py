"""Performance feature F5 — emit() cost controls.

At Linux-kernel scale ``emit()`` was observed inside a single-core,
memory-multiplying tail: full Turtle serialization, then JSON-LD
serialization plus a read-back/re-sort/rewrite of the whole document,
then a full pySHACL validation of the in-memory graph. The feature:

- ``emit(..., validate_shacl=False)`` skips pySHACL and *discloses* the
  skip in the manifest (never silently — PALS's Law);
- ``emit(..., emit_jsonld=False)`` skips the JSON-LD serialization and
  drops it from the manifest's artifact map;
- the JSON-LD canonicalization happens in one write (serialize to
  string → sort → write), not write-read-rewrite; bytes are unchanged;
- ``run_l4.py`` exposes ``--skip-shacl`` / ``--no-jsonld`` and still
  exits 0 when validation was skipped rather than failed.

Run from the repo root:  python -m pytest tests/test_perf_emit_flags.py
"""
from __future__ import annotations

import json
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
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "__init__.py").write_text("")
    (r / "pkg" / "a.py").write_text("import os\n")
    (r / "README.md").write_text("# hi\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    return map_codebase(r, "HEAD")


def _sort_jsonld(node):
    """Independent oracle: the documented canonical ordering."""
    if isinstance(node, dict):
        return {k: _sort_jsonld(v) for k, v in sorted(node.items())}
    if isinstance(node, list):
        items = [_sort_jsonld(x) for x in node]

        def key(x):
            if isinstance(x, dict):
                return (0, x.get("@id", ""), json.dumps(x, sort_keys=True))
            return (1, str(x))
        return sorted(items, key=key)
    return node


def test_emit_default_still_validates_and_canonicalizes(mapped, tmp_path):
    out = tmp_path / "bundle"
    manifest = emit("fixture", mapped, out, emit_blobs_flag=False)
    sc = manifest["shacl_self_check"]
    assert sc["conforms"] is True
    assert not sc.get("skipped")
    jsonld = out / "inventory.jsonld"
    assert jsonld.exists()
    text = jsonld.read_text()
    doc = json.loads(text)
    assert text == json.dumps(_sort_jsonld(doc), indent=2, sort_keys=True) + "\n"
    assert "inventory.jsonld" in manifest["artifacts"]


def test_emit_skip_shacl_is_disclosed_not_silent(mapped, tmp_path):
    out = tmp_path / "bundle"
    manifest = emit("fixture", mapped, out, emit_blobs_flag=False,
                    validate_shacl=False)
    sc = manifest["shacl_self_check"]
    assert sc["skipped"] is True
    assert sc["conforms"] is None
    # the on-disk manifest carries the same disclosure
    on_disk = json.loads((out / "run_manifest.json").read_text())
    assert on_disk["shacl_self_check"]["skipped"] is True


def test_emit_skip_jsonld_drops_file_and_manifest_entry(mapped, tmp_path):
    out = tmp_path / "bundle"
    manifest = emit("fixture", mapped, out, emit_blobs_flag=False,
                    emit_jsonld=False)
    assert not (out / "inventory.jsonld").exists()
    assert "inventory.jsonld" not in manifest["artifacts"]
    # the rest of the bundle contract is intact
    assert (out / "inventory.ttl").exists()
    assert "inventory.ttl" in manifest["artifacts"]


def test_emit_skip_jsonld_removes_stale_file_from_prior_run(mapped, tmp_path):
    out = tmp_path / "bundle"
    emit("fixture", mapped, out, emit_blobs_flag=False)  # writes inventory.jsonld
    assert (out / "inventory.jsonld").exists()
    manifest = emit("fixture", mapped, out, emit_blobs_flag=False,
                    emit_jsonld=False)
    assert not (out / "inventory.jsonld").exists(), \
        "a stale JSON-LD from a prior emit must not survive a skipping run"
    assert "inventory.jsonld" not in manifest["artifacts"]


def test_run_l4_flags_wire_through_and_exit_zero(tmp_path):
    import scripts.run_l4 as run_l4  # noqa: F401  (import checks the flags exist)
    r = tmp_path / "repo"
    r.mkdir()
    (r / "a.py").write_text("x = 1\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True,
                       capture_output=True, env=_ENV)
    out = tmp_path / "bundle"
    rc = run_l4.main([
        "--repo", str(r), "--out", str(out), "--name", "fixture",
        "--no-l2", "--no-llm", "--no-builtin-vocab",
        "--skip-shacl", "--no-jsonld", "--no-emit-blobs",
    ])
    assert rc == 0, "a skipped validation is not a failed validation"
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["shacl_self_check"]["skipped"] is True
    assert not (out / "inventory.jsonld").exists()
