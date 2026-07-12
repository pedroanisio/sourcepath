"""Fixtures for the FastAPI backend tests.

The suite drives the live `_tmp/usl-ng-core-map` bundle through TestClient.
If the bundle isn't present (e.g. a fresh checkout), the suite is skipped.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "frontend" / "backend"
DEFAULT_BUNDLE = REPO_ROOT / "_tmp" / "usl-ng-core-map"

# The API perimeter fails closed. This suite exercises endpoint behavior,
# not the perimeter, so opt the whole session in; tests/test_perimeter.py
# monkeypatches these away per-test to exercise the fail-closed default.
os.environ.setdefault("CBM_ALLOW_ANONYMOUS", "1")
os.environ.pop("CBM_API_TOKEN", None)


LIVE_BUNDLE_FIXTURES = {"client", "summary", "bundle_dir"}


def pytest_collection_modifyitems(config, items):
    bundle = Path(os.environ.get("CBM_OUTPUT_DIR", DEFAULT_BUNDLE))
    if (bundle / "run_manifest.json").exists():
        return

    # The skip is a convenience for a fresh checkout, not a licence for CI to
    # report green on an untested REST surface (BL-024). CI generates the
    # bundle and sets CBM_REQUIRE_LIVE_BUNDLE=1; if the bundle is then missing,
    # that is a broken pipeline, not a reason to pass quietly.
    if os.environ.get("CBM_REQUIRE_LIVE_BUNDLE", "").strip() not in ("", "0", "false"):
        raise pytest.UsageError(
            f"CBM_REQUIRE_LIVE_BUNDLE is set but no live bundle exists at {bundle} "
            f"(expected {bundle / 'run_manifest.json'}). Every live-bundle test "
            f"would have skipped and this run would have reported success. "
            f"Generate the bundle with scripts/run_l3.py, or unset "
            f"CBM_REQUIRE_LIVE_BUNDLE to allow skipping."
        )

    skip = pytest.mark.skip(
        reason=f"live bundle not found at {bundle}; "
        "generate one with scripts/run_l3.py or set CBM_OUTPUT_DIR"
    )
    for item in items:
        if LIVE_BUNDLE_FIXTURES.intersection(set(item.fixturenames)):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def bundle_dir() -> Path:
    return Path(os.environ.get("CBM_OUTPUT_DIR", DEFAULT_BUNDLE)).resolve()


@pytest.fixture(scope="session")
def client(bundle_dir: Path):
    os.environ["CBM_OUTPUT_DIR"] = str(bundle_dir)
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    # Import after env var is set so the lru_cache picks up the right dir.
    import app as app_module  # type: ignore

    app_module.get_bundle.cache_clear()
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture(scope="session")
def summary(client):
    r = client.get("/api/summary")
    r.raise_for_status()
    return r.json()
