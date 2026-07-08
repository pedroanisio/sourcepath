"""Shared fixtures for the MCP server tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "frontend" / "backend"
BUNDLES_ROOT = REPO_ROOT / "_tmp"
LIVE_BUNDLE_FIXTURES = {"bundle_name", "live_bundle", "representative_file", "heavy_concept", "representative_args"}


def _is_live_suite_bundle(p: Path) -> bool:
    """A bundle this suite can exercise end-to-end.

    The live tests assert on chunks, concepts, and co-occurrence, so a bare
    L1 bundle (run_manifest.json but no L3 layer) must NOT be selected —
    picking one turns every concept test into a spurious failure instead of
    the honest skip whose message says how to generate a full bundle.
    """
    return (p / "run_manifest.json").exists() and (p / "concepts.json").exists()


def _discover_bundle() -> Path | None:
    """Pick the first full ``_tmp/<name>`` bundle, or honor an explicit
    ``CBM_OUTPUT_DIR`` if it points at one."""
    env = os.environ.get("CBM_OUTPUT_DIR")
    if env:
        p = Path(env)
        if _is_live_suite_bundle(p):
            return p
    if BUNDLES_ROOT.exists():
        for child in sorted(BUNDLES_ROOT.iterdir()):
            if _is_live_suite_bundle(child):
                return child
    return None


_DETECTED = _discover_bundle()


def pytest_collection_modifyitems(config, items):
    """Skip live-bundle tests when no bundle is present anywhere."""
    if _DETECTED is not None:
        return
    skip = pytest.mark.skip(
        reason=f"no bundle found under {BUNDLES_ROOT}; "
        "generate one with scripts/run_l3.py or set CBM_OUTPUT_DIR"
    )
    for item in items:
        if LIVE_BUNDLE_FIXTURES.intersection(set(item.fixturenames)):
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _env():
    """Anchor CBM_OUTPUT_DIR + CBM_BUNDLES_ROOT for the suite."""
    if _DETECTED is not None:
        os.environ["CBM_OUTPUT_DIR"] = str(_DETECTED)
        os.environ["CBM_BUNDLES_ROOT"] = str(_DETECTED.parent)
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="session")
def bundle_name() -> str:
    assert _DETECTED is not None, "bundle missing — should have been skipped"
    return _DETECTED.name


@pytest.fixture(scope="session")
def live_bundle(_env, bundle_name):
    """Load the live bundle once for the session."""
    from frontend.mcp_server import dispatch  # noqa: F401 — forces import path
    import app as backend_app

    backend_app.get_bundle.cache_clear()
    return backend_app.get_bundle(bundle_name)
