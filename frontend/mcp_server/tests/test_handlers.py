"""Phase 2 contract tests — every handler returns a payload that conforms
to its outputSchema, driven against the live ``_tmp/usl-ng-core-map``
bundle.

Each handler also has a few negative/edge-case assertions to lock in
behavior (ToolError on missing path, lexical fallback when sbert isn't
available, etc.).
"""
from __future__ import annotations

import pytest

from frontend.mcp_server import (
    DESCRIPTIONS,
    HANDLERS,
    INPUT_SCHEMAS,
    NOT_FOUND,
    INVALID_ARGUMENT,
    OUTPUT_SCHEMAS,
    ToolError,
    dispatch,
    validate_out,
)


# --------------------------------------------------------------------------
# Sanity: registry matches schemas
# --------------------------------------------------------------------------

def test_registry_covers_every_tool():
    missing = set(INPUT_SCHEMAS) - set(HANDLERS)
    assert not missing, f"tools missing a handler: {sorted(missing)}"


def test_every_handler_has_description():
    missing = set(HANDLERS) - set(DESCRIPTIONS)
    assert not missing, f"handlers missing a description: {sorted(missing)}"


# --------------------------------------------------------------------------
# Dispatcher error paths
# --------------------------------------------------------------------------

def test_dispatch_unknown_tool_raises():
    with pytest.raises(ToolError) as exc:
        dispatch("nope")
    assert exc.value.code == NOT_FOUND


def test_dispatch_input_schema_rejected_before_handler():
    from jsonschema import ValidationError

    with pytest.raises(ValidationError):
        dispatch("file_detail", {"path": ""})  # minLength: 1


# --------------------------------------------------------------------------
# Bundle-listing tools (no live bundle required, but easier with one)
# --------------------------------------------------------------------------

def test_list_bundles_lists_the_live_bundle(bundle_name):
    payload = dispatch("list_bundles", {})
    names = [b["name"] for b in payload["bundles"]]
    assert bundle_name in names


def test_select_bundle_validates_existence(bundle_name):
    payload = dispatch("select_bundle", {"bundle": bundle_name})
    assert payload == {"selected": bundle_name}


def test_select_bundle_rejects_path_traversal():
    from jsonschema import ValidationError

    # Schema pattern catches `../etc` first; if it didn't, the validator
    # would catch it on the way through select_bundle's handler. Either
    # rejection is fine — both prove the input never reaches the
    # bundle-resolution code.
    with pytest.raises((ValidationError, ToolError)):
        dispatch("select_bundle", {"bundle": "../etc"})


def test_select_bundle_404_on_unknown(bundle_name):
    with pytest.raises(ToolError) as exc:
        dispatch("select_bundle", {"bundle": "no-such-bundle-xyz"})
    assert exc.value.code == NOT_FOUND


# --------------------------------------------------------------------------
# orient + bundle_summary
# --------------------------------------------------------------------------

def test_orient_bundle_advertises_dependency_predicates(live_bundle, bundle_name):
    """#1: cross-package / external dependency edges live under
    ``cbm:importsExternal`` (workspace + third-party imports),
    ``cbm:declaresDependency`` and ``cbm:packageName``. orient_bundle's L1
    schema hint must advertise them — otherwise an analyst following the hint
    has no pointer to where the cross-package dependency graph is stored and
    wrongly concludes it cannot be extracted."""
    p = dispatch("orient_bundle", {"bundle": bundle_name})
    l1 = next(l for l in p["schema_hint"]["layers"] if l["name"] == "L1 host")
    preds = set(l1["key_predicates"])
    assert {"cbm:importsExternal", "cbm:declaresDependency", "cbm:packageName"} <= preds, preds


def test_file_detail_surfaces_external_imports(live_bundle, representative_file):
    """#2: file_detail must expose a file's external/workspace imports, not only
    its internal file->file edges. The list may be empty for a given file, but
    the field must always be present (and conform to the output schema)."""
    payload = dispatch("file_detail", {"path": representative_file})
    assert "external_imports" in payload, payload.keys()
    assert isinstance(payload["external_imports"], list)


