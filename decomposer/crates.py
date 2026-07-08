"""Crate-aware model for Cargo workspaces (round-2 review #1/#3).

Workspace members become first-class ``crate:`` parts, and cross-crate module
edges that exist only through a *dev*-dependency (legal Cargo cycles, e.g.
``tokio`` dev-depending on ``tokio-test`` which depends on ``tokio``) are
classified test-only so they never drive SCC/build-order computation.

Evidence: the manifest blobs already in the bundle (``EvidenceGraph
.manifest_deps``); no re-extraction needed. Everything here is inert for
repositories without Cargo manifests.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .evidence import EvidenceGraph
from .model import Classification, Confidence, DepRef, Evidence, Part
from .parts import ModuleGraph

# Crate-name/dir signals for test/dev-support members (probable, naming-based).
_TEST_SUPPORT_HINTS = ("test", "bench", "fuzz", "example", "stress")


@dataclass
class CrateMap:
    """Workspace crates and the file->crate assignment derived from them."""

    crates: dict[str, dict] = field(default_factory=dict)   # dir -> manifest info
    crate_of_file: dict[str, str] = field(default_factory=dict)
    name_to_dir: dict[str, str] = field(default_factory=dict)

    def crate_of(self, path: str) -> str | None:
        return self.crate_of_file.get(path)


def detect_crates(ev: EvidenceGraph) -> CrateMap:
    cm = CrateMap()
    for manifest_path, info in sorted(ev.manifest_deps.items()):
        if not info.get("name"):
            continue    # a workspace-root (virtual) manifest, not a crate
        crate_dir = manifest_path.rsplit("/", 1)[0] if "/" in manifest_path else ""
        cm.crates[crate_dir] = {**info, "manifest": manifest_path, "dir": crate_dir}
        cm.name_to_dir[info["name"]] = crate_dir
    if not cm.crates:
        return cm
    # Longest-prefix assignment: nested crates (fuzz/) win over their parent.
    dirs = sorted(cm.crates, key=len, reverse=True)
    for f in ev.files:
        path = f["path"]
        for d in dirs:
            if d == "" or path.startswith(d + "/"):
                cm.crate_of_file[path] = d
                break
    return cm


def test_only_module_edges(
    ev: EvidenceGraph, mg: ModuleGraph, cm: CrateMap,
) -> set[tuple[str, str]]:
    """Module edges crossing a crate boundary where the target crate is only a
    dev-dependency of the source crate (never a prod dependency)."""
    if not cm.crates:
        return set()
    out: set[tuple[str, str]] = set()
    for (sm, dm) in mg.edge_weight:
        sc = _module_crate(sm, mg, cm)
        dc = _module_crate(dm, mg, cm)
        if sc is None or dc is None or sc == dc:
            continue
        src = cm.crates[sc]
        dst_name = cm.crates[dc].get("name")
        if dst_name and dst_name in src.get("dev_deps", []) \
                and dst_name not in src.get("deps", []):
            out.add((sm, dm))
    return out


def _module_crate(mod: str, mg: ModuleGraph, cm: CrateMap) -> str | None:
    for f in mg.files_of_module.get(mod, []):
        c = cm.crate_of_file.get(f)
        if c is not None:
            return c
    return None


def build_crate_parts(ev: EvidenceGraph, mg: ModuleGraph, cm: CrateMap) -> list[Part]:
    parts: list[Part] = []
    if not cm.crates:
        return parts
    # Crate-level prod dependency edges, from declared deps that name workspace
    # members (mechanically read out of the manifests: certain).
    for crate_dir in sorted(cm.crates):
        info = cm.crates[crate_dir]
        files = sorted(p for p, c in cm.crate_of_file.items() if c == crate_dir)
        dep_dirs = sorted({
            cm.name_to_dir[d] for d in info.get("deps", []) if d in cm.name_to_dir
        })
        dev_dirs = sorted({
            cm.name_to_dir[d] for d in info.get("dev_deps", []) if d in cm.name_to_dir
        })
        importers = sorted(
            c for c, ci in cm.crates.items()
            if info["name"] in ci.get("deps", [])
        )
        is_test_support = any(h in info["name"] for h in _TEST_SUPPORT_HINTS) \
            and not importers
        has_main = any(p.endswith("src/main.rs") or "/bin/" in p for p in files)
        parts.append(Part(
            id=f"crate:{info['name']}", name=info["name"],
            kind="application" if has_main else "library",
            layer="crate",
            responsibility=(f"Cargo workspace member `{info['name']}` "
                            f"({len(files)} files)."),
            responsibility_confidence=Confidence.CERTAIN,
            evidence=Evidence(
                files=files,
                signals=[f"manifest {info['manifest']} declares package "
                         f"`{info['name']}`",
                         f"deps: {info.get('deps', [])}",
                         f"dev-deps: {info.get('dev_deps', [])}"],
            ),
            dependencies=DepRef(
                incoming=[f"crate:{cm.crates[c]['name']}" for c in importers],
                outgoing=[f"crate:{cm.crates[d]['name']}" for d in dep_dirs],
                test_only_outgoing=[f"crate:{cm.crates[d]['name']}"
                                    for d in dev_dirs],
            ),
            classification=Classification(
                role="test" if is_test_support else "core",
                role_confidence=(Confidence.PROBABLE if is_test_support
                                 else Confidence.STRONG),
                reusability="public" if importers else "internal",
                risk="low",
            ),
            metrics={"n_files": len(files),
                     "n_prod_deps": len(info.get("deps", [])),
                     "n_dev_deps": len(info.get("dev_deps", []))},
            overall_confidence=Confidence.CERTAIN,
        ))
    return parts
