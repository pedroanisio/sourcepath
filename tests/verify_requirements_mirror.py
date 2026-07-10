#!/usr/bin/env python3
"""verify_requirements_mirror.py — requirements.txt <-> pyproject.toml parity.

drift-risk H8: the Docker images install `frontend/backend/requirements.txt`,
`frontend/mcp_server/requirements.txt`, and
`frontend/backend/requirements-sbert.txt` directly, while CI and editable
installs resolve `pyproject.toml` — whose `[frontend]` extra *claims* to
mirror the requirements files. Nothing enforced the mirror, and it had
already diverged (rdflib/pyshacl/numpy pinned for Docker, unpinned for
everyone else), so CI and production resolved different worlds.

Contract enforced here:

  1. every package a requirements file installs is declared in pyproject
     (base dependencies or an extra) — no Docker-only dependencies;
  2. for every such package, the exact version specifier in the
     requirements file also appears in pyproject — same pin, same world;
  3. the `[frontend]` extra mirrors the two frontend requirements files
     both ways: each extra entry (minus documented base-dep overlaps)
     exists in a requirements file with the identical specifier;
  4. parse floors guard both parsers.

Run from the repo root:  uv run python tests/verify_requirements_mirror.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIREMENT_FILES = [
    REPO_ROOT / "frontend" / "backend" / "requirements.txt",
    REPO_ROOT / "frontend" / "mcp_server" / "requirements.txt",
    REPO_ROOT / "frontend" / "backend" / "requirements-sbert.txt",
]
FRONTEND_MIRROR_FILES = REQUIREMENT_FILES[:2]  # what the [frontend] extra mirrors

_REQ_LINE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9,._-]+\])?\s*([<>=!~;].*)?$")

_FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"== {label} == {'ok' if ok else 'FAIL'}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        _FAILURES.append(label)


def _norm_name(name: str) -> str:
    return name.lower().replace("_", "-")


def parse_requirement(line: str) -> tuple[str, str, str] | None:
    """-> (normalized name, extras, specifier) or None for blanks/comments."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = _REQ_LINE.match(line)
    if not m:
        raise ValueError(f"unparseable requirement line: {line!r}")
    name, extras, spec = m.group(1), m.group(2) or "", m.group(3) or ""
    return _norm_name(name), extras, spec.replace(" ", "")


def requirements_entries() -> dict[Path, list[tuple[str, str, str]]]:
    out: dict[Path, list[tuple[str, str, str]]] = {}
    for path in REQUIREMENT_FILES:
        entries = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            parsed = parse_requirement(raw)
            if parsed:
                entries.append(parsed)
        out[path] = entries
    return out


def pyproject_specs() -> dict[str, set[str]]:
    """name -> set of '<extras><specifier>' strings across base deps + extras."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    specs: dict[str, set[str]] = {}
    groups = [data["project"].get("dependencies", [])]
    groups += list(data["project"].get("optional-dependencies", {}).values())
    for group in groups:
        for raw in group:
            parsed = parse_requirement(raw)
            if parsed is None:
                continue
            name, extras, spec = parsed
            if name == "codebase-mapper":  # self-referential extra chaining
                continue
            specs.setdefault(name, set()).add(extras + spec)
    return specs


def main() -> int:
    reqs = requirements_entries()
    pyp = pyproject_specs()

    total = sum(len(v) for v in reqs.values())
    check(f"requirements parse floor ({total} entries, need >= 10)", total >= 10)
    check(f"pyproject parse floor ({len(pyp)} packages, need >= 15)",
          len(pyp) >= 15)

    # 1 + 2: every requirements entry exists in pyproject with the same pin.
    for path, entries in reqs.items():
        rel = path.relative_to(REPO_ROOT)
        missing = [n for n, _, _ in entries if n not in pyp]
        check(f"{rel}: every package is declared in pyproject", not missing,
              f"docker-only packages: {missing}")
        mismatched = [
            f"{n}: requirements={extras + spec!r} pyproject={sorted(pyp[n])}"
            for n, extras, spec in entries
            if n in pyp and (extras + spec) not in pyp[n]
        ]
        check(f"{rel}: pins match pyproject exactly", not mismatched,
              "; ".join(mismatched))

    # 3: the [frontend] extra mirrors the frontend requirements bidirectionally.
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    extra = data["project"]["optional-dependencies"]["frontend"]
    mirror: set[tuple[str, str]] = set()
    for p in FRONTEND_MIRROR_FILES:
        for n, extras, spec in reqs[p]:
            mirror.add((n, extras + spec))
    stale = []
    for raw in extra:
        parsed = parse_requirement(raw)
        if parsed is None:
            continue
        n, extras, spec = parsed
        if (n, extras + spec) not in mirror:
            stale.append(f"{n}{extras}{spec}")
    check("[frontend] extra entries all exist in the requirements files",
          not stale, f"extra-only (stale mirror?): {stale}")

    if _FAILURES:
        print(f"\n{len(_FAILURES)} check(s) failed", file=sys.stderr)
        return 1
    print("\nall requirements-mirror checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
