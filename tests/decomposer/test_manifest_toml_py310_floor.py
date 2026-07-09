"""Regression — TOML manifest parsing on the declared Python floor (3.10).

``decomposer/evidence.py`` used a bare ``import tomllib`` (stdlib only on
3.11+) inside ``_parse_cargo_toml`` / ``_parse_pyproject_toml``. On Python
3.10 — inside the project's ``requires-python = ">=3.10"`` floor — the
resulting ``ModuleNotFoundError`` was swallowed by ``_read_manifest_deps``'s
per-manifest ``except Exception: continue``, so every Cargo.toml and
pyproject.toml in every bundle was silently dropped: a systemic environment
failure disguised as a per-blob parse skip (PALS's Law: silent degradation).

The fix mirrors the idiom used everywhere else in the repo (module-level
``tomllib``-with-``tomli``-fallback; ``tomli`` is already a declared
dependency for ``python_version < '3.11'``). This suite pins the fallback
branch on *any* interpreter by blocking stdlib ``tomllib`` and reloading the
module — otherwise the branch is only exercised on 3.10 machines and could
regress unnoticed on 3.11+ dev environments.

Run from the repo root:  python -m pytest tests/decomposer/test_manifest_toml_py310_floor.py
"""
from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

import decomposer.evidence as evidence


def _write_bundle(tmp_path: Path, files: dict[str, str]) -> Path:
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    graph = []
    for path, content in files.items():
        sha = hashlib.sha256(content.encode()).hexdigest()
        (blobs / sha).write_text(content)
        graph.append({"cbm:path": path, "cbm:contentSha256": sha})
    (tmp_path / "inventory.jsonld").write_text(json.dumps({"@graph": graph}))
    return tmp_path


@pytest.fixture
def evidence_without_stdlib_tomllib():
    """Reload decomposer.evidence with stdlib ``tomllib`` blocked, forcing
    the ``tomli`` fallback branch; restore the real module state afterwards."""
    pytest.importorskip(
        "tomli",
        reason="tomli absent (only guaranteed as a dependency below 3.11); "
               "cannot simulate the 3.10 floor on this interpreter",
    )
    saved = sys.modules.get("tomllib")
    sys.modules["tomllib"] = None  # import -> ModuleNotFoundError, as on 3.10
    try:
        yield importlib.reload(evidence)
    finally:
        if saved is not None:
            sys.modules["tomllib"] = saved
        else:
            sys.modules.pop("tomllib", None)
        importlib.reload(evidence)


def test_module_imports_without_stdlib_tomllib(evidence_without_stdlib_tomllib):
    # The import itself must not require Python 3.11's stdlib tomllib.
    assert evidence_without_stdlib_tomllib.tomllib is not None


def test_cargo_manifest_parses_without_stdlib_tomllib(
    evidence_without_stdlib_tomllib, tmp_path,
):
    ev = evidence_without_stdlib_tomllib
    _write_bundle(tmp_path, {"Cargo.toml": (
        '[package]\nname = "core"\n'
        '[dependencies]\nserde = "1"\n'
    )})
    deps = ev._read_manifest_deps(tmp_path)
    assert deps["Cargo.toml"]["name"] == "core"
    assert deps["Cargo.toml"]["deps"] == ["serde"]


def test_pyproject_manifest_parses_without_stdlib_tomllib(
    evidence_without_stdlib_tomllib, tmp_path,
):
    ev = evidence_without_stdlib_tomllib
    _write_bundle(tmp_path, {"pkg/pyproject.toml": (
        '[project]\nname = "pkg"\ndependencies = ["boto3>=1.28"]\n'
    )})
    deps = ev._read_manifest_deps(tmp_path)
    assert deps["pkg/pyproject.toml"]["name"] == "pkg"
    assert deps["pkg/pyproject.toml"]["deps"] == ["boto3"]


def test_parser_unavailability_is_loud_not_a_silent_skip(tmp_path):
    """The original defect shape: if no TOML parser can be imported at all,
    the failure must surface at module import — never as a silently empty
    manifest_deps. Blocking both tomllib and tomli must make the reload
    raise, not degrade."""
    saved_tomllib = sys.modules.get("tomllib")
    saved_tomli = sys.modules.get("tomli")
    sys.modules["tomllib"] = None
    sys.modules["tomli"] = None
    try:
        with pytest.raises(ImportError):
            importlib.reload(evidence)
    finally:
        for name, saved in (("tomllib", saved_tomllib), ("tomli", saved_tomli)):
            if saved is not None:
                sys.modules[name] = saved
            else:
                sys.modules.pop(name, None)
        importlib.reload(evidence)