def test_orient_bundle_includes_cheatsheet_and_suggested_calls(live_bundle, bundle_name):
    p = dispatch("orient_bundle", {"bundle": bundle_name})
    assert p["bundle"]["name"] == bundle_name
    assert "cbm" in p["schema_hint"]["namespaces"]
    assert "skos" in p["schema_hint"]["namespaces"]
    layer_names = {l["name"] for l in p["schema_hint"]["layers"]}
    # All four layers MUST be declared by orient_bundle so MCP clients learn
    # the L4 contract even on bundles built without --llm-enrich.
    assert layer_names >= {"L1 host", "L2 chunks_embeddings", "L3 concept_graph", "L4 llm_enrich"}
    # Namespaces must include cbml4 for the same reason.
    assert "cbml4" in p["schema_hint"]["namespaces"]
    # L4 must be flagged optional so clients know it can be empty/absent.
    l4 = next(l for l in p["schema_hint"]["layers"] if l["name"] == "L4 llm_enrich")
    assert l4.get("optional") is True
    tools_named = {c["tool"] for c in p["suggested_first_calls"]}
    assert tools_named.issubset(set(HANDLERS))
    # Every namespace the producer can emit MUST be declared by orient_bundle.
    # Regression guard: when a future L5+ layer ships, this assertion forces
    # the new namespace to be added here before the producer can ship it.
    # Without this guard, the L4 second-class defect (cbml4 emitted as data
    # but never declared by orient_bundle) can re-occur for any future layer.
    declared_ns = set(p["schema_hint"]["namespaces"])
    required_ns = {"cbm", "cbml2", "cbml3", "cbml4", "skos", "nif"}
    missing = required_ns - declared_ns
    assert not missing, (
        f"orient_bundle.namespaces omits {missing}; every namespace the "
        f"producer can emit (cbml2/3/4) MUST be declared so MCP clients can "
        f"interpret cbml*: triples. See docs/cbm-l4-second-class-impact.md."
    )
    # Every layer entry must reference a namespace via its key_predicates.
    # This couples the layer table to the namespace map: dropping a layer's
    # namespace would silently strand its predicates.
    declared_prefixes = {ns + ":" for ns in declared_ns}
    for layer in p["schema_hint"]["layers"]:
        preds = layer.get("key_predicates", [])
        if not preds:
            continue
        assert any(pred.startswith(tuple(declared_prefixes)) for pred in preds), (
            f"layer {layer['name']!r} declares predicates {preds} but none "
            f"match a declared namespace prefix in {sorted(declared_ns)}"
        )


def test_bundle_summary_counts(live_bundle, bundle_name):
    p = dispatch("bundle_summary", {"bundle": bundle_name})
    assert p["counts"]["files"] > 0
    assert p["n_chunks"] > 0
    assert p["n_concepts"] > 0
    assert p["files_by_language"]
    assert p["files_by_type"]


# --------------------------------------------------------------------------
# list_files
# --------------------------------------------------------------------------

def test_list_files_default_sorted_by_degree(live_bundle, bundle_name):
    p = dispatch("list_files", {"bundle": bundle_name, "limit": 5})
    assert len(p["files"]) == 5
    # We can't assert exact paths but degree-sort should put a heavy file first
    assert p["total"] > 5
    assert p["truncated"] is True


def test_list_files_filter_by_language(live_bundle, bundle_name):
    p = dispatch("list_files", {"bundle": bundle_name, "language": "python", "limit": 20})
    assert all(r["language"] == "python" for r in p["files"])


def test_list_files_prefix_filter(live_bundle, bundle_name):
    p = dispatch("list_files", {"bundle": bundle_name, "prefix": "crates", "sort": "path", "limit": 5})
    assert all(r["path"].startswith("crates/") or r["path"] == "crates" for r in p["files"])


# --------------------------------------------------------------------------
# file_detail / file_impact / imports_*
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def representative_file(live_bundle):
    """Pick a file with both outgoing and incoming imports — that's the
    interesting case for file_detail and file_impact."""
    for r in live_bundle.files:
        path = r["path"]
        if live_bundle.imports_out.get(path) and live_bundle.imports_in.get(path):
            return path
    # Fallback: any file with outgoing imports
    for r in live_bundle.files:
        path = r["path"]
        if live_bundle.imports_out.get(path):
            return path
    raise RuntimeError("bundle has no files with imports — fixture broken")


