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
from codebase_mapper.emission.application.emit_bundle import emit
from codebase_mapper.inspection.pipeline import map_codebase
from codebase_mapper.shared_kernel.extensions import reset_registries
from codebase_mapper.inspection.repo_source import resolve_repo_source


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True,
                   help="Local repository path or Git URL, including GitHub URLs.")
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
    p.add_argument("--concept-vocab", type=Path, default=None,
                   help="Override the bundled L3 controlled vocabulary "
                        "(YAML). When omitted, software_primitives.yaml is "
                        "used. See codebase_mapper/emission/infrastructure/vocab/loader.py.")
    p.add_argument("--no-builtin-vocab", action="store_true",
                   help="Disable typed concepts entirely. Emitted L3 graphs "
                        "match pre-vocab bundles (no cbml3:conceptKind, no "
                        "skos:Collection nodes).")
    p.add_argument("--llm-enrich", action="store_true",
                   help="Also register the L4 LLM-enrichment plugin "
                        "(plugins/llm_enrich/). Default model "
                        "qwen2.5-coder:7b, default scopes "
                        "files+concepts+schemas, default cache "
                        "~/.cache/cbm-llm/. Requires a local Ollama "
                        "server; on failure the bundle degrades cleanly "
                        "to L1+L2+L3. For fine-grained control "
                        "(--llm-model, --llm-host, --llm-scope, ...) use "
                        "scripts/run_l4.py.")
    args = p.parse_args(argv)

    if args.concept_vocab and args.no_builtin_vocab:
        p.error("--concept-vocab and --no-builtin-vocab are mutually exclusive")

    reset_registries()

    if not args.no_l2:
        if args.backend == "sbert":
            backend = chunks_embeddings.SentenceTransformerBackend(args.sbert_model)
        else:
            backend = chunks_embeddings.DeterministicHashBackend(args.hash_dim)
        chunks_embeddings.register_all(backend)

    if args.no_builtin_vocab:
        l3_vocab = None
    elif args.concept_vocab is not None:
        from codebase_mapper.emission.infrastructure.vocab import load_vocabulary
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
                f"still emit a SHACL-conforming L1+L2+L3 bundle.",
                file=sys.stderr,
            )
        llm_enrich.register_all(
            client=client,
            cache=llm_enrich.Cache(),
            scopes=llm_enrich.ALL_SCOPES,
        )

    with resolve_repo_source(args.repo, args.state, work_dir=args.out.resolve().parent) as repo:
        repo_name = args.name or repo.name
        mapped = map_codebase(repo.path, repo.state, exclude_patterns=args.exclude)
        manifest = emit(repo_name, mapped, args.out.resolve(),
                        emit_blobs_flag=not args.no_emit_blobs)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest.get("shacl_self_check", {}).get("conforms") else 1


if __name__ == "__main__":
    sys.exit(main())
