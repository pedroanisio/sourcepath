"""Phase 7 — targeted tests filling coverage gaps from phases 1-6.

These exercise the rejection branches in validators.py, missing-file
paths for shapes/ontology resources, watcher edge cases, and the
configure_logging side effects. The subprocess-only paths in
``run_stdio`` and ``main`` are exercised by ``test_server.py::
test_stdout_is_pure_jsonrpc`` but coverage.py doesn't span process
boundaries — those are marked ``# pragma: no cover`` in the source.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from frontend.mcp_server import (
    INVALID_ARGUMENT,
    NOT_FOUND,
    ManifestWatcher,
    SubscriptionManager,
    ToolError,
    manifest_uri,
    read_resource,
)


# --------------------------------------------------------------------------
# validators.py — every rejection branch
# --------------------------------------------------------------------------

from frontend.mcp_server.validators import (
    truncate_text,
    validate_bundle_name,
    validate_relative_path,
    validate_sha256,
)


@pytest.mark.parametrize("bad", ["", "a/b", "a\\b", "..", "a/..", ".hidden"])
def test_validate_bundle_name_rejects(bad):
    with pytest.raises(ToolError) as exc:
        validate_bundle_name(bad)
    assert exc.value.code == INVALID_ARGUMENT


@pytest.mark.parametrize("bad", ["", "/etc/passwd", "\\windows\\x", "../etc/passwd", "a/../etc"])
def test_validate_relative_path_rejects(bad):
    with pytest.raises(ToolError) as exc:
        validate_relative_path(bad)
    assert exc.value.code == INVALID_ARGUMENT


@pytest.mark.parametrize("bad", ["", "abc", "g" * 64, "a" * 63, "A" * 64])
def test_validate_sha256_rejects(bad):
    with pytest.raises(ToolError) as exc:
        validate_sha256(bad)
    assert exc.value.code == INVALID_ARGUMENT


def test_truncate_text_none():
    assert truncate_text(None, 10) == (None, False)


def test_truncate_text_within_budget():
    text, was = truncate_text("hello", 100)
    assert text == "hello"
    assert was is False


def test_truncate_text_oversize_keeps_utf8_boundary():
    # A multi-byte string with budget that lands mid-character
    text = "é" * 100  # each é is 2 bytes in UTF-8
    truncated, was = truncate_text(text, 5)
    assert was is True
    # Re-encoding the truncated piece must succeed and stay under budget
    assert len(truncated.encode("utf-8")) <= 5
    # Must contain only complete characters
    assert all(ord(c) > 0 for c in truncated)


def test_truncate_text_oversize_ascii():
    truncated, was = truncate_text("a" * 50, 10)
    assert was is True
    assert truncated == "a" * 10


# --------------------------------------------------------------------------
# resources.py — shapes / ontology missing
# --------------------------------------------------------------------------


def _make_minimal_bundle(root: Path, name: str = "tiny") -> str:
    """Create a bundle with only a manifest + inventory.ttl — no shapes/ontology."""
    import json
    p = root / name
    p.mkdir()
    (p / "run_manifest.json").write_text(json.dumps({
        "repo_name": "tiny", "counts": {"files": 0},
        "files_by_language": {}, "files_by_type": {},
        "tool_version": "0.0.0", "generated_at": "2026-01-01T00:00:00Z",
    }))
    (p / "inventory.ttl").write_text("@prefix cbm: <https://x/cbm#> .\n")
    return name


def test_read_bundle_shacl_missing_404(tmp_path, monkeypatch):
    name = _make_minimal_bundle(tmp_path)
    monkeypatch.setenv("CBM_BUNDLES_ROOT", str(tmp_path))
    monkeypatch.setenv("CBM_OUTPUT_DIR", str(tmp_path / name))
    # New bundle path → clear the cache to force a fresh load
    import app as backend_app
    backend_app.get_bundle.cache_clear()
    with pytest.raises(ToolError) as exc:
        read_resource(f"cbm://bundle/{name}/shapes.shacl.ttl")
    assert exc.value.code == NOT_FOUND
    assert "shapes" in str(exc.value)


def test_read_bundle_ontology_missing_404(tmp_path, monkeypatch):
    name = _make_minimal_bundle(tmp_path)
    monkeypatch.setenv("CBM_BUNDLES_ROOT", str(tmp_path))
    monkeypatch.setenv("CBM_OUTPUT_DIR", str(tmp_path / name))
    import app as backend_app
    backend_app.get_bundle.cache_clear()
    with pytest.raises(ToolError) as exc:
        read_resource(f"cbm://bundle/{name}/ontology-mapping.ttl")
    assert exc.value.code == NOT_FOUND
    assert "ontology" in str(exc.value)


# --------------------------------------------------------------------------
# server.py — error remapping branches + configure_logging
# --------------------------------------------------------------------------


def test_configure_logging_replaces_handlers_with_stderr():
    from frontend.mcp_server.server import configure_logging

    root = logging.getLogger()
    # Install a sentinel handler we can detect
    sentinel = logging.NullHandler()
    root.addHandler(sentinel)
    try:
        configure_logging()
        # Sentinel must have been swept away
        assert sentinel not in root.handlers
        # Exactly one handler, on stderr
        assert len(root.handlers) == 1
        h = root.handlers[0]
        assert isinstance(h, logging.StreamHandler)
        assert h.stream is sys.stderr
    finally:
        # Restore a sane state for any subsequent tests
        for h in list(root.handlers):
            root.removeHandler(h)


@pytest.mark.anyio
async def test_call_tool_remaps_tool_error_to_runtime_error(bundle_name):
    """Cover the ToolError → RuntimeError branch in _call_tool."""
    from mcp.shared.memory import create_connected_server_and_client_session

    from frontend.mcp_server.server import build_server

    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        # file_detail with a valid-looking but nonexistent path triggers
        # ToolError(NOT_FOUND) inside the handler → reraised as RuntimeError
        # → SDK packs into an error CallToolResult.
        result = await client.call_tool(
            "file_detail",
            {"bundle": bundle_name, "path": "does/not/exist.foo"},
        )
    assert result.isError is True
    txt = "".join(c.text for c in result.content if hasattr(c, "text"))
    assert "not_found" in txt.lower()


@pytest.mark.anyio
async def test_read_resource_remaps_tool_error(bundle_name):
    """Cover the ToolError → RuntimeError branch in _read_resource."""
    from mcp.shared.memory import create_connected_server_and_client_session

    from frontend.mcp_server.server import build_server

    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        with pytest.raises(Exception):  # noqa: BLE001
            await client.read_resource(
                f"cbm://bundle/{bundle_name}/file/does/not/exist.foo"
            )


# --------------------------------------------------------------------------
# subscriptions.py — watcher edges
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_watcher_root_missing_returns_empty(tmp_path: Path):
    notified: list[str] = []

    async def cb(uri: str) -> None:
        notified.append(uri)

    w = ManifestWatcher(root=tmp_path / "does-not-exist", on_change=cb, interval=0)
    await w.poll_once()  # seed empty
    await w.poll_once()  # second poll still empty
    assert notified == []


@pytest.mark.anyio
async def test_watcher_skips_non_directories(tmp_path: Path):
    """A file (not a dir) under the bundles root must not crash the scan."""
    notified: list[str] = []

    async def cb(uri: str) -> None:
        notified.append(uri)

    (tmp_path / "stray-file.txt").write_text("noise")
    w = ManifestWatcher(root=tmp_path, on_change=cb, interval=0)
    await w.poll_once()  # seed
    assert notified == []


@pytest.mark.anyio
async def test_watcher_callback_exception_does_not_break_loop(tmp_path):
    """A failing callback for one bundle still lets the watcher continue."""
    import os
    bundle = tmp_path / "alpha"
    bundle.mkdir()
    (bundle / "run_manifest.json").write_text("{}")
    calls = {"n": 0}

    async def cb(uri: str) -> None:
        calls["n"] += 1
        raise RuntimeError("boom")

    w = ManifestWatcher(root=tmp_path, on_change=cb, interval=0)
    await w.poll_once()  # seed
    # bump mtime
    m = bundle / "run_manifest.json"
    os.utime(m, (m.stat().st_mtime + 5, m.stat().st_mtime + 5))
    notified = await w.poll_once()
    # callback raised but watcher logs + continues; nothing returned because
    # the URI wasn't successfully delivered (the function captures that)
    assert calls["n"] == 1
    assert notified == []  # the watcher swallowed the exception


@pytest.mark.anyio
async def test_subscription_manager_on_subscribe_hook():
    fired: list[str] = []
    sm = SubscriptionManager(on_subscribe=lambda uri: fired.append(uri))

    class _Sess:
        async def send_resource_updated(self, uri):
            pass

    await sm.subscribe(manifest_uri("alpha"), _Sess())
    assert fired == [manifest_uri("alpha")]


@pytest.mark.anyio
async def test_subscription_manager_is_subscribable_handles_bad_uri():
    sm = SubscriptionManager()
    # Returns False (doesn't raise) on a totally malformed URI
    assert sm.is_subscribable("not-a-uri") is False


# --------------------------------------------------------------------------
# anyio backend
# --------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"
