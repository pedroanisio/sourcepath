"""Phase 10 tests — per-tool timeouts + audit log."""
from __future__ import annotations

import json
import logging
import time

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from frontend.mcp_server import (
    DEFAULT_TIMEOUT_SECONDS,
    TIMEOUTS,
    ToolError,
    ToolTimeoutError,
    audit_logger,
    configure_audit_logger,
    dispatch_with_budget,
    timeout_for,
)
from frontend.mcp_server.server import build_server


# --------------------------------------------------------------------------
# Per-tool timeout table
# --------------------------------------------------------------------------


def test_default_timeout_is_5s():
    assert timeout_for("imports_of") == DEFAULT_TIMEOUT_SECONDS


def test_semantic_neighbors_gets_longer_budget():
    assert timeout_for("semantic_neighbors") > DEFAULT_TIMEOUT_SECONDS
    assert TIMEOUTS["semantic_neighbors"] == 10.0


def test_env_override_takes_priority(monkeypatch):
    monkeypatch.setenv("CBM_MCP_TIMEOUT_FILE_DETAIL", "2.5")
    assert timeout_for("file_detail") == 2.5


def test_env_override_invalid_value_ignored(monkeypatch):
    monkeypatch.setenv("CBM_MCP_TIMEOUT_FILE_DETAIL", "garbage")
    assert timeout_for("file_detail") == DEFAULT_TIMEOUT_SECONDS


# --------------------------------------------------------------------------
# dispatch_with_budget — timeout cancels the wait, audit logs status
# --------------------------------------------------------------------------


class _AuditRecorder(logging.Handler):
    """Captures audit-log records into a list. Bypasses caplog's reliance
    on logger propagation (the audit logger has propagate=False by design)."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def clear(self) -> None:
        self.records.clear()


@pytest.fixture
def audit_capture(bundle_name):
    """Attach a recording handler to the audit logger for the test, remove it after.

    Also pre-warms the bundle cache so the first dispatched tool doesn't
    pay the ~5s RDF parse on the audit-log timing path.
    """
    import app as backend_app  # type: ignore

    backend_app.get_bundle(bundle_name)  # warm

    configure_audit_logger()
    audit_logger.setLevel(logging.INFO)
    recorder = _AuditRecorder()
    audit_logger.addHandler(recorder)
    try:
        yield recorder
    finally:
        audit_logger.removeHandler(recorder)


@pytest.mark.anyio
async def test_dispatch_with_budget_ok_emits_audit(audit_capture, bundle_name):
    payload = await dispatch_with_budget(
        "bundle_summary", {"bundle": bundle_name},
        transport="test",
    )
    assert "files_by_language" in payload
    line = _last_audit(audit_capture)
    assert line["tool"] == "bundle_summary"
    assert line["status"] == "ok"
    assert line["transport"] == "test"
    assert line["latency_ms"] >= 0
    assert "args_digest" in line


@pytest.mark.anyio
async def test_dispatch_with_budget_error_emits_audit(audit_capture, bundle_name):
    with pytest.raises(ToolError):
        await dispatch_with_budget(
            "file_detail",
            {"bundle": bundle_name, "path": "does/not/exist.py"},
            transport="test",
        )
    line = _last_audit(audit_capture)
    assert line["status"] == "error"
    assert "not_found" in line["error"]


@pytest.mark.anyio
async def test_dispatch_with_budget_timeout_fires(monkeypatch, audit_capture, bundle_name):
    """Monkeypatch the inner dispatch to sleep so the watchdog has
    something to interrupt deterministically."""
    import time as _time
    import frontend.mcp_server.observability as obs

    real_dispatch = obs.dispatch

    def slow_dispatch(name, args, **kw):
        _time.sleep(0.5)
        return real_dispatch(name, args, **kw)

    monkeypatch.setattr(obs, "dispatch", slow_dispatch)

    with pytest.raises(ToolTimeoutError) as exc:
        await dispatch_with_budget(
            "bundle_summary", {"bundle": bundle_name},
            transport="test", timeout=0.05,
        )
    assert exc.value.code == "timeout"
    line = _last_audit(audit_capture)
    assert line["status"] == "timeout"


@pytest.mark.anyio
async def test_args_digest_is_stable_across_calls(audit_capture, bundle_name):
    args = {"bundle": bundle_name}
    await dispatch_with_budget("bundle_summary", args, transport="test")
    d1 = _last_audit(audit_capture)["args_digest"]
    audit_capture.clear()
    await dispatch_with_budget("bundle_summary", args, transport="test")
    d2 = _last_audit(audit_capture)["args_digest"]
    assert d1 == d2


# --------------------------------------------------------------------------
# End-to-end: timeouts surface as call_tool errors via the SDK
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_call_tool_returns_error_on_timeout(monkeypatch, bundle_name):
    """A pathological timeout reaches the MCP client as an isError result
    with a 'timeout' marker — not as an unhandled exception."""
    import time as _time
    import frontend.mcp_server.observability as obs

    monkeypatch.setenv("CBM_MCP_TIMEOUT_BUNDLE_SUMMARY", "0.05")
    real = obs.dispatch
    monkeypatch.setattr(obs, "dispatch",
        lambda n, a, **k: (_time.sleep(0.5), real(n, a, **k))[1])

    server, _ = build_server()
    async with create_connected_server_and_client_session(server) as client:
        await client.initialize()
        result = await client.call_tool(
            "bundle_summary", {"bundle": bundle_name}
        )
    assert result.isError is True
    txt = "".join(c.text for c in result.content if hasattr(c, "text"))
    assert "timeout" in txt.lower()


# --------------------------------------------------------------------------
# configure_audit_logger is idempotent
# --------------------------------------------------------------------------


def test_configure_audit_logger_idempotent():
    configure_audit_logger()
    before = len(audit_logger.handlers)
    configure_audit_logger()
    configure_audit_logger()
    assert len(audit_logger.handlers) == before


def test_audit_logger_file_handler_optional(tmp_path, monkeypatch):
    """When CBM_MCP_AUDIT_LOG_PATH is set on first configure call, a
    RotatingFileHandler is attached. We reset the module-level flag to
    force a fresh setup."""
    import frontend.mcp_server.observability as obs

    monkeypatch.setattr(obs, "_audit_configured", False)
    obs.audit_logger.handlers.clear()

    path = tmp_path / "audit.log"
    obs.configure_audit_logger(path=str(path))
    # Emit one record
    obs.audit_log(
        transport="test", tool="bundle_summary", args={"x": 1},
        latency_ms=1.0, status="ok",
    )
    assert path.exists()
    data = path.read_text().strip().splitlines()
    assert data
    assert json.loads(data[-1])["tool"] == "bundle_summary"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _last_audit(recorder: "_AuditRecorder") -> dict:
    """Pull the most recent record from the recorder and parse it as JSON."""
    assert recorder.records, "no audit log line emitted"
    return json.loads(recorder.records[-1].getMessage())


# --------------------------------------------------------------------------
# anyio backend
# --------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"
