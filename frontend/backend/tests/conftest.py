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


def pytest_collection_modifyitems(config, items):
    bundle = Path(os.environ.get("CBM_OUTPUT_DIR", DEFAULT_BUNDLE))
    if not (bundle / "run_manifest.json").exists():
        skip = pytest.mark.skip(
            reason=f"bundle not found at {bundle}; "
            "generate one with scripts/run_l3.py or set CBM_OUTPUT_DIR"
        )
        for item in items:
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
