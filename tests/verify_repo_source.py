#!/usr/bin/env python3
"""verify_repo_source.py — local path and Git URL input contract."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codebase_mapper.inspection.pipeline import map_codebase
from codebase_mapper.inspection.repo_source import (
    normalize_git_source,
    repo_name_from_source,
    resolve_repo_source,
)


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


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True)


def build_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch", "master"], cwd=path, check=True)
    git(path, "config", "user.email", "t@t")
    git(path, "config", "user.name", "t")
    (path / "app.py").write_text("import lib\n\nprint(lib.VALUE)\n")
    (path / "lib.py").write_text("VALUE = 42\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    git(path, "checkout", "-q", "-b", "feature")
    (path / "feature.py").write_text("ENABLED = True\n")
    git(path, "add", "feature.py")
    git(path, "commit", "-q", "-m", "feature")
    git(path, "checkout", "-q", "master")


def main(argv: list[str] | None = None) -> int:
    global PASS, FAIL
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true")
    args = p.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="verify_repo_source_"))
    try:
        check(
            "GitHub shorthand normalizes to HTTPS clone URL",
            normalize_git_source("github.com/openai/example") == "https://github.com/openai/example.git",
        )
        check(
            "HTTPS GitHub URL is accepted unchanged",
            normalize_git_source("https://github.com/openai/example.git")
            == "https://github.com/openai/example.git",
        )
        check(
            "SSH scp-like GitHub URL is accepted unchanged",
            normalize_git_source("git@github.com:openai/example.git")
            == "git@github.com:openai/example.git",
        )
        check(
            "local path does not get treated as a remote source",
            normalize_git_source("/tmp/example") is None,
        )
        check(
            "repo name strips .git suffix from URLs",
            repo_name_from_source("https://github.com/openai/example.git") == "example",
        )

        src = work / "src"
        build_repo(src)
        bare = work / "example.git"
        subprocess.run(["git", "clone", "--bare", str(src), str(bare)], check=True)

        with resolve_repo_source(src, "HEAD") as local:
            check("local path resolves without cloning", local.path == src.resolve() and not local.cloned)

        file_url = bare.resolve().as_uri()
        with resolve_repo_source(file_url, "HEAD") as cloned:
            mapped = map_codebase(cloned.path, "HEAD")
            paths = {r.path for r in mapped["records"]}
            check("file:// Git URL clones into a temporary repo", cloned.cloned and cloned.path.exists())
            check("cloned Git URL can be mapped", {"app.py", "lib.py"} <= paths, json.dumps(sorted(paths)))

        check("temporary clone is cleaned after context exit", not cloned.path.exists())

        with resolve_repo_source(file_url, "feature") as cloned_feature:
            mapped_feature = map_codebase(cloned_feature.path, cloned_feature.state)
            feature_paths = {r.path for r in mapped_feature["records"]}
            check(
                "Git URL --state can resolve a remote branch name",
                "feature.py" in feature_paths,
                json.dumps(sorted(feature_paths)),
            )

        out = work / "cli-out"
        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "codebase_mapper",
                "--repo",
                file_url,
                "--out",
                str(out),
            ],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
        )
        check(
            "host CLI accepts a Git URL in --repo",
            cli.returncode == 0 and (out / "run_manifest.json").exists(),
            cli.stderr or cli.stdout,
        )
    finally:
        if args.keep:
            print(f"kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)

    print(f"\nResult: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
