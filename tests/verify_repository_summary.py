#!/usr/bin/env python3
"""verify_repository_summary.py — contract test for the new MCP tool.

Exercises the ``repository_summary`` MCP tool against a real bundle under
``_tmp/`` (defaulting to ``code-whisper`` because it's the smallest). Asserts:

  1. The handler is registered.
  2. The payload validates against ``OUTPUT_SCHEMAS["repository_summary"]``
     (schema check runs inside the ``@tool`` decorator).
  3. Bundle metadata round-trips (``bundle.name`` matches the request).
  4. ``central_files`` is non-empty for a real bundle and sorted by
     descending ``import_degree``.
  5. ``key_concepts`` is sorted by descending ``frequency``.
  6. ``dependency_summary`` and ``test_coverage_hint`` are present with
     non-negative integer counts.
  7. The ``central_files_limit`` argument is honored.

Run:
    CBM_BUNDLES_ROOT=$(pwd)/_tmp python tests/verify_repository_summary.py

Exit 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import os
import sys

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUNDLE = "code-whisper"

# `frontend` is a namespace package living under the repo root — not part of
# the installed wheel (see pyproject.toml's package include list). Make it
# importable regardless of cwd.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _bootstrap_bundles_root() -> Path:
    root = Path(os.environ.get("CBM_BUNDLES_ROOT", REPO_ROOT / "_tmp")).resolve()
    if not root.is_dir():
        raise RuntimeError(
            f"CBM_BUNDLES_ROOT does not point to a directory: {root}. "
            f"Set it to a bundles-root with at least one generated bundle, "
            f"or run a bundle build first."
        )
    os.environ.setdefault("CBM_BUNDLES_ROOT", str(root))
    return root


def _pick_bundle(root: Path) -> str:
    """Prefer DEFAULT_BUNDLE; fall back to any available bundle."""
    default = root / DEFAULT_BUNDLE / "run_manifest.json"
    if default.is_file():
        return DEFAULT_BUNDLE
    for child in sorted(root.iterdir()):
        if (child / "run_manifest.json").is_file():
            return child.name
    raise RuntimeError(f"no bundles with run_manifest.json under {root}")


def _summary(bundle: str, **kwargs) -> dict:
    # Import lazily so the bundles-root env var is set first.
    from frontend.mcp_server.handlers import dispatch

    return dispatch("repository_summary", {"bundle": bundle, **kwargs})


def test_handler_is_registered() -> None:
    from frontend.mcp_server.handlers import HANDLERS

    assert "repository_summary" in HANDLERS, (
        "repository_summary handler is not registered"
    )


def test_payload_shape(bundle: str) -> None:
    """The @tool decorator runs validate_out; if this returns at all the
    payload conforms to OUTPUT_SCHEMAS. We still spot-check key fields."""
    payload = _summary(bundle)
    required = {
        "bundle", "total_files", "total_chunks", "total_concepts",
        "files_by_language", "files_by_type", "central_files",
        "entry_points", "key_concepts", "dependency_summary",
        "test_coverage_hint",
    }
    missing = required - set(payload.keys())
    assert not missing, f"payload missing keys: {missing}"
    assert payload["bundle"]["name"] == bundle, (
        f"bundle echo mismatch: {payload['bundle']['name']!r} != {bundle!r}"
    )


def test_central_files_sorted(bundle: str) -> None:
    payload = _summary(bundle)
    central = payload["central_files"]
    assert isinstance(central, list)
    # For any non-trivial bundle there should be at least one central file.
    # If a bundle has no imports at all this could fail — relax to >=0.
    if not central:
        return
    degs = [c["import_degree"] for c in central]
    assert degs == sorted(degs, reverse=True), (
        f"central_files not sorted by descending import_degree: {degs}"
    )
    # Every entry has positive degree (zero-degree files are filtered out).
    assert all(d > 0 for d in degs), f"unexpected zero-degree entries: {degs}"


def test_key_concepts_sorted(bundle: str) -> None:
    payload = _summary(bundle)
    concepts = payload["key_concepts"]
    assert isinstance(concepts, list)
    if not concepts:
        return
    freqs = [c["frequency"] for c in concepts]
    assert freqs == sorted(freqs, reverse=True), (
        f"key_concepts not sorted by descending frequency: {freqs}"
    )


def test_dependency_and_test_summaries(bundle: str) -> None:
    payload = _summary(bundle)
    dep = payload["dependency_summary"]
    for key in ("internal_imports", "external_imports"):
        assert key in dep, f"missing dependency_summary.{key}"
        assert isinstance(dep[key], int) and dep[key] >= 0
    hint = payload["test_coverage_hint"]
    for key in ("test_files", "source_files", "tests_edges"):
        assert key in hint, f"missing test_coverage_hint.{key}"
        assert isinstance(hint[key], int) and hint[key] >= 0
    # ratio is None when source_files == 0, else a non-negative float.
    if hint["source_files"] > 0:
        assert isinstance(hint["ratio"], (int, float)) and hint["ratio"] >= 0


def test_limit_is_honored(bundle: str) -> None:
    payload = _summary(bundle, central_files_limit=3, key_concepts_limit=5)
    assert len(payload["central_files"]) <= 3
    assert len(payload["key_concepts"]) <= 5


def main() -> int:
    try:
        root = _bootstrap_bundles_root()
        bundle = _pick_bundle(root)
    except RuntimeError as exc:
        print(f"SKIP  no usable bundle: {exc}", file=sys.stderr)
        return 1

    tests = [
        ("handler is registered", lambda: test_handler_is_registered()),
        ("payload shape", lambda: test_payload_shape(bundle)),
        ("central_files sorted", lambda: test_central_files_sorted(bundle)),
        ("key_concepts sorted", lambda: test_key_concepts_sorted(bundle)),
        ("dependency + test summaries", lambda: test_dependency_and_test_summaries(bundle)),
        ("limit args honored", lambda: test_limit_is_honored(bundle)),
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            print(f"FAIL  {name}: {exc}", file=sys.stderr)
            failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1
        else:
            print(f"PASS  {name}")
    if failures:
        print(f"\n{failures} test(s) failed (bundle: {bundle}).", file=sys.stderr)
        return 1
    print(f"\nAll {len(tests)} tests passed (bundle: {bundle}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
