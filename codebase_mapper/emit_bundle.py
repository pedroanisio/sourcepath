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
from .tests_edges import count_rust_inline_test_files


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

    # --- Stage 4: Rust items sidecar ---
    # rust_items.jsonl: one line per Rust function/method/struct/enum/
    # trait/impl/mod/etc. that carries at least one attribute. Lets the
    # MCP layer answer "every #[test] function" / "every struct with
    # #[derive(Debug, Clone)]" without re-parsing every ast_summary at
    # query time. Always emitted (zero-byte file when no attributes).
    rust_items_fragment = _emit_rust_items_sidecar(mapped["records"], out_dir)

    from pyshacl import validate
    conforms, _vg, report_text = validate(
        data_graph=inv, shacl_graph=shapes, inference="none",
        abort_on_first=False, meta_shacl=False, advanced=False, debug=False,
    )

    def sha256_file(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    ast_full_bodies_python = 0
    ast_full_bodies_tsjs = 0
    ast_full_bodies_rust = 0
    ast_summary_total_bytes = 0
    for r in mapped["records"]:
        if r.ast_summary is None:
            continue
        ast_summary_total_bytes += len(json.dumps(r.ast_summary, sort_keys=True))
        if r.language == "python" and r.ast_summary.get("ast_json") is not None:
            ast_full_bodies_python += 1
        elif (r.language in ("typescript", "javascript")
              and r.ast_summary.get("cst_json") is not None):
            ast_full_bodies_tsjs += 1
        elif r.language == "rust" and r.ast_summary.get("cst_json") is not None:
            ast_full_bodies_rust += 1

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
            "ast_full_bodies_python": ast_full_bodies_python,
            "ast_full_bodies_tsjs": ast_full_bodies_tsjs,
            "ast_full_bodies_rust": ast_full_bodies_rust,
            "ast_summary_total_bytes": ast_summary_total_bytes,
            # Rust-specific: source files containing inline #[test]
            # functions (the #[cfg(test)] mod tests pattern). Surfaces
            # tests the path classifier can't see.
            "rust_files_with_inline_tests": count_rust_inline_test_files(
                mapped["records"],
            ),
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
    if rust_items_fragment.get("n_items", 0) > 0:
        # Surface sidecar stats in the manifest so consumers can detect
        # its presence without listing the directory.
        manifest["rust_items_sidecar"] = rust_items_fragment
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


# ---------------------------------------------------------------------------
# Rust items sidecar (Stage 4)
# ---------------------------------------------------------------------------


_RUST_ITEMS_SIDECAR = "rust_items.jsonl"


def _emit_rust_items_sidecar(records: list, out_dir: Path) -> dict:
    """Write ``rust_items.jsonl`` listing every Rust item that carries
    at least one attribute. Schema is one flat JSON object per line so
    streaming readers don't need an indexer.

    Returns a manifest fragment with counts. The file is always created
    (zero-byte when no attributes) so the bundle contract is uniform.
    """
    sidecar = out_dir / _RUST_ITEMS_SIDECAR
    rows: list[dict] = []
    for r in records:
        if getattr(r, "language", None) != "rust":
            continue
        if r.ast_summary is None:
            continue
        items = r.ast_summary.get("items") or []
        for item in items:
            attrs = item.get("attributes") or []
            if not attrs:
                continue
            rows.append({
                "path": r.path,
                "kind": item.get("kind"),
                "name": item.get("name"),
                "parent": item.get("parent"),
                "line_start": item.get("begin_line"),
                "line_end": item.get("end_line"),
                "byte_start": item.get("begin_byte"),
                "byte_end": item.get("end_byte"),
                "is_pub": bool(item.get("is_pub", False)),
                "is_async": bool(item.get("is_async", False)),
                "attributes": list(attrs),
            })
    # Deterministic sort: (path, line_start, name).
    rows.sort(key=lambda r: (r["path"], r["line_start"] or 0, r["name"] or ""))

    sidecar.write_text(
        "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n"
                for r in rows)
    )
    sha = (hashlib.sha256(sidecar.read_bytes()).hexdigest()
           if rows else hashlib.sha256(b"").hexdigest())

    attr_counter: Counter = Counter()
    for row in rows:
        for attr in row["attributes"]:
            attr_counter[attr] += 1

    return {
        "n_items": len(rows),
        "n_files": len({r["path"] for r in rows}),
        "by_kind": dict(Counter(r["kind"] for r in rows)),
        "top_attributes": attr_counter.most_common(20),
        "files": {
            _RUST_ITEMS_SIDECAR: {
                "path": _RUST_ITEMS_SIDECAR,
                "sha256": sha,
                "size_bytes": sidecar.stat().st_size,
            },
        },
    }
