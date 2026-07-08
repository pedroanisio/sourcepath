"""codebase_mapper.cli."""
from __future__ import annotations

import argparse
import json

from pathlib import Path

from .emission.application.emit_bundle import emit
from .emission.application.reconstruct import reconstruct, verify_roundtrip
from .emission.application.regenerate import regenerate
from .inspection.pipeline import map_codebase
from .inspection.repo_source import resolve_repo_source
from .self_test import self_test


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Map a codebase to RDF + SHACL; optionally roundtrip-verify.")
    p.add_argument("--repo",
                   help="Local repository path or Git URL, including GitHub URLs.")
    p.add_argument("--state", default="HEAD")
    p.add_argument("--out", type=Path)
    p.add_argument("--name", default=None)
    p.add_argument("--exclude", action="append", default=[],
                   help="POSIX-glob pattern; files matching are dropped. "
                        "Repeatable. A bare name without wildcards (e.g. '.repo') "
                        "also excludes everything under it. Merged with patterns "
                        "from <repo>/.cbmignore.")
    p.add_argument("--no-emit-blobs", action="store_true",
                   help="Skip writing blobs/ dir (no roundtrip support).")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--reconstruct", action="store_true",
                   help="Reconstruct a codebase from --inventory + --blobs into --out.")
    p.add_argument("--regenerate", action="store_true",
                   help="Regenerate source from --inventory + cbm:astSummary alone "
                        "(no blobs). Semantic roundtrip; not byte-identical.")
    p.add_argument("--inventory", type=Path,
                   help="Path to inventory.ttl (used with --reconstruct/--regenerate).")
    p.add_argument("--blobs", type=Path,
                   help="Path to blobs/ directory (used with --reconstruct).")
    p.add_argument("--report", type=Path,
                   help="Optional report destination (used with --regenerate).")
    p.add_argument("--verify-roundtrip", action="store_true",
                   help="Map the repo, reconstruct from emitted artifacts, verify identity.")
    args = p.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.reconstruct:
        if not (args.inventory and args.blobs and args.out):
            p.error("--reconstruct requires --inventory, --blobs, --out")
        report = reconstruct(args.inventory.resolve(), args.blobs.resolve(), args.out.resolve())
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["missing_blob_count"] == 0 else 1

    if args.regenerate:
        if not (args.inventory and args.out):
            p.error("--regenerate requires --inventory and --out")
        rp = args.report.resolve() if args.report else None
        report = regenerate(args.inventory.resolve(), args.out.resolve(), rp)
        print(json.dumps(report, indent=2, sort_keys=True))
        failures = (len(report["ast_parse_errors"])
                    + len(report["regenerate_errors"])
                    + sum(v.get("failed", 0) for v in report["by_language"].values()))
        return 0 if failures == 0 else 1

    if args.verify_roundtrip:
        if not args.repo:
            p.error("--verify-roundtrip requires --repo")
        with resolve_repo_source(args.repo, args.state) as repo:
            report = verify_roundtrip(repo.path, repo.state, args.exclude)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["roundtrip_ok"] else 1

    if not args.repo or not args.out:
        p.error("--repo and --out are required unless --self-test/--reconstruct/--regenerate/--verify-roundtrip is given")

    with resolve_repo_source(args.repo, args.state, work_dir=args.out.resolve().parent) as repo:
        repo_name = args.name or repo.name
        mapped = map_codebase(repo.path, repo.state, exclude_patterns=args.exclude)
        manifest = emit(repo_name, mapped, args.out.resolve(),
                        emit_blobs_flag=not args.no_emit_blobs)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["shacl_self_check"]["conforms"] else 1
