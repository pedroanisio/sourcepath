"""Phase 8 — Streamable HTTP transport mounted on the existing FastAPI app.

Same ``build_server()`` from Phase 3; new wire format. Bearer-token
auth on every request to ``/mcp``. TLS is expected to be terminated
upstream (nginx in the docker-compose stack).

Usage from ``frontend/backend/app.py``:

    if os.environ.get("CBM_MCP_TOKEN"):
        from frontend.mcp_server.http_transport import mount_mcp
        mount_mcp(app)
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

from fastapi import FastAPI, HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .auth import (
    AuthError,
    StaticTokenVerifier,
    TokenVerifier,
    build_verifier_from_env,
)
from .server import build_server, declare_subscribe_capability  # noqa: F401

TOKEN_ENV = "CBM_MCP_TOKEN"
DEFAULT_MOUNT_PATH = "/mcp"

logger = logging.getLogger("cbm-mcp.http")


# --------------------------------------------------------------------------
# Bearer-token auth dependency
# --------------------------------------------------------------------------


def _extract_bearer(scope: Scope) -> tuple[str | None, AuthError | None]:
    """Pull ``Authorization: Bearer <token>`` out of an ASGI scope.

    Returns ``(token, None)`` on success, or ``(None, AuthError)`` to
    short-circuit. Missing header → 401 with no error code (per RFC 6750
    section 3.1: "the resource server SHOULD NOT include an error code
    or other error information"). Malformed → 400 invalid_request.
    """
    auth = ""
    for k, v in scope.get("headers", []):
        if k == b"authorization":
            auth = v.decode("latin-1", errors="replace")
            break
    if not auth:
        return None, AuthError(
            status=401, code=None,
            description="authorization header missing",
        )
    if not auth.lower().startswith("bearer "):
        return None, AuthError(
            status=400, code="invalid_request",
            description="unsupported auth scheme; expected Bearer",
        )
    token = auth[len("Bearer "):].strip()
    if not token:
        return None, AuthError(
            status=400, code="invalid_request",
            description="empty bearer token",
        )
    return token, None


def _auth_error_response(err: AuthError) -> Response:
    body: dict[str, str] = {"error": err.code or "unauthorized"}
    if err.description:
        body["error_description"] = err.description
    if err.required_scope:
        body["scope"] = err.required_scope
    return JSONResponse(
        body,
        status_code=err.status,
        headers={"WWW-Authenticate": err.www_authenticate()},
    )


class BearerAuthMiddleware:
    """ASGI middleware that enforces ``Authorization: Bearer <token>``
    and delegates token verification to a ``TokenVerifier``.

    Maps :class:`AuthError` to RFC 6750 status codes:

      * 401 — missing or invalid token (no error code, or ``invalid_token``)
      * 400 — malformed bearer header (``invalid_request``)
      * 403 — token valid but lacks required scope (``insufficient_scope``)
    """

    def __init__(self, app: ASGIApp, verifier: TokenVerifier) -> None:
        self._app = app
        self._verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":  # lifespan/websocket — pass through
            await self._app(scope, receive, send)
            return
        token, err = _extract_bearer(scope)
        if err is not None:
            await _auth_error_response(err)(scope, receive, send)
            return
        try:
            self._verifier.verify(token)  # type: ignore[arg-type]
        except AuthError as auth_err:
            await _auth_error_response(auth_err)(scope, receive, send)
            return
        await self._app(scope, receive, send)


def make_bearer_dependency(expected_token: str) -> Callable[..., Awaitable[None]]:  # pragma: no cover
    """Legacy FastAPI dependency form. Production path uses
    ``BearerAuthMiddleware`` because the endpoint runs as raw ASGI."""
    from fastapi import Header

    if not expected_token:
        raise ValueError("expected_token must be non-empty")

    async def require_bearer(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing_bearer_token",
                                headers={"WWW-Authenticate": "Bearer"})
        if authorization[len("Bearer "):].strip() != expected_token:
            raise HTTPException(status_code=403, detail="invalid_token",
                                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})

    return require_bearer


# --------------------------------------------------------------------------
# Lifespan composition + mount
# --------------------------------------------------------------------------


def mount_mcp(
    app: FastAPI,
    *,
    token: str | None = None,
    verifier: TokenVerifier | None = None,
    path: str = DEFAULT_MOUNT_PATH,
    json_response: bool = False,
    stateless: bool = False,
) -> StreamableHTTPSessionManager:
    """Mount the MCP HTTP transport on ``app`` at ``path``.

    Verifier resolution (in order):
      1. ``verifier`` argument
      2. ``token`` argument → wrapped in StaticTokenVerifier
      3. Environment (``CBM_MCP_JWT_*`` for OAuth, ``CBM_MCP_TOKEN`` for static)
      4. Refuse to mount

    The MCP session manager runs inside the parent app's lifespan; we
    compose with whatever lifespan ``app`` already has.
    """
    if verifier is None:
        if token is not None:
            verifier = StaticTokenVerifier(expected_token=token)
        else:
            verifier = build_verifier_from_env()
    if verifier is None:
        raise RuntimeError(
            "refusing to mount MCP HTTP without a verifier; set "
            "CBM_MCP_JWT_AUDIENCE (+ CBM_MCP_JWT_PUBLIC_KEY or _JWKS_URI) "
            "or CBM_MCP_TOKEN, or pass token=... / verifier=..."
        )

    server, _ = build_server(transport_label="http")
    manager = StreamableHTTPSessionManager(
        app=server, json_response=json_response, stateless=stateless
    )

    # Compose lifespan: wrap whatever the parent already has so manager.run()
    # is active for the lifetime of the server.
    existing_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def combined_lifespan(app_: FastAPI):
        async with manager.run():
            async with existing_lifespan(app_):
                yield

    app.router.lifespan_context = combined_lifespan
    endpoint_path = path.rstrip("/") + "/"

    async def mcp_asgi(scope: Scope, receive: Receive, send: Send) -> None:
        """Raw ASGI handler — MCP session manager sends its own response."""
        await manager.handle_request(scope, receive, send)

    auth_wrapped = BearerAuthMiddleware(mcp_asgi, verifier)

    # Use a Starlette Route directly so the endpoint can be pure ASGI
    # (FastAPI's response handling would double-send after the manager).
    route_with = Route(endpoint_path, endpoint=auth_wrapped,
                      methods=["GET", "POST", "DELETE"])
    route_without = Route(endpoint_path.rstrip("/"), endpoint=auth_wrapped,
                         methods=["GET", "POST", "DELETE"])
    app.router.routes.append(route_with)
    app.router.routes.append(route_without)

    logger.info(
        "mounted MCP HTTP transport at %s (verifier=%s)",
        endpoint_path, type(verifier).__name__,
    )
    return manager
