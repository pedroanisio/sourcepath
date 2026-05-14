#!/usr/bin/env python3
"""verify_excludes.py — exclude pattern contract.

Covers three exclusion sources, applied together:
  1. ``--exclude`` CLI flag / ``exclude_patterns`` argument.
  2. ``<repo>/.cbmignore`` (one POSIX-glob per line, ``#`` comments).
  3. The convenience rule that a wildcard-free bare pattern (e.g. ``.repo``)
     excludes both the path itself and any descendant.

Tests:
  - path_excluded handles bare names, dir/** form, fnmatch globs.
  - read_repo_ignore parses comments and blank lines, missing-file is no-op.
  - map_codebase drops .cbmignore-listed paths from the mapped records.
  - map_codebase merges CLI patterns with .cbmignore patterns in the manifest.
  - The CLI flag's manifest entry includes everything that was actually
    applied (so emit_bundle's run_manifest.json is a faithful audit log).

Exit code: 0 if all pass.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codebase_mapper.inspection.classify import path_excluded, read_repo_ignore
from codebase_mapper.inspection.pipeline import map_codebase


PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        print(f"  PASS  {name}")
        PASS += 1
    else:
        print(f"  FAIL  {name}")
        if detail:
            for line in detail.splitlines()[:10]:
                print(f"        {line}")
        FAIL += 1


def build_fixture(target: Path, cbmignore_body: str | None) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)

    (target / "app.py").write_text('def main(): pass\n')
    (target / "lib.py").write_text('X = 1\n')
    (target / "README.md").write_text("# hi\n")

    # Top-level `.repo` directory with a tracked file — the user's example.
    (target / ".repo").mkdir()
    (target / ".repo" / "manifest.xml").write_text("<repo/>\n")

    # vendored area to verify dir/** form
    (target / "vendor").mkdir()
    (target / "vendor" / "lib.py").write_text("# vendored\n")

    # nested data directory
    (target / "docs").mkdir()
    (target / "docs" / "_build").mkdir()
    (target / "docs" / "_build" / "page.html").write_text("<html/>\n")
    (target / "docs" / "real.md").write_text("# real\n")

    if cbmignore_body is not None:
        (target / ".cbmignore").write_text(cbmignore_body)

    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True
    )


def main(argv: list[str] | None = None) -> int:
    global PASS, FAIL
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true")
    args = p.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="verify_excludes_"))
    try:
        # --- 1. path_excluded — pure unit cases ---
        check(
            "fnmatch glob excludes a matching path",
            path_excluded("vendor/lib.py", ["vendor/*.py"]),
        )
        check(
            "fnmatch glob does not exclude a non-matching path",
            not path_excluded("app.py", ["vendor/*.py"]),
        )
        check(
            "dir/** form excludes the dir itself",
            path_excluded(".repo", [".repo/**"]),
        )
        check(
            "dir/** form excludes a descendant",
            path_excluded(".repo/manifest.xml", [".repo/**"]),
        )
        check(
            "bare dir name excludes the dir itself",
            path_excluded(".repo", [".repo"]),
        )
        check(
            "bare dir name excludes a descendant (convenience rule)",
            path_excluded(".repo/manifest.xml", [".repo"]),
        )
        check(
            "bare dir name does not over-match sibling paths",
            not path_excluded(".reporting", [".repo"]),
        )
        check(
            "empty pattern list matches nothing",
            not path_excluded("anything", []),
        )
        # gitignore-style trailing slash on a directory pattern.
        check(
            "trailing-slash dir name excludes descendants ('.repo/' form)",
            path_excluded(".repo/prompts/x.md", [".repo/"]),
        )
        check(
            "trailing-slash dir name excludes the dir itself",
            path_excluded(".repo", [".repo/"]),
        )
        check(
            "trailing slash on a wildcarded pattern is harmless",
            path_excluded("vendor/sub/lib.py", ["vendor/**/"]),
        )
        check(
            "pure-slash pattern is a no-op (doesn't match everything)",
            not path_excluded("app.py", ["/"]),
        )

        # --- 2. read_repo_ignore — file parsing ---
        ig_dir = work / "ig"
        ig_dir.mkdir()
        (ig_dir / ".cbmignore").write_text(
            "# leading comment\n"
            "\n"
            ".repo\n"
            "vendor/**\n"
            "  # indented comment\n"
            "docs/_build/**\n"
        )
        patterns = read_repo_ignore(ig_dir)
        check(
            "read_repo_ignore strips comments + blank lines",
            patterns == [".repo", "vendor/**", "docs/_build/**"],
            f"got: {patterns!r}",
        )
        check(
            "read_repo_ignore returns [] when .cbmignore absent",
            read_repo_ignore(work) == [],
        )

        # --- 3. map_codebase honors .cbmignore ---
        fixture = work / "fixture-cbmignore"
        build_fixture(
            fixture,
            cbmignore_body=".repo\nvendor/**\ndocs/_build/**\n",
        )
        mapped = map_codebase(fixture.resolve(), "HEAD")
        paths = sorted(r.path for r in mapped["records"])
        # .repo and its contents excluded; vendor/** excluded; docs/_build/** excluded;
        # .cbmignore itself is NOT excluded (no pattern says so) — should be in the inventory.
        assert ".repo/manifest.xml" not in paths
        check(
            ".cbmignore drops the .repo directory contents",
            ".repo" not in paths and ".repo/manifest.xml" not in paths,
            f"paths={paths}",
        )
        check(
            ".cbmignore vendor/** drops vendor descendants",
            "vendor/lib.py" not in paths,
            f"paths={paths}",
        )
        check(
            ".cbmignore docs/_build/** drops the nested build dir",
            "docs/_build/page.html" not in paths and "docs/real.md" in paths,
            f"paths={paths}",
        )
        check(
            "non-ignored files pass through",
            "app.py" in paths and "lib.py" in paths and "README.md" in paths,
            f"paths={paths}",
        )

        # --- 4. Merging .cbmignore with CLI exclude_patterns ---
        fixture2 = work / "fixture-merge"
        build_fixture(fixture2, cbmignore_body=".repo\n")
        mapped2 = map_codebase(
            fixture2.resolve(), "HEAD", exclude_patterns=["vendor/**"]
        )
        paths2 = sorted(r.path for r in mapped2["records"])
        check(
            "CLI exclude + .cbmignore both applied",
            ".repo/manifest.xml" not in paths2 and "vendor/lib.py" not in paths2,
            f"paths={paths2}",
        )
        check(
            "manifest exclude_patterns reports merged list (CLI first, then file)",
            mapped2["exclude_patterns"] == ["vendor/**", ".repo"],
            f"got: {mapped2['exclude_patterns']!r}",
        )

        # --- 5. .cbmignore absent → CLI-only excludes still work ---
        fixture3 = work / "fixture-cli-only"
        build_fixture(fixture3, cbmignore_body=None)
        mapped3 = map_codebase(
            fixture3.resolve(), "HEAD", exclude_patterns=[".repo", "vendor/**"]
        )
        paths3 = sorted(r.path for r in mapped3["records"])
        check(
            "CLI-only excludes still work without .cbmignore",
            ".repo/manifest.xml" not in paths3 and "vendor/lib.py" not in paths3
            and "app.py" in paths3,
            f"paths={paths3}",
        )
        check(
            "manifest exclude_patterns reflects CLI-only list",
            mapped3["exclude_patterns"] == [".repo", "vendor/**"],
            f"got: {mapped3['exclude_patterns']!r}",
        )

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
