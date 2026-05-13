#!/usr/bin/env python3
"""run_l3.py — register L2 + L3 plugins and run the host pipeline.

Default: both layers registered. --no-l2 runs L3 alone (no chunks, no
concept centroids; tests that L3 degrades cleanly when L2 is absent).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from plugins import chunks_embeddings
from plugins import concept_graph
from codebase_mapper import emit, map_codebase, reset_registries


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--name", default=None)
    p.add_argument("--state", default="HEAD")
    p.add_argument("--backend", choices=["sbert", "hash"], default="sbert")
    p.add_argument("--hash-dim", type=int, default=256)
    p.add_argument("--sbert-model", default="sentence-transformers/all-MiniLM-L6-v2")
    p.add_argument("--no-l2", action="store_true",
                   help="run L3 alone (no chunks, no concept centroids)")
    p.add_argument("--no-emit-blobs", action="store_true")
    p.add_argument("--exclude", action="append", default=[],
                   help="POSIX-glob pattern; files matching are dropped. "
                        "Repeatable. Merged with patterns from <repo>/.cbmignore.")
    args = p.parse_args(argv)

    reset_registries()

    if not args.no_l2:
        if args.backend == "sbert":
            backend = chunks_embeddings.SentenceTransformerBackend(args.sbert_model)
        else:
            backend = chunks_embeddings.DeterministicHashBackend(args.hash_dim)
        chunks_embeddings.register_all(backend)

    concept_graph.register_all()

    repo_name = args.name or args.repo.resolve().name
    mapped = map_codebase(args.repo.resolve(), args.state, exclude_patterns=args.exclude)
    manifest = emit(repo_name, mapped, args.out.resolve(),
                    emit_blobs_flag=not args.no_emit_blobs)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest.get("shacl_self_check", {}).get("conforms") else 1


if __name__ == "__main__":
    sys.exit(main())
