#!/usr/bin/env python3
"""verify_proto_fixture.py — assert static/proto/ .proto files are classified correctly.

The directory is a vendored test fixture: protobuf contracts from the
``requirements.engineering.dsl.v2`` family (sibling project repo-intel)
used as a typed-schema corpus distinct from XML Schema (``static/schemas/``).

Exercises:
  1. The fixture dir exists and contains at least one ``.proto`` file.
  2. Every ``.proto`` under ``static/proto/`` classifies as ``source_code``
     with language ``protobuf``.
  3. Inter-proto imports resolve within the fixture (no dangling imports
     beyond ``google/protobuf/*`` well-known types).
  4. No file under the fixture falls through to ``unknown``.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import re
import sys

from pathlib import Path

from codebase_mapper.inspection.classify import classify
from codebase_mapper.shared_kernel.constants import LANG_BY_EXT


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "static" / "proto"

# Imports starting with this prefix come from protoc's well-known types
# bundle and aren't expected to be vendored locally.
_WELL_KNOWN_PREFIX = "google/protobuf/"

_IMPORT_RE = re.compile(r'^\s*import\s+"([^"]+)"\s*;', re.MULTILINE)


def _bundle_relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def test_fixture_dir_exists() -> None:
    assert FIXTURE_DIR.is_dir(), f"missing fixture dir: {FIXTURE_DIR}"


def test_fixture_dir_has_protos() -> None:
    protos = sorted(FIXTURE_DIR.rglob("*.proto"))
    assert protos, f"no .proto files found under {FIXTURE_DIR}"


def test_lang_by_ext_knows_proto() -> None:
    assert LANG_BY_EXT.get(".proto") == "protobuf", (
        ".proto extension is not mapped to 'protobuf' in LANG_BY_EXT"
    )


def test_every_proto_classifies_as_source_code() -> None:
    failures: list[tuple[str, str]] = []
    for path in sorted(FIXTURE_DIR.rglob("*.proto")):
        rel = _bundle_relative(path)
        actual = classify(rel, b"")
        if actual != "source_code":
            failures.append((rel, actual))
    assert not failures, (
        "expected every .proto to classify as 'source_code'; got: "
        + ", ".join(f"{p}={t!r}" for p, t in failures)
    )


def test_inter_proto_imports_resolve() -> None:
    """Every non-well-known import names a sibling .proto present in the
    same fixture directory. Catches the case where a copy missed a file
    or someone renamed one without updating its dependents."""
    protos: dict[str, Path] = {}
    for path in FIXTURE_DIR.rglob("*.proto"):
        protos[path.name] = path

    failures: list[tuple[str, str]] = []
    for path in sorted(protos.values()):
        text = path.read_text(encoding="utf-8")
        for imp in _IMPORT_RE.findall(text):
            if imp.startswith(_WELL_KNOWN_PREFIX):
                continue
            # The import is a basename relative to proto_path. Resolve
            # by basename match anywhere in the fixture.
            basename = imp.split("/")[-1]
            if basename not in protos:
                failures.append((_bundle_relative(path), imp))
    assert not failures, (
        "dangling proto imports: "
        + ", ".join(f"{src} imports {imp!r}" for src, imp in failures)
    )


def test_no_fixture_file_is_unknown() -> None:
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
        ("fixture has protos", test_fixture_dir_has_protos),
        ("LANG_BY_EXT knows .proto", test_lang_by_ext_knows_proto),
        ("every proto classifies as source_code", test_every_proto_classifies_as_source_code),
        ("inter-proto imports resolve", test_inter_proto_imports_resolve),
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
