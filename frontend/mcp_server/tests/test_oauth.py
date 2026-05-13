"""Phase 9 tests — OAuth 2.1 JWT bearer tokens.

Each test generates an RSA keypair, signs a token, and feeds it to a
``JwtVerifier`` configured with the matching public key. Validates the
RFC 6750 status-code mapping for every failure mode.
"""
from __future__ import annotations

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from frontend.mcp_server.auth import (
    AuthError,
    JwtVerifier,
    StaticTokenVerifier,
    build_verifier_from_env,
    static_key_resolver,
)
from frontend.mcp_server.http_transport import mount_mcp

AUDIENCE = "urn:cbm:mcp"
ISSUER = "https://issuer.example.test"


# --------------------------------------------------------------------------
# Keypair fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest.fixture()
def verifier(keypair):
    _, public_pem = keypair
    return JwtVerifier(
        audience=AUDIENCE,
        key_resolver=static_key_resolver(public_pem),
        issuer=ISSUER,
        algorithms=("RS256",),
        required_scope="bundle:read",
        leeway_seconds=5,
    )


def _make_token(
    keypair,
    *,
    aud: str | list[str] = AUDIENCE,
    iss: str = ISSUER,
    exp_offset: int = 300,
    scope: str | None = "bundle:read",
    extra: dict[str, Any] | None = None,
    algorithm: str = "RS256",
) -> str:
    private_pem, _ = keypair
    now = int(time.time())
    claims = {
        "iss": iss,
        "aud": aud,
        "iat": now,
        "exp": now + exp_offset,
        "sub": "test-user",
    }
    if scope is not None:
        claims["scope"] = scope
    if extra:
        claims.update(extra)
    return jwt.encode(claims, private_pem, algorithm=algorithm)


# --------------------------------------------------------------------------
# JwtVerifier — unit-level
# --------------------------------------------------------------------------


def test_verify_valid_token_returns_claims(verifier, keypair):
    tok = _make_token(keypair)
    claims = verifier.verify(tok)
    assert claims["aud"] == AUDIENCE
    assert claims["iss"] == ISSUER
    assert "bundle:read" in claims["scope"]


def test_verify_expired_token_raises_invalid_token(verifier, keypair):
    tok = _make_token(keypair, exp_offset=-3600)
    with pytest.raises(AuthError) as exc:
        verifier.verify(tok)
    assert exc.value.status == 401
    assert exc.value.code == "invalid_token"
    assert "expired" in exc.value.description.lower()


def test_verify_wrong_audience_raises_invalid_token(verifier, keypair):
    tok = _make_token(keypair, aud="urn:someone:else")
    with pytest.raises(AuthError) as exc:
        verifier.verify(tok)
    assert exc.value.status == 401
    assert exc.value.code == "invalid_token"
    assert "audience" in exc.value.description.lower()


def test_verify_wrong_issuer_raises_invalid_token(verifier, keypair):
    tok = _make_token(keypair, iss="https://other.example/")
    with pytest.raises(AuthError) as exc:
        verifier.verify(tok)
    assert exc.value.status == 401
    assert exc.value.code == "invalid_token"
    assert "issuer" in exc.value.description.lower()


def test_verify_insufficient_scope_raises_403(verifier, keypair):
    tok = _make_token(keypair, scope="something:else")
    with pytest.raises(AuthError) as exc:
        verifier.verify(tok)
    assert exc.value.status == 403
    assert exc.value.code == "insufficient_scope"
    assert exc.value.required_scope == "bundle:read"


def test_verify_no_scope_claim_raises_403(verifier, keypair):
    tok = _make_token(keypair, scope=None)
    with pytest.raises(AuthError) as exc:
        verifier.verify(tok)
    assert exc.value.code == "insufficient_scope"


def test_verify_scp_array_claim_accepted(keypair):
    """Some IdPs use ``scp`` (list) instead of ``scope`` (space-string)."""
    _, public_pem = keypair
    v = JwtVerifier(
        audience=AUDIENCE,
        key_resolver=static_key_resolver(public_pem),
        algorithms=("RS256",),
        required_scope="bundle:read",
    )
    tok = _make_token(
        keypair, iss=ISSUER, scope=None,
        extra={"scp": ["bundle:read", "extra:scope"]},
    )
    claims = v.verify(tok)
    assert "scp" in claims


def test_verify_tampered_signature_raises_invalid_token(verifier, keypair):
    tok = _make_token(keypair)
    # Flip a byte in the signature segment
    parts = tok.split(".")
    parts[2] = parts[2][:-2] + ("AA" if parts[2][-2:] != "AA" else "BB")
    tampered = ".".join(parts)
    with pytest.raises(AuthError) as exc:
        verifier.verify(tampered)
    assert exc.value.status == 401
    assert exc.value.code == "invalid_token"


def test_verify_malformed_jwt_raises_invalid_token(verifier):
    with pytest.raises(AuthError) as exc:
        verifier.verify("not-a-jwt")
    assert exc.value.status == 401
    assert exc.value.code == "invalid_token"


def test_verify_missing_aud_claim_raises_invalid_token(verifier, keypair):
    # Token without aud — PyJWT raises MissingRequiredClaimError
    private_pem, _ = keypair
    now = int(time.time())
    claims = {"iss": ISSUER, "iat": now, "exp": now + 300, "scope": "bundle:read"}
    tok = jwt.encode(claims, private_pem, algorithm="RS256")
    with pytest.raises(AuthError) as exc:
        verifier.verify(tok)
    assert exc.value.status == 401
    assert exc.value.code == "invalid_token"


