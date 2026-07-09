"""F4/F5 — degradation disclosure must reach run_manifest.json.

PALS's Law: a layer that degrades must leave a machine-readable record in
the bundle. Producers already write ``ctx.scratch["degradations"]``
(pipeline.py shallow-clone path, llm_enrich self-disable paths); this suite
pins the missing half of the contract:

- ``emit()`` surfaces ``ctx.scratch["degradations"]`` as
  ``manifest["degradations"]`` — in memory and on disk;
- the key is ALWAYS present: an empty list is the healthy-run statement,
  so absence can never be read as health;
- the shallow-clone degradation recorded by ``map_codebase`` round-trips
  end-to-end into the manifest;
- the concept-description self-disable path registers a degradation entry
  (previously it only logged — silent degradation).

Run from the repo root:  python -m pytest tests/test_degradations_manifest.py
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


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, env=_ENV)


def _make_repo(root):
    r = root / "repo"
    r.mkdir()
    (r / "a.py").write_text("import os\n")
    (r / "README.md").write_text("# hi\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "one")
    return r


@pytest.fixture()
def mapped(tmp_path):
    reset_registries()
    return map_codebase(_make_repo(tmp_path), "HEAD")


def test_healthy_run_manifest_carries_empty_degradations(mapped, tmp_path):
    manifest = emit("fixture", mapped, tmp_path / "bundle",
                    emit_blobs_flag=False)
    assert manifest["degradations"] == [], \
        "healthy runs must state health explicitly (empty list, not absence)"
    on_disk = json.loads((tmp_path / "bundle" / "run_manifest.json").read_text())
    assert on_disk["degradations"] == []


def test_scratch_degradations_surface_in_manifest(mapped, tmp_path):
    entry = {
        "component": "llm_enrich",
        "reason": "client_failure_self_disabled",
        "kind": "file_summary",
        "skipped": 7,
    }
    mapped["ctx"].scratch.setdefault("degradations", []).append(entry)
    manifest = emit("fixture", mapped, tmp_path / "bundle",
                    emit_blobs_flag=False)
    assert entry in manifest["degradations"]
    on_disk = json.loads((tmp_path / "bundle" / "run_manifest.json").read_text())
    assert entry in on_disk["degradations"]


def test_shallow_clone_degradation_round_trips_to_manifest(tmp_path):
    origin = _make_repo(tmp_path)
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(shallow)],
        check=True, capture_output=True, env=_ENV,
    )
    reset_registries()
    mapped = map_codebase(shallow, "HEAD")
    manifest = emit("fixture", mapped, tmp_path / "bundle",
                    emit_blobs_flag=False)
    reasons = {d["reason"] for d in manifest["degradations"]}
    assert "shallow_clone_no_history" in reasons
    entry = next(d for d in manifest["degradations"]
                 if d["reason"] == "shallow_clone_no_history")
    assert entry["component"] == "git_provenance"
    assert entry["affected_files"] == len(mapped["records"])


def test_concept_description_disable_registers_degradation():
    """F5: the aggregator's self-disable path must disclose, not just log."""
    from plugins.llm_enrich.aggregator import LlmAggregator
    from plugins.llm_enrich.client import OllamaUnreachable

    class _Ctx:
        def __init__(self, concepts):
            self.scratch = {}
            self.indices = {
                "l3_20_concepts": {
                    "concepts": concepts,
                    "cooccurrence": [],
                    "per_path_concepts": {},
                }
            }

    class _FailingCache:
        def get_or_compute(self, **kwargs):
            raise OllamaUnreachable("connection refused")

    agg = LlmAggregator.__new__(LlmAggregator)
    agg.model = "test-model"
    agg.client = object()
    agg.cache = _FailingCache()
    agg._disabled = False

    concepts = {
        f"c{i}": {"kind": "domain-primitive", "frequency": 5}
        for i in range(4)
    }
    ctx = _Ctx(concepts)
    out: dict = {}
    agg._do_concept_descriptions(ctx, out)

    assert agg._disabled is True
    assert out == {}
    degradations = ctx.scratch.get("degradations", [])
    assert degradations, "self-disable must register a degradation entry"
    entry = degradations[0]
    assert entry["component"] == "llm_enrich"
    assert entry["kind"] == "concept_description"
    assert entry["skipped"] == 4, \
        "the blast radius (eligible concepts left undescribed) must be counted"
    assert "connection refused" in entry["error"]
