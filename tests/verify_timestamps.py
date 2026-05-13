#!/usr/bin/env python3
"""verify_timestamps.py — atime / mtime / ctime / gitCommitTime contract.

Tests:
  - Filesystem times: os.lstat values appear on each cbm:File as
    cbm:atime / cbm:mtime / cbm:ctime (xsd:dateTime UTC).
  - Setting mtime via os.utime is reflected in the next mapping.
  - Git commit times: cbm:gitCommitTime matches the author timestamp of
    the last commit that touched the path. Two-commit fixture exercises
    the "most recent commit wins" rule.
  - Symlinks: a tracked symlink does not raise and gets its own metadata
    (not the target's).
  - SHACL: the emitted graph conforms with the new shapes attached.
  - Determinism: two consecutive runs produce byte-identical inventory.ttl
    (stat() does not bump atime, so the file is stable between runs).

Exit code: 0 if all pass.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from codebase_mapper.constants import CBM, CBMI_NS
from codebase_mapper.pipeline import map_codebase
from codebase_mapper.rdf_emit import (
    _iso_utc,
    build_inventory_graph,
    build_shacl_graph,
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


def _git(repo: Path, *args: str, env: dict | None = None) -> None:
    e = {**os.environ, **(env or {})}
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=e)


def build_fixture(target: Path) -> tuple[int, int]:
    """Build a 2-commit fixture and return (ts1, ts2) — the author times.

    Commit 1: introduces app.py, lib.py, README.md.
    Commit 2: re-touches app.py (so its gitCommitTime == ts2 > ts1).
    """
    target.mkdir()
    _git(target.parent, "init", target.name, "-q")
    _git(target, "config", "user.email", "t@t")
    _git(target, "config", "user.name", "t")

    (target / "app.py").write_text("def main(): pass\n")
    (target / "lib.py").write_text("X = 1\n")
    (target / "README.md").write_text("# fixture\n")
    # symlink whose target doesn't exist — lstat must still succeed.
    (target / "danglink").symlink_to("does/not/exist")

    ts1 = 1_700_000_000
    env1 = {
        "GIT_AUTHOR_DATE": f"{ts1} +0000",
        "GIT_COMMITTER_DATE": f"{ts1} +0000",
    }
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "init", env=env1)

    # second commit: touches only app.py
    (target / "app.py").write_text("def main(): return 1\n")
    ts2 = 1_700_086_400  # ts1 + 1 day
    env2 = {
        "GIT_AUTHOR_DATE": f"{ts2} +0000",
        "GIT_COMMITTER_DATE": f"{ts2} +0000",
    }
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "update app", env=env2)
    return ts1, ts2


def main(argv: list[str] | None = None) -> int:
    global PASS, FAIL
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true")
    args = p.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="verify_timestamps_"))
    try:
        fixture = work / "fixture"
        ts1, ts2 = build_fixture(fixture)

        # Stamp a known mtime on lib.py so we can round-trip it.
        target_mtime = 1_600_000_123
        os.utime(fixture / "lib.py", (target_mtime, target_mtime))

        mapped = map_codebase(fixture.resolve(), "HEAD")
        by_path = {r.path: r for r in mapped["records"]}

        # --- 1. filesystem times present on every regular file ---
        app = by_path["app.py"]
        check(
            "atime / mtime / ctime populated for tracked files",
            app.atime is not None and app.mtime is not None and app.ctime is not None,
            f"app={app}",
        )

        # --- 2. os.utime round-trips through the pipeline ---
        lib = by_path["lib.py"]
        check(
            "mtime survives os.utime → map_codebase",
            int(lib.mtime) == target_mtime,
            f"got {lib.mtime}, expected {target_mtime}",
        )

        # --- 3. git_commit_time semantics ---
        check(
            "app.py.gitCommitTime == ts of its latest commit",
            app.git_commit_time == ts2,
            f"got {app.git_commit_time}, expected {ts2}",
        )
        readme = by_path["README.md"]
        check(
            "README.md.gitCommitTime == ts of its only (initial) commit",
            readme.git_commit_time == ts1,
            f"got {readme.git_commit_time}, expected {ts1}",
        )

        # --- 4. dangling symlink doesn't break the run ---
        check(
            "dangling symlink is mapped with its own metadata (lstat)",
            "danglink" in by_path
            and by_path["danglink"].mtime is not None,
            f"keys={sorted(by_path)}",
        )

        # --- 5. RDF triples + SHACL conformance ---
        inv = build_inventory_graph(
            repo_iri=URIRef(f"{CBMI_NS}repo/{fixture.name}"),
            commit_sha=mapped["commit"],
            records=mapped["records"],
            import_edges=mapped["import_edges"],
            import_ext_edges=mapped["import_ext_edges"],
            dep_edges=mapped["dep_edges"],
            pin_edges=mapped["pin_edges"],
            tests_edges=mapped["tests_edges"],
        )
        shapes = build_shacl_graph()

        from urllib.parse import quote

        app_iri = URIRef(f"{CBMI_NS}file/{quote('app.py', safe='')}")
        atime_lit = inv.value(app_iri, CBM.atime)
        mtime_lit = inv.value(app_iri, CBM.mtime)
        ctime_lit = inv.value(app_iri, CBM.ctime)
        git_lit = inv.value(app_iri, CBM.gitCommitTime)
        check(
            "inventory.ttl has all four time predicates on app.py",
            all(x is not None for x in (atime_lit, mtime_lit, ctime_lit, git_lit)),
            f"atime={atime_lit} mtime={mtime_lit} ctime={ctime_lit} git={git_lit}",
        )
        # rdflib normalizes the lexical form of xsd:dateTime (Z -> +00:00),
        # so compare the parsed datetime instead of raw string.
        import datetime as _dt

        expected_dt = _dt.datetime.fromtimestamp(ts2, tz=_dt.timezone.utc)
        check(
            "gitCommitTime literal parses back to the expected UTC datetime",
            git_lit.toPython() == expected_dt,
            f"got {git_lit.toPython()!r}, expected {expected_dt!r}",
        )

        from pyshacl import validate
        conforms, _vg, report = validate(
            data_graph=inv, shacl_graph=shapes,
            inference="none", abort_on_first=False,
        )
        check(
            "SHACL conformance with new time shapes",
            bool(conforms),
            (report or "")[:500],
        )

        # --- 6. Determinism: two consecutive maps -> identical TTL ---
        mapped2 = map_codebase(fixture.resolve(), "HEAD")
        inv2 = build_inventory_graph(
            repo_iri=URIRef(f"{CBMI_NS}repo/{fixture.name}"),
            commit_sha=mapped2["commit"],
            records=mapped2["records"],
            import_edges=mapped2["import_edges"],
            import_ext_edges=mapped2["import_ext_edges"],
            dep_edges=mapped2["dep_edges"],
            pin_edges=mapped2["pin_edges"],
            tests_edges=mapped2["tests_edges"],
        )
        a = inv.serialize(format="turtle")
        b = inv2.serialize(format="turtle")
        check(
            "determinism: two consecutive maps produce identical inventory.ttl",
            a == b,
            f"len a={len(a)} len b={len(b)}",
        )

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
