"""codebase_mapper.reconstruct."""
from __future__ import annotations

import hashlib
import os
import tempfile

from pathlib import Path
from rdflib import Graph
from rdflib import URIRef
from rdflib.namespace import RDF

from .blobs import emit_blobs
from .constants import CBM, CBMI_NS
from .extensions import iter_graph_contributors
from .pipeline import map_codebase
from .rdf_emit import build_inventory_graph


def reconstruct(inventory_path: Path, blobs_dir: Path, out_dir: Path) -> dict:
    """Materialize files from inventory + blob store. Returns a report dict."""
    g = Graph()
    g.parse(str(inventory_path), format="turtle")
    out_dir.mkdir(parents=True, exist_ok=True)

    file_records: list[tuple[str, str]] = []  # (path, content_sha256)
    for s in g.subjects(RDF.type, CBM.File):
        path_lits = list(g.objects(s, CBM.path))
        sha_lits = list(g.objects(s, CBM.contentSha256))
        if not path_lits or not sha_lits:
            continue
        file_records.append((str(path_lits[0]), str(sha_lits[0])))
    file_records.sort()

    written = 0
    missing_blobs: list[str] = []
    for path, sha in file_records:
        blob_path = blobs_dir / sha
        if not blob_path.exists():
            missing_blobs.append(sha)
            continue
        dst = out_dir / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(blob_path.read_bytes())
        written += 1

    return {
        "files_written": written,
        "files_in_inventory": len(file_records),
        "missing_blobs": missing_blobs[:50],  # cap for brevity
        "missing_blob_count": len(missing_blobs),
    }

def verify_reconstructed(out_dir: Path, expected: dict[str, str]) -> dict:
    """Check that for each (path, sha256), the file on disk matches.

    Returns dict with `ok` boolean and `mismatches`/`extras` lists.
    """
    mismatches: list[tuple[str, str, str]] = []  # path, expected, actual
    missing: list[str] = []
    found_paths: set[str] = set()
    for path, expected_sha in expected.items():
        f = out_dir / path
        if not f.exists():
            missing.append(path)
            continue
        actual = hashlib.sha256(f.read_bytes()).hexdigest()
        if actual != expected_sha:
            mismatches.append((path, expected_sha, actual))
        found_paths.add(path)

    # Look for extras (files on disk not in the inventory).
    extras: list[str] = []
    for root, _, files in os.walk(out_dir):
        for name in files:
            full = Path(root) / name
            rel = str(full.relative_to(out_dir)).replace(os.sep, "/")
            if rel not in expected:
                extras.append(rel)

    ok = not (mismatches or missing or extras)
    return {
        "ok": ok,
        "checked": len(expected),
        "mismatches": mismatches[:20],
        "missing_count": len(missing),
        "missing_sample": missing[:20],
        "extras_count": len(extras),
        "extras_sample": extras[:20],
    }

def verify_roundtrip(repo: Path, state: str, exclude: list[str] | None = None) -> dict:
    """Map repo, write inventory + blobs to a temp dir, reconstruct, compare."""
    mapped = map_codebase(repo, state, exclude_patterns=exclude or [])
    expected = {r.path: r.content_sha256 for r in mapped["records"]}

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        out_dir = td_path / "out"
        blobs_dir = out_dir / "blobs"
        out_dir.mkdir()
        # Emit inventory + blobs
        repo_iri = URIRef(f"{CBMI_NS}repo/{repo.name}")
        inv = build_inventory_graph(
            repo_iri=repo_iri, commit_sha=mapped["commit"],
            records=mapped["records"], import_edges=mapped["import_edges"],
            import_ext_edges=mapped["import_ext_edges"],
            dep_edges=mapped["dep_edges"], pin_edges=mapped["pin_edges"],
            tests_edges=mapped["tests_edges"],
        )
        # Run GraphContributors so the roundtrip path also covers extension
        # triples. No-op when no plugins registered.
        ctx = mapped.get("ctx")
        if ctx is not None:
            for gc in iter_graph_contributors():
                gc.contribute(inv, ctx)
        inv_path = out_dir / "inventory.ttl"
        inv.serialize(destination=str(inv_path), format="turtle")
        blob_count = emit_blobs(mapped["records"], repo, mapped["blob_by_path"], blobs_dir)

        # Reconstruct
        recon_dir = td_path / "recon"
        recon_dir.mkdir()
        recon_report = reconstruct(inv_path, blobs_dir, recon_dir)
        verify_report = verify_reconstructed(recon_dir, expected)

    return {
        "commit": mapped["commit"],
        "files_mapped": len(mapped["records"]),
        "blobs_written": blob_count,
        "reconstruction": recon_report,
        "verification": verify_report,
        "roundtrip_ok": verify_report["ok"]
                        and recon_report["missing_blob_count"] == 0
                        and recon_report["files_written"] == recon_report["files_in_inventory"],
    }
