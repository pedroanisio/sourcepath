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
from .shared_kernel.settings import load_env


VALID_LAYERS = ("l1", "l2", "l3", "xrefs")


def register_layers(spec: str, backend_name: str = "hash") -> list[str]:
    """Register the plugin layers named in ``spec``. Returns what was enabled.

    The console script previously had no way to reach any plugin: registration
    lived only in ``scripts/run_l*.py``, and ``scripts*`` is excluded from the
    wheel while ``plugins*`` ships in it. An installed ``codebase-mapper`` could
    therefore emit L1 bundles only, with the plugin code present but
    unreachable — every documented L2/L3 workflow silently required a git
    checkout.

    Layer order is load-bearing: L3's concept aggregator reads L2's index
    entry, and the xref resolvers read L2's chunks, so both imply L2.
    """
    requested = {part.strip().lower() for part in spec.split(",") if part.strip()}
    unknown = sorted(requested - set(VALID_LAYERS))
    if unknown:
        raise ValueError(
            f"unknown layer(s): {', '.join(unknown)}; valid: {', '.join(VALID_LAYERS)}"
        )

    wants_l3 = "l3" in requested
    wants_xrefs = "xrefs" in requested
    wants_l2 = "l2" in requested or wants_l3 or wants_xrefs
    enabled = ["l1"]

    if wants_l2:
        from plugins import chunks_embeddings

        chunks_embeddings.register_all(
            chunks_embeddings.build_backend(backend_name)
        )
        enabled.append("l2")
    if wants_xrefs:
        from plugins import symbol_xrefs

        symbol_xrefs.register_all()
        enabled.append("xrefs")
    if wants_l3:
        from plugins import concept_graph

        concept_graph.register_all(vocab=concept_graph.USE_BUILTIN)
        enabled.append("l3")
    return enabled


def main(argv: list[str] | None = None) -> int:
    load_env()  # .env (repo-scoped) fills gaps; real environment always wins
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
    p.add_argument("--layers", default="l1",
                   help="Comma-separated enrichment layers to register: "
                        "l1 (host only, the default), l2 (chunks + embeddings), "
                        "l3 (concept graph), xrefs (symbol cross-references). "
                        "l3 and xrefs both imply l2.")
    p.add_argument("--backend", default="hash", choices=("hash", "sbert"),
                   help="Embedding backend for l2. 'hash' is a deterministic "
                        "SHA-256 fake with no semantics; 'sbert' needs the "
                        "[sbert] extra.")
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

    try:
        enabled = register_layers(args.layers, args.backend)
    except ValueError as exc:
        p.error(str(exc))
    except ImportError as exc:  # pragma: no cover - packaging regression
        p.error(f"layer requested but its plugin is not installed: {exc}")

    with resolve_repo_source(args.repo, args.state, work_dir=args.out.resolve().parent) as repo:
        repo_name = args.name or repo.name
        mapped = map_codebase(repo.path, repo.state, exclude_patterns=args.exclude)
        manifest = emit(repo_name, mapped, args.out.resolve(),
                        emit_blobs_flag=not args.no_emit_blobs)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["shacl_self_check"]["conforms"] else 1
