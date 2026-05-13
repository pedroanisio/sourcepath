"""Phase 10 — per-tool timeouts + audit log.

* ``dispatch_with_budget`` wraps the Phase 2 sync dispatch in an async
  call that enforces a per-tool wall-clock budget. Sync handlers can't
  be interrupted mid-call, so on timeout we cancel the wait and orphan
  the worker thread (``abandon_on_cancel=True``); the client gets a
  prompt timeout response while the orphan eventually completes and is
  garbage-collected.

* ``audit_log`` emits a single structured JSON line per tool invocation
  to a dedicated ``cbm-mcp.audit`` logger. Default handler is stderr; if
  ``CBM_MCP_AUDIT_LOG_PATH`` is set, also writes to a 10MB rotating file.

Audit shape::

    {"ts": "2026-05-13T17:42:01.123Z", "transport": "stdio",
     "tool": "file_detail", "args_digest": "9c3f...", "latency_ms": 4,
     "status": "ok"}

Arguments are hashed (first 12 hex chars of sha256) rather than logged
verbatim — protects against accidentally leaking large payloads or
sensitive paths into the audit trail.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

import anyio

from .handlers import dispatch
from .validators import INTERNAL, ToolError

# --------------------------------------------------------------------------
# Per-tool timeout table
# --------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS = 5.0
TIMEOUTS: dict[str, float] = {
    # sbert loads ~100 MB of weights on first call and runs a cosine NN
    "semantic_neighbors": 10.0,
    # SPARQL queries can scan the whole graph; tight cap is the safety net
    "sparql": 10.0,
}


def _env_override(tool: str) -> float | None:
    """Allow per-tool override via ``CBM_MCP_TIMEOUT_<TOOL_NAME>`` env vars."""
    key = f"CBM_MCP_TIMEOUT_{tool.upper()}"
    raw = os.environ.get(key)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def timeout_for(tool: str) -> float:
    """Resolve a budget for ``tool``: env override > table > default."""
    override = _env_override(tool)
    if override is not None:
        return override
    return TIMEOUTS.get(tool, DEFAULT_TIMEOUT_SECONDS)


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------

audit_logger = logging.getLogger("cbm-mcp.audit")
_audit_configured = False


def configure_audit_logger(
    path: str | None = None, max_bytes: int = 10 * 1024 * 1024, backups: int = 5
) -> None:
    """Initialize the audit logger.

    * No-op if already configured (idempotent).
    * Default: a stderr StreamHandler (so stdio transport's protocol on
      stdout stays clean).
    * If ``path`` (or env ``CBM_MCP_AUDIT_LOG_PATH``) is set, also attach
      a ``RotatingFileHandler``.
    """
    global _audit_configured
    if _audit_configured:
        return
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False  # never duplicate via the root logger

    # Single-line JSON formatter — the message is already a JSON object.
    formatter = logging.Formatter("%(message)s")
    import sys
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    audit_logger.addHandler(stderr_handler)

    resolved_path = path or os.environ.get("CBM_MCP_AUDIT_LOG_PATH")
    if resolved_path:
        file_handler = RotatingFileHandler(
            resolved_path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        audit_logger.addHandler(file_handler)
    _audit_configured = True


def _args_digest(args: dict[str, Any] | None) -> str:
    """Return a 12-char stable digest of a (sorted) JSON-encoded args dict."""
    encoded = json.dumps(args or {}, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def audit_log(
    *,
    transport: str,
    tool: str,
    args: dict[str, Any] | None,
    latency_ms: float,
    status: str,
    error: str | None = None,
) -> None:
    """Emit a single structured audit line. Status ∈ {ok, error, timeout}."""
    if not _audit_configured:
        configure_audit_logger()
    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "transport": transport,
        "tool": tool,
        "args_digest": _args_digest(args),
        "latency_ms": round(latency_ms, 2),
        "status": status,
    }
    if error:
        payload["error"] = error
    audit_logger.info(json.dumps(payload, sort_keys=True))


# --------------------------------------------------------------------------
# Dispatch wrapper — timeout + audit
# --------------------------------------------------------------------------


class ToolTimeoutError(ToolError):
    """ToolError specialization for budget overrun. status code ``timeout``."""

    def __init__(self, tool: str, budget: float) -> None:
        super().__init__(
            "timeout",
            f"tool {tool!r} exceeded {budget}s budget",
        )
        self.tool = tool
        self.budget = budget


async def dispatch_with_budget(
    tool: str,
    args: dict[str, Any] | None,
    *,
    bundle_default: str | None = None,
    transport: str = "mcp",
    timeout: float | None = None,
) -> dict[str, Any]:
    """Async wrapper around :func:`handlers.dispatch` that enforces the
    per-tool wall-clock budget and emits an audit log line.

    On timeout the worker thread is abandoned. Subsequent calls are not
    blocked because each call gets its own thread; the orphan eventually
    finishes and Python collects it.

    Any exception inside the handler is re-raised after the audit line
    is emitted — the transport layer maps it to the protocol error.
    """
    if timeout is None:
        timeout = timeout_for(tool)
    started = time.perf_counter()
    status = "ok"
    error: str | None = None
    try:
        with anyio.fail_after(timeout):
            return await anyio.to_thread.run_sync(
                lambda: dispatch(tool, args, bundle_default=bundle_default),
                abandon_on_cancel=True,
            )
    except TimeoutError as e:  # raised by fail_after
        status = "timeout"
        error = f"exceeded {timeout}s"
        raise ToolTimeoutError(tool, timeout) from e
    except ToolError as e:
        status = "error"
        error = f"{e.code}: {e.message}"
        raise
    except Exception as e:  # noqa: BLE001 — anything else
        status = "error"
        error = f"{type(e).__name__}: {e}"
        raise ToolError(INTERNAL, error) from e
    finally:
        latency_ms = (time.perf_counter() - started) * 1000.0
        audit_log(
            transport=transport,
            tool=tool,
            args=args,
            latency_ms=latency_ms,
            status=status,
            error=error,
        )
