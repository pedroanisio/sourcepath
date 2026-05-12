"""codebase_mapper.cli."""
from __future__ import annotations

import argparse
import json

from pathlib import Path

from .emit_bundle import emit
from .pipeline import map_codebase
from .reconstruct import reconstruct, verify_roundtrip
from .self_test import self_test


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Map a codebase to RDF + SHACL; optionally roundtrip-verify.")
    p.add_argument("--repo", type=Path)
    p.add_argument("--state", default="HEAD")
    p.add_argument("--out", type=Path)
    p.add_argument("--name", default=None)
    p.add_argument("--exclude", action="append", default=[],
                   help="POSIX-glob pattern; files matching are dropped. Repeatable.")
    p.add_argument("--no-emit-blobs", action="store_true",
                   help="Skip writing blobs/ dir (no roundtrip support).")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--reconstruct", action="store_true",
                   help="Reconstruct a codebase from --inventory + --blobs into --out.")
    p.add_argument("--inventory", type=Path,
                   help="Path to inventory.ttl (used with --reconstruct).")
    p.add_argument("--blobs", type=Path,
                   help="Path to blobs/ directory (used with --reconstruct).")
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

    if args.verify_roundtrip:
        if not args.repo:
            p.error("--verify-roundtrip requires --repo")
        report = verify_roundtrip(args.repo.resolve(), args.state, args.exclude)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["roundtrip_ok"] else 1

    if not args.repo or not args.out:
        p.error("--repo and --out are required unless --self-test/--reconstruct/--verify-roundtrip is given")

    repo = args.repo.resolve()
    repo_name = args.name or repo.name
    mapped = map_codebase(repo, args.state, exclude_patterns=args.exclude)
    manifest = emit(repo_name, mapped, args.out.resolve(),
                    emit_blobs_flag=not args.no_emit_blobs)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["shacl_self_check"]["conforms"] else 1
