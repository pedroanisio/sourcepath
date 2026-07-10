"""E9 (error-free-mapping plan) — `cbm verify-bundle`: errors fail, not describe.

The gate turns the caveat layer's disclosures into FAILING checks: an
independent recount of the inventory vs the manifest, artifact hash
recompute, error budgets (parse-error share, unlanguaged share, silent
zeros, import resolution), SHACL verdict, and degradation acknowledgement.
A bundle that ships errors exits non-zero instead of shipping a caveat.

Run from the repo root:  python -m pytest tests/test_verify_bundle.py
"""
from __future__ import annotations

import json
import subprocess

import pytest

from codebase_mapper.verification.bundle_gate import (
    Budgets,
    check_bundle,
    recount_inventory,
)

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "1111111111 +0000",
    "GIT_COMMITTER_DATE": "1111111111 +0000",
    "PATH": "/usr/bin:/bin",
}


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    """A real end-to-end bundle from a healthy fixture repo."""
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries

    root = tmp_path_factory.mktemp("gate")
    r = root / "repo"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "a.py").write_text("import os\n\ndef f():\n    return 1\n")
    (r / "pkg" / "b.py").write_text("from pkg.a import f\n")
    (r / "README.md").write_text("# fixture\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    mapped = map_codebase(r, "HEAD")
    out = root / "bundle"
    emit("fixture", mapped, out, emit_blobs_flag=True)
    return out


def test_healthy_bundle_passes(bundle):
    violations = check_bundle(bundle, Budgets())
    assert violations == []


def test_recount_matches_manifest(bundle):
    man = json.loads((bundle / "run_manifest.json").read_text())
    counts = recount_inventory(bundle / "inventory.jsonld")
    assert counts["files"] == man["counts"]["files"]


def test_tampered_artifact_fails(bundle, tmp_path):
    import shutil
    tampered = tmp_path / "tampered"
    shutil.copytree(bundle, tampered)
    with open(tampered / "inventory.ttl", "a") as f:
        f.write("\n# tampered\n")
    violations = check_bundle(tampered, Budgets())
    assert any(v["id"] == "artifact_hash" for v in violations)


def test_manifest_count_drift_fails(bundle, tmp_path):
    import shutil
    drifted = tmp_path / "drifted"
    shutil.copytree(bundle, drifted)
    man = json.loads((drifted / "run_manifest.json").read_text())
    man["counts"]["files"] += 5
    (drifted / "run_manifest.json").write_text(json.dumps(man))
    violations = check_bundle(drifted, Budgets(), skip_hashes=True)
    assert any(v["id"] == "recount_files" for v in violations)


def test_unacknowledged_degradation_fails(bundle, tmp_path):
    import shutil
    degraded = tmp_path / "degraded"
    shutil.copytree(bundle, degraded)
    man = json.loads((degraded / "run_manifest.json").read_text())
    man["degradations"] = [{"component": "git_provenance",
                            "reason": "shallow_clone_no_history",
                            "affected_files": 3}]
    (degraded / "run_manifest.json").write_text(json.dumps(man))
    violations = check_bundle(degraded, Budgets(), skip_hashes=True)
    assert any(v["id"] == "degradation" for v in violations)
    accepted = check_bundle(degraded, Budgets(), skip_hashes=True,
                            accept_degradations={"git_provenance"})
    assert not any(v["id"] == "degradation" for v in accepted)


def test_budget_violation_fails(bundle, tmp_path):
    import shutil
    noisy = tmp_path / "noisy"
    shutil.copytree(bundle, noisy)
    man = json.loads((noisy / "run_manifest.json").read_text())
    t = man["ast_coverage"]["totals"]
    t["files_with_parse_errors"] = t["files"]  # 100% flagged
    (noisy / "run_manifest.json").write_text(json.dumps(man))
    violations = check_bundle(noisy, Budgets(), skip_hashes=True)
    assert any(v["id"] == "parse_error_budget" for v in violations)


def test_skipped_shacl_fails_by_default(bundle, tmp_path):
    import shutil
    skipped = tmp_path / "skipped"
    shutil.copytree(bundle, skipped)
    man = json.loads((skipped / "run_manifest.json").read_text())
    man["shacl_self_check"] = {"conforms": None, "skipped": True,
                               "report_excerpt": ""}
    (skipped / "run_manifest.json").write_text(json.dumps(man))
    violations = check_bundle(skipped, Budgets(), skip_hashes=True)
    assert any(v["id"] == "shacl" for v in violations)


def test_cli_exit_codes(bundle):
    from scripts.cbm_verify import main
    assert main(["--bundle", str(bundle)]) == 0
    assert main(["--bundle", str(bundle / "nope")]) != 0
