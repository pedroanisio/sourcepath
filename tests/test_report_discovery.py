"""Companion-file discovery must not cross bundle boundaries.

Found live: reporting on the 583-file zod bundle hung for what would
have been hours. discover() auto-globs the bundle's *parent* directory
for companion files (abox / decomposition / buildplan) — and in a
shared output directory holding many bundles it attached
``linux.decomposition.symbols.yaml`` (380 MB, a different repository's
artifact) to zod's report, then fed it to the pure-Python YAML parser.

Two defects pinned here:

- outside the bundle directory, discovery only accepts companions whose
  filename references the bundle's name — another repo's files are
  never silently attached (a report quoting a foreign decomposition as
  its own would violate the project's provenance rules);
- inside the bundle directory any match is still accepted (unambiguous);
- YAML companions load through libyaml's C loader when available —
  pure-Python parsing is unusable at decomposition scale.

Run from the repo root:  python -m pytest tests/test_report_discovery.py
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "scripts"))
import cbm_report as CR  # noqa: E402


class _NoExplicit:
    abox = decomposition = buildplan = None


def _mkbundle(tmp_path, name="zod"):
    bundle = tmp_path / name
    bundle.mkdir()
    (bundle / "run_manifest.json").write_text("{}")
    return bundle


def test_parent_dir_companion_of_other_repo_is_ignored(tmp_path):
    bundle = _mkbundle(tmp_path)
    (tmp_path / "linux.decomposition.symbols.yaml").write_text("parts: []\n")
    (tmp_path / "linux-abox.ttl").write_text("")
    found = CR.discover(str(bundle), _NoExplicit())
    assert found["decomposition"] is None
    assert found["abox"] is None


def test_parent_dir_companion_naming_the_bundle_is_accepted(tmp_path):
    bundle = _mkbundle(tmp_path)
    companion = tmp_path / "zod.decomposition.yaml"
    companion.write_text("parts: []\n")
    found = CR.discover(str(bundle), _NoExplicit())
    assert found["decomposition"] == str(companion)


def test_companion_inside_bundle_dir_is_accepted_regardless_of_name(tmp_path):
    bundle = _mkbundle(tmp_path)
    companion = bundle / "final.decomposition.yaml"
    companion.write_text("parts: []\n")
    found = CR.discover(str(bundle), _NoExplicit())
    assert found["decomposition"] == str(companion)


def test_explicit_argument_always_wins(tmp_path):
    bundle = _mkbundle(tmp_path)
    explicit = tmp_path / "elsewhere.yaml"
    explicit.write_text("parts: []\n")

    class _Explicit(_NoExplicit):
        decomposition = str(explicit)

    found = CR.discover(str(bundle), _Explicit())
    assert found["decomposition"] == str(explicit)


def test_yaml_companions_use_c_loader_when_available(tmp_path):
    import yaml
    if not hasattr(yaml, "CSafeLoader"):
        pytest.skip("libyaml not available")
    path = tmp_path / "d.yaml"
    path.write_text("parts:\n  - name: a\n    kind: module\n")
    seen = {}
    real_load = yaml.load

    def spy(stream, Loader=None):
        seen["loader"] = Loader
        return real_load(stream, Loader=Loader)

    yaml.load = spy
    try:
        out = CR._yaml_load(str(path))
    finally:
        yaml.load = real_load
    assert out == {"parts": [{"name": "a", "kind": "module"}]}
    assert seen["loader"] is yaml.CSafeLoader


def test_load_decomp_parses_through_the_helper(tmp_path):
    path = tmp_path / "d.yaml"
    path.write_text("parts:\n  - name: a\n    kind: module\n")
    out = CR.load_decomp(str(path))
    assert out["n_parts"] == 1
