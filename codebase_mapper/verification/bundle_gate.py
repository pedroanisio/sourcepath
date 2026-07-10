"""`cbm verify-bundle` — errors fail the run instead of describing it.

The disclosure layers (manifest degradations, report caveats) made errors
visible; this gate makes them terminal (error-free-mapping E9). Checks:

  1. recount — files/chunks/concepts counted independently from
     inventory.jsonld must equal the manifest's totals;
  2. artifact hashes — every listed artifact's sha256 recomputed;
  3. SHACL — the self-check must have run and conformed (a skip is a
     violation unless explicitly accepted);
  4. degradations — every recorded degradation must be explicitly
     acknowledged by component name, or the gate fails;
  5. budgets — parse-error share, unlanguaged share, silent zeros, and
     import resolution against the ledger targets.

Every violation names its check id, so CI and humans see the same thing.
"""
from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Budgets:
    """Ledger targets (error-free-mapping plan §0). Overridable per run."""
    max_parse_error_share: float = 0.05
    max_unlanguaged_share: float = 0.03
    max_silent_zero_files: int = 0
    min_import_resolution: float = 0.5
    allow_skipped_shacl: bool = False


def _fail(check_id: str, text: str) -> dict:
    return {"id": check_id, "severity": "fail", "text": text}


def recount_inventory(jsonld_path: Path) -> dict[str, int]:
    """Independent node recount from the canonical (sort_keys, indent=2)
    JSON-LD our emitter writes: node-level @id lines carry a 6-space indent
    and the instance-IRI prefix names the node family."""
    counts = {"files": 0, "chunks": 0, "concepts": 0}
    prefixes = {
        b'      "@id": "cbmi:file/': "files",
        b'      "@id": "cbmi:chunk/': "chunks",
        b'      "@id": "cbmi:concept/': "concepts",
    }
    with open(jsonld_path, "rb") as f:
        for line in f:
            for prefix, key in prefixes.items():
                if line.startswith(prefix):
                    counts[key] += 1
                    break
    return counts


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _iter_listed_artifacts(man: dict):
    """(name, sha256, size) for every artifact file the manifest lists."""
    def emit_from(mapping):
        for name, entry in (mapping or {}).items():
            if isinstance(entry, dict) and "sha256" in entry:
                yield name, entry["sha256"]

    yield from emit_from(man.get("artifacts"))
    yield from emit_from((man.get("ast_coverage") or {}).get("files"))
    for ext in (man.get("extensions") or {}).values():
        yield from emit_from((ext or {}).get("files"))
    yield from emit_from((man.get("rust_items_sidecar") or {}).get("files"))


def check_bundle(
    bundle_dir: Path,
    budgets: Budgets,
    *,
    accept_degradations: set[str] | None = None,
    skip_hashes: bool = False,
) -> list[dict]:
    bundle_dir = Path(bundle_dir)
    accept = accept_degradations or set()
    manifest_path = bundle_dir / "run_manifest.json"
    if not manifest_path.is_file():
        return [_fail("manifest", f"run_manifest.json not found in {bundle_dir}")]
    try:
        man = json.loads(manifest_path.read_text())
    except ValueError as e:
        return [_fail("manifest", f"run_manifest.json unparseable: {e}")]

    violations: list[dict] = []
    counts = man.get("counts") or {}

    # 1. recount
    jsonld = bundle_dir / "inventory.jsonld"
    if jsonld.is_file():
        recount = recount_inventory(jsonld)
        if recount["files"] != counts.get("files"):
            violations.append(_fail(
                "recount_files",
                f"inventory holds {recount['files']:,} file nodes; manifest "
                f"records {counts.get('files'):,}"))
        emb = (man.get("extensions") or {}).get("l2_40_embeddings_artifact") or {}
        if emb.get("n_chunks") is not None and recount["chunks"] != emb["n_chunks"]:
            violations.append(_fail(
                "recount_chunks",
                f"inventory holds {recount['chunks']:,} chunk nodes; manifest "
                f"records {emb['n_chunks']:,}"))
        l3 = (man.get("extensions") or {}).get("l3_40_concepts_artifact") or {}
        if l3.get("n_concepts") is not None and recount["concepts"] != l3["n_concepts"]:
            violations.append(_fail(
                "recount_concepts",
                f"inventory holds {recount['concepts']:,} concept nodes; "
                f"manifest records {l3['n_concepts']:,}"))
    elif "inventory.jsonld" in (man.get("artifacts") or {}):
        violations.append(_fail("recount_files",
                                "manifest lists inventory.jsonld but the file is absent"))

    # 2. artifact hashes
    if not skip_hashes:
        for name, claimed in _iter_listed_artifacts(man):
            p = bundle_dir / name
            if not p.is_file():
                violations.append(_fail("artifact_hash", f"{name}: listed but absent"))
            elif _sha256(p) != claimed:
                violations.append(_fail(
                    "artifact_hash", f"{name}: sha256 differs from the manifest claim"))

    # 3. SHACL
    shacl = man.get("shacl_self_check") or {}
    if shacl.get("conforms") is not True:
        if shacl.get("skipped") and budgets.allow_skipped_shacl:
            pass
        else:
            violations.append(_fail(
                "shacl",
                f"SHACL self-check is {shacl.get('conforms')!r}"
                + (" (skipped)" if shacl.get("skipped") else "")))

    # 4. degradations
    if "degradations" not in man:
        violations.append(_fail(
            "degradation", "manifest predates degradation disclosure"))
    else:
        for d in man["degradations"]:
            comp = d.get("component", "?")
            if comp not in accept:
                violations.append(_fail(
                    "degradation",
                    f"unacknowledged degradation: {comp} — {d.get('reason', '?')}"
                    f" (pass --accept-degradation {comp} to acknowledge)"))

    # 5. budgets
    cov = (man.get("ast_coverage") or {}).get("totals") or {}
    n_src = (man.get("ast_coverage") or {}).get("n_source_files") or cov.get("files") or 0
    if n_src:
        share = (cov.get("files_with_parse_errors") or 0) / n_src
        if share > budgets.max_parse_error_share:
            violations.append(_fail(
                "parse_error_budget",
                f"parse-error share {share:.1%} exceeds budget "
                f"{budgets.max_parse_error_share:.0%}"))
        silent = cov.get("silent_zero_symbol_files") or 0
        if silent > budgets.max_silent_zero_files:
            violations.append(_fail(
                "silent_zero_budget",
                f"{silent:,} silent zero-symbol files exceed budget "
                f"{budgets.max_silent_zero_files}"))
    n_files = counts.get("files") or 0
    unlang = (man.get("files_by_language") or {}).get("(none)") or 0
    if n_files and unlang / n_files > budgets.max_unlanguaged_share:
        violations.append(_fail(
            "unlanguaged_budget",
            f"unlanguaged share {unlang / n_files:.1%} exceeds budget "
            f"{budgets.max_unlanguaged_share:.0%}"))
    extracted = cov.get("imports_extracted") or 0
    resolved = counts.get("import_edges") or 0
    if extracted and resolved / extracted < budgets.min_import_resolution:
        violations.append(_fail(
            "import_resolution_budget",
            f"import resolution {resolved / extracted:.0%} below budget "
            f"{budgets.min_import_resolution:.0%}"))

    return violations
