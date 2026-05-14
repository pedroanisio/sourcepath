#!/usr/bin/env python3
"""verify_xsd_fixture.py — assert static/schemas/ XSDs are classified correctly.

The directory is a vendored test fixture: industry-standard XML Schema
Definition files (IEEE 12207, IEEE 29148, IEC 5055, DDD v3, etc.) used
to exercise our classifier and downstream layers against XML-shaped
schema definitions.

Exercises:
  1. Every ``.xsd`` under ``static/schemas/`` is classified as ``data``.
  2. The fixture directory is non-empty (catches accidental deletion).
  3. No file in the fixture is mis-classified as ``unknown``.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import sys

from pathlib import Path

from codebase_mapper.inspection.classify import classify


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "static" / "schemas"


def _bundle_relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_fixture_dir_exists() -> None:
    assert FIXTURE_DIR.is_dir(), f"missing fixture dir: {FIXTURE_DIR}"


def test_fixture_dir_has_xsds() -> None:
    xsds = sorted(FIXTURE_DIR.rglob("*.xsd"))
    assert xsds, f"no .xsd files found under {FIXTURE_DIR}"


def test_every_xsd_classifies_as_data() -> None:
    failures: list[tuple[str, str]] = []
    for path in sorted(FIXTURE_DIR.rglob("*.xsd")):
        rel = _bundle_relative(path)
        # classify() reads only the head bytes; pass empty since the rule
        # is suffix-based and we don't want to depend on file content.
        actual = classify(rel, b"")
        if actual != "data":
            failures.append((rel, actual))
    assert not failures, (
        "expected every .xsd to classify as 'data'; got: "
        + ", ".join(f"{p}={t!r}" for p, t in failures)
    )


def test_no_fixture_file_is_unknown() -> None:
    """Catch-all: nothing under the fixture should fall through to 'unknown'.

    A new file type added under static/schemas/ without a classifier
    rule would silently degrade query quality on every bundle.
    """
    failures: list[str] = []
    for path in sorted(FIXTURE_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = _bundle_relative(path)
        head = path.read_bytes()[:512]
        actual = classify(rel, head)
        if actual == "unknown":
            failures.append(rel)
    assert not failures, (
        "fixture files fell through to 'unknown' — extend classify.py: "
        + ", ".join(failures)
    )


def main() -> int:
    tests = [
        ("fixture dir exists", test_fixture_dir_exists),
        ("fixture has xsds", test_fixture_dir_has_xsds),
        ("every xsd classifies as data", test_every_xsd_classifies_as_data),
        ("no fixture file is unknown", test_no_fixture_file_is_unknown),
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            print(f"FAIL  {name}: {exc}", file=sys.stderr)
            failures += 1
        else:
            print(f"PASS  {name}")
    if failures:
        print(f"\n{failures} test(s) failed.", file=sys.stderr)
        return 1
    print(f"\nAll {len(tests)} tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
