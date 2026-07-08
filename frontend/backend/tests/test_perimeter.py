"""Perimeter regression tests: fail-closed auth and narrowed CORS.

Regression target: the backend used to ship `allow_origins=["*"]` with no
authentication on /api/* — an open-by-default perimeter over bundles that
contain source-derived facts about (possibly private) codebases. The
contract locked here:

  1. Anonymous /api access is DENIED unless the operator opts in with
     CBM_ALLOW_ANONYMOUS=1.
  2. A configured CBM_API_TOKEN gates /api/* behind `Authorization: Bearer`,
     and CBM_ALLOW_ANONYMOUS cannot weaken a token-protected deployment.
  3. /api/healthz stays open (Docker HEALTHCHECK probes it unauthenticated).
  4. CORS is never wildcard: unknown origins get no allow-origin grant,
     loopback dev origins do, and preflight resolves without credentials.

None of this needs a live bundle: /api/bundles degrades to an empty listing
over an empty bundles root, so these tests always run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

DEV_ORIGIN = "http://localhost:5173"
EVIL_ORIGIN = "https://evil.example"


@pytest.fixture()
def perimeter_client(tmp_path, monkeypatch):
    """TestClient over an empty bundles root with NO perimeter env set.

    The auth gate reads env per request, so monkeypatch inside each test
    reconfigures the perimeter without re-importing the app module.
    """
    monkeypatch.setenv("CBM_BUNDLES_ROOT", str(tmp_path))
    monkeypatch.delenv("CBM_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("CBM_API_TOKEN", raising=False)
    monkeypatch.delenv("CBM_ALLOW_ANONYMOUS", raising=False)
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    import app as app_module  # type: ignore

    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as c:
        yield c


# -- fail-closed default ------------------------------------------------------

def test_anonymous_denied_by_default(perimeter_client):
    r = perimeter_client.get("/api/bundles")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


def test_anonymous_allowed_only_with_explicit_opt_in(perimeter_client, monkeypatch):
    monkeypatch.setenv("CBM_ALLOW_ANONYMOUS", "1")
    r = perimeter_client.get("/api/bundles")
    assert r.status_code == 200
    assert r.json()["bundles"] == []


def test_opt_in_requires_exact_value(perimeter_client, monkeypatch):
    # "true"/"yes" must not silently open the perimeter.
    monkeypatch.setenv("CBM_ALLOW_ANONYMOUS", "true")
    assert perimeter_client.get("/api/bundles").status_code == 401


def test_healthz_stays_open_for_healthchecks(perimeter_client):
    assert perimeter_client.get("/api/healthz").status_code == 200


# -- bearer-token mode --------------------------------------------------------

def test_valid_bearer_token_passes(perimeter_client, monkeypatch):
    monkeypatch.setenv("CBM_API_TOKEN", "s3cret")
    r = perimeter_client.get(
        "/api/bundles", headers={"Authorization": "Bearer s3cret"},
    )
    assert r.status_code == 200


def test_wrong_or_missing_token_denied(perimeter_client, monkeypatch):
    monkeypatch.setenv("CBM_API_TOKEN", "s3cret")
    assert perimeter_client.get("/api/bundles").status_code == 401
    r = perimeter_client.get(
        "/api/bundles", headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401
    r = perimeter_client.get(
        "/api/bundles", headers={"Authorization": "Basic s3cret"},
    )
    assert r.status_code == 401


def test_anonymous_flag_cannot_weaken_token_mode(perimeter_client, monkeypatch):
    monkeypatch.setenv("CBM_API_TOKEN", "s3cret")
    monkeypatch.setenv("CBM_ALLOW_ANONYMOUS", "1")
    assert perimeter_client.get("/api/bundles").status_code == 401
    r = perimeter_client.get(
        "/api/bundles", headers={"Authorization": "Bearer s3cret"},
    )
    assert r.status_code == 200


# -- CORS narrowing -----------------------------------------------------------

def test_unknown_origin_gets_no_cors_grant(perimeter_client, monkeypatch):
    monkeypatch.setenv("CBM_ALLOW_ANONYMOUS", "1")
    r = perimeter_client.get("/api/bundles", headers={"Origin": EVIL_ORIGIN})
    assert "access-control-allow-origin" not in r.headers


def test_dev_origin_is_granted_exactly_not_wildcard(perimeter_client, monkeypatch):
    monkeypatch.setenv("CBM_ALLOW_ANONYMOUS", "1")
    r = perimeter_client.get("/api/bundles", headers={"Origin": DEV_ORIGIN})
    assert r.headers.get("access-control-allow-origin") == DEV_ORIGIN


def test_preflight_resolves_without_credentials(perimeter_client):
    # No auth env at all: preflight must still succeed (browsers cannot
    # attach Authorization to it), while the actual GET stays denied.
    r = perimeter_client.options(
        "/api/bundles",
        headers={
            "Origin": DEV_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == DEV_ORIGIN
    assert perimeter_client.get("/api/bundles").status_code == 401


def test_denied_response_carries_cors_headers(perimeter_client):
    # A browser on an allowed origin must see the 401 (not a CORS failure).
    r = perimeter_client.get("/api/bundles", headers={"Origin": DEV_ORIGIN})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == DEV_ORIGIN
