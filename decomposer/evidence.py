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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    # ^ dependency manifests parsed from bundle blobs (Cargo.toml for now):
    #   {manifest_path: {name, deps, dev_deps, workspace_members}}

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


def _read_manifest_deps(bundle_dir: Path) -> dict[str, dict[str, Any]]:
    """Parse dependency manifests from the bundle's own blob store.

    The graph records ``cbm:declaresDependency`` without dev/prod scope, but
    the manifest *contents* are in the bundle (content-addressed blobs), so
    scope is recoverable without re-extraction. Cargo.toml only for now —
    the shape is generic: ``{manifest_path: {name, deps, dev_deps,
    workspace_members}}``. Best-effort: unparseable manifests are skipped.
    """
    jsonld = bundle_dir / "inventory.jsonld"
    if not jsonld.exists():
        return {}
    try:
        data = json.loads(jsonld.read_text())
    except Exception:  # noqa: BLE001 — optional evidence
        return {}
    import tomllib
    out: dict[str, dict[str, Any]] = {}
    for node in data.get("@graph") or []:
        path = node.get("cbm:path")
        if not isinstance(path, str) or not path.endswith("Cargo.toml"):
            continue
        sha = node.get("cbm:contentSha256")
        if isinstance(sha, dict):
            sha = sha.get("@value")
        if not isinstance(sha, str):
            continue
        blob = bundle_dir / "blobs" / sha
        try:
            toml = tomllib.loads(blob.read_text())
        except Exception:  # noqa: BLE001 — a bad blob must not sink the load
            continue
        out[path] = {
            "name": (toml.get("package") or {}).get("name"),
            "deps": sorted((toml.get("dependencies") or {})),
            "dev_deps": sorted((toml.get("dev-dependencies") or {})),
            "workspace_members": list(
                (toml.get("workspace") or {}).get("members") or []),
        }
    return out


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
