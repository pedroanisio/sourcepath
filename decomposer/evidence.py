"""Evidence loader — the single seam between the decomposer and the CBM bundle.

Everything the decomposer knows about a repository flows through
``load_evidence``. It reuses the project's own projection (`load_bundle`) so the
decomposer never re-implements graph parsing, then augments it with the one
signal the serving projection drops — ``cbm:hasPhase`` — read directly and
cheaply from the JSON-LD artifact.

Isolation note: this is the *only* module that imports from ``frontend``.
Extracting the pure loader (`Bundle` + `load_bundle`) into a shared kernel and
importing it here instead is the recommended target state; when that happens
only this file changes. Until then the coupling is contained to one function.

Graceful degradation (PALS's Law — untrusted/incomplete generator output):
missing sidecars (no ``concepts.json``, no ``enrichments.jsonl``) never raise;
the corresponding maps are simply empty and downstream confidence is lowered.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class EvidenceGraph:
    """A decomposition-tailored, read-only view over one bundle.

    Wraps the serving-layer :class:`Bundle` and exposes exactly the structures
    the decomposer consumes, plus per-file phases. Keeping this thin adapter
    means the rest of the package depends on *this* stable surface, not on the
    Bundle's incidental field names.
    """

    bundle_dir: Path
    manifest: dict[str, Any]
    files: list[dict[str, Any]]                    # path, language, type, size, uri
    file_by_path: dict[str, dict[str, Any]]
    imports_out: dict[str, list[str]]              # path -> internal import targets
    imports_in: dict[str, list[str]]               # path -> internal importers
    external_imports: dict[str, list[str]]         # path -> external package specifiers
    tests_for_subject: dict[str, list[str]]        # subject path -> test paths
    subjects_for_test: dict[str, list[str]]        # test path -> subject paths
    chunks: list[dict[str, Any]]                   # symbol, kind, file, lines, idx
    chunks_by_file: dict[str, list[int]]
    xrefs: list[dict[str, Any]]                    # src_idx, dst_idx, kind, resolution
    concepts: dict[str, dict[str, Any]]            # name -> {kind, broader, frequency, ...}
    per_path_concepts: dict[str, list[str]]        # path -> concept names
    collections: dict[str, list[str]]              # broader-collection -> member concept names
    file_summaries: dict[str, dict[str, Any]]      # path -> {text, model, ...}  (LLM, unverified)
    schema_purposes: dict[str, dict[str, Any]]     # path -> {text, model, ...}  (LLM, unverified)
    phases: dict[str, list[str]]                   # path -> phase local names
    rust_items: list[dict[str, Any]] = field(default_factory=list)
    manifest_sha256: str = ""                      # run identity for provenance
    manifest_deps: dict[str, dict[str, Any]] = field(default_factory=dict)
    # ^ dependency manifests parsed from bundle blobs (Cargo.toml, pyproject.toml,
    #   package.json): {manifest_path: {name, deps, dev_deps, workspace_members,
    #   manifest_type}}
    revision_chains: dict[str, dict[str, Any]] = field(default_factory=dict)
    # ^ Alembic-shaped revision markers parsed from bundle blobs under any
    #   */versions/*.py path: {file_path: {revision, down_revision}}. These
    #   never show up as import edges, so build order can't infer them —
    #   decomposer.migrations turns this into a file_orderings entry.

    # ── convenience accessors ────────────────────────────────────────────────
    def code_files(self) -> list[dict[str, Any]]:
        return [f for f in self.files if f.get("type") in {"source_code", "test_code"}]

    def source_files(self) -> list[dict[str, Any]]:
        return [f for f in self.files if f.get("type") == "source_code"]

    def symbols_of(self, path: str) -> list[dict[str, Any]]:
        return [self.chunks[i] for i in self.chunks_by_file.get(path, [])]

    def import_degree(self, path: str) -> int:
        return len(self.imports_in.get(path, [])) + len(self.imports_out.get(path, []))


def load_evidence(bundle_dir: str | Path) -> EvidenceGraph:
    """Load a CBM bundle into an :class:`EvidenceGraph`.

    Raises ``FileNotFoundError`` if the directory has no ``run_manifest.json``.
    """
    bundle_dir = Path(bundle_dir)
    b = _load_bundle(bundle_dir)

    concepts = (b.concepts.get("concepts") or {}) if isinstance(b.concepts, dict) else {}
    per_path_concepts = (
        (b.concepts.get("per_path_concepts") or {}) if isinstance(b.concepts, dict) else {}
    )
    collections = _collections_from_concepts(concepts)
    phases = _read_phases(bundle_dir)

    return EvidenceGraph(
        bundle_dir=bundle_dir,
        manifest=b.manifest,
        files=b.files,
        file_by_path=b.file_by_path,
        imports_out=b.imports_out,
        imports_in=b.imports_in,
        external_imports=b.external_imports,
        tests_for_subject=b.tests_for_subject,
        subjects_for_test=b.subjects_for_test,
        chunks=b.chunks,
        chunks_by_file=b.chunks_by_file,
        xrefs=b.xrefs,
        concepts=concepts,
        per_path_concepts=per_path_concepts,
        collections=collections,
        file_summaries=b.enrichment_file_summary,
        schema_purposes=b.enrichment_schema_purpose,
        phases=phases,
        rust_items=b.rust_items,
        manifest_sha256=_manifest_sha256(bundle_dir),
        manifest_deps=_read_manifest_deps(bundle_dir),
        revision_chains=_read_revision_markers(bundle_dir),
    )


def _manifest_sha256(bundle_dir: Path) -> str:
    """Hash of run_manifest.json — the bundle's run identity. Decomposition
    consumers need it to tell apart bundles built from the same commit with
    different plugin sets (concepts present vs. absent, etc.)."""
    manifest = bundle_dir / "run_manifest.json"
    try:
        return hashlib.sha256(manifest.read_bytes()).hexdigest()
    except OSError:
        return ""


def _load_bundle(bundle_dir: Path):
    """Import seam. Kept in its own function so the ``frontend`` dependency has a
    single, replaceable call site (see module docstring)."""
    try:
        from frontend.backend.serving.application.bundle_data import load_bundle
    except Exception as exc:  # noqa: BLE001 — surface a precise, actionable error
        raise RuntimeError(
            "decomposer.evidence could not import the bundle loader "
            "(frontend.backend.serving.application.bundle_data.load_bundle). "
            "Run from the repository root with its virtualenv active."
        ) from exc
    return load_bundle(bundle_dir)


def _collections_from_concepts(concepts: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Invert concept ``broader`` links into collection -> members.

    ``broader`` (from the curated vocabulary, e.g. ``code_structure``,
    ``intent_first_ontology``) is the closest thing the graph carries to a
    semantic domain grouping. Only typed concepts have it, so this is naturally
    sparse and its consumers must treat absence as ``unknown``.
    """
    out: dict[str, list[str]] = {}
    for name, rec in concepts.items():
        broader = rec.get("broader")
        if broader:
            out.setdefault(str(broader), []).append(name)
    for k in out:
        out[k].sort()
    return out


