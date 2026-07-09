"""Distribution-aware model for multi-package monorepos (round-2 review #1/#3;
generalized beyond Cargo in round-3, same fix pattern).

Workspace members (Cargo crates, or pyproject.toml/package.json packages)
become first-class ``crate:``/``dist:`` parts, and cross-distribution module
edges that exist only through a *dev*-dependency (legal cycles, e.g. ``tokio``
dev-depending on ``tokio-test`` which depends on ``tokio``, or a provider
depending on a shared test-helper distribution that depends back on it) are
classified test-only so they never drive SCC/build-order computation.

Evidence: the manifest blobs already in the bundle (``EvidenceGraph
.manifest_deps``); no re-extraction needed. The dev-dependency mechanism is
inert for repositories with no recognized manifest at all.

A second, manifest-independent signal lives here too: ``test_role_module_edges``
excludes any edge touching a module whose code files are *all* test_code (the
"test-tree imports treated as hard edges" half of the defect) — this needs no
manifest, so it also covers monorepos without per-package dependency
declarations (e.g. a single `pyproject.toml` at the root with a `tests/` tree).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .evidence import EvidenceGraph
from .model import Classification, Confidence, DepRef, Evidence, Part
from .parts import _CODE_TYPES, ModuleGraph

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


def test_role_module_edges(ev: EvidenceGraph, mg: ModuleGraph) -> set[tuple[str, str]]:
    """Module edges where either endpoint is a pure test-role module.

    Mirrors ``classify.module_role``'s "all code files are test_code" rule
    (CERTAIN confidence there) at the pre-parts adjacency level: a module
    whose only code files are tests is never a legitimate SCC/build-order
    partner for production modules, manifest or no manifest. This is the
    module==directory analogue of ``test_only_module_edges`` — that one
    needs a declared dev-dependency to fire (cross-distribution); this one
    fires from file-type evidence alone (within or across distributions).
    """
    test_modules = set()
    for mod, files in mg.files_of_module.items():
        code = [f for f in files if ev.file_by_path.get(f, {}).get("type") in _CODE_TYPES]
        if code and all(
            ev.file_by_path.get(f, {}).get("type") == "test_code" for f in code
        ):
            test_modules.add(mod)
    if not test_modules:
        return set()
    return {
        (sm, dm) for (sm, dm) in mg.edge_weight
        if sm in test_modules or dm in test_modules
    }


def _module_crate(mod: str, mg: ModuleGraph, cm: CrateMap) -> str | None:
    for f in mg.files_of_module.get(mod, []):
        c = cm.crate_of_file.get(f)
        if c is not None:
            return c
    return None


# manifest_type -> (id prefix, human label). Cargo keeps its original "crate:"
# prefix/wording for backward compatibility with existing consumers; Python
# and npm distributions get their own generic, non-Rust-flavored id space so
# "crate" never leaks into a pip/npm package's identity or prose.
_DIST_ID_PREFIX = {"cargo": "crate", "python": "dist", "npm": "dist"}
_DIST_LABEL = {"cargo": "Cargo workspace member", "python": "Python distribution",
               "npm": "npm package"}


def build_crate_parts(ev: EvidenceGraph, mg: ModuleGraph, cm: CrateMap) -> list[Part]:
    parts: list[Part] = []
    if not cm.crates:
        return parts

    def _ref(dir_: str) -> str:
        other = cm.crates[dir_]
        prefix = _DIST_ID_PREFIX.get(other.get("manifest_type", "cargo"), "crate")
        return f"{prefix}:{other['name']}"

    # Crate/distribution-level prod dependency edges, from declared deps that
    # name workspace members (mechanically read out of the manifests: certain).
    for crate_dir in sorted(cm.crates):
        info = cm.crates[crate_dir]
        manifest_type = info.get("manifest_type", "cargo")
        id_prefix = _DIST_ID_PREFIX.get(manifest_type, "crate")
        label = _DIST_LABEL.get(manifest_type, "workspace member")
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
        has_main = manifest_type == "cargo" and any(
            p.endswith("src/main.rs") or "/bin/" in p for p in files)
        parts.append(Part(
            id=f"{id_prefix}:{info['name']}", name=info["name"],
            kind="application" if has_main else "library",
            layer="crate",
            responsibility=f"{label} `{info['name']}` ({len(files)} files).",
            responsibility_confidence=Confidence.CERTAIN,
            evidence=Evidence(
                files=files,
                signals=[f"manifest {info['manifest']} declares package "
                         f"`{info['name']}`",
                         f"deps: {info.get('deps', [])}",
                         f"dev-deps: {info.get('dev_deps', [])}"],
            ),
            dependencies=DepRef(
                incoming=[_ref(c) for c in importers],
                outgoing=[_ref(d) for d in dep_dirs],
                test_only_outgoing=[_ref(d) for d in dev_dirs],
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
