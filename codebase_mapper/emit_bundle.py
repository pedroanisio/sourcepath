"""codebase_mapper.emit_bundle."""
from __future__ import annotations

import hashlib
import json
import sys
import time

from collections import Counter
from pathlib import Path
from rdflib import URIRef

from .blobs import emit_blobs
from .constants import CBMI_NS, TOOL_VERSION, VOCABULARY_VERSION
from .extensions import (
    iter_artifact_emitters, iter_graph_contributors, iter_shape_contributors,
)
from .rdf_emit import build_inventory_graph, build_ontology_mapping_graph, build_shacl_graph


def emit(repo_name: str, mapped: dict, out_dir: Path, emit_blobs_flag: bool = True) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_iri = URIRef(f"{CBMI_NS}repo/{repo_name}")
    inv = build_inventory_graph(
        repo_iri=repo_iri, commit_sha=mapped["commit"],
        records=mapped["records"], import_edges=mapped["import_edges"],
        import_ext_edges=mapped["import_ext_edges"],
        dep_edges=mapped["dep_edges"], pin_edges=mapped["pin_edges"],
        tests_edges=mapped["tests_edges"],
    )
    shapes = build_shacl_graph()
    mapping = build_ontology_mapping_graph()

    # --- Extension hooks: GraphContributors + ShapeContributors ---
    # Run before serialization so emitted triples land in inventory.ttl and
    # JSON-LD. When no plugins are registered both loops are no-ops.
    ctx = mapped.get("ctx")
    if ctx is not None:
        for gc in iter_graph_contributors():
            gc.contribute(inv, ctx)
    for sc in iter_shape_contributors():
        sc.contribute(shapes)

    inv_path = out_dir / "inventory.ttl"
    shapes_path = out_dir / "shapes.shacl.ttl"
    jsonld_path = out_dir / "inventory.jsonld"
    mapping_path = out_dir / "ontology-mapping.ttl"
    inv.serialize(destination=str(inv_path), format="turtle")
    shapes.serialize(destination=str(shapes_path), format="turtle")
    inv.serialize(destination=str(jsonld_path), format="json-ld",
                  auto_compact=True, indent=2, sort_keys=True)
    mapping.serialize(destination=str(mapping_path), format="turtle")

    # JSON-LD post-sort for byte-stable determinism
    try:
        doc = json.loads(jsonld_path.read_text())

        def _sort_jsonld(node):
            if isinstance(node, dict):
                return {k: _sort_jsonld(v) for k, v in sorted(node.items())}
            if isinstance(node, list):
                items = [_sort_jsonld(x) for x in node]
                def key(x):
                    if isinstance(x, dict):
                        return (0, x.get("@id", ""), json.dumps(x, sort_keys=True))
                    return (1, str(x))
                return sorted(items, key=key)
            return node

        jsonld_path.write_text(json.dumps(_sort_jsonld(doc), indent=2, sort_keys=True) + "\n")
    except Exception as e:
        sys.stderr.write(f"[warn] jsonld canonicalization failed: {e}\n")

    blob_count = 0
    if emit_blobs_flag:
        blobs_dir = out_dir / "blobs"
        blob_count = emit_blobs(mapped["records"], mapped["repo"],
                                mapped["blob_by_path"], blobs_dir)

    # --- Extension hook: ArtifactEmitters ---
    extension_fragments: dict[str, dict] = {}
    if ctx is not None:
        for ae in iter_artifact_emitters():
            extension_fragments[ae.name] = ae.emit(out_dir, ctx)

    from pyshacl import validate
    conforms, _vg, report_text = validate(
        data_graph=inv, shacl_graph=shapes, inference="none",
        abort_on_first=False, meta_shacl=False, advanced=False, debug=False,
    )

    def sha256_file(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    manifest = {
        "tool_version": TOOL_VERSION,
        "vocabulary_version": VOCABULARY_VERSION,
        "repo_name": repo_name,
        "commit_sha": mapped["commit"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_source_roots": mapped["python_source_roots"],
        "rust_crates": [{"name": c["name"], "crate_dir": c["crate_dir"]} for c in mapped["rust_crates"]],
        "tsconfig_count": mapped["tsconfig_count"],
        "go_module": mapped["go_module"],
        "swift_local_modules": mapped["swift_local_modules"],
        "swift_product_modules": mapped["swift_product_modules"],
        "dart_package_name": mapped["dart_package_name"],
        "kotlin_prefix_matched_packages": mapped["kotlin_prefix_matched_packages"],
        "exclude_patterns": mapped["exclude_patterns"],
        "counts": {
            "files": len(mapped["records"]),
            "import_edges": len(mapped["import_edges"]),
            "import_external_edges": len(mapped["import_ext_edges"]),
            "declares_dependency_edges": len(mapped["dep_edges"]),
            "pins_dependency_edges": len(mapped["pin_edges"]),
            "tests_edges": len(mapped["tests_edges"]),
            "unique_blobs_written": blob_count,
        },
        "files_by_type": dict(sorted(
            Counter(r.type_ for r in mapped["records"]).items(),
            key=lambda x: (-x[1], x[0]))),
        "files_by_language": dict(sorted(
            Counter((r.language or "(none)") for r in mapped["records"]).items(),
            key=lambda x: (-x[1], x[0]))),
        "artifacts": {
            "inventory.ttl": {"path": inv_path.name, "sha256": sha256_file(inv_path),
                              "size_bytes": inv_path.stat().st_size},
            "shapes.shacl.ttl": {"path": shapes_path.name, "sha256": sha256_file(shapes_path),
                                 "size_bytes": shapes_path.stat().st_size},
            "inventory.jsonld": {"path": jsonld_path.name, "sha256": sha256_file(jsonld_path),
                                 "size_bytes": jsonld_path.stat().st_size},
            "ontology-mapping.ttl": {"path": mapping_path.name, "sha256": sha256_file(mapping_path),
                                     "size_bytes": mapping_path.stat().st_size},
        },
        "shacl_self_check": {
            "conforms": bool(conforms),
            "report_excerpt": report_text[:2000] if not conforms else "",
        },
    }
    if extension_fragments:
        manifest["extensions"] = extension_fragments
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
