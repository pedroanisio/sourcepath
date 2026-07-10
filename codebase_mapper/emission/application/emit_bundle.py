"""codebase_mapper.emit_bundle."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

from collections import Counter
from pathlib import Path
from rdflib import URIRef

from ..infrastructure.storage.filesystem_blob_store import emit_blobs
from ...shared_kernel.constants import CBMI_NS, TOOL_VERSION, VOCABULARY_VERSION
from ...shared_kernel.extensions import (
    iter_artifact_emitters, iter_graph_contributors, iter_shape_contributors,
)
from ..infrastructure.rdf.fast_serializer import serialize_inventory
from ..infrastructure.rdf.streaming_jsonld import write_jsonld_streaming
from ...shared_kernel.json_safety import dump_ast_summary
from ..infrastructure.rdf.rdflib_emitter import (
    build_inventory_graph,
    build_ontology_mapping_graph,
    build_shacl_graph,
)
from ...inspection.tests_edges import count_rust_inline_test_files
from ...inspection.coverage import aggregate_coverage


def _flag_from_env(name: str) -> bool | None:
    """Tri-state env switch: None when unset/blank, else truthiness."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return None
    return raw in ("1", "true", "yes", "on")


def emit(repo_name: str, mapped: dict, out_dir: Path,
         emit_blobs_flag: bool = True, *,
         validate_shacl: bool | None = None,
         emit_jsonld: bool | None = None) -> dict:
    """Serialize the mapped repository into the bundle directory.

    ``validate_shacl=False`` skips the pySHACL self-check and
    ``emit_jsonld=False`` skips the JSON-LD serialization — both are
    cost controls for very large graphs, and both are disclosed in the
    manifest rather than silently absent (PALS's Law: a skipped check
    must never read as a passed one).

    When a flag is not passed explicitly it resolves from the
    environment — ``CBM_SKIP_SHACL`` truthy skips validation,
    ``CBM_EMIT_JSONLD`` falsy skips JSON-LD — so every entry point
    (including the main CLI) is cost-controllable without new flags.
    Explicit arguments always win; with neither argument nor env var,
    both steps run, exactly as before.
    """
    if validate_shacl is None:
        skip = _flag_from_env("CBM_SKIP_SHACL")
        validate_shacl = True if skip is None else not skip
    if emit_jsonld is None:
        env = _flag_from_env("CBM_EMIT_JSONLD")
        emit_jsonld = True if env is None else env
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_iri = URIRef(f"{CBMI_NS}repo/{repo_name}")
    truncated_ast_paths: list[str] = []
    inv = build_inventory_graph(
        repo_iri=repo_iri, commit_sha=mapped["commit"],
        records=mapped["records"], import_edges=mapped["import_edges"],
        import_ext_edges=mapped["import_ext_edges"],
        dep_edges=mapped["dep_edges"], pin_edges=mapped["pin_edges"],
        tests_edges=mapped["tests_edges"],
        possible_import_edges=mapped.get("possible_import_edges", []),
        truncated_ast_paths=truncated_ast_paths,
    )
    if truncated_ast_paths:
        # Disclosed through manifest["degradations"] below — a CST too deep
        # to serialize is dropped per-field, never silently (flaw F19).
        target = mapped.get("ctx")
        if target is not None:
            target.scratch.setdefault("degradations", []).append({
                "component": "emission",
                "reason": "ast_summary_depth_truncated",
                "affected_files": len(truncated_ast_paths),
                "paths_sample": truncated_ast_paths[:10],
            })
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
    # The inventory graph is the only artifact that reaches tens of
    # millions of triples; it goes through the Rust-backed fast path.
    # Shapes and ontology mapping are tiny — rdflib native is fine.
    inv_engine = serialize_inventory(inv, inv_path)
    shapes.serialize(destination=str(shapes_path), format="turtle")
    mapping.serialize(destination=str(mapping_path), format="turtle")

    jsonld_engine = None
    if emit_jsonld:
        # Streaming canonical writer (plan E8): N-Triples → external sort →
        # node-by-node emission. Peak memory is one subject group, not the
        # document — the step class that failed kernel-scale emits (F9/F19)
        # is gone. Byte-identical to the legacy rdflib path (parity suite:
        # tests/test_streaming_jsonld.py); blank-node graphs fall back.
        jsonld_engine = write_jsonld_streaming(inv, jsonld_path)
    else:
        # A stale inventory.jsonld from a previous emit into the same
        # directory would misrepresent this run's output set.
        jsonld_path.unlink(missing_ok=True)

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

    # --- AST extraction coverage asset (R2) ---
    # A mechanically-derived honesty table: per-language symbol yield,
    # parse errors, zero-AST files, and files that parsed cleanly yet
    # produced zero symbols (the tree-sitter-macro under-capture signal).
    # Always emitted so the bundle carries its own stated limitations.
    coverage_fragment = _emit_coverage_sidecar(mapped["records"], out_dir)

    if validate_shacl:
        from pyshacl import validate
        conforms, _vg, report_text = validate(
            data_graph=inv, shacl_graph=shapes, inference="none",
            abort_on_first=False, meta_shacl=False, advanced=False, debug=False,
        )
        shacl_self_check = {
            "conforms": bool(conforms),
            "report_excerpt": report_text[:2000] if not conforms else "",
        }
    else:
        # Skipped ≠ passed: conforms stays None and the skip is stated.
        shacl_self_check = {
            "conforms": None,
            "skipped": True,
            "report_excerpt": "",
        }

    def sha256_file(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    ast_full_bodies_python = 0
    ast_full_bodies_tsjs = 0
    ast_full_bodies_rust = 0
    ast_summary_total_bytes = 0
    for r in mapped["records"]:
        if r.ast_summary is None:
            continue
        ast_summary_total_bytes += len(dump_ast_summary(r.ast_summary)[0])
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
            "possible_import_edges": len(mapped.get("possible_import_edges", [])),
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
            p.name: {"path": p.name, "sha256": sha256_file(p),
                     "size_bytes": p.stat().st_size}
            for p in ([inv_path, shapes_path, mapping_path]
                      + ([jsonld_path] if emit_jsonld else []))
        },
        "shacl_self_check": shacl_self_check,
        # Provenance of the serialization itself: which engine produced
        # each artifact (fast path vs rdflib fallback).
        "emit_engines": {
            "inventory.ttl": inv_engine,
            **({"inventory.jsonld": jsonld_engine} if jsonld_engine else {}),
        },
        # Degradation disclosures registered by any layer during the run
        # (shallow-clone provenance, LLM self-disable, ...). Always
        # present: an empty list is the healthy-run statement, so absence
        # can never be misread as health (PALS's Law).
        "degradations": (
            list(ctx.scratch.get("degradations", []))
            if ctx is not None else []
        ),
    }
    if extension_fragments:
        manifest["extensions"] = extension_fragments
    # Always surfaced — the coverage asset is a first-class part of the
    # bundle contract (it states what extraction did and did not capture).
    manifest["ast_coverage"] = coverage_fragment
    if rust_items_fragment.get("n_items", 0) > 0:
        # Surface sidecar stats in the manifest so consumers can detect
        # its presence without listing the directory.
        manifest["rust_items_sidecar"] = rust_items_fragment
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