def _read_phases(bundle_dir: Path) -> dict[str, list[str]]:
    """Read ``cbm:hasPhase`` per file from the JSON-LD artifact.

    The serving projection drops phases; the decomposer uses them to separate
    runtime code from build/ci/deploy tooling. Best-effort: any parse problem
    yields an empty map (phases become an *absent* signal, not an error).
    """
    jsonld = bundle_dir / "inventory.jsonld"
    if not jsonld.exists():
        return {}
    try:
        data = json.loads(jsonld.read_text())
    except Exception:  # noqa: BLE001 — phases are optional enrichment
        return {}
    graph = data.get("@graph") or []
    out: dict[str, list[str]] = {}
    for node in graph:
        if "cbm:File" not in _types(node):
            continue
        path = node.get("cbm:path")
        if not isinstance(path, str):
            continue
        phases = _local_names(node.get("cbm:hasPhase"))
        if phases:
            out[path] = sorted(set(phases))
    return out


_MANIFEST_READERS: dict[str, str] = {
    "Cargo.toml": "cargo", "pyproject.toml": "python", "package.json": "npm",
}


def _read_manifest_deps(bundle_dir: Path) -> dict[str, dict[str, Any]]:
    """Parse dependency manifests from the bundle's own blob store.

    The graph records ``cbm:declaresDependency`` without dev/prod scope, but
    the manifest *contents* are in the bundle (content-addressed blobs), so
    scope is recoverable without re-extraction. Cargo.toml, pyproject.toml
    (PEP 621 or Poetry) and package.json — the shape is generic:
    ``{manifest_path: {name, deps, dev_deps, workspace_members,
    manifest_type}}``. Best-effort: unparseable manifests are skipped.
    """
    jsonld = bundle_dir / "inventory.jsonld"
    if not jsonld.exists():
        return {}
    try:
        data = json.loads(jsonld.read_text())
    except Exception:  # noqa: BLE001 — optional evidence
        return {}
    out: dict[str, dict[str, Any]] = {}
    for node in data.get("@graph") or []:
        path = node.get("cbm:path")
        if not isinstance(path, str):
            continue
        basename = path.rsplit("/", 1)[-1]
        manifest_type = _MANIFEST_READERS.get(basename)
        if manifest_type is None:
            continue
        sha = node.get("cbm:contentSha256")
        if isinstance(sha, dict):
            sha = sha.get("@value")
        if not isinstance(sha, str):
            continue
        blob = bundle_dir / "blobs" / sha
        try:
            text = blob.read_text()
            if manifest_type == "cargo":
                info = _parse_cargo_toml(text)
            elif manifest_type == "python":
                info = _parse_pyproject_toml(text)
            else:
                info = _parse_package_json(text)
        except Exception:  # noqa: BLE001 — a bad blob must not sink the load
            continue
        if info is None:
            continue
        out[path] = {**info, "manifest_type": manifest_type}
    return out


_REVISION_RE = re.compile(r'^revision\s*(?::[^=]+)?=\s*[\'"]([^\'"]+)[\'"]', re.M)
_DOWN_REVISION_RE = re.compile(
    r'^down_revision\s*(?::[^=]+)?=\s*(None|[\'"]([^\'"]+)[\'"])', re.M)


