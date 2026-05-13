"""Phase 8 tests — streamable HTTP transport + bearer-token auth + parity.

Three slices:

* **Auth gate**: requests without a token (or with the wrong token) are
  rejected before any MCP code runs. Uses raw httpx ASGI calls.
* **Round-trip**: the SDK's ``streamablehttp_client`` drives the ASGI app
  end-to-end. ``tools/list`` and ``tools/call`` exit criteria.
* **Parity**: the same ``tools/list`` payload comes out over stdio (Phase 3)
  and over HTTP (this phase), proving the second wire format doesn't
  drift from the first.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.memory import create_connected_server_and_client_session

from frontend.mcp_server import (
    TOOL_NAMES,
    mount_mcp,
)
from frontend.mcp_server.server import build_server

TOKEN = "phase8-secret"


def _build_app():
    """Fresh FastAPI app per test, with MCP mounted under a fixed token.

    Returns ``(app, manager)``. Tests must enter ``manager.run()`` before
    making requests — httpx.ASGITransport doesn't propagate the parent
    app's lifespan to the SDK's session manager. In production this is
    done automatically by the combined lifespan that ``mount_mcp``
    installs.
    """
    app = FastAPI()
    manager = mount_mcp(app, token=TOKEN)
    return app, manager


# --------------------------------------------------------------------------
# Bearer-token gate
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_http_rejects_missing_authorization():
    app, _ = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").lower().startswith("bearer")


@pytest.mark.anyio
async def test_http_rejects_wrong_token():
    """RFC 6750: invalid_token → 401, distinguished from missing by the
    presence of the error parameter on the WWW-Authenticate header."""
    app, _ = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert r.status_code == 401
    assert "invalid_token" in r.headers.get("www-authenticate", "").lower()


@pytest.mark.anyio
async def test_http_rejects_non_bearer_scheme():
    """RFC 6750: malformed authorization header → 400 invalid_request."""
    app, _ = _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
    assert r.status_code == 400
    assert "invalid_request" in r.headers.get("www-authenticate", "").lower()


def test_mount_refuses_without_token():
    """No silent anonymous mount — refusing is the safer default."""
    app = FastAPI()
    with pytest.raises(RuntimeError) as exc:
        mount_mcp(app, token=None)
    assert "token" in str(exc.value).lower()


# --------------------------------------------------------------------------
# End-to-end round-trip — SDK client over ASGI
# --------------------------------------------------------------------------


def _asgi_client_factory(app: FastAPI):
    """Wrap an ASGI app as an httpx.AsyncClient factory the SDK can use."""
    def factory(headers=None, timeout=None, auth=None):
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://t",
            headers=headers,
            timeout=timeout,
            auth=auth,
        )
    return factory


@pytest.mark.anyio
async def test_http_round_trip_lists_tools(bundle_name):  # noqa: ARG001
    """Exit criterion: a real MCP client over HTTP can initialize and
    list every tool the stdio server exposes."""
    app, manager = _build_app()
    async with manager.run():
        async with streamablehttp_client(
            "http://t/mcp/",
            headers={"Authorization": f"Bearer {TOKEN}"},
            httpx_client_factory=_asgi_client_factory(app),
        ) as (read, write, get_session_id):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = await client.list_tools()
    names = {t.name for t in tools.tools}
    assert names == set(TOOL_NAMES)


@pytest.mark.anyio
async def test_http_round_trip_calls_orient_bundle(bundle_name):
    app, manager = _build_app()
    async with manager.run():
        async with streamablehttp_client(
            "http://t/mcp/",
            headers={"Authorization": f"Bearer {TOKEN}"},
            httpx_client_factory=_asgi_client_factory(app),
        ) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                result = await client.call_tool("orient_bundle", {"bundle": bundle_name})
    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["bundle"]["name"] == bundle_name


# --------------------------------------------------------------------------
# Parity: HTTP vs in-memory transports produce the same tools/list
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_parity_tools_list_between_transports(bundle_name):  # noqa: ARG001
    """Behavioral drift between transports is the worst class of bug — a
    tool that works over stdio but not HTTP, or vice versa. Sort both
    snapshots and compare."""
    # HTTP path
    app, manager = _build_app()
    async with manager.run():
        async with streamablehttp_client(
            "http://t/mcp/",
            headers={"Authorization": f"Bearer {TOKEN}"},
            httpx_client_factory=_asgi_client_factory(app),
        ) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                http_tools = await client.list_tools()
    http_shape = sorted(
        (t.name, sorted(t.inputSchema.get("required", []))) for t in http_tools.tools
    )

    # In-memory path (Phase 3's transport)
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        mem_tools = await client.list_tools()
    mem_shape = sorted(
        (t.name, sorted(t.inputSchema.get("required", []))) for t in mem_tools.tools
    )

    assert http_shape == mem_shape


# --------------------------------------------------------------------------
# Token sourced from CBM_MCP_TOKEN env when not passed explicitly
# --------------------------------------------------------------------------


def test_mount_reads_token_from_env(monkeypatch):
    monkeypatch.setenv("CBM_MCP_TOKEN", "from-env")
    app = FastAPI()
    sub = mount_mcp(app)
    assert sub is not None  # mount succeeded → middleware installed with env token


# --------------------------------------------------------------------------
# anyio backend
# --------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"