def test_verify_key_resolver_failure_raises_invalid_token(keypair):
    _, public_pem = keypair  # noqa: F841

    def bad_resolver(_kid):
        raise RuntimeError("simulated network failure")

    v = JwtVerifier(audience=AUDIENCE, key_resolver=bad_resolver, algorithms=("RS256",))
    tok = _make_token(keypair)
    with pytest.raises(AuthError) as exc:
        v.verify(tok)
    assert exc.value.code == "invalid_token"
    assert "signing key unavailable" in exc.value.description


# --------------------------------------------------------------------------
# AuthError serialization (WWW-Authenticate header)
# --------------------------------------------------------------------------


def test_auth_error_www_authenticate_header_shape():
    err = AuthError(
        status=403, code="insufficient_scope",
        description="needs bundle:read", required_scope="bundle:read",
    )
    h = err.www_authenticate()
    assert h.startswith('Bearer realm="cbm-mcp"')
    assert 'error="insufficient_scope"' in h
    assert 'scope="bundle:read"' in h
    assert 'error_description="needs bundle:read"' in h


def test_auth_error_missing_token_has_no_error_code():
    """RFC 6750 §3.1: no error param when the client didn't authenticate."""
    err = AuthError(status=401, code=None, description="")
    h = err.www_authenticate()
    assert h == 'Bearer realm="cbm-mcp"'


# --------------------------------------------------------------------------
# Env factory
# --------------------------------------------------------------------------


def test_factory_picks_jwt_when_audience_set(keypair, monkeypatch):
    _, public_pem = keypair
    monkeypatch.setenv("CBM_MCP_JWT_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("CBM_MCP_JWT_PUBLIC_KEY", public_pem)
    monkeypatch.setenv("CBM_MCP_JWT_ISSUER", ISSUER)
    monkeypatch.delenv("CBM_MCP_TOKEN", raising=False)
    v = build_verifier_from_env()
    assert isinstance(v, JwtVerifier)
    assert v.audience == AUDIENCE
    assert v.issuer == ISSUER


def test_factory_picks_static_when_only_token_set(monkeypatch):
    monkeypatch.delenv("CBM_MCP_JWT_AUDIENCE", raising=False)
    monkeypatch.setenv("CBM_MCP_TOKEN", "stat")
    v = build_verifier_from_env()
    assert isinstance(v, StaticTokenVerifier)


def test_factory_returns_none_with_no_env(monkeypatch):
    for var in (
        "CBM_MCP_JWT_AUDIENCE",
        "CBM_MCP_JWT_PUBLIC_KEY",
        "CBM_MCP_JWT_JWKS_URI",
        "CBM_MCP_JWT_ISSUER",
        "CBM_MCP_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    assert build_verifier_from_env() is None


def test_factory_audience_without_key_source_raises(monkeypatch):
    monkeypatch.setenv("CBM_MCP_JWT_AUDIENCE", AUDIENCE)
    monkeypatch.delenv("CBM_MCP_JWT_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("CBM_MCP_JWT_JWKS_URI", raising=False)
    with pytest.raises(RuntimeError) as exc:
        build_verifier_from_env()
    assert "audience" in str(exc.value).lower() or "public_key" in str(exc.value).lower()


# --------------------------------------------------------------------------
# Static verifier — constant-time compare
# --------------------------------------------------------------------------


def test_static_verifier_accepts_matching_token():
    v = StaticTokenVerifier(expected_token="pre-shared")
    claims = v.verify("pre-shared")
    assert claims["scope"] == "bundle:read"


def test_static_verifier_rejects_wrong_token():
    v = StaticTokenVerifier(expected_token="pre-shared")
    with pytest.raises(AuthError) as exc:
        v.verify("wrong")
    assert exc.value.status == 401
    assert exc.value.code == "invalid_token"


def test_static_verifier_refuses_empty_secret():
    with pytest.raises(ValueError):
        StaticTokenVerifier(expected_token="")


# --------------------------------------------------------------------------
# Integration — HTTP endpoint with JwtVerifier
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_http_endpoint_accepts_valid_jwt(verifier, keypair, bundle_name):
    """A correctly-signed token with the right audience + scope reaches
    the MCP manager and the JSON-RPC initialize succeeds."""
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    app = FastAPI()
    manager = mount_mcp(app, verifier=verifier)
    tok = _make_token(keypair)

    def factory(headers=None, timeout=None, auth=None):
        return AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
            headers=headers, timeout=timeout, auth=auth,
        )

    async with manager.run():
        async with streamablehttp_client(
            "http://t/mcp/",
            headers={"Authorization": f"Bearer {tok}"},
            httpx_client_factory=factory,
        ) as (read, write, _):
            async with ClientSession(read, write) as client:
                init = await client.initialize()
                tools = await client.list_tools()
    assert init.serverInfo.name == "cbm-mcp"
    from frontend.mcp_server import TOOL_NAMES
    assert len(tools.tools) == len(TOOL_NAMES)


@pytest.mark.anyio
async def test_http_endpoint_rejects_expired_jwt(verifier, keypair):
    app = FastAPI()
    mount_mcp(app, verifier=verifier)
    tok = _make_token(keypair, exp_offset=-60)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Authorization": f"Bearer {tok}"},
        )
    assert r.status_code == 401
    assert "invalid_token" in r.headers.get("www-authenticate", "").lower()
    assert "expired" in r.json().get("error_description", "").lower()


@pytest.mark.anyio
async def test_http_endpoint_rejects_insufficient_scope(verifier, keypair):
    app = FastAPI()
    mount_mcp(app, verifier=verifier)
    tok = _make_token(keypair, scope="wrong:scope")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Authorization": f"Bearer {tok}"},
        )
    assert r.status_code == 403
    assert "insufficient_scope" in r.headers.get("www-authenticate", "").lower()
    assert r.json().get("scope") == "bundle:read"


# --------------------------------------------------------------------------
# anyio backend
# --------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"
