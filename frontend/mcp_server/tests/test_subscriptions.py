"""Phase 6 tests — subscriptions + manifest watcher + transport push.

* Unit: SubscriptionManager + ManifestWatcher in isolation.
* Transport: subscribe via MCP client, simulate a manifest change,
  assert the client receives a ``notifications/resources/updated`` push
  with the right URI.
* Cache invalidation: changing a manifest's mtime clears the cached
  Bundle so the next read sees fresh data.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from frontend.mcp_server import (
    INVALID_ARGUMENT,
    ManifestWatcher,
    SubscriptionManager,
    ToolError,
    manifest_uri,
)
from frontend.mcp_server.server import build_server, manifest_changed


# --------------------------------------------------------------------------
# SubscriptionManager — unit
# --------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, *, fail: bool = False) -> None:
        self.received: list[str] = []
        self._fail = fail

    async def send_resource_updated(self, uri) -> None:
        if self._fail:
            raise RuntimeError("connection closed")
        self.received.append(str(uri))


@pytest.mark.anyio
async def test_subscription_manager_rejects_non_manifest_uri():
    sm = SubscriptionManager()
    s = _FakeSession()
    with pytest.raises(ToolError) as exc:
        await sm.subscribe("cbm://bundle/alpha/summary", s)
    assert exc.value.code == INVALID_ARGUMENT
    assert sm.subscriber_count("cbm://bundle/alpha/summary") == 0


@pytest.mark.anyio
async def test_subscription_manager_subscribe_and_notify():
    sm = SubscriptionManager()
    s1 = _FakeSession()
    s2 = _FakeSession()
    uri = manifest_uri("alpha")
    await sm.subscribe(uri, s1)
    await sm.subscribe(uri, s2)
    assert sm.subscriber_count(uri) == 2

    sent = await sm.notify(uri)
    assert sent == 2
    assert s1.received == [uri]
    assert s2.received == [uri]


@pytest.mark.anyio
async def test_subscription_manager_dead_session_dropped():
    sm = SubscriptionManager()
    alive = _FakeSession()
    dead = _FakeSession(fail=True)
    uri = manifest_uri("alpha")
    await sm.subscribe(uri, alive)
    await sm.subscribe(uri, dead)
    sent = await sm.notify(uri)
    assert sent == 1
    # second notify should only see alive
    sent2 = await sm.notify(uri)
    assert sent2 == 1
    assert sm.subscriber_count(uri) == 1


@pytest.mark.anyio
async def test_subscription_manager_unsubscribe():
    sm = SubscriptionManager()
    s = _FakeSession()
    uri = manifest_uri("alpha")
    await sm.subscribe(uri, s)
    await sm.unsubscribe(uri, s)
    sent = await sm.notify(uri)
    assert sent == 0
    assert sm.subscriber_count(uri) == 0


# --------------------------------------------------------------------------
# ManifestWatcher — poll-driven mtime detection
# --------------------------------------------------------------------------


def _make_bundle(root: Path, name: str, body: dict | None = None) -> Path:
    p = root / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "run_manifest.json").write_text(json.dumps(body or {"repo_name": name}))
    return p


@pytest.mark.anyio
async def test_watcher_seeds_silently_on_first_poll(tmp_path: Path):
    _make_bundle(tmp_path, "alpha")
    notified: list[str] = []

    async def cb(uri: str) -> None:
        notified.append(uri)

    w = ManifestWatcher(root=tmp_path, on_change=cb, interval=0)
    await w.poll_once()  # seed
    assert notified == []


@pytest.mark.anyio
async def test_watcher_detects_mtime_change(tmp_path: Path):
    bundle = _make_bundle(tmp_path, "alpha")
    notified: list[str] = []

    async def cb(uri: str) -> None:
        notified.append(uri)

    w = ManifestWatcher(root=tmp_path, on_change=cb, interval=0)
    await w.poll_once()  # seed

    # Bump mtime — write requires the new mtime to differ from old, which
    # on fast filesystems may need an explicit time skew. Force it.
    manifest = bundle / "run_manifest.json"
    new_mtime = manifest.stat().st_mtime + 5
    import os
    os.utime(manifest, (new_mtime, new_mtime))

    await w.poll_once()
    assert notified == [manifest_uri("alpha")]


@pytest.mark.anyio
async def test_watcher_detects_new_bundle(tmp_path: Path):
    notified: list[str] = []

    async def cb(uri: str) -> None:
        notified.append(uri)

    w = ManifestWatcher(root=tmp_path, on_change=cb, interval=0)
    await w.poll_once()  # seed empty

    _make_bundle(tmp_path, "beta")
    await w.poll_once()
    assert notified == [manifest_uri("beta")]


@pytest.mark.anyio
async def test_watcher_detects_deletion(tmp_path: Path):
    bundle = _make_bundle(tmp_path, "gamma")
    notified: list[str] = []

    async def cb(uri: str) -> None:
        notified.append(uri)

    w = ManifestWatcher(root=tmp_path, on_change=cb, interval=0)
    await w.poll_once()  # seed
    # remove the bundle
    (bundle / "run_manifest.json").unlink()
    await w.poll_once()
    assert notified == [manifest_uri("gamma")]


# --------------------------------------------------------------------------
# Transport round-trip — subscribe → simulate change → assert push
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_transport_subscribe_then_simulated_change_pushes_update(bundle_name):
    """The Phase 6 exit criterion: a regeneration produces an 'updated'
    notification on the subscribed manifest URI."""
    server, session = build_server()

    received: list[str] = []

    async def message_handler(msg) -> None:
        # The MCP SDK delivers notifications as RequestResponder/JSONRPCMessage
        # wrappers depending on direction. We just check for any payload
        # whose .root looks like ResourceUpdatedNotification.
        try:
            inner = getattr(msg, "message", msg)
            root = getattr(inner, "root", inner)
            method = getattr(root, "method", "") or getattr(getattr(root, "params", None), "method", "")
        except Exception:  # noqa: BLE001
            return
        try:
            params = getattr(root, "params", None)
            uri = getattr(params, "uri", None)
            if uri is not None and "resources/updated" in str(method):
                received.append(str(uri))
        except Exception:  # noqa: BLE001
            pass

    async with create_connected_server_and_client_session(
        server, message_handler=message_handler
    ) as client:
        await client.initialize()
        uri = manifest_uri(bundle_name)
        await client.subscribe_resource(uri)
        # Simulate the watcher firing its callback
        await manifest_changed(uri, session.subscription_manager)
        # Yield control so the notification is delivered
        await anyio.sleep(0.05)

    assert received == [uri], f"expected one push, got {received!r}"


@pytest.mark.anyio
async def test_transport_subscribe_to_non_manifest_uri_errors(bundle_name):  # noqa: ARG001
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        with pytest.raises(Exception):  # SDK surfaces JSON-RPC error  # noqa: BLE001
            await client.subscribe_resource(f"cbm://bundle/{bundle_name}/summary")


@pytest.mark.anyio
async def test_transport_unsubscribe(bundle_name):
    server, session = build_server()

    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        uri = manifest_uri(bundle_name)
        await client.subscribe_resource(uri)
        assert session.subscription_manager.subscriber_count(uri) == 1
        await client.unsubscribe_resource(uri)
        assert session.subscription_manager.subscriber_count(uri) == 0


# --------------------------------------------------------------------------
# Capability advertised
# --------------------------------------------------------------------------


def test_resources_subscribe_capability_advertised():
    from mcp.server import NotificationOptions

    from frontend.mcp_server.server import declare_subscribe_capability

    server, _ = build_server()
    caps = server.get_capabilities(NotificationOptions(), {})
    assert caps.resources is not None
    # Pre-mutation the SDK hardcodes False
    assert caps.resources.subscribe is False
    declare_subscribe_capability(caps)
    assert caps.resources.subscribe is True


# --------------------------------------------------------------------------
# Cache invalidation
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_manifest_changed_clears_bundle_cache(bundle_name):
    import app as backend_app  # type: ignore

    # Prime the cache
    b1 = backend_app.get_bundle(bundle_name)
    cache_info_before = backend_app._load_bundle_cached.cache_info()
    assert cache_info_before.currsize >= 1

    sm = SubscriptionManager()
    await manifest_changed(manifest_uri(bundle_name), sm)

    cache_info_after = backend_app._load_bundle_cached.cache_info()
    assert cache_info_after.currsize == 0


# --------------------------------------------------------------------------
# anyio backend
# --------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"
