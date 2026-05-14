"""MCP stdio server (Phase 3).

Wires the Phase 2 handler registry into the official MCP Python SDK.
Tool discovery, dispatch, schema validation, and lifecycle/negotiation
are all driven by ``mcp.server.Server``. Resources and prompts are
Phase 4/5.

Run:
    python -m frontend.mcp_server

The protocol uses stdio: stdout is reserved for JSON-RPC frames, stderr
for logs. ``configure_logging()`` enforces that — any ``print()`` or
default-handler logger that escapes to stdout will corrupt the protocol,
and ``tests/test_server.py::test_stdout_is_pure_jsonrpc`` catches it.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field

import anyio
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import GetPromptResult, Prompt, Resource, ResourceTemplate, Tool
from pydantic import AnyUrl

from frontend.backend.serving.application import bundle_data as backend_bundle_data

from . import prompts as _p
from . import resources as _r
from .handlers import HANDLERS, dispatch
from .observability import configure_audit_logger, dispatch_with_budget
from .schemas import DESCRIPTIONS, INPUT_SCHEMAS, OUTPUT_SCHEMAS, TOOL_NAMES
from .subscriptions import ManifestWatcher, SubscriptionManager, manifest_uri
from .validators import ToolError

SERVER_NAME = "cbm-mcp"
SERVER_VERSION = "0.1.0"

logger = logging.getLogger(SERVER_NAME)


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------


@dataclass
class Session:
    """Per-connection state."""

    selected_bundle: str | None = None
    subscriptions: set[str] = field(default_factory=set)
    subscription_manager: SubscriptionManager = field(default_factory=SubscriptionManager)


# --------------------------------------------------------------------------
# Server factory
# --------------------------------------------------------------------------


def prewarm_default_bundle() -> None:
    """Force-load the default bundle so the first tool call doesn't pay
    the ~5s RDF parse on the budget-enforced path.

    Idempotent: subsequent calls are cache hits. Failures are swallowed
    so the server still boots when no bundle is configured (the first
    tool call will surface the configuration error to the agent).
    """
    try:
        backend_bundle_data.get_bundle(None)
    except Exception:  # noqa: BLE001
        logger.info("prewarm skipped — no default bundle resolvable")


def build_server(
    session: Session | None = None,
    *,
    transport_label: str = "stdio",
    prewarm: bool = True,
) -> tuple[Server, Session]:
    """Construct a configured MCP Server. The session is returned so tests
    can introspect side effects (e.g. assert ``select_bundle`` updated
    ``selected_bundle``).

    ``transport_label`` is stamped into every audit log line so a single
    log stream can separate stdio traffic from HTTP traffic.

    ``prewarm`` controls whether to load the default bundle eagerly.
    Default True so the first tool call is fast; tests that need a
    cold-cache path can pass ``prewarm=False``.
    """
    if prewarm:
        prewarm_default_bundle()
    s = session or Session()
    server: Server = Server(name=SERVER_NAME, version=SERVER_VERSION)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name=name,
                description=DESCRIPTIONS[name],
                inputSchema=INPUT_SCHEMAS[name],
                outputSchema=OUTPUT_SCHEMAS[name],
            )
            for name in TOOL_NAMES
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict):
        if name not in HANDLERS:
            raise ValueError(f"unknown tool: {name}")
        try:
            result = await dispatch_with_budget(
                name, arguments,
                bundle_default=s.selected_bundle,
                transport=transport_label,
            )
        except ToolError as e:
            raise RuntimeError(f"{e.code}: {e.message}") from e

        # Side effect: select_bundle persists the choice for subsequent calls.
        if name == "select_bundle":
            s.selected_bundle = arguments["bundle"]
            logger.info("session bundle selected: %s", s.selected_bundle)

        return result  # dict → structuredContent

    # ----- Phase 4: resources -----

    @server.list_resources()
    async def _list_resources() -> list[Resource]:
        return _r.list_static_resources(bundle_default=s.selected_bundle)

    @server.list_resource_templates()
    async def _list_resource_templates() -> list[ResourceTemplate]:
        return _r.list_resource_templates()

    @server.read_resource()
    async def _read_resource(uri: AnyUrl):
        try:
            return _r.read_resource(str(uri), bundle_default=s.selected_bundle)
        except ToolError as e:
            raise RuntimeError(f"{e.code}: {e.message}") from e

    # ----- Phase 5: prompts -----

    @server.list_prompts()
    async def _list_prompts() -> list[Prompt]:
        return _p.list_prompts()

    @server.get_prompt()
    async def _get_prompt(name: str, arguments: dict[str, str] | None) -> GetPromptResult:
        try:
            return _p.get_prompt(name, arguments)
        except ToolError as e:
            raise RuntimeError(f"{e.code}: {e.message}") from e

    # ----- Phase 6: subscriptions -----

    @server.subscribe_resource()
    async def _subscribe(uri: AnyUrl) -> None:
        try:
            session = server.request_context.session
        except (LookupError, AttributeError):
            session = None
        try:
            await s.subscription_manager.subscribe(str(uri), session)
        except ToolError as e:
            raise RuntimeError(f"{e.code}: {e.message}") from e
        s.subscriptions.add(str(uri))

    @server.unsubscribe_resource()
    async def _unsubscribe(uri: AnyUrl) -> None:
        try:
            session = server.request_context.session
        except (LookupError, AttributeError):
            session = None
        await s.subscription_manager.unsubscribe(str(uri), session)
        s.subscriptions.discard(str(uri))

    return server, s


def declare_subscribe_capability(caps) -> None:
    """Mutate a ``ServerCapabilities`` to advertise ``resources.subscribe=true``.

    The SDK's ``Server.get_capabilities`` hardcodes ``subscribe=False``;
    since we *do* support it, we flip the bit before passing the
    capabilities into ``InitializationOptions``.
    """
    if caps.resources is not None:
        caps.resources = caps.resources.model_copy(update={"subscribe": True})


async def manifest_changed(uri: str, manager: SubscriptionManager) -> None:
    """Invalidate the bundle cache and notify subscribers.

    Used both by the watcher (via a bound callback in ``run_stdio``) and
    by tests that want to simulate a manifest change without touching
    the filesystem.
    """
    backend_bundle_data.get_bundle.cache_clear()
    try:  # Keep the legacy `import app` compatibility surface coherent too.
        import app as legacy_backend_app  # type: ignore
    except ImportError:
        legacy_backend_app = None
    if legacy_backend_app is not None:
        clear_legacy_cache = getattr(legacy_backend_app.get_bundle, "cache_clear", None)
        if callable(clear_legacy_cache):
            clear_legacy_cache()
    # Drop the SPARQL graph cache too — same data, separate parse.
    from . import sparql as _sparql_mod
    _sparql_mod.clear_graph_cache()
    sent = await manager.notify(uri)
    logger.info("manifest changed: %s (%d subscriber(s) notified)", uri, sent)


# --------------------------------------------------------------------------
# Logging — stderr only
# --------------------------------------------------------------------------


def configure_logging(level: int = logging.INFO) -> None:
    """Replace the root logger's handlers with a single stderr handler.

    Critical for stdio: anything written to stdout corrupts the JSON-RPC
    framing. We blow away whatever default handlers a host process may
    have installed and own the logging pipeline outright.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)


