#!/usr/bin/env python3
"""run_l4.py — register L1+L2+L3+L4 plugins and run the host pipeline.

L4 is the LLM enrichment layer (cbml4: namespace). Default behavior:
chunks/embeddings + concept_graph + llm_enrich all registered. Talks
to a local Ollama server at ``$OLLAMA_HOST`` (default
``http://localhost:11434``) using the model selected by the POC
benchmark (qwen2.5-coder:7b).

Three enrichment kinds fire by default:
  - file_summary       — one sentence per source-code cbm:File
  - concept_description — one paragraph per curated cbml3:Concept
  - schema_purpose     — one paragraph per static/schemas/*.xsd

Each requires Ollama to be reachable; if it's not, the pipeline logs
once per kind and produces a SHACL-conforming pre-L4 bundle (the
"degradation, not breakage" failure mode from plan Commitment #7).

Cache is content-addressed at ``$CBM_LLM_CACHE`` (default
``~/.cache/cbm-llm/``). Re-running over an unchanged repo with a warm
cache is byte-identical to the previous run — that's the warm-cache
determinism guarantee from plan Commitment #5.

For fine-grained L4 control on top of run_l3/run_xrefs, see the
--llm-enrich flag on those scripts; this script is the dedicated L4
entry point with all the knobs surfaced.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codebase_mapper.emission.application.emit_bundle import emit
from codebase_mapper.inspection.pipeline import map_codebase
from codebase_mapper.shared_kernel.extensions import reset_registries
from codebase_mapper.inspection.repo_source import resolve_repo_source
from plugins import chunks_embeddings, concept_graph, llm_enrich


# Default model — the benchmark winner from docs/llm-baseline-results.md.
# Override via --llm-model or $CBM_LLM_MODEL; register_all auto-resolves to
# an installed same-family tag when this one is not pulled.
from plugins.llm_enrich import DEFAULT_MODEL
DEFAULT_SCOPES = ("files", "concepts", "schemas")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # ---- L1+L2+L3 flags mirror run_l3.py ----
    p.add_argument("--repo", required=True,
                   help="Local repository path or Git URL, including GitHub URLs.")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--name", default=None)
    p.add_argument("--state", default="HEAD")
    p.add_argument("--backend", choices=["sbert", "hash"], default="sbert",
                   help="L2 embedding backend.")
    p.add_argument("--hash-dim", type=int, default=256)
    p.add_argument("--sbert-model",
                   default="sentence-transformers/all-MiniLM-L6-v2")
    p.add_argument("--no-l2", action="store_true",
                   help="Skip L2 chunks/embeddings. L4's file_summary "
                        "still works; concept_description loses concept "
                        "centroids but the prompts don't depend on them.")
    p.add_argument("--no-emit-blobs", action="store_true")
    p.add_argument("--exclude", action="append", default=[],
                   help="POSIX-glob pattern; files matching are dropped. "
                        "Repeatable. Merged with patterns from <repo>/.cbmignore.")
    p.add_argument("--concept-vocab", type=Path, default=None,
                   help="Override the bundled L3 controlled vocabulary (YAML). "
                        "See codebase_mapper/emission/infrastructure/vocab/loader.py.")
    p.add_argument("--no-builtin-vocab", action="store_true",
                   help="Disable typed concepts entirely.")

    # ---- L4-specific flags ----
    p.add_argument("--llm-model", default=DEFAULT_MODEL,
                   help=f"Ollama model tag for L4 enrichment "
                        f"(default: {DEFAULT_MODEL!r}).")
    p.add_argument("--llm-host", default=None,
                   help="Ollama base URL. When omitted, honors $OLLAMA_HOST, "
                        "then falls back to http://localhost:11434.")
    p.add_argument("--llm-scope", default=",".join(DEFAULT_SCOPES),
                   help="Comma-separated subset of "
                        "{files,concepts,schemas}. Default: all three. "
                        "Pass --llm-scope '' to register the plugin but "
                        "produce zero enrichments (verifier path).")
    p.add_argument("--llm-cache-dir", type=Path, default=None,
                   help="Override the cache location. When omitted, honors "
                        "$CBM_LLM_CACHE, then falls back to ~/.cache/cbm-llm/.")
    p.add_argument("--llm-no-cache", action="store_true",
                   help="Force every prompt to re-call Ollama. Verifier-only "
                        "— breaks warm-cache determinism by design.")
    p.add_argument("--no-llm", action="store_true",
                   help="Skip L4 entirely. Equivalent to scripts/run_l3.py "
                        "but preserved here for symmetry with --no-l2.")
    args = p.parse_args(argv)

    # ---- Validation ----
    if args.concept_vocab and args.no_builtin_vocab:
        p.error("--concept-vocab and --no-builtin-vocab are mutually exclusive")

    scopes = _parse_scopes(args.llm_scope, parser=p)

    # ---- Registration ----
    reset_registries()

    if not args.no_l2:
        if args.backend == "sbert":
            backend = chunks_embeddings.SentenceTransformerBackend(args.sbert_model)
        else:
            backend = chunks_embeddings.DeterministicHashBackend(args.hash_dim)
        chunks_embeddings.register_all(backend)

    # L3 vocab resolution mirrors run_l3.py exactly.
    if args.no_builtin_vocab:
        l3_vocab: object = None
    elif args.concept_vocab is not None:
        from codebase_mapper.emission.infrastructure.vocab import load_vocabulary
        l3_vocab = load_vocabulary(args.concept_vocab.resolve())
    else:
        l3_vocab = concept_graph.USE_BUILTIN
    concept_graph.register_all(vocab=l3_vocab)

    if not args.no_llm:
        client = llm_enrich.OllamaClient(host=args.llm_host) \
            if args.llm_host else llm_enrich.OllamaClient()
        cache = llm_enrich.Cache(
            cache_dir=args.llm_cache_dir.resolve()
                      if args.llm_cache_dir else _default_cache_dir(),
            enabled=not args.llm_no_cache,
        )
        # Preflight: a quick non-fatal probe. The enricher/aggregator
        # handle unreachable Ollama on their own (plan Commitment #7),
        # but a CLI hint up-front saves the user from wondering why
        # there are no cbml4: triples in the output.
        if not client.ping():
            print(
                f"NOTE: Ollama unreachable at {client.host} — L4 "
                f"enrichment will be skipped, but the pipeline will "
                f"still emit a SHACL-conforming L1+L2+L3 bundle.",
                file=sys.stderr,
            )
        llm_enrich.register_all(
            client=client, cache=cache,
            model=args.llm_model, scopes=scopes,
        )

    # ---- Run ----
    with resolve_repo_source(args.repo, args.state, work_dir=args.out.resolve().parent) as repo:
        repo_name = args.name or repo.name
        mapped = map_codebase(repo.path, repo.state,
                              exclude_patterns=args.exclude)
        manifest = emit(repo_name, mapped, args.out.resolve(),
                        emit_blobs_flag=not args.no_emit_blobs)
    _print_l4_summary(manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest.get("shacl_self_check", {}).get("conforms") else 1


def _parse_scopes(raw: str, *, parser: argparse.ArgumentParser) -> tuple[str, ...]:
    """Parse the --llm-scope CSV. Empty string → empty tuple
    (plugin registered, zero enrichments — the verifier path).
    Unknown scope literals → loud failure."""
    if not raw.strip():
        return ()
    parts = tuple(s.strip() for s in raw.split(",") if s.strip())
    unknown = set(parts) - set(llm_enrich.ALL_SCOPES)
    if unknown:
        parser.error(
            f"--llm-scope: unknown scope(s) {sorted(unknown)}. "
            f"Valid values: {llm_enrich.ALL_SCOPES}."
        )
    return parts


def _default_cache_dir() -> Path:
    """Match the cache layer's default — honors $CBM_LLM_CACHE."""
    from plugins.llm_enrich.cache import default_cache_dir
    return default_cache_dir()


def _print_l4_summary(manifest: dict) -> None:
    """Final progress line: reuses the counts LlmArtifact already tallied
    (run_manifest.json["extensions"]["l4_50_artifact"]) rather than
    recomputing them — one source of truth for "how many enrichments"."""
    artifact = manifest.get("extensions", {}).get("l4_50_artifact", {})
    n = artifact.get("n_enrichments", 0)
    by_kind = artifact.get("by_kind", {})
    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items()))
    print(f"[L4] done — {n} enrichment record(s) written"
          f"{f' ({breakdown})' if breakdown else ''}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
