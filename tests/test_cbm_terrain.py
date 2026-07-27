"""cbm_terrain — general SourcePath terrain-map builder.

Pins the pure pipeline: directory roll-up, edge aggregation with disclosed
drops, semantic-distance annotation, frontier derivation, payload assembly
(projection injectable — tests never run t-SNE), and template rendering
that leaves no placeholder unresolved. Bundle I/O is a thin seam tested by
an integration smoke that skips when no bundle exists under _tmp/.

Run from the repo root:  python -m pytest tests/test_cbm_terrain.py
"""
from __future__ import annotations

import json
import re

import numpy as np
import pytest

from scripts.cbm_terrain import (
    assemble_payload,
    auto_segments,
    group_of,
    render_html,
    rollup,
)


# ---------------------------------------------------------------- roll-up

def test_group_of_caps_depth():
    assert group_of("drivers/gpu/drm/amd/display", 4) == "drivers/gpu/drm/amd"
    assert group_of("drivers/gpu", 4) == "drivers/gpu"
    assert group_of("(root)", 4) == "(root)"


def test_rollup_aggregates_files_and_edges():
    files_of_module = {
        "a/b/c/d/e": ["f1", "f2"], "a/b/c/d": ["f3"], "x": ["f4"],
    }
    edge_weight = {
        ("a/b/c/d/e", "x"): 2, ("a/b/c/d", "x"): 3,
        ("a/b/c/d/e", "a/b/c/d"): 5,          # becomes intra-group -> dropped
    }
    gfiles, gmods, gedges = rollup(files_of_module, edge_weight, 4)
    assert gfiles == {"a/b/c/d": 3, "x": 1}
    assert gmods == {"a/b/c/d": 2, "x": 1}
    assert gedges == {("a/b/c/d", "x"): 5}    # 2 + 3 summed, intra dropped


def test_auto_segments_prefers_depth_under_cap():
    mods = {f"top/mid{i}/leaf{j}": ["f"] for i in range(3) for j in range(4)}
    # depth 3 -> 12 groups; depth 2 -> 3 groups; cap 5 forces depth 2
    assert auto_segments(mods, cap=5) == 2
    assert auto_segments(mods, cap=100) >= 3   # full detail fits under cap


# ---------------------------------------------------------------- payload

def _synthetic(n_groups=6, chunks_per=4):
    rng = np.random.RandomState(0)
    files_of_module, edge_weight = {}, {}
    ids, vecs = [], []
    for g in range(n_groups):
        mod = f"zone{g % 2}/pkg{g}"
        files_of_module[mod] = [f"{mod}/f{k}.c" for k in range(2)]
        base = rng.rand(384)
        for k in range(chunks_per):
            ids.append(f"{mod}/f0.c#function:fn{k}:L1-L9:b0-9")
            vecs.append(base + rng.rand(384) * 0.01)
    for g in range(n_groups - 1):
        edge_weight[(f"zone{g % 2}/pkg{g}", f"zone{(g + 1) % 2}/pkg{g + 1}")] = g + 1
    return files_of_module, edge_weight, np.array(ids), np.array(vecs, dtype=np.float32)


def _grid_projection(means):
    """Deterministic stand-in for t-SNE: spread points on a diagonal."""
    n = len(means)
    t = np.linspace(0.05, 0.95, n)
    return np.column_stack([t, t[::-1]])


def _payload(**over):
    fom, ew, ids, vecs = _synthetic()
    kw = dict(files_of_module=fom, edge_weight=ew, ids=ids, vectors=vecs,
              manifest={"commit_sha": "abc123def456789", "generated_at": "2026-07-10T00:00:00Z"},
              repo="synthetic", max_segments=4, grid=(40, 28), frontier_grid=(20, 14),
              roads=10, seed=42, project_fn=_grid_projection)
    kw.update(over)
    return assemble_payload(**kw)


def test_payload_shape_and_meta():
    p = _payload()
    assert p["meta"]["repo"] == "synthetic"
    assert p["meta"]["commit"] == "abc123def456"          # 12 chars
    assert p["meta"]["gw"] == 40 and p["meta"]["gh"] == 28
    assert p["meta"]["layers"] >= 1
    assert len(p["points"]) == 6
    assert p["meta"]["chunks"] == 24
    # grid decodes to gw*gh bytes
    import base64
    assert len(base64.b64decode(p["grid_b64"])) == 40 * 28