# ---------------------------------------------------------------------------
# AST extraction coverage asset (R2)
# ---------------------------------------------------------------------------


_AST_COVERAGE_SIDECAR = "ast_coverage.json"


def _emit_coverage_sidecar(records: list, out_dir: Path, *,
                           preview: int = 20) -> dict:
    """Write ``ast_coverage.json`` (the full, uncapped coverage report)
    and return a compact manifest fragment.

    The on-disk asset carries the complete silent-zero file list for
    auditing; the manifest fragment carries the aggregate counts plus a
    short preview (with a truncation flag) so a consumer can see the
    headline numbers without reading the file. Always emitted.
    """
    report = aggregate_coverage(records, max_listed=len(records) + 1)
    sidecar = out_dir / _AST_COVERAGE_SIDECAR
    sidecar.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    sha = hashlib.sha256(sidecar.read_bytes()).hexdigest()

    full_list = report["silent_zero_symbol_file_list"]
    return {
        "n_source_files": report["n_source_files"],
        "totals": report["totals"],
        "by_language": report["by_language"],
        "silent_zero_symbol_preview": full_list[:preview],
        "silent_zero_symbol_preview_truncated": len(full_list) > preview,
        "files": {
            _AST_COVERAGE_SIDECAR: {
                "path": _AST_COVERAGE_SIDECAR,
                "sha256": sha,
                "size_bytes": sidecar.stat().st_size,
            },
        },
    }


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
