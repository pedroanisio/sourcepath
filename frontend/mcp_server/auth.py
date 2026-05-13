"""Phase 9 — OAuth 2.1 / RFC 6750 token verification.

Two verifier flavors share a single interface:

* ``StaticTokenVerifier`` — Phase 8's pre-shared bearer key. Useful for
  local/internal deployments where issuing JWTs is overkill.
* ``JwtVerifier`` — RFC 8725-shaped JWT bearer. Validates signature,
  audience, expiry, issuer, and scope. Signing keys come from either a
  pinned PEM (CBM_MCP_JWT_PUBLIC_KEY) or a JWKS endpoint
  (CBM_MCP_JWT_JWKS_URI).

The middleware uses one ``TokenVerifier`` and maps ``AuthError`` to the
RFC 6750 status codes:

  * missing token        → 401, no error param
  * invalid_request      → 400 (malformed bearer header)
  * invalid_token        → 401 (bad sig, expired, wrong aud, wrong iss)
  * insufficient_scope   → 403 (token valid but missing required scope)

No token passthrough: tokens are validated against this server's own
audience. We never accept upstream-issued tokens for proxying.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

import jwt
from jwt.algorithms import get_default_algorithms

REQUIRED_SCOPE_ENV = "CBM_MCP_REQUIRED_SCOPE"
TOKEN_ENV = "CBM_MCP_TOKEN"
JWT_AUDIENCE_ENV = "CBM_MCP_JWT_AUDIENCE"
JWT_ISSUER_ENV = "CBM_MCP_JWT_ISSUER"
JWT_JWKS_URI_ENV = "CBM_MCP_JWT_JWKS_URI"
JWT_PUBLIC_KEY_ENV = "CBM_MCP_JWT_PUBLIC_KEY"
JWT_ALGORITHMS_ENV = "CBM_MCP_JWT_ALGORITHMS"

DEFAULT_REQUIRED_SCOPE = "bundle:read"
DEFAULT_ALGORITHMS = ("RS256", "ES256")


# --------------------------------------------------------------------------
# Error type
# --------------------------------------------------------------------------


@dataclass
class AuthError(Exception):
    """Carries the data the middleware needs to build an RFC 6750 reply."""

    status: int
    code: str | None
    description: str
    required_scope: str | None = None

    def __str__(self) -> str:
        return f"{self.code or '<no-code>'}: {self.description}"

    def www_authenticate(self, realm: str = "cbm-mcp") -> str:
        parts = [f'Bearer realm="{realm}"']
        if self.code:
            parts.append(f'error="{self.code}"')
        if self.description:
            parts.append(f'error_description="{self.description}"')
        if self.required_scope:
            parts.append(f'scope="{self.required_scope}"')
        return ", ".join(parts)


# --------------------------------------------------------------------------
# Verifier protocol
# --------------------------------------------------------------------------


class TokenVerifier(Protocol):
    required_scope: str

    def verify(self, token: str) -> dict[str, Any]: ...  # noqa: E704


# --------------------------------------------------------------------------
# Helpers — scope check
# --------------------------------------------------------------------------


def _claim_scopes(claims: dict[str, Any]) -> set[str]:
    """Extract scope set from a claims dict.

    Supports the standard ``scope`` claim (space-separated string) and
    the alternate ``scp`` claim (list) used by some IdPs.
    """
    out: set[str] = set()
    raw = claims.get("scope")
    if isinstance(raw, str):
        out.update(raw.split())
    scp = claims.get("scp")
    if isinstance(scp, str):
        out.update(scp.split())
    elif isinstance(scp, (list, tuple)):
        out.update(str(s) for s in scp)
    return out


def _require_scope(claims: dict[str, Any], required: str) -> None:
    if not required:
        return
    if required not in _claim_scopes(claims):
        raise AuthError(
            status=403,
            code="insufficient_scope",
            description=f"required scope not present: {required}",
            required_scope=required,
        )


# --------------------------------------------------------------------------
# StaticTokenVerifier — Phase 8 pre-shared key
# --------------------------------------------------------------------------


@dataclass
class StaticTokenVerifier:
    expected_token: str
    required_scope: str = DEFAULT_REQUIRED_SCOPE

    def __post_init__(self) -> None:
        if not self.expected_token:
            raise ValueError("expected_token must be non-empty")

    def verify(self, token: str) -> dict[str, Any]:
        # Constant-time compare to avoid timing leaks on the shared secret.
        import hmac

        if not hmac.compare_digest(token, self.expected_token):
            raise AuthError(status=401, code="invalid_token", description="token mismatch")
        # Static tokens are issued by the deployer, so we grant the
        # configured required scope by construction.
        return {"scope": self.required_scope}


# --------------------------------------------------------------------------
# JwtVerifier — Phase 9 OAuth
# --------------------------------------------------------------------------


SigningKeyResolver = Callable[[str | None], Any]


@dataclass
class JwtVerifier:
    audience: str
    key_resolver: SigningKeyResolver
    issuer: str | None = None
    algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS
    required_scope: str = DEFAULT_REQUIRED_SCOPE
    leeway_seconds: int = 30

    def verify(self, token: str) -> dict[str, Any]:
        try:
            unverified = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as e:
            raise AuthError(status=401, code="invalid_token", description=f"malformed JWT: {e}") from e
        kid = unverified.get("kid")
        try:
            key = self.key_resolver(kid)
        except AuthError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AuthError(status=401, code="invalid_token", description=f"signing key unavailable: {e}") from e

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self.algorithms),
                audience=self.audience,
                issuer=self.issuer,
                leeway=self.leeway_seconds,
                options={"require": ["exp", "aud"]},
            )
        except jwt.ExpiredSignatureError as e:
            raise AuthError(status=401, code="invalid_token", description="token expired") from e
        except jwt.InvalidAudienceError as e:
            raise AuthError(status=401, code="invalid_token", description="wrong audience") from e
        except jwt.InvalidIssuerError as e:
            raise AuthError(status=401, code="invalid_token", description="wrong issuer") from e
        except jwt.MissingRequiredClaimError as e:
            raise AuthError(status=401, code="invalid_token", description=f"missing required claim: {e.claim}") from e
        except jwt.InvalidSignatureError as e:
            raise AuthError(status=401, code="invalid_token", description="bad signature") from e
        except jwt.InvalidTokenError as e:
            raise AuthError(status=401, code="invalid_token", description=str(e)) from e

        _require_scope(claims, self.required_scope)
        return claims


# --------------------------------------------------------------------------
# Key resolvers
# --------------------------------------------------------------------------


def static_key_resolver(pem: str) -> SigningKeyResolver:
    """Resolver that always returns the same pinned PEM key, regardless of kid."""

    def _resolve(_kid: str | None) -> Any:
        return pem

    return _resolve


def jwks_key_resolver(jwks_uri: str, *, ttl_seconds: int = 3600) -> SigningKeyResolver:  # pragma: no cover — network
    """Resolver that fetches JWKS and caches by kid.

    Production path; not exercised in unit tests (network). Tests use
    ``static_key_resolver`` with a generated keypair.
    """
    import httpx

    cache: dict[str, tuple[float, dict[str, jwt.PyJWK]]] = {}

    def _resolve(kid: str | None) -> Any:
        now = time.time()
        entry = cache.get("jwks")
        if entry is None or now - entry[0] > ttl_seconds:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(jwks_uri)
                resp.raise_for_status()
                jwks = resp.json()
            keys_by_kid: dict[str, jwt.PyJWK] = {}
            for k in jwks.get("keys", []):
                keys_by_kid[k.get("kid", "")] = jwt.PyJWK.from_dict(k)
            cache["jwks"] = (now, keys_by_kid)
            entry = cache["jwks"]
        keys = entry[1]
        if kid is None and len(keys) == 1:
            return next(iter(keys.values())).key
        key = keys.get(kid or "")
        if key is None:
            raise AuthError(
                status=401, code="invalid_token",
                description=f"no JWKS key matches kid={kid!r}",
            )
        return key.key

    return _resolve


# --------------------------------------------------------------------------
# Factory: pick a verifier from env vars
# --------------------------------------------------------------------------


def build_verifier_from_env() -> TokenVerifier | None:
    """Build a verifier based on environment configuration.

    Precedence:
      1. CBM_MCP_JWT_AUDIENCE + (CBM_MCP_JWT_PUBLIC_KEY or CBM_MCP_JWT_JWKS_URI) → JwtVerifier
      2. CBM_MCP_TOKEN → StaticTokenVerifier
      3. None (caller must decide)
    """
    audience = os.environ.get(JWT_AUDIENCE_ENV)
    if audience:
        pubkey = os.environ.get(JWT_PUBLIC_KEY_ENV)
        jwks_uri = os.environ.get(JWT_JWKS_URI_ENV)
        if not (pubkey or jwks_uri):
            raise RuntimeError(
                f"{JWT_AUDIENCE_ENV} is set but neither {JWT_PUBLIC_KEY_ENV} "
                f"nor {JWT_JWKS_URI_ENV} is configured"
            )
        resolver: SigningKeyResolver = (
            static_key_resolver(pubkey) if pubkey else jwks_key_resolver(jwks_uri)  # type: ignore[arg-type]
        )
        algorithms_env = os.environ.get(JWT_ALGORITHMS_ENV)
        algorithms: Iterable[str] = (
            tuple(a.strip() for a in algorithms_env.split(",") if a.strip())
            if algorithms_env
            else DEFAULT_ALGORITHMS
        )
        return JwtVerifier(
            audience=audience,
            key_resolver=resolver,
            issuer=os.environ.get(JWT_ISSUER_ENV) or None,
            algorithms=tuple(algorithms),
            required_scope=os.environ.get(REQUIRED_SCOPE_ENV, DEFAULT_REQUIRED_SCOPE),
        )

    token = os.environ.get(TOKEN_ENV)
    if token:
        return StaticTokenVerifier(
            expected_token=token,
            required_scope=os.environ.get(REQUIRED_SCOPE_ENV, DEFAULT_REQUIRED_SCOPE),
        )
    return None
