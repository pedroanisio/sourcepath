"""BL-014 — enriched-bundle runners must register the symbol-xref layer.

run_l3.py / run_l4.py produced bundles with chunks, concepts, and (L4)
enrichment but **zero** cbmxr edges, no xrefs.jsonl, and no l3_10_xrefs
manifest fragment, because only run_xrefs.py registered the symbol_xrefs
plugin. Downstream consumers then read a capability gap where only a
runner-configuration gap existed (doc-ray dossier, 2026-07-11).

Contract under test:
  1. A run_l3.py bundle contains the xref layer by default: xrefs.jsonl,
     an ``l3_10_xrefs`` fragment in run_manifest.json, and at least one
     subclassOf edge for a repo with local inheritance.
  2. ``--no-xrefs`` opts out, and the opt-out is visible (no fragment).
  3. run_l4.py registers the layer by default too (no Ollama needed —
     enrichment degrades gracefully; the xref layer must not).

Run: uv run python -m pytest tests/test_runner_xref_registration.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}

# Local inheritance + a call: guarantees subclassOf and calls edges.
_FILES = {
    "pkg/__init__.py": "",
    "pkg/base.py": (
        "class Base:\n"
        "    def greet(self):\n"
        "        return 'hi'\n"
    ),
    "pkg/sub.py": (
        "from pkg.base import Base\n\n\n"
        "def helper():\n"
        "    return 1\n\n\n"
        "class Sub(Base):\n"
        "    def greet(self):\n"
        "        return helper()\n"
    ),
}


def _mk_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for rel, content in _FILES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       capture_output=True, env=_ENV)
    return repo


def _run(script: str, repo: Path, out: Path, *extra: str) -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / script),
         "--repo", str(repo), "--out", str(out), "--backend", "hash",
         *extra],
        capture_output=True, text=True, cwd=REPO_ROOT, env=_ENV,
    )
    assert proc.returncode == 0, f"{script} failed:\n{proc.stderr[-2000:]}"


def _xref_fragment(bundle: Path) -> dict | None:
    # The xref artifact emitter's fragment key (plugins/symbol_xrefs/artifact.py).
    man = json.loads((bundle / "run_manifest.json").read_text())
    return man.get("extensions", {}).get("l3_50_xrefs_artifact")


@pytest.mark.parametrize("script", ["run_l3.py", "run_l4.py"])
def test_enriched_runner_registers_xrefs_by_default(tmp_path, script):
    repo = _mk_repo(tmp_path)
    out = tmp_path / "bundle"
    _run(script, repo, out)

    assert (out / "xrefs.jsonl").exists(), "xref artifact missing from bundle"
    frag = _xref_fragment(out)
    assert frag is not None, "no l3_10_xrefs fragment in run_manifest.json"

    edges = [json.loads(line)
             for line in (out / "xrefs.jsonl").read_text().splitlines() if line]
    kinds = {e["kind"] for e in edges}
    assert "subclassOf" in kinds, f"no subclassOf edge resolved: {kinds}"
    assert "calls" in kinds, f"no calls edge resolved: {kinds}"

    ttl = (out / "inventory.ttl").read_text()
    assert "cbmxr" in ttl, "no cbmxr triples in the emitted graph"


def test_no_xrefs_flag_opts_out(tmp_path):
    repo = _mk_repo(tmp_path)
    out = tmp_path / "bundle"
    _run("run_l3.py", repo, out, "--no-xrefs")

    assert not (out / "xrefs.jsonl").exists()
    assert _xref_fragment(out) is None
    assert "cbmxr" not in (out / "inventory.ttl").read_text()
