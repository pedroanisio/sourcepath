"""cbm.py cartogram — the interactive structure map as a first-class report.

Assessment outcome (external-review follow-up): the cartogram belongs in the
reports family as a peer deliverable — not embedded in the dossier PDF. This
suite pins the integration contract, TDD-first:

- the unified CLI routes a ``cartogram`` command;
- no Node is an actionable error (build hint), not a traceback;
- a bundle without ``inventory.jsonld`` is an actionable error;
- a bare L1 bundle is refused with the normalizer's own guidance (run_l3);
- the default output is the standardized timestamped name under
  ``CBM_REPORTS_DIR`` (``<bundle>__cartogram__<UTC>.html``);
- end-to-end: an L3 bundle builds a self-contained standalone HTML that
  actually carries the mapped repository's data.

Run from the repo root:  uv run python -m pytest tests/test_cbm_cartogram.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cbm  # noqa: E402
import cbm_cartogram  # noqa: E402


def _make_l3_bundle(base: Path) -> Path:
    repo = base / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "core.py").write_text(
        "def compute_account_balance():\n    return 42\n")
    (repo / "app.py").write_text(
        "from pkg import core\n\ndef resolve_user():\n"
        "    return core.compute_account_balance()\n")
    (repo / "test_app.py").write_text(
        "import app\n\ndef test_resolve():\n    assert app.resolve_user() == 42\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "fixture"], cwd=repo, check=True)
    out = base / "bundle"
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_l3.py"),
         "--repo", str(repo), "--out", str(out), "--backend", "hash"],
        cwd=REPO_ROOT, check=True, capture_output=True)
    return out


def test_dispatcher_routes_cartogram(capsys):
    assert "cartogram" in cbm.COMMANDS
    rc = cbm.main(["--help"])
    assert rc == 0
    assert "cartogram" in capsys.readouterr().out


def test_missing_node_gives_actionable_error(monkeypatch, tmp_path, capsys):
    (tmp_path / "inventory.jsonld").write_text("{}")
    monkeypatch.setattr(cbm_cartogram.shutil, "which", lambda _: None)
    rc = cbm_cartogram.main([str(tmp_path)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "node" in err.lower()
    assert "Traceback" not in err


def test_missing_inventory_gives_actionable_error(tmp_path, capsys):
    rc = cbm_cartogram.main([str(tmp_path)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "inventory.jsonld" in err
    assert "run_l3" in err


def test_default_output_is_standardized(monkeypatch, tmp_path):
    (tmp_path / "b" / "my-bundle").mkdir(parents=True)
    bundle = tmp_path / "b" / "my-bundle"
    (bundle / "inventory.jsonld").write_text("{}")
    monkeypatch.setenv("CBM_REPORTS_DIR", str(tmp_path / "reports"))
    seen: dict = {}

    def fake_run(cmd, **kw):
        seen.setdefault("cmds", []).append([str(c) for c in cmd])
        return types.SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cbm_cartogram.subprocess, "run", fake_run)
    rc = cbm_cartogram.main([str(bundle)])
    assert rc == 0
    out_arg = seen["cmds"][-1][-1]  # bundler's output path argument
    assert "my-bundle__cartogram__" in out_arg
    assert out_arg.endswith(".html")
    assert str(tmp_path / "reports") in out_arg


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_end_to_end_l3_bundle_builds_standalone(tmp_path):
    bundle = _make_l3_bundle(tmp_path)
    out = tmp_path / "map.html"
    rc = cbm_cartogram.main([str(bundle), "-o", str(out)])
    assert rc == 0
    html = out.read_text(encoding="utf-8")
    assert len(html) > 100_000            # data + D3 + renderer inlined
    assert "pkg/core.py" in html          # the mapped repo's own files
    assert "app.py" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_l1_bundle_is_refused_with_guidance(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("X = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "f"], cwd=repo, check=True)
    out = tmp_path / "bundle"
    subprocess.run(
        [sys.executable, "-m", "codebase_mapper",
         "--repo", str(repo), "--out", str(out)],
        cwd=REPO_ROOT, check=True, capture_output=True)
    rc = cbm_cartogram.main([str(out), "-o", str(tmp_path / "m.html")])
    assert rc != 0
    err = capsys.readouterr().err
    assert "run_l3" in err  # the normalizer's refusal reaches the user