def test_file_detail_round_trip(live_bundle, bundle_name, representative_file):
    p = dispatch("file_detail", {"bundle": bundle_name, "path": representative_file})
    assert p["file"]["path"] == representative_file
    for chunk in p["chunks"]:
        assert chunk["idx"] is not None


def test_file_detail_404(live_bundle, bundle_name):
    with pytest.raises(ToolError) as exc:
        dispatch("file_detail", {"bundle": bundle_name, "path": "does/not/exist.foo"})
    assert exc.value.code == NOT_FOUND


def test_file_detail_path_traversal_rejected(live_bundle, bundle_name):
    with pytest.raises(ToolError) as exc:
        dispatch("file_detail", {"bundle": bundle_name, "path": "../etc/passwd"})
    assert exc.value.code == INVALID_ARGUMENT


def test_imports_of_one_hop(live_bundle, bundle_name, representative_file):
    p = dispatch("imports_of", {"bundle": bundle_name, "path": representative_file})
    assert p["file"] == representative_file
    assert p["imports"] == sorted(live_bundle.imports_out.get(representative_file, []))


def test_imported_by_one_hop(live_bundle, bundle_name, representative_file):
    p = dispatch("imported_by", {"bundle": bundle_name, "path": representative_file})
    assert p["file"] == representative_file
    assert p["imported_by"] == sorted(live_bundle.imports_in.get(representative_file, []))


def test_file_impact_transitive(live_bundle, bundle_name, representative_file):
    p = dispatch("file_impact", {"bundle": bundle_name, "path": representative_file, "depth": 2})
    assert p["file"] == representative_file
    assert p["depth"] == 2
    # transitive should be a superset of direct (or equal at depth 1)
    direct_deps = set(p["direct_dependencies"])
    transitive_deps = set(p["transitive_dependencies"])
    assert direct_deps.issubset(transitive_deps) or direct_deps == set()


# --------------------------------------------------------------------------
# chunks: list / detail / blob / semantic_neighbors
# --------------------------------------------------------------------------

def test_list_chunks_has_idx(live_bundle, bundle_name):
    p = dispatch("list_chunks", {"bundle": bundle_name, "limit": 10})
    assert p["mode"] == "lexical"
    assert all(c["idx"] is not None for c in p["chunks"])
    assert p["total"] >= len(p["chunks"])


def test_list_chunks_lexical_filter(live_bundle, bundle_name):
    p = dispatch("list_chunks", {"bundle": bundle_name, "q": "schema", "limit": 5})
    for c in p["chunks"]:
        joined = (c.get("symbol") or "") + (c.get("file") or "")
        assert "schema" in joined.lower()


def test_chunk_detail_blob_preview_for_file_kind(live_bundle, bundle_name):
    # find a file-kind chunk; only those have materialized blobs
    page = dispatch("list_chunks", {"bundle": bundle_name, "limit": 200})
    file_chunks = [c for c in page["chunks"] if c["kind"] == "file"]
    assert file_chunks, "expected at least one file-kind chunk in the live bundle"
    p = dispatch("chunk_detail", {"bundle": bundle_name, "idx": file_chunks[0]["idx"]})
    assert p["chunk"]["idx"] == file_chunks[0]["idx"]
    assert isinstance(p["blob_preview"], str)
    # 2KB preview cap
    assert len(p["blob_preview"].encode("utf-8")) <= 2048


def test_chunk_detail_404(live_bundle, bundle_name):
    with pytest.raises(ToolError):
        dispatch("chunk_detail", {"bundle": bundle_name, "idx": 999_999_999})


def test_chunk_blob_invalid_sha(live_bundle, bundle_name):
    from jsonschema import ValidationError

    with pytest.raises(ValidationError):
        dispatch("chunk_blob", {"bundle": bundle_name, "sha": "abc"})


