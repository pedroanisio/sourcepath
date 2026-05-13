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

def test_orient_bundle_includes_cheatsheet_and_suggested_calls(live_bundle, bundle_name):
    p = dispatch("orient_bundle", {"bundle": bundle_name})
    assert p["bundle"]["name"] == bundle_name
    assert "cbm" in p["schema_hint"]["namespaces"]
    assert "skos" in p["schema_hint"]["namespaces"]
    assert {l["name"] for l in p["schema_hint"]["layers"]} >= {"L1 host", "L2 chunks_embeddings", "L3 concept_graph"}
    tools_named = {c["tool"] for c in p["suggested_first_calls"]}
    assert tools_named.issubset(set(HANDLERS))


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
