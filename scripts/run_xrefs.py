#!/usr/bin/env python3
"""run_xrefs.py — register L2 + symbol_xrefs plugins and run the host pipeline.

Produces a full L1+L2+xref bundle so downstream consumers (frontend
backend, verifiers) can load it with no extra setup. Concepts (L3) are
not registered by default — they're orthogonal to xrefs and slow the
verifier when not needed; pass --concepts to opt in.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from plugins import chunks_embeddings, symbol_xrefs
from codebase_mapper import emit, map_codebase, reset_registries


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--name", default=None)
    p.add_argument("--state", default="HEAD")
    p.add_argument("--backend", choices=["sbert", "hash"], default="hash",
                   help="hash = deterministic SHA-256 fake (fast, verifier-friendly). "
                        "sbert = sentence-transformers/all-MiniLM-L6-v2 (real).")
    p.add_argument("--hash-dim", type=int, default=256)
    p.add_argument("--sbert-model", default="sentence-transformers/all-MiniLM-L6-v2")
    p.add_argument("--concepts", action="store_true",
                   help="Also register the L3 concept_graph plugin.")
    p.add_argument("--no-emit-blobs", action="store_true")
    p.add_argument("--exclude", action="append", default=[],
                   help="POSIX-glob pattern; files matching are dropped. "
                        "Repeatable. Merged with patterns from <repo>/.cbmignore.")
    args = p.parse_args(argv)

    reset_registries()
    if args.backend == "sbert":
        backend = chunks_embeddings.SentenceTransformerBackend(args.sbert_model)
    else:
        backend = chunks_embeddings.DeterministicHashBackend(args.hash_dim)
    chunks_embeddings.register_all(backend)
    symbol_xrefs.register_all()
    if args.concepts:
        from plugins import concept_graph
        concept_graph.register_all()

    repo_name = args.name or args.repo.resolve().name
    mapped = map_codebase(args.repo.resolve(), args.state, exclude_patterns=args.exclude)
    manifest = emit(repo_name, mapped, args.out.resolve(),
                    emit_blobs_flag=not args.no_emit_blobs)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest.get("shacl_self_check", {}).get("conforms") else 1


if __name__ == "__main__":
    sys.exit(main())