def _in_versions_dir(path: str) -> bool:
    return path.startswith("versions/") or "/versions/" in path


def _read_revision_markers(bundle_dir: Path) -> dict[str, dict[str, Any]]:
    """Parse Alembic ``revision``/``down_revision`` markers from ``*/versions/*.py``
    blobs. These never appear as import edges — Alembic chains revisions by a
    string id stored in a DB metadata table, not by importing one revision
    module from another — so build order can't infer them from the graph.
    Scoped to files that look like Alembic revisions (path under a
    ``versions/`` directory *and* an actual ``revision =`` assignment);
    anything else is left alone rather than guessed at. Best-effort:
    unparseable blobs are skipped.
    """
    jsonld = bundle_dir / "inventory.jsonld"
    if not jsonld.exists():
        return {}
    try:
        data = json.loads(jsonld.read_text())
    except Exception:  # noqa: BLE001 — optional evidence
        return {}
    out: dict[str, dict[str, Any]] = {}
    for node in data.get("@graph") or []:
        path = node.get("cbm:path")
        if not isinstance(path, str) or not path.endswith(".py") \
                or not _in_versions_dir(path):
            continue
        sha = node.get("cbm:contentSha256")
        if isinstance(sha, dict):
            sha = sha.get("@value")
        if not isinstance(sha, str):
            continue
        blob = bundle_dir / "blobs" / sha
        try:
            text = blob.read_text()
        except Exception:  # noqa: BLE001 — a bad blob must not sink the load
            continue
        rev_m = _REVISION_RE.search(text)
        if not rev_m:
            continue   # versions/ file with no revision marker -- not Alembic
        down_m = _DOWN_REVISION_RE.search(text)
        down = None if not down_m or down_m.group(1) == "None" else down_m.group(2)
        out[path] = {"revision": rev_m.group(1), "down_revision": down}
    return out


def _parse_cargo_toml(text: str) -> dict[str, Any] | None:
    toml = tomllib.loads(text)
    return {
        "name": (toml.get("package") or {}).get("name"),
        "deps": sorted((toml.get("dependencies") or {})),
        "dev_deps": sorted((toml.get("dev-dependencies") or {})),
        "workspace_members": list((toml.get("workspace") or {}).get("members") or []),
    }


def _pep508_name(spec: str) -> str | None:
    """The bare distribution name from a PEP 508 requirement string, e.g.
    ``"apache-airflow-core>=2.9"`` or ``"boto3[extra]>=1.28"`` -> the prefix
    before any version/extra/marker/URL syntax."""
    import re
    m = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", spec.strip())
    return m.group(0) if m else None


def _parse_pyproject_toml(text: str) -> dict[str, Any] | None:
    toml = tomllib.loads(text)
    project = toml.get("project") or {}
    poetry = ((toml.get("tool") or {}).get("poetry")) or {}

    name = project.get("name") or poetry.get("name")

    deps: set[str] = set()
    for spec in project.get("dependencies") or []:
        n = _pep508_name(spec)
        if n:
            deps.add(n)
    deps.update(d for d in (poetry.get("dependencies") or {}) if d != "python")

    dev_deps: set[str] = set()
    for group_specs in (project.get("optional-dependencies") or {}).values():
        for spec in group_specs:
            n = _pep508_name(spec)
            if n:
                dev_deps.add(n)
    dev_deps.update(poetry.get("dev-dependencies") or {})
    groups = (poetry.get("group") or {})
    for group in groups.values():
        dev_deps.update((group or {}).get("dependencies") or {})

    # A name in both buckets is a real (prod) dependency first: mirrors
    # test_only_module_edges's own "dev_deps and NOT deps" precedence, so a
    # package genuinely needed at runtime is never soft-classified just
    # because it's also redundantly listed in an optional group.
    return {
        "name": name, "deps": sorted(deps),
        "dev_deps": sorted(dev_deps - deps), "workspace_members": [],
    }


def _parse_package_json(text: str) -> dict[str, Any] | None:
    pkg = json.loads(text)
    return {
        "name": pkg.get("name"),
        "deps": sorted((pkg.get("dependencies") or {})),
        "dev_deps": sorted((pkg.get("devDependencies") or {})),
        "workspace_members": list(pkg.get("workspaces") or []),
    }


def _types(node: dict[str, Any]) -> list[str]:
    t = node.get("@type")
    if isinstance(t, list):
        return t
    return [t] if t else []


def _local_names(value: Any) -> list[str]:
    """Extract local names from JSON-LD id references (``cbmp:runtime`` ->
    ``runtime``; ``.../phase#build`` -> ``build``)."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for it in items:
        ref = it.get("@id") if isinstance(it, dict) else it
        if not isinstance(ref, str):
            continue
        if ":" in ref and "/" not in ref and "#" not in ref:
            out.append(ref.split(":", 1)[1])          # curie form  cbmp:runtime
        elif "#" in ref:
            out.append(ref.rsplit("#", 1)[-1])         # full IRI with fragment
        else:
            out.append(ref.rsplit("/", 1)[-1])
    return out
