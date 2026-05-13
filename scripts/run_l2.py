#!/usr/bin/env python3
"""run_l2.py — register L2 plugins and run the host pipeline.

Replaces the prototype harness: now that the host exposes register_*
functions and runs the iteration internally, plugins go through the same
path as host-side code.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from plugins import chunks_embeddings
from codebase_mapper import emit, map_codebase, reset_registries


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--name", default=None)
    p.add_argument("--state", default="HEAD")
    p.add_argument("--backend", choices=["sbert", "hash"], default="sbert",
                   help="sbert = sentence-transformers/all-MiniLM-L6-v2 (real, 384-dim). "
                        "hash = deterministic SHA-256 fake (256-dim, no semantics).")
    p.add_argument("--hash-dim", type=int, default=256)
    p.add_argument("--sbert-model", default="sentence-transformers/all-MiniLM-L6-v2")
    p.add_argument("--no-emit-blobs", action="store_true")
    p.add_argument("--exclude", action="append", default=[],
                   help="POSIX-glob pattern; files matching are dropped. "
                        "Repeatable. Merged with patterns from <repo>/.cbmignore.")
    args = p.parse_args(argv)

    # Start from a clean slate: a previous process may have registered
    # plugins. In a fresh subprocess this is a no-op but cheap insurance.
    reset_registries()

    if args.backend == "sbert":
        backend = chunks_embeddings.SentenceTransformerBackend(args.sbert_model)
    else:
        backend = chunks_embeddings.DeterministicHashBackend(args.hash_dim)
    chunks_embeddings.register_all(backend)

    repo_name = args.name or args.repo.resolve().name
    mapped = map_codebase(args.repo.resolve(), args.state, exclude_patterns=args.exclude)
    manifest = emit(repo_name, mapped, args.out.resolve(),
                    emit_blobs_flag=not args.no_emit_blobs)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest.get("shacl_self_check", {}).get("conforms") else 1


if __name__ == "__main__":
    sys.exit(main())
