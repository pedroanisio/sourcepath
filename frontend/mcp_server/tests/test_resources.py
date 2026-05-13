"""Phase 4 tests — URI parsing, resource listing, resource reading,
and a transport-level round-trip via the in-memory MCP client.
"""
from __future__ import annotations

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from frontend.mcp_server import (
    INVALID_ARGUMENT,
    NOT_FOUND,
    ToolError,
    list_resource_templates,
    list_static_resources,
    parse_uri,
    read_resource,
)
from frontend.mcp_server.server import build_server


# --------------------------------------------------------------------------
# parse_uri — happy + reject
# --------------------------------------------------------------------------


@pytest.mark.parametrize("uri, expected_kind", [
    ("cbm://bundles", "bundles_index"),
    ("cbm://bundle/alpha/manifest", "bundle_manifest"),
    ("cbm://bundle/alpha/summary", "bundle_summary"),
    ("cbm://bundle/alpha/shapes.shacl.ttl", "bundle_shacl"),
    ("cbm://bundle/alpha/ontology-mapping.ttl", "bundle_ontology"),
    ("cbm://bundle/alpha/file/src/app.py", "file"),
    ("cbm://bundle/alpha/chunk/42", "chunk"),
    ("cbm://bundle/alpha/concept/schema", "concept"),
])
def test_parse_uri_dispatches_to_each_kind(uri, expected_kind):
    parsed = parse_uri(uri)
    assert parsed.kind == expected_kind


@pytest.mark.parametrize("uri, fragment", [
    ("https://example.com/foo", "scheme"),                # alien scheme
    ("file:///etc/passwd", "scheme"),                     # alien scheme
    ("cbm://", "unrecognized"),                           # missing path
    ("cbm://bundle/alpha/file/../../etc/passwd", "traversal"),  # path traversal
    ("cbm://bundle/../etc/manifest", "bundle"),           # bundle traversal
    ("cbm://bundle/alpha/concept/foo/bar", "concept"),    # concept with slash
    ("cbm://bundle/alpha/concept/..", "concept"),         # concept with ..
    ("cbm://bundle//manifest", "bundle"),                 # empty bundle
])
def test_parse_uri_rejects_malformed(uri, fragment):
    with pytest.raises(ToolError) as exc:
        parse_uri(uri)
    assert exc.value.code == INVALID_ARGUMENT
    assert fragment.lower() in str(exc.value).lower()


# --------------------------------------------------------------------------
# list_static_resources + list_resource_templates
# --------------------------------------------------------------------------


def test_list_static_resources_includes_bundles_index_and_per_bundle(bundle_name):
    resources = list_static_resources()
    uris = [str(r.uri) for r in resources]
    # the bundles index is always present
    assert "cbm://bundles" in uris
    # for the live bundle, all four static URIs exist
    for kind in ("manifest", "summary", "shapes.shacl.ttl", "ontology-mapping.ttl"):
        assert f"cbm://bundle/{bundle_name}/{kind}" in uris


def test_list_resource_templates_advertises_parametric_uris():
    templates = list_resource_templates()
    templates_by_uri = {t.uriTemplate: t for t in templates}
    assert "cbm://bundle/{bundle}/file/{path}" in templates_by_uri
    assert "cbm://bundle/{bundle}/chunk/{idx}" in templates_by_uri
    assert "cbm://bundle/{bundle}/concept/{name}" in templates_by_uri


def test_list_resource_templates_does_not_double_list_static_ones():
    templates = list_resource_templates()
    template_uris = {t.uriTemplate for t in templates}
    assert "cbm://bundle/{bundle}/manifest" not in template_uris
    assert "cbm://bundles" not in template_uris


# --------------------------------------------------------------------------
# read_resource per kind (direct dispatch, no MCP transport)
# --------------------------------------------------------------------------


def test_read_bundles_index_returns_list(bundle_name):
    contents = read_resource("cbm://bundles")
    assert len(contents) == 1
    payload = json.loads(contents[0].content)
    assert any(b["name"] == bundle_name for b in payload["bundles"])
    assert contents[0].mime_type == "application/json"


