"""Phase 3 tests — stdio transport + lifecycle + tool discovery and dispatch.

* In-memory: discover tools, call orient_bundle, exercise select_bundle's
  session side effect.
* Subprocess: spawn the real ``python -m frontend.mcp_server``, drive it
  with handwritten JSON-RPC frames, and prove stdout never carries
  anything other than valid JSON-RPC.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from frontend.mcp_server.handlers import HANDLERS
from frontend.mcp_server.schemas import INPUT_SCHEMAS, TOOL_NAMES
from frontend.mcp_server.server import Session, build_server

REPO_ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------
# build_server() — synchronous shape
# --------------------------------------------------------------------------


def test_build_server_returns_server_and_session():
    server, session = build_server()
    assert server.name == "cbm-mcp"
    assert isinstance(session, Session)
    assert session.selected_bundle is None


def test_build_server_accepts_preconstructed_session():
    s = Session(selected_bundle="alpha")
    _, returned = build_server(session=s)
    assert returned is s


# --------------------------------------------------------------------------
# In-memory client: discovery + dispatch + session side effect
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_tools_exposes_every_handler(bundle_name):  # noqa: ARG001 — anchors live fixture skip
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.list_tools()
    names = {t.name for t in result.tools}
    assert names == set(TOOL_NAMES)
    # Spot-check schemas are attached so the client can validate
    by_name = {t.name: t for t in result.tools}
    assert by_name["file_detail"].inputSchema["required"] == ["path"]
    assert by_name["file_detail"].outputSchema is not None


@pytest.mark.anyio
async def test_call_orient_bundle_returns_structured_content(bundle_name):
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool("orient_bundle", {"bundle": bundle_name})
    assert result.isError is False
    payload = result.structuredContent
    assert payload is not None
    assert payload["bundle"]["name"] == bundle_name
    assert "cbm" in payload["schema_hint"]["namespaces"]
    assert payload["suggested_first_calls"]


@pytest.mark.anyio
async def test_select_bundle_persists_in_session(bundle_name):
    server, session = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool("select_bundle", {"bundle": bundle_name})
        assert result.isError is False
        # Same session: subsequent call without explicit `bundle` should
        # resolve through session state.
        result2 = await client.call_tool("bundle_summary", {})
    assert session.selected_bundle == bundle_name
    assert result2.isError is False
    assert result2.structuredContent is not None
    assert result2.structuredContent["output_dir"].endswith(bundle_name)


@pytest.mark.anyio
async def test_call_tool_unknown_returns_error(bundle_name):  # noqa: ARG001
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool("does_not_exist", {})
    assert result.isError is True
    # The content carries the message; the SDK wraps RuntimeError(str(e))
    txt = "".join(c.text for c in result.content if hasattr(c, "text"))
    assert "unknown tool" in txt.lower()


@pytest.mark.anyio
async def test_call_tool_invalid_input_returns_error(bundle_name):  # noqa: ARG001
    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        # file_detail requires path → schema rejects this
        result = await client.call_tool("file_detail", {})
    assert result.isError is True
    txt = "".join(c.text for c in result.content if hasattr(c, "text"))
    assert "path" in txt.lower() or "required" in txt.lower() or "validation" in txt.lower()


# --------------------------------------------------------------------------
# Subprocess: stdout-cleanliness guarantee
# --------------------------------------------------------------------------


def _spawn_server() -> subprocess.Popen:
    """Launch the stdio server in a subprocess with the project on PYTHONPATH."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-u", "-m", "frontend.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(REPO_ROOT),
        text=True,
        bufsize=1,
    )


def _send(proc: subprocess.Popen, msg: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def _read_response(proc: subprocess.Popen, timeout: float = 10.0) -> dict:
    """Read one JSON-RPC message from stdout, blocking until a newline."""
    assert proc.stdout is not None

    async def _wait() -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, proc.stdout.readline)

    line = asyncio.run(asyncio.wait_for(_wait(), timeout=timeout))
    if not line:
        raise AssertionError(
            "server closed stdout without a response. stderr was:\n"
            + (proc.stderr.read() if proc.stderr else "<no stderr>")
        )
    return json.loads(line)


def test_stdout_is_pure_jsonrpc(bundle_name):
    """The exit criterion for Phase 3: every line on the server's stdout
    parses as JSON-RPC. Logging that leaks to stdout corrupts the framing
    and would break any compliant MCP client."""
    proc = _spawn_server()
    try:
        # 1) initialize
        _send(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "phase3-test", "version": "0"},
            },
        })
        init_resp = _read_response(proc)
        assert init_resp["jsonrpc"] == "2.0"
        assert init_resp["id"] == 1
        assert "result" in init_resp
        caps = init_resp["result"]["capabilities"]
        assert "tools" in caps

        # 2) initialized notification (no response)
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 3) tools/list
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        list_resp = _read_response(proc)
        assert list_resp["id"] == 2
        names = {t["name"] for t in list_resp["result"]["tools"]}
        assert names == set(TOOL_NAMES)

        # 4) tools/call orient_bundle
        _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "orient_bundle", "arguments": {"bundle": bundle_name}},
        })
        call_resp = _read_response(proc)
        assert call_resp["id"] == 3
        assert call_resp["result"]["isError"] is False
        sc = call_resp["result"]["structuredContent"]
        assert sc["bundle"]["name"] == bundle_name
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)

    # All output read so far has been parsed as JSON. Drain anything left
    # (notifications etc.) and assert every non-empty line still parses.
    remaining = proc.stdout.read() if proc.stdout else ""
    for ln in remaining.splitlines():
        if ln.strip():
            try:
                json.loads(ln)
            except json.JSONDecodeError as e:
                stderr = proc.stderr.read() if proc.stderr else ""
                raise AssertionError(
                    f"non-JSON line on stdout: {ln!r}\n"
                    f"stderr:\n{stderr}"
                ) from e


# --------------------------------------------------------------------------
# anyio backend fixture
# --------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    """Run anyio-marked tests on asyncio only (avoids requiring trio)."""
    return "asyncio"