def test_edges_carry_semantic_distance_and_disclose_drops():
    p = _payload()
    for e in p["graph"]["edges"]:
        assert len(e) == 4 and 0.0 <= e[3] <= 2.0
    assert p["graph"]["dropped_edges"] == 0
    # chart-less module: give one module no chunks -> its edges are dropped, disclosed
    fom, ew, ids, vecs = _synthetic()
    fom["zone0/chartless"] = ["zone0/chartless/f.c"]
    ew[("zone0/chartless", "zone0/pkg0")] = 9
    p2 = assemble_payload(files_of_module=fom, edge_weight=ew, ids=ids, vectors=vecs,
                          manifest={}, repo="s", max_segments=4, grid=(40, 28),
                          frontier_grid=(20, 14), roads=10, seed=42,
                          project_fn=_grid_projection)
    assert p2["graph"]["dropped_edges"] == 1


def test_points_are_deterministic_for_fixed_seed():
    a, b = _payload(), _payload()
    assert json.dumps(a["points"]) == json.dumps(b["points"])


def test_frontiers_exist_between_zones():
    p = _payload()
    assert len(p["frontiers"]) > 0
    for seg in p["frontiers"]:
        assert len(seg) == 4
        assert all(0.0 <= v <= 1.0 for v in seg)


# ---------------------------------------------------------------- template

def test_render_html_resolves_every_placeholder():
    p = _payload()
    html = render_html(p, repo_title="Synthetic")
    assert re.findall(r"__[A-Z]+__", html) == []
    assert "Code Terrain: Synthetic" in html
    assert '"repo":"synthetic"' in html.replace(" ", "")


def test_render_html_footer_discloses_scale_and_drops():
    p = _payload()
    html = render_html(p, repo_title="Synthetic")
    assert "rolled to" in html or "full depth" in html
    assert str(len(p["graph"]["edges"])) in html


# ---------------------------------------------------------------- CLI seam

def test_cli_rejects_missing_bundle(tmp_path):
    from scripts.cbm_terrain import main
    with pytest.raises(SystemExit) as exc:
        main(["--bundle", str(tmp_path / "nope")])
    assert exc.value.code == 2


def _stub_bundle(tmp_path, monkeypatch):
    """Minimal bundle + stubbed loaders: exercises main()'s output routing
    without running the real bundle loader or t-SNE."""
    from scripts import cbm_terrain as T

    bundle = tmp_path / "graphite"
    bundle.mkdir()
    (bundle / "run_manifest.json").write_text("{}")
    fom, ew, ids, vecs = _synthetic()

    class _MG:
        files_of_module, edge_weight = fom, ew

    monkeypatch.setattr(
        T, "_load_module_graph",
        lambda d: (_MG(), {"repo_name": "graphite", "commit_sha": "abc123def456789",
                           "generated_at": "2026-07-10T00:00:00Z"}))
    monkeypatch.setattr(T.np, "load",
                        lambda *a, **k: {"ids": ids, "vectors": vecs})
    return bundle


def test_cli_default_out_lands_in_reports_dir_not_the_bundle(tmp_path, monkeypatch):
    """Regression: the map used to be written INTO the bundle directory,
    mixing a derived render in with the measured artifacts."""
    from scripts.cbm_terrain import main

    bundle = _stub_bundle(tmp_path, monkeypatch)
    reports = tmp_path / "reports"
    monkeypatch.setenv("CBM_REPORTS_DIR", str(reports))

    assert main(["--bundle", str(bundle)]) == 0
    hits = list(reports.glob("graphite__terrain__*.html"))
    assert len(hits) == 1
    assert list(bundle.glob("*.html")) == []


def test_cli_style_selects_the_report_kind(tmp_path, monkeypatch):
    from scripts.cbm_terrain import main

    bundle = _stub_bundle(tmp_path, monkeypatch)
    reports = tmp_path / "reports"
    monkeypatch.setenv("CBM_REPORTS_DIR", str(reports))

    assert main(["--bundle", str(bundle), "--style", "tolkien"]) == 0
    assert len(list(reports.glob("graphite__tolkien__*.html"))) == 1


def test_cli_explicit_out_still_wins(tmp_path, monkeypatch):
    from scripts.cbm_terrain import main

    bundle = _stub_bundle(tmp_path, monkeypatch)
    monkeypatch.setenv("CBM_REPORTS_DIR", str(tmp_path / "reports"))
    mine = tmp_path / "elsewhere" / "mine.html"

    assert main(["--bundle", str(bundle), "--out", str(mine)]) == 0
    assert mine.is_file()
    assert not (tmp_path / "reports").exists()