def test_chunk_blob_round_trip(live_bundle, bundle_name):
    # Look up a file-kind chunk's contentSha256 (file-level blobs are materialized)
    page = dispatch("list_chunks", {"bundle": bundle_name, "limit": 200})
    file_chunks = [c for c in page["chunks"] if c["kind"] == "file"]
    sha = next(c["contentSha256"] for c in file_chunks if c.get("contentSha256"))
    p = dispatch("chunk_blob", {"bundle": bundle_name, "sha": sha})
    assert p["sha256"] == sha
    assert isinstance(p["text"], str)


def test_semantic_neighbors_mode_matches_backend(live_bundle, bundle_name):
    """When the bundle's embeddings backend is sbert-shaped we expect
    ``semantic`` mode and real cosine scores; otherwise lexical fallback."""
    summary = dispatch("bundle_summary", {"bundle": bundle_name})
    backend = (summary.get("embeddings_backend") or "").lower()
    is_sbert = any(s in backend for s in ("sentence-transformer", "sbert", "minilm"))

    p = dispatch("semantic_neighbors", {"bundle": bundle_name, "q": "schema", "k": 5})
    if is_sbert:
        assert p["mode"] == "semantic"
        assert all(c.get("score") is not None for c in p["chunks"])
    else:
        assert p["mode"] == "lexical"
        for c in p["chunks"]:
            joined = (c.get("symbol") or "") + (c.get("file") or "")
            assert "schema" in joined.lower()
    assert p["chunks"], f"semantic_neighbors returned no results for backend={backend!r}"


# --------------------------------------------------------------------------
# concepts
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def heavy_concept(live_bundle):
    """Pick a concept with non-empty cooccurrence."""
    for name, neighbors in live_bundle.cooccur.items():
        if len(neighbors) >= 3:
            return name
    raise RuntimeError("no concept with ≥3 neighbors in bundle — fixture broken")


def test_concept_detail_shape(live_bundle, bundle_name, heavy_concept):
    p = dispatch("concept_detail", {"bundle": bundle_name, "name": heavy_concept})
    assert p["concept"]["label"]
    assert p["concept"]["frequency"] > 0
    # cooccurring sorted by weight desc
    weights = [c["weight"] for c in p["cooccurring"]]
    assert weights == sorted(weights, reverse=True)


def test_concept_detail_404(live_bundle, bundle_name):
    with pytest.raises(ToolError) as exc:
        dispatch("concept_detail", {"bundle": bundle_name, "name": "__no_such_concept__"})
    assert exc.value.code == NOT_FOUND


def test_concept_neighborhood_depth_1(live_bundle, bundle_name, heavy_concept):
    p = dispatch("concept_neighborhood", {"bundle": bundle_name, "name": heavy_concept, "depth": 1, "limit": 5})
    assert p["root"] == heavy_concept
    assert all(n["depth"] == 1 for n in p["neighbors"])
    assert len(p["neighbors"]) <= 5


def test_concept_neighborhood_depth_2_expands(live_bundle, bundle_name, heavy_concept):
    p1 = dispatch("concept_neighborhood", {"bundle": bundle_name, "name": heavy_concept, "depth": 1, "limit": 100})
    p2 = dispatch("concept_neighborhood", {"bundle": bundle_name, "name": heavy_concept, "depth": 2, "limit": 100})
    # depth 2 sees at least as many neighbors as depth 1
    assert len(p2["neighbors"]) >= len(p1["neighbors"])
    assert any(n["depth"] >= 2 for n in p2["neighbors"]) or len(p2["neighbors"]) == len(p1["neighbors"])


# --------------------------------------------------------------------------
# Defence-in-depth: every handler's payload validates against outputSchema
# --------------------------------------------------------------------------