def test_read_bundle_manifest_returns_run_manifest(live_bundle, bundle_name):
    contents = read_resource(f"cbm://bundle/{bundle_name}/manifest")
    payload = json.loads(contents[0].content)
    assert payload.get("repo_name") == live_bundle.manifest.get("repo_name")
    assert "counts" in payload


def test_read_bundle_summary_matches_tool(bundle_name):
    contents = read_resource(f"cbm://bundle/{bundle_name}/summary")
    payload = json.loads(contents[0].content)
    assert payload["output_dir"].endswith(bundle_name)
    assert "files_by_language" in payload


def test_read_bundle_shacl_is_turtle(bundle_name):
    contents = read_resource(f"cbm://bundle/{bundle_name}/shapes.shacl.ttl")
    assert contents[0].mime_type == "text/turtle"
    assert "@prefix" in contents[0].content or "PREFIX" in contents[0].content.upper()


def test_read_bundle_ontology_is_turtle(bundle_name):
    contents = read_resource(f"cbm://bundle/{bundle_name}/ontology-mapping.ttl")
    assert contents[0].mime_type == "text/turtle"


def test_read_file_resource_matches_handler(live_bundle, bundle_name):
    # Pick any file with imports both ways
    target = next(
        r["path"] for r in live_bundle.files
        if live_bundle.imports_out.get(r["path"]) and live_bundle.imports_in.get(r["path"])
    )
    contents = read_resource(f"cbm://bundle/{bundle_name}/file/{target}")
    payload = json.loads(contents[0].content)
    assert payload["file"]["path"] == target
    assert payload["imports_out"]


def test_read_chunk_resource(bundle_name):
    contents = read_resource(f"cbm://bundle/{bundle_name}/chunk/0")
    payload = json.loads(contents[0].content)
    assert payload["chunk"]["idx"] == 0


def test_read_concept_resource(live_bundle, bundle_name):
    # Pick any concept
    name = next(iter(live_bundle.concepts.get("concepts", {})))
    contents = read_resource(f"cbm://bundle/{bundle_name}/concept/{name}")
    payload = json.loads(contents[0].content)
    assert payload["concept"]["label"] in (name, str(payload["concept"].get("label")))


def test_read_resource_unknown_bundle_404(bundle_name):  # noqa: ARG001
    with pytest.raises(ToolError) as exc:
        read_resource("cbm://bundle/__no_such_bundle__/manifest")
    assert exc.value.code == NOT_FOUND


def test_read_resource_unknown_file_404(bundle_name):
    with pytest.raises(ToolError) as exc:
        read_resource(f"cbm://bundle/{bundle_name}/file/does/not/exist.py")
    assert exc.value.code == NOT_FOUND


def test_read_resource_malformed_uri_is_invalid_argument():
    with pytest.raises(ToolError) as exc:
        read_resource("https://wrong/scheme")
    assert exc.value.code == INVALID_ARGUMENT


# --------------------------------------------------------------------------
# Transport round-trip — MCP client lists + reads via the in-memory server
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_transport_list_resources_includes_bundles_index(bundle_name):
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.list_resources()
    uris = {str(r.uri) for r in result.resources}
    assert "cbm://bundles" in uris
    assert f"cbm://bundle/{bundle_name}/manifest" in uris


@pytest.mark.anyio
async def test_transport_list_resource_templates(bundle_name):  # noqa: ARG001
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.list_resource_templates()
    uris = {t.uriTemplate for t in result.resourceTemplates}
    assert "cbm://bundle/{bundle}/file/{path}" in uris


@pytest.mark.anyio
async def test_transport_read_resource_round_trip(bundle_name):
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.read_resource(f"cbm://bundle/{bundle_name}/summary")
    assert result.contents
    txt = "".join(c.text for c in result.contents if hasattr(c, "text"))
    payload = json.loads(txt)
    assert payload["output_dir"].endswith(bundle_name)


@pytest.mark.anyio
async def test_transport_read_resource_invalid_uri_returns_error(bundle_name):  # noqa: ARG001
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        with pytest.raises(Exception):  # SDK raises on JSON-RPC errors  # noqa: BLE001
            await client.read_resource("https://example.com/not-a-cbm-uri")


# --------------------------------------------------------------------------
# anyio backend
# --------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"
