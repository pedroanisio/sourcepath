"""RED (round-3 review #4): Alembic-shaped revision chains.

`migrations/versions` (or any `*/versions/*.py` tree) is strictly ordered by
`revision`/`down_revision` markers that never show up as import edges --
Alembic revisions reference each other by string id in a metadata table, not
by `import`. Scoped to the Alembic shape specifically (the review's own
example, "127 Alembic files, strictly revision-chain-ordered"); other
migration frameworks (Django, Flyway, ...) use a different mechanism and are
out of scope here rather than guessed at.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from decomposer.decompose import decompose_evidence
from decomposer.evidence import EvidenceGraph, _read_revision_markers
from decomposer.migrations import _build_chain, revision_orderings
from decomposer.parts import build_module_graph

# ── _read_revision_markers: real blob parsing ──────────────────────────────

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


def test_reads_revision_and_down_revision_markers(tmp_path):
    _write_bundle(tmp_path, {
        "migrations/versions/0001_init.py": (
            "revision = '0001'\ndown_revision = None\n"),
        "migrations/versions/0002_add_col.py": (
            "revision: str = '0002'\ndown_revision = '0001'\n"),
        "migrations/versions/__init__.py": "",
        "app/core.py": "import os\n",
    })
    markers = _read_revision_markers(tmp_path)
    assert markers["migrations/versions/0001_init.py"] == {
        "revision": "0001", "down_revision": None}
    assert markers["migrations/versions/0002_add_col.py"] == {
        "revision": "0002", "down_revision": "0001"}
    # Not a versions/ path, and no revision marker either -- both excluded.
    assert "app/core.py" not in markers
    assert "migrations/versions/__init__.py" not in markers


# ── _build_chain: pure ordering logic ──────────────────────────────────────

def test_build_chain_linear_three_files():
    markers = {
        "a.py": {"revision": "r1", "down_revision": None},
        "b.py": {"revision": "r2", "down_revision": "r1"},
        "c.py": {"revision": "r3", "down_revision": "r2"},
    }
    assert _build_chain(markers) == ["a.py", "b.py", "c.py"]


def test_build_chain_none_on_multiple_heads():
    markers = {
        "a.py": {"revision": "r1", "down_revision": None},
        "b.py": {"revision": "r2", "down_revision": None},   # two roots
    }
    assert _build_chain(markers) is None


def test_build_chain_none_on_branch_point():
    markers = {
        "a.py": {"revision": "r1", "down_revision": None},
        "b.py": {"revision": "r2", "down_revision": "r1"},
        "c.py": {"revision": "r3", "down_revision": "r1"},   # b and c both fork from a
    }
    assert _build_chain(markers) is None


def test_build_chain_none_on_dangling_down_revision():
    markers = {
        "a.py": {"revision": "r1", "down_revision": "missing"},
    }
    assert _build_chain(markers) is None


def test_build_chain_none_on_duplicate_revision_id():
    markers = {
        "a.py": {"revision": "r1", "down_revision": None},
        "b.py": {"revision": "r1", "down_revision": None},
    }
    assert _build_chain(markers) is None


# ── end-to-end: decompose_evidence() emits file_orderings ─────────────────

def _ev(revision_chains, files=None):
    files = files or [
        {"path": p, "type": "source_code", "language": "python", "size": 1, "uri": f"urn:{p}"}
        for p in revision_chains
    ]
    return EvidenceGraph(
        bundle_dir=Path("."), manifest={}, files=files,
        file_by_path={f["path"]: f for f in files},
        imports_out={}, imports_in={},
        external_imports={}, tests_for_subject={}, subjects_for_test={},
        chunks=[], chunks_by_file={}, xrefs=[],
        concepts={}, per_path_concepts={}, collections={},
        file_summaries={}, schema_purposes={}, phases={},
        revision_chains=revision_chains,
    )


def test_revision_orderings_emits_entry_for_clean_chain():
    revision_chains = {
        "migrations/versions/0001_init.py": {"revision": "0001", "down_revision": None},
        "migrations/versions/0002_add_col.py": {"revision": "0002", "down_revision": "0001"},
    }
    ev = _ev(revision_chains)
    mg = build_module_graph(ev)
    orderings = revision_orderings(ev, mg)
    assert len(orderings) == 1
    entry = orderings[0]
    assert entry["part"] == "module:migrations/versions"
    assert entry["file_order"] == [
        "migrations/versions/0001_init.py", "migrations/versions/0002_add_col.py"]


def test_revision_orderings_skips_unresolvable_chain():
    revision_chains = {
        "migrations/versions/0001_init.py": {"revision": "0001", "down_revision": None},
        "migrations/versions/0002_alt.py": {"revision": "0002", "down_revision": None},
    }
    ev = _ev(revision_chains)
    mg = build_module_graph(ev)
    assert revision_orderings(ev, mg) == []


def test_decompose_evidence_surfaces_file_orderings(tmp_path, monkeypatch):
    revision_chains = {
        "migrations/versions/0001_init.py": {"revision": "0001", "down_revision": None},
        "migrations/versions/0002_add_col.py": {"revision": "0002", "down_revision": "0001"},
    }
    files = [
        {"path": p, "type": "source_code", "language": "python", "size": 1, "uri": f"urn:{p}"}
        for p in revision_chains
    ] + [{"path": "app/core.py", "type": "source_code", "language": "python",
          "size": 1, "uri": "urn:app/core.py"}]
    ev = _ev(revision_chains, files=files)
    decomp = decompose_evidence(ev)
    assert decomp.file_orderings == [{
        "part": "module:migrations/versions",
        "file_order": ["migrations/versions/0001_init.py",
                       "migrations/versions/0002_add_col.py"],
        "note": "topological order from Alembic revision/down_revision markers",
    }]
