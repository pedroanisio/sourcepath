#!/usr/bin/env python3
"""cbm_terrain.py — SourcePath 3D code-terrain map from any bundle.

Reads a codebase-mapper bundle and emits one self-contained HTML file
(WebGL2, no external assets): a semantic terrain where position is a t-SNE
projection of per-directory mean chunk embeddings, elevation is chunk
density, and the L1 import graph supplies roads, build-tide layers, cycles,
impact floods, path tracing, and stress (strong-import × semantically-
distant) fault lines.

Epistemics (PALS's Law): roads, layers, cycles, and floods traverse the
mechanical L1 graph only; positions, frontiers, and stress scores are
derived views and the rendered page discloses them as such, together with
every truncation (roll-up depth, road cap, dropped chart-less edges).

Usage:
    python scripts/cbm_terrain.py --bundle _tmp/<repo> [--out map.html]
        [--max-segments N | 0=auto] [--max-points 4000] [--roads 600]
        [--grid 280x196] [--seed 42] [--title <Display Name>]

Determinism: the projection is seeded (default 42) so a repo's geography
stays recognizable across regenerations — the map's cognitive value is
spatial memory, so keep the seed fixed per repo.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decomposer.metrics import build_order, cycles as graph_cycles  # noqa: E402

TEMPLATE_PATH = Path(__file__).parent / "site_assets" / "terrain_template.html"
ROOT = "(root)"


# --------------------------------------------------------------------------
# Roll-up (pure)
# --------------------------------------------------------------------------


def group_of(module: str, max_segments: int) -> str:
    """A module's component group: its path capped at ``max_segments``."""
    return "/".join(module.split("/")[:max_segments])


def rollup(
    files_of_module: dict[str, list[str]],
    edge_weight: dict[tuple[str, str], int],
    max_segments: int,
) -> tuple[dict[str, int], dict[str, int], dict[tuple[str, str], int]]:
    """Aggregate directory modules and their edges into component groups.

    Returns (files-per-group, modules-per-group, summed inter-group edges);
    edges that become intra-group are dropped — they are cohesion, not
    coupling, at the rendered granularity.
    """
    gfiles: dict[str, int] = defaultdict(int)
    gmods: dict[str, int] = defaultdict(int)
    for m, fl in files_of_module.items():
        g = group_of(m, max_segments)
        gfiles[g] += len(fl)
        gmods[g] += 1
    gedges: dict[tuple[str, str], int] = defaultdict(int)
    for (s, t), w in edge_weight.items():
        gs, gt = group_of(s, max_segments), group_of(t, max_segments)
        if gs != gt:
            gedges[(gs, gt)] += w
    return dict(gfiles), dict(gmods), dict(gedges)


def auto_segments(files_of_module: dict[str, Any], cap: int) -> int:
    """Deepest roll-up (most detail) whose group count fits under ``cap``."""
    for d in range(8, 1, -1):
        if len({group_of(m, d) for m in files_of_module}) <= cap:
            return d
    return 1


# --------------------------------------------------------------------------
# Payload assembly (pure given inputs; projection injectable)
# --------------------------------------------------------------------------


def _module_of(path: str) -> str:
    i = path.rfind("/")
    return path[:i] if i > 0 else ROOT


