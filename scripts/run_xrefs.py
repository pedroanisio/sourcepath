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
from codebase_mapper.repo_source import resolve_repo_source


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True,
                   help="Local repository path or Git URL, including GitHub URLs.")
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
    p.add_argument("--concept-vocab", type=Path, default=None,
                   help="Override the bundled L3 controlled vocabulary "
                        "(YAML). Implies --concepts. See "
                        "codebase_mapper/vocab/loader.py.")
    p.add_argument("--no-builtin-vocab", action="store_true",
                   help="Disable typed concepts entirely. Implies "
                        "--concepts. Emitted L3 graphs match pre-vocab "
                        "bundles (no cbml3:conceptKind, no "
                        "skos:Collection nodes).")
    p.add_argument("--llm-enrich", action="store_true",
                   help="Also register the L4 LLM-enrichment plugin. "
                        "Implies --concepts (concept_description "
                        "enrichment needs L3's typed concepts). "
                        "Default model qwen2.5-coder:7b. Requires a "
                        "local Ollama server; on failure the bundle "
                        "degrades cleanly. For fine-grained control "
                        "use scripts/run_l4.py.")
    p.add_argument("--no-emit-blobs", action="store_true")
    p.add_argument("--exclude", action="append", default=[],
                   help="POSIX-glob pattern; files matching are dropped. "
                        "Repeatable. Merged with patterns from <repo>/.cbmignore.")
    args = p.parse_args(argv)

    if args.concept_vocab and args.no_builtin_vocab:
        p.error("--concept-vocab and --no-builtin-vocab are mutually exclusive")

    # Vocab and llm-enrich flags imply --concepts; otherwise they'd
    # silently no-op (concept_graph wouldn't be registered to consume them).
    want_concepts = (args.concepts
                     or args.concept_vocab is not None
                     or args.no_builtin_vocab
                     or args.llm_enrich)

    reset_registries()
    if args.backend == "sbert":
        backend = chunks_embeddings.SentenceTransformerBackend(args.sbert_model)
    else:
        backend = chunks_embeddings.DeterministicHashBackend(args.hash_dim)
    chunks_embeddings.register_all(backend)
    symbol_xrefs.register_all()
    if want_concepts:
        from plugins import concept_graph
        if args.no_builtin_vocab:
            l3_vocab = None
        elif args.concept_vocab is not None:
            from codebase_mapper.vocab import load_vocabulary
            l3_vocab = load_vocabulary(args.concept_vocab.resolve())
        else:
            l3_vocab = concept_graph.USE_BUILTIN
        concept_graph.register_all(vocab=l3_vocab)

    if args.llm_enrich:
        from plugins import llm_enrich
        client = llm_enrich.OllamaClient()
        if not client.ping():
            print(
                f"NOTE: Ollama unreachable at {client.host} — L4 "
                f"enrichment will be skipped, but the pipeline will "
                f"still emit a SHACL-conforming bundle.",
                file=sys.stderr,
            )
        llm_enrich.register_all(
            client=client,
            cache=llm_enrich.Cache(),
            scopes=llm_enrich.ALL_SCOPES,
        )

    with resolve_repo_source(args.repo, args.state) as repo:
        repo_name = args.name or repo.name
        mapped = map_codebase(repo.path, repo.state, exclude_patterns=args.exclude)
        manifest = emit(repo_name, mapped, args.out.resolve(),
                        emit_blobs_flag=not args.no_emit_blobs)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest.get("shacl_self_check", {}).get("conforms") else 1


if __name__ == "__main__":
    sys.exit(main())