@pytest.fixture(scope="session")
def representative_args(live_bundle, bundle_name, representative_file, heavy_concept):
    """Build a {tool -> args} map that exercises every handler against
    the live bundle. file-kind chunk idx is chosen so the blob_preview
    field is populated."""
    page = HANDLERS["list_chunks"]({"bundle": bundle_name, "limit": 200}, None)
    file_chunks = [c for c in page["chunks"] if c["kind"] == "file"]
    sha = next(c["contentSha256"] for c in file_chunks if c.get("contentSha256"))
    return {
        "orient_bundle": {"bundle": bundle_name},
        "bundle_summary": {"bundle": bundle_name},
        "list_bundles": {},
        "select_bundle": {"bundle": bundle_name},
        "list_files": {"bundle": bundle_name, "limit": 5},
        "file_detail": {"bundle": bundle_name, "path": representative_file},
        "file_impact": {"bundle": bundle_name, "path": representative_file, "depth": 2},
        "imports_of": {"bundle": bundle_name, "path": representative_file},
        "imported_by": {"bundle": bundle_name, "path": representative_file},
        "chunk_detail": {"bundle": bundle_name, "idx": file_chunks[0]["idx"]},
        "chunk_blob": {"bundle": bundle_name, "sha": sha},
        "list_chunks": {"bundle": bundle_name, "limit": 5},
        "semantic_neighbors": {"bundle": bundle_name, "q": "schema", "k": 5},
        "concept_detail": {"bundle": bundle_name, "name": heavy_concept},
        "concept_neighborhood": {"bundle": bundle_name, "name": heavy_concept, "depth": 1, "limit": 5},
        "repository_summary": {"bundle": bundle_name},
        "items_by_attribute": {"bundle": bundle_name, "pattern": "#[derive(", "limit": 5},
    }


def test_every_handler_payload_validates(representative_args):
    """The exit criterion for Phase 2: every handler returns a payload that
    passes its outputSchema against the live bundle."""
    failures: list[str] = []
    for tool, args in representative_args.items():
        try:
            payload = dispatch(tool, args)
            validate_out(tool, payload)
        except Exception as e:  # noqa: BLE001 — we want to report all of them
            failures.append(f"{tool}: {type(e).__name__}: {e}")
    assert not failures, "handler/schema mismatches:\n  " + "\n  ".join(failures)


def test_representative_args_covers_every_tool(representative_args):
    """Live-validation completeness (drift-risk H4): a new tool must join
    the representative_args matrix — where its output is validated against
    OUTPUT_SCHEMAS on a real bundle — or be excluded here with a reason.
    Silent omission is how an advertised schema starts lying."""
    excluded = {
        # Env-gated (CBM_ENABLE_SPARQL); hardening + output shape are
        # exercised directly by test_sparql.py.
        "sparql",
    }
    assert set(representative_args) == set(HANDLERS) - excluded


def test_orient_bundle_advertised_predicates_are_emitter_real(live_bundle, bundle_name):
    """Every predicate orient_bundle's schema_hint advertises must be one
    the emitter actually writes under the same namespace (drift-risk H4:
    the advertised map must not lie to agents)."""
    import re
    from pathlib import Path

    ns_consts = {"cbm": "CBM", "cbmt": "CBMT", "cbmp": "CBMP", "cbmi": "CBMI",
                 "cbmxr": "CBMXR", "cbml2": "CBML2", "cbml3": "CBML3",
                 "cbml4": "CBML4", "skos": "SKOS", "nif": "NIF", "rdf": None,
                 "rdfs": None, "spdx": None}
    repo = Path(__file__).resolve().parents[3]
    sources = "\n".join(
        p.read_text(errors="ignore")
        for root in ("codebase_mapper", "plugins")
        for p in (repo / root).rglob("*.py"))

    payload = dispatch("orient_bundle", {"bundle": bundle_name})
    bad: list[str] = []
    for layer in payload["schema_hint"]["layers"]:
        for curie in layer.get("key_predicates", []):
            prefix, _, suffix = curie.partition(":")
            if prefix not in ns_consts:
                bad.append(f"{curie} (unknown namespace)")
                continue
            const = ns_consts[prefix]
            if const is None:  # framework namespaces: nothing to cross-check
                continue
            if not re.search(
                    rf'\b{const}\.{suffix}\b|\b{const}\["{suffix}"\]'
                    rf'|"{suffix}"', sources):
                bad.append(curie)
    assert not bad, f"advertised but never emitted under that namespace: {bad}"
