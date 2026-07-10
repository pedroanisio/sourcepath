#!/usr/bin/env python3
"""verify_repo_source.py — local path and Git URL input contract."""
from __future__ import annotations

import argparse
import json
import os
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


def git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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


def build_deep_repo(path: Path) -> str:
    """master with three commits + a tag on the second; return the first SHA.

    Multiple commits on the default branch are what make a depth-1 clone
    observably shallow (commit count < source count).
    """
    path.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch", "master"], cwd=path, check=True)
    git(path, "config", "user.email", "t@t")
    git(path, "config", "user.name", "t")
    (path / "a.py").write_text("V = 1\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "c1")
    first = git_out(path, "rev-parse", "HEAD")
    (path / "a.py").write_text("V = 2\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "c2")
    git(path, "tag", "v1")
    (path / "a.py").write_text("V = 3\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "c3")
    return first


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

        # ---- Shallow-clone + work-dir relocation regression tests ----
        deep = work / "deep"
        first_sha = build_deep_repo(deep)
        deep_bare = work / "deep.git"
        subprocess.run(["git", "clone", "--bare", str(deep), str(deep_bare)], check=True)
        deep_url = deep_bare.resolve().as_uri()

        # E5 (docs/plan/error-free-mapping.md): correct provenance is the
        # default — the depth-1 transfer is followed by a blob-free history
        # deepen, so full commit history exists without opting in. The
        # shallow-transfer property survives behind CBM_UNSHALLOW=0.
        with resolve_repo_source(deep_url, "HEAD") as c:
            is_shallow = git_out(c.path, "rev-parse", "--is-shallow-repository")
            count = git_out(c.path, "rev-list", "--count", "HEAD")
            head_v = (c.path / "a.py").read_text()
            check(
                "HEAD clone recovers full history by default (E5)",
                is_shallow == "false" and int(count) > 1,
                f"is_shallow={is_shallow} count={count}",
            )
            check("HEAD clone checks out the latest commit", head_v == "V = 3\n", head_v)

        os.environ["CBM_UNSHALLOW"] = "0"
        try:
            with resolve_repo_source(deep_url, "HEAD") as c:
                is_shallow = git_out(c.path, "rev-parse", "--is-shallow-repository")
                count = git_out(c.path, "rev-list", "--count", "HEAD")
                check(
                    "CBM_UNSHALLOW=0 opts out: depth-1 single-commit clone",
                    is_shallow == "true" and count == "1",
                    f"is_shallow={is_shallow} count={count}",
                )
        finally:
            del os.environ["CBM_UNSHALLOW"]

        with resolve_repo_source(deep_url, "v1") as c:
            is_shallow = git_out(c.path, "rev-parse", "--is-shallow-repository")
            tag_v = (c.path / "a.py").read_text()
            check("tag --state deepens to full history by default (E5)",
                  is_shallow == "false", is_shallow)
            check("tag --state checks out the tagged tree", tag_v == "V = 2\n", tag_v)

        with resolve_repo_source(deep_url, first_sha) as c:
            is_shallow = git_out(c.path, "rev-parse", "--is-shallow-repository")
            sha_v = (c.path / "a.py").read_text()
            check(
                "commit SHA --state falls back to a full clone",
                is_shallow == "false",
                is_shallow,
            )
            check("commit SHA --state checks out that commit", sha_v == "V = 1\n", sha_v)

        relo = work / "relocated"
        relo.mkdir()
        with resolve_repo_source(deep_url, "HEAD", work_dir=relo) as c:
            check(
                "work_dir places the clone on the chosen filesystem",
                str(c.path).startswith(str(relo.resolve())),
                f"{c.path} not under {relo}",
            )
        check("work_dir clone is cleaned up after context exit", not any(relo.iterdir()))

        env_root = work / "env-root"
        env_root.mkdir()
        prev_env = os.environ.get("CBM_WORK_DIR")
        os.environ["CBM_WORK_DIR"] = str(env_root)
        try:
            with resolve_repo_source(deep_url, "HEAD") as c:
                check(
                    "CBM_WORK_DIR env var relocates the clone",
                    str(c.path).startswith(str(env_root.resolve())),
                    str(c.path),
                )
        finally:
            if prev_env is None:
                os.environ.pop("CBM_WORK_DIR", None)
            else:
                os.environ["CBM_WORK_DIR"] = prev_env

        missing_url = (work / "does-not-exist.git").resolve().as_uri()
        raised = ""
        try:
            with resolve_repo_source(missing_url, "HEAD"):
                pass
        except RuntimeError as exc:
            raised = str(exc)
        check(
            "failed clone raises an actionable RuntimeError with a relocation hint",
            "CBM_WORK_DIR" in raised and "GiB free" in raised,
            raised,
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