def _tsne(seed: int):
    def project(means: np.ndarray) -> np.ndarray:
        from sklearn.manifold import TSNE
        perp = min(40, max(5, len(means) // 100))
        return TSNE(n_components=2, perplexity=perp, init="pca",
                    random_state=seed, max_iter=1000).fit_transform(means)
    return project


def assemble_payload(
    *,
    files_of_module: dict[str, list[str]],
    edge_weight: dict[tuple[str, str], int],
    ids: np.ndarray,
    vectors: np.ndarray,
    manifest: dict[str, Any],
    repo: str,
    max_segments: int,
    grid: tuple[int, int],
    frontier_grid: tuple[int, int],
    roads: int,
    seed: int,
    project_fn: Callable[[np.ndarray], np.ndarray] | None = None,
) -> dict[str, Any]:
    gfiles, gmods, gedges = rollup(files_of_module, edge_weight, max_segments)
    groups = sorted(gfiles)
    gadj: dict[str, list[str]] = defaultdict(list)
    for (s, t) in gedges:
        gadj[s].append(t)
    layers = build_order(groups, gadj)
    layer_of = {g: i for i, lay in enumerate(layers) for g in lay}
    cyc_groups = {g for c in graph_cycles(groups, gadj) for g in c}

    rows: dict[str, list[int]] = defaultdict(list)
    for i, cid in enumerate(ids):
        rows[group_of(_module_of(str(cid).split("#", 1)[0]), max_segments)].append(i)
    charted = [g for g in groups if g in rows]
    means = np.stack([vectors[rows[g]].mean(axis=0) for g in charted]).astype(np.float64)
    means /= np.linalg.norm(means, axis=1, keepdims=True).clip(1e-9)
    weights = np.array([len(rows[g]) for g in charted], dtype=float)

    xy = (project_fn or _tsne(seed))(means)
    xy = np.asarray(xy, dtype=float)
    xy -= xy.min(axis=0)
    span = xy.max(axis=0)
    xy /= np.where(span > 0, span, 1.0)

    gw, gh = grid
    dens = np.zeros((gh, gw))
    gx = np.clip((xy[:, 0] * (gw - 1)).astype(int), 0, gw - 1)
    gy = np.clip((xy[:, 1] * (gh - 1)).astype(int), 0, gh - 1)
    for i in range(len(charted)):
        dens[gy[i], gx[i]] += weights[i]
    from scipy.ndimage import gaussian_filter
    dens = gaussian_filter(dens, sigma=max(1.5, min(gw, gh) / 50))
    peak = dens.max() or 1.0
    h8 = (np.power(dens / peak, 0.42) * 255).astype(np.uint8)

    gidx = {g: i for i, g in enumerate(charted)}
    edges, dropped = [], 0
    for (s, t), w in gedges.items():
        if s in gidx and t in gidx:
            a, b = gidx[s], gidx[t]
            sd = float(1.0 - float(np.dot(means[a], means[b])))
            edges.append([a, b, int(w), round(sd, 4)])
        else:
            dropped += 1
    edges.sort(key=lambda e: (e[0], e[1]))
    road_idx = sorted(range(len(edges)), key=lambda i: -edges[i][2])[:roads]

    zones = sorted({g.split("/")[0] for g in charted})
    zid = np.array([zones.index(g.split("/")[0]) for g in charted])
    segs = _frontier_segments(xy, zid, frontier_grid)

    backend = (manifest.get("extensions", {})
               .get("l2_40_embeddings_artifact", {}).get("backend", {}))
    n_dirs = len(files_of_module)
    return {
        "meta": {
            "repo": repo,
            "commit": str(manifest.get("commit_sha", ""))[:12],
            "generated_at": manifest.get("generated_at", ""),
            "chunks": int(weights.sum()),
            "modules": len(charted),
            "gw": gw, "gh": gh,
            "layers": len(layers),
            "model": backend.get("name", "bundle embedding model"),
            "method": ("injected projection" if project_fn
                       else f"t-SNE perplexity={min(40, max(5, len(charted) // 100))} "
                            f"seed={seed}"),
            "rollup": {"max_segments": max_segments, "n_dirs": n_dirs,
                       "n_groups": len(groups), "n_charted": len(charted)},
        },
        "grid_b64": base64.b64encode(h8.tobytes()).decode(),
        "points": [
            {"m": g, "x": round(float(xy[i, 0]), 4), "y": round(float(xy[i, 1]), 4),
             "c": int(weights[i]), "f": gfiles[g],
             "l": layer_of.get(g, -1), "z": g.split("/")[0],
             "cy": g in cyc_groups}
            for i, g in enumerate(charted)
        ],
        "graph": {"edges": edges, "roads": road_idx, "dropped_edges": dropped},
        "frontiers": segs,
    }


def _frontier_segments(xy: np.ndarray, zid: np.ndarray,
                       frontier_grid: tuple[int, int]) -> list[list[float]]:
    from scipy.spatial import cKDTree
    fw, fh = frontier_grid
    mgx, mgy = np.meshgrid(np.linspace(0, 1, fw), np.linspace(0, 1, fh))
    cells = np.column_stack([mgx.ravel(), mgy.ravel()])
    _, nearest = cKDTree(xy).query(cells)
    terr = zid[nearest].reshape(fh, fw)
    segs: list[list[float]] = []
    for j in range(fh):
        for i in range(fw):
            if i + 1 < fw and terr[j, i] != terr[j, i + 1]:
                x = (i + 0.5) / (fw - 1)
                segs.append([round(x, 4), round(max(0, j - 0.5) / (fh - 1), 4),
                             round(x, 4), round(min(fh - 1, j + 0.5) / (fh - 1), 4)])
            if j + 1 < fh and terr[j, i] != terr[j + 1, i]:
                y = (j + 0.5) / (fh - 1)
                segs.append([round(max(0, i - 0.5) / (fw - 1), 4), round(y, 4),
                             round(min(fw - 1, i + 0.5) / (fw - 1), 4), round(y, 4)])
    return segs


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_html(payload: dict[str, Any], repo_title: str,
                template_text: str | None = None) -> str:
    html = template_text if template_text is not None \
        else TEMPLATE_PATH.read_text()
    m = payload["meta"]
    r = m["rollup"]
    n_edges = len(payload["graph"]["edges"])
    if r["n_groups"] < r["n_dirs"]:
        rollup_txt = (f"<strong>Scale:</strong> directories rolled to "
                      f"≤ {r['max_segments']} path segments "
                      f"({r['n_dirs']:,} code dirs → {r['n_groups']:,} groups; "
                      f"{r['n_charted']:,} charted with parsed chunks)")
    else:
        rollup_txt = (f"<strong>Scale:</strong> directories charted at full depth "
                      f"({r['n_charted']:,} of {r['n_dirs']:,} carry parsed chunks)")
    html = (html
            .replace("__DATA__", json.dumps(payload, separators=(",", ":")))
            .replace("__REPONAME__", m["repo"])
            .replace("__REPO__", repo_title)
            .replace("__COMMIT__", m["commit"] or "unknown")
            .replace("__CHUNKS__", f"{m['chunks']:,}")
            .replace("__MODS__", f"{m['modules']:,}")
            .replace("__METHOD__", m["method"])
            .replace("__ROLLUP__", rollup_txt)
            .replace("__ROADS__", str(len(payload["graph"]["roads"])))
            .replace("__GEDGES__", f"{n_edges:,}")
            .replace("__NSTRESS__", str(min(150, max(60, round(n_edges * 0.05)))))
            .replace("__DROPPED__", str(payload["graph"]["dropped_edges"])))
    leftover = re.findall(r"__[A-Z]+__", html)
    if leftover:
        raise ValueError(f"unresolved template placeholders: {leftover}")
    return html


# --------------------------------------------------------------------------
# Bundle seam + CLI
# --------------------------------------------------------------------------


def _load_module_graph(bundle_dir: Path):
    """Single-parse bundle load; returns (module graph, manifest)."""
    from decomposer.evidence import EvidenceGraph
    from decomposer.parts import build_module_graph
    from frontend.backend.serving.application.bundle_data import load_bundle
    b = load_bundle(bundle_dir)
    ev = EvidenceGraph(
        bundle_dir=bundle_dir, manifest=b.manifest, files=b.files,
        file_by_path=b.file_by_path, imports_out=b.imports_out,
        imports_in=b.imports_in, external_imports=b.external_imports,
        tests_for_subject={}, subjects_for_test={}, chunks=[],
        chunks_by_file={}, xrefs=[], concepts={}, per_path_concepts={},
        collections={}, file_summaries={}, schema_purposes={}, phases={},
    )
    return build_module_graph(ev), b.manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python scripts/cbm_terrain.py",
        description="Emit a self-contained SourcePath 3D code-terrain HTML "
                    "map from a codebase-mapper bundle.")
    p.add_argument("--bundle", required=True, type=Path,
                   help="Bundle directory (contains run_manifest.json).")
    p.add_argument("--out", type=Path, default=None,
                   help="Output HTML path (default: <bundle>/<repo>-terrain.html).")
    p.add_argument("--max-segments", type=int, default=0,
                   help="Directory roll-up depth; 0 = deepest that keeps "
                        "groups under --max-points (default).")
    p.add_argument("--max-points", type=int, default=4000,
                   help="Group-count cap used by --max-segments 0.")
    p.add_argument("--roads", type=int, default=600,
                   help="How many strongest edges to draw as roads.")
    p.add_argument("--grid", default="280x196",
                   help="Elevation grid as WxH (default 280x196).")
    p.add_argument("--seed", type=int, default=42,
                   help="Projection seed — keep fixed per repo so the "
                        "geography stays recognizable.")
    p.add_argument("--title", default=None,
                   help="Display name (default: repo name, capitalized).")
    args = p.parse_args(argv)

    if not (args.bundle / "run_manifest.json").exists():
        p.error(f"not a bundle directory (no run_manifest.json): {args.bundle}")
    gw, gh = (int(v) for v in args.grid.lower().split("x"))

    mg, manifest = _load_module_graph(args.bundle)
    z = np.load(args.bundle / "embeddings.npz", allow_pickle=True)
    ids, vectors = z["ids"], z["vectors"]

    max_seg = args.max_segments or auto_segments(mg.files_of_module, args.max_points)
    repo = str(manifest.get("repo_name") or args.bundle.name)
    payload = assemble_payload(
        files_of_module=mg.files_of_module, edge_weight=mg.edge_weight,
        ids=ids, vectors=vectors, manifest=manifest, repo=repo,
        max_segments=max_seg, grid=(gw, gh),
        frontier_grid=(gw // 2, gh // 2), roads=args.roads, seed=args.seed)
    html = render_html(payload, repo_title=args.title or repo.capitalize())
    out = args.out or (args.bundle / f"{repo}-terrain.html")
    out.write_text(html)
    print(f"wrote terrain map -> {out} "
          f"({len(html) // 1024} KB, {payload['meta']['modules']} settlements, "
          f"{len(payload['graph']['edges'])} edges, "
          f"{payload['meta']['layers']} layers)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
