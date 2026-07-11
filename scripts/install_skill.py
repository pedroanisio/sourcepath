#!/usr/bin/env python3
"""Copy a Claude Code skill package from another checkout into this repo's
``.claude/skills/`` so it's usable as a project-local skill here.

Source-agnostic: works with any skill directory that has a ``SKILL.md``
(e.g. one vendored under another repo's ``skills/<name>/``), not just a
single hardcoded skill.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".git")


def repo_root() -> Path:
    """Return this checkout's root (the directory containing this script's ``scripts/``)."""
    return Path(__file__).resolve().parents[1]


def read_version(root: Path) -> str | None:
    """Read and validate a SemVer ``VERSION`` file under *root*, or ``None`` if absent."""
    version_file = root / "VERSION"
    if not version_file.exists():
        return None
    version = version_file.read_text(encoding="utf-8").strip()
    if not SEMVER_RE.match(version):
        raise ValueError(f"{version_file} is not SemVer MAJOR.MINOR.PATCH: {version!r}")
    return version


def version_tuple(version: str) -> tuple[int, int, int]:
    """Convert a SemVer string to a comparable tuple."""
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def install(src: Path, dest: Path, *, force: bool, dry_run: bool) -> int:
    """Copy the skill package at *src* to *dest*. Returns a process exit code."""
    if not (src / "SKILL.md").exists():
        print(f"error: {src} has no SKILL.md — not a skill package", file=sys.stderr)
        return 1

    src_version = read_version(src)
    installed_version = read_version(dest) if dest.exists() else None
    if (
        installed_version is not None
        and src_version is not None
        and version_tuple(installed_version) > version_tuple(src_version)
        and not force
    ):
        print(
            f"refusing to replace newer installed {installed_version} with {src_version} "
            "(use --force)",
            file=sys.stderr,
        )
        return 1
    if dest.exists() and installed_version is None and not force:
        print(f"error: {dest} already exists (use --force to overwrite)", file=sys.stderr)
        return 1

    action = "install" if not dest.exists() else f"update {installed_version} -> {src_version}"
    print(f"{action}: {src} -> {dest}")
    if dry_run:
        return 0

    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=IGNORE)
    print(f"installed at {dest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the installer."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Path to the skill package (contains SKILL.md).")
    parser.add_argument(
        "--dest-root",
        type=Path,
        default=repo_root() / ".claude" / "skills",
        help="Skills root to install into (default: <this repo>/.claude/skills).",
    )
    parser.add_argument("--name", help="Override the installed skill's directory name.")
    parser.add_argument("--force", action="store_true", help="Overwrite regardless of version.")
    parser.add_argument("--dry-run", action="store_true", help="Print the action without copying.")
    args = parser.parse_args(argv)

    src = args.source.resolve()
    if not src.is_dir():
        print(f"error: source directory not found: {src}", file=sys.stderr)
        return 1
    name = args.name or src.name
    dest = (args.dest_root / name).resolve()

    return install(src, dest, force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
