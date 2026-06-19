"""Regression tests for surfacing a file's external / workspace imports (#2).

cbm extracts ``cbm:importsExternal`` (file -> package specifier), but the
serving ``Bundle`` and the ``file_detail`` tool previously exposed only
*internal* file->file edges (``imports_out`` / ``imports_in``). A file's
workspace/third-party dependencies were therefore invisible through the normal
tools — which led an analyst to wrongly conclude a monorepo's cross-package
graph could not be mechanically extracted (the deps were there, just unsurfaced).

These tests pin that external imports are projected onto the Bundle and surfaced
by ``file_detail``, at every layer they pass through.

Run from the repo root:  python -m pytest tests/test_external_imports.py
"""
from __future__ import annotations

import json
from pathlib import Path

from frontend.backend.serving.application import bundle_data as bd
from frontend.backend.serving.application.files import get_file_detail_response


_CTX = {
    "cbm": "https://codebase-mapper.example.org/cbm#",
    "cbmi": "https://codebase-mapper.example.org/cbm/instance#",
}

# external_imports is appended as the final element of the projection tuple, so
# the existing positional indices (e.g. chunks at [7]) stay stable.
_EXTERNAL_IMPORTS_IDX = 12


def _inventory() -> dict:
    """src/a.ts imports src/b.ts internally and two packages externally
    (a scoped workspace-style package and a plain third-party one)."""
    return {
        "@context": _CTX,
        "@graph": [
            {
                "@id": "cbmi:file/src%2Fa.ts",
                "@type": "cbm:File",
                "cbm:path": "src/a.ts",
                "cbm:imports": [{"@id": "cbmi:file/src%2Fb.ts"}],
                "cbm:importsExternal": [
                    {"@id": "cbmi:pkg/@scope%2Fui"},
                    {"@id": "cbmi:pkg/react"},
                ],
            },
            {
                "@id": "cbmi:file/src%2Fb.ts",
                "@type": "cbm:File",
                "cbm:path": "src/b.ts",
            },
        ],
    }


def test_projection_extracts_external_imports(tmp_path: Path):
    inv = tmp_path / "inventory.jsonld"
    inv.write_text(json.dumps(_inventory()))
    projection = bd._project_from_jsonld(inv)
    external = projection[_EXTERNAL_IMPORTS_IDX]
    # package IRIs are decoded back to their specifiers, deduped + sorted
    assert external.get("src/a.ts") == ["@scope/ui", "react"]
    # a file with no external imports does not appear
    assert "src/b.ts" not in external


def test_external_imports_attached_to_loaded_bundle(tmp_path: Path):
    d = tmp_path / "extbundle"
    d.mkdir()
    (d / "run_manifest.json").write_text("{}")
    (d / "inventory.jsonld").write_text(json.dumps(_inventory()))
    b = bd.load_bundle(d)
    assert b.external_imports.get("src/a.ts") == ["@scope/ui", "react"]
    # internal import graph is unchanged and kept separate
    assert b.imports_out.get("src/a.ts") == ["src/b.ts"]


def test_file_detail_surfaces_external_imports(tmp_path: Path, monkeypatch):
    d = tmp_path / "extbundle"
    d.mkdir()
    (d / "run_manifest.json").write_text("{}")
    (d / "inventory.jsonld").write_text(json.dumps(_inventory()))
    monkeypatch.setenv("CBM_BUNDLES_ROOT", str(tmp_path))
    monkeypatch.delenv("CBM_OUTPUT_DIR", raising=False)
    bd._load_bundle_cached.cache_clear()

    resp = get_file_detail_response("src/a.ts", "extbundle")
    assert resp["external_imports"] == ["@scope/ui", "react"]
    assert resp["imports_out"] == ["src/b.ts"]