# --------------------------------------------------------------------------
# Stdio runner
# --------------------------------------------------------------------------


async def run_stdio() -> None:  # pragma: no cover — exercised by subprocess test
    """Run the server over stdio until the client disconnects.

    Also starts the manifest-watcher in a sibling task that polls every
    ``CBM_WATCH_INTERVAL`` seconds (default 30) under ``CBM_BUNDLES_ROOT``.

    Subprocess-exercised path; coverage.py doesn't cross process
    boundaries. ``tests/test_server.py::test_stdout_is_pure_jsonrpc``
    is the end-to-end check.
    """
    import os
    from pathlib import Path

    configure_logging()
    server, session = build_server()
    caps = server.get_capabilities(
        notification_options=NotificationOptions(),
        experimental_capabilities={},
    )
    declare_subscribe_capability(caps)
    init_opts = InitializationOptions(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        capabilities=caps,
    )

    bundles_root = Path(os.environ.get("CBM_BUNDLES_ROOT", "_tmp")).resolve()
    interval = float(os.environ.get("CBM_WATCH_INTERVAL", "30"))
    watcher = ManifestWatcher(
        root=bundles_root,
        on_change=lambda uri: manifest_changed(uri, session.subscription_manager),
        interval=interval,
    )

    async with anyio.create_task_group() as tg, stdio_server() as (read, write):
        tg.start_soon(watcher.run)
        logger.info(
            "%s %s starting on stdio (watching %s every %ss)",
            SERVER_NAME, SERVER_VERSION, bundles_root, interval,
        )
        try:
            await server.run(read, write, init_opts)
        finally:
            tg.cancel_scope.cancel()
            logger.info("%s shutting down", SERVER_NAME)


def main() -> None:  # pragma: no cover — exercised by subprocess test
    """Entry point. Catches KeyboardInterrupt for clean SIGINT/SIGTERM."""
    try:
        anyio.run(run_stdio)
    except KeyboardInterrupt:
        # Clean exit; the stdio task will have already closed.
        pass


if __name__ == "__main__":  # pragma: no cover — exercised by subprocess test
    main()
