"""RED (round-3 review: 2,528-step / 13,445-file scale run against Airflow).

#1 edge-typing defect, generalized beyond Cargo:
* manifest parsing recognizes pyproject.toml (PEP 621 + Poetry) and
  package.json alongside Cargo.toml, so `detect_crates`/`test_only_module_edges`
  -- already manifest-shape-agnostic -- exclude cross-distribution dev/test-only
  edges for Python and npm monorepos, not just Cargo workspaces.
* module edges to/from a test-role module are excluded from SCC/build-order
  math even with *no* manifest at all -- the "test-tree imports treated as
  hard edges" half of the defect, independent of distribution boundaries.

Out of scope for this round (see PR notes): TYPE_CHECKING-guarded imports and
packaging entry-point/plugin edges. Both would need the core inspector to
capture a signal it doesn't today (grep -r TYPE_CHECKING codebase_mapper/ is
empty) -- fabricating that detection here without evidence it's wired through
would be exactly the kind of unverified claim PALS's Law rules out.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from decomposer.crates import build_crate_parts, detect_crates
from decomposer.crates import test_only_module_edges as _test_only_module_edges
from decomposer.decompose import _crate_names_by_module, _prod_adjacency
from decomposer.evidence import EvidenceGraph, _read_manifest_deps
from decomposer.metrics import cycles
from decomposer.parts import build_module_graph, build_module_parts

# ── _read_manifest_deps: real blob parsing, all three manifest shapes ─────────

def _write_bundle(tmp_path: Path, files: dict[str, str]) -> Path:
    """A minimal bundle dir: inventory.jsonld + content-addressed blobs, one
    node per manifest path, matching what _read_manifest_deps actually reads."""
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    graph = []
    for path, content in files.items():
        sha = hashlib.sha256(content.encode()).hexdigest()
        (blobs / sha).write_text(content)
        graph.append({"cbm:path": path, "cbm:contentSha256": sha})
    (tmp_path / "inventory.jsonld").write_text(json.dumps({"@graph": graph}))
    return tmp_path


def test_cargo_toml_parsing_is_unchanged(tmp_path):
    _write_bundle(tmp_path, {"Cargo.toml": (
        '[package]\nname = "core"\n'
        '[dependencies]\nserde = "1"\n'
        '[dev-dependencies]\nhelper = { path = "../helper" }\n'
    )})
    deps = _read_manifest_deps(tmp_path)
    assert deps["Cargo.toml"] == {
        "name": "core", "deps": ["serde"], "dev_deps": ["helper"],
        "workspace_members": [], "manifest_type": "cargo",
    }


def test_pyproject_pep621(tmp_path):
    _write_bundle(tmp_path, {"providers/amazon/pyproject.toml": (
        '[project]\nname = "apache-airflow-providers-amazon"\n'
        'dependencies = ["apache-airflow-core>=2.9", "boto3>=1.28"]\n'
        '[project.optional-dependencies]\n'
        'devel = ["apache-airflow-devel-common", "pytest"]\n'
    )})
    deps = _read_manifest_deps(tmp_path)
    info = deps["providers/amazon/pyproject.toml"]
    assert info["name"] == "apache-airflow-providers-amazon"
    assert set(info["deps"]) == {"apache-airflow-core", "boto3"}
    assert set(info["dev_deps"]) == {"apache-airflow-devel-common", "pytest"}


def test_pyproject_poetry_style(tmp_path):
    _write_bundle(tmp_path, {"pyproject.toml": (
        '[tool.poetry]\nname = "helper"\n'
        '[tool.poetry.dependencies]\npython = "^3.11"\ncore = { path = "../core" }\n'
        '[tool.poetry.group.dev.dependencies]\npytest = "^8"\n'
    )})
    deps = _read_manifest_deps(tmp_path)
    info = deps["pyproject.toml"]
    assert info["name"] == "helper"
    assert info["deps"] == ["core"]   # "python" is never a real dependency edge
    assert info["dev_deps"] == ["pytest"]


def test_package_json_parsing(tmp_path):
    _write_bundle(tmp_path, {"package.json": json.dumps({
        "name": "ui",
        "dependencies": {"react": "^18.0.0"},
        "devDependencies": {"jest": "^29.0.0"},
    })})
    deps = _read_manifest_deps(tmp_path)
    info = deps["package.json"]
    assert info["name"] == "ui"
    assert info["deps"] == ["react"]
    assert info["dev_deps"] == ["jest"]


def test_malformed_pyproject_is_skipped_not_fatal(tmp_path):
    _write_bundle(tmp_path, {"broken/pyproject.toml": "this is not [ valid toml"})
    assert _read_manifest_deps(tmp_path) == {}


# ── end-to-end: Python monorepo dev-only edge excluded from SCC math ──────────

def _ev(file_specs, imports_out=None, manifest_deps=None):
    files = [
        {"path": p, "type": t, "language": lang, "size": 1, "uri": f"urn:{p}"}
        for (p, t, lang) in file_specs
    ]
    imports_out = imports_out or {}
    imports_in: dict[str, list[str]] = {}
    for src, tgts in imports_out.items():
        for t in tgts:
            imports_in.setdefault(t, []).append(src)
    return EvidenceGraph(
        bundle_dir=Path("."), manifest={}, files=files,
        file_by_path={f["path"]: f for f in files},
        imports_out=imports_out, imports_in=imports_in,
        external_imports={}, tests_for_subject={}, subjects_for_test={},
        chunks=[], chunks_by_file={}, xrefs=[],
        concepts={}, per_path_concepts={}, collections={},
        file_summaries={}, schema_purposes={}, phases={},
        manifest_deps=manifest_deps or {},
    )


# core provider imports devel-common only for its test suite (devel is a dev/
# optional dependency, never listed under core deps); devel-common imports
# core back for real (it wraps core's test harness) -- the classic dev-cycle,
# Python-flavored.
_SPECS = [
    ("providers/amazon/pyproject.toml", "dependency_manifest", None),
    ("providers/amazon/src/hook.py", "source_code", "python"),
    ("devel-common/pyproject.toml", "dependency_manifest", None),
    ("devel-common/tests_common/fixtures.py", "source_code", "python"),
]
_IMPORTS = {
    "providers/amazon/src/hook.py": ["devel-common/tests_common/fixtures.py"],
    "devel-common/tests_common/fixtures.py": ["providers/amazon/src/hook.py"],
}
_MANIFESTS = {
    "providers/amazon/pyproject.toml": {
        "name": "apache-airflow-providers-amazon", "deps": [],
        "dev_deps": ["apache-airflow-devel-common"], "workspace_members": [],
        "manifest_type": "python",
    },
    "devel-common/pyproject.toml": {
        "name": "apache-airflow-devel-common",
        "deps": ["apache-airflow-providers-amazon"], "dev_deps": [],
        "workspace_members": [], "manifest_type": "python",
    },
}


def test_pyproject_distribution_boundary_excludes_dev_cycle():
    ev = _ev(_SPECS, _IMPORTS, _MANIFESTS)
    mg = build_module_graph(ev)
    cm = detect_crates(ev)
    assert set(cm.crates) == {"providers/amazon", "devel-common"}
    test_edges = _test_only_module_edges(ev, mg, cm)
    assert ("providers/amazon/src", "devel-common/tests_common") in test_edges
    assert ("devel-common/tests_common", "providers/amazon/src") not in test_edges


def test_pyproject_distribution_scc_dissolves():
    ev = _ev(_SPECS, _IMPORTS, _MANIFESTS)
    mg = build_module_graph(ev)
    adj = _prod_adjacency(ev, mg)
    assert "devel-common/tests_common" not in adj.get("providers/amazon/src", [])
    assert cycles(list(mg.files_of_module), adj) == []


def test_pyproject_module_parts_stamped_with_distribution():
    ev = _ev(_SPECS, _IMPORTS, _MANIFESTS)
    mg = build_module_graph(ev)
    cm = detect_crates(ev)
    test_edges = _test_only_module_edges(ev, mg, cm)
    crate_of_module = _crate_names_by_module(mg, cm)
    parts = build_module_parts(ev, mg, set(), crate_of_module, test_edges)
    amazon_mod = next(p for p in parts if p.id == "module:providers/amazon/src")
    assert amazon_mod.metrics.get("crate") == "apache-airflow-providers-amazon"


def test_pyproject_distributions_become_parts_with_non_crate_id():
    ev = _ev(_SPECS, _IMPORTS, _MANIFESTS)
    mg = build_module_graph(ev)
    cm = detect_crates(ev)
    parts = build_crate_parts(ev, mg, cm)
    ids = {p.id for p in parts}
    # Cargo-flavored wording must not leak onto Python/npm distributions.
    assert "dist:apache-airflow-providers-amazon" in ids
    assert "dist:apache-airflow-devel-common" in ids
    assert not any(i.startswith("crate:") for i in ids)


# ── #1b: test-role modules excluded from SCC math with *no* manifest at all ───

def test_test_role_module_edge_excluded_without_any_manifest():
    # No Cargo/pyproject/package.json anywhere -- pure role-based signal.
    ev = _ev([
        ("app/core.py", "source_code", "python"),
        ("app/tests/helpers.py", "test_code", "python"),
    ], {
        "app/core.py": ["app/tests/helpers.py"],
        "app/tests/helpers.py": ["app/core.py"],
    })
    mg = build_module_graph(ev)
    adj = _prod_adjacency(ev, mg)
    assert "app/tests" not in adj.get("app", [])
    assert cycles(list(mg.files_of_module), adj) == []
