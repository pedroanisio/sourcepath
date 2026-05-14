"""codebase_mapper.pipeline."""
from __future__ import annotations

import hashlib
import os

from pathlib import Path


from .classify import classify, language_of, path_excluded, read_repo_ignore, refine_phases
from .constants import DEFAULT_PHASES
from .extensions import (
    PipelineCtx, iter_aggregators, iter_import_resolvers,
    iter_language_analyzers, iter_record_enrichers,
)
from .git_plumbing import list_commit_times, list_tree, read_blob, resolve_commit
from .languages.dart import detect_dart_package_name
from .languages.go import detect_go_module
from .languages.kotlin import build_kotlin_fqn_index
from .languages.python import build_python_module_index, detect_python_source_roots
from .languages.rust import detect_rust_workspaces
from .languages.swift import detect_swift_modules
from .languages.tsjs import load_tsconfigs
from .lockfiles import pinned_dependencies
from .manifests import declared_dependencies
from .models import DeclaresDependencyEdge, FileRecord, ImportEdge, ImportExternalEdge, PinsDependencyEdge
from .tests_edges import infer_tests_edges


def map_codebase(
    repo: Path, state: str, exclude_patterns: list[str] | None = None
) -> dict:
    # Merge CLI-supplied patterns with any `.cbmignore` at the repo root so
    # the per-repo config is honored automatically. The merged list ends up
    # in run_manifest.json's exclude_patterns for full traceability.
    exclude_patterns = [*(exclude_patterns or []), *read_repo_ignore(repo)]
    commit = resolve_commit(repo, state)
    blobs = list_tree(repo, commit)
    blob_by_path = {p: sha for p, sha, _mode in blobs}
    mode_by_path = {p: mode for p, _sha, mode in blobs}
    # Last-touched commit time per path. One `git log` walk; safe to call
    # once up-front since the commit graph doesn't change during a run.
    commit_times = list_commit_times(repo, commit)

    records: list[FileRecord] = []
    # Build a stub PipelineCtx now so LanguageAnalyzers receive a ctx with
    # `records` (still growing), `read_path`, and `paths_set` references.
    # The full ctx (with all `host:*` indices) is populated later, before
    # ImportResolvers run.
    paths_set: set[str] = set()

    def read_path(p: str) -> bytes:
        return read_blob(repo, blob_by_path[p])

    ctx = PipelineCtx(
        repo=repo, commit=commit, records=records,
        blob_by_path=blob_by_path, mode_by_path=mode_by_path,
        paths_set=paths_set, read_path=read_path,
    )

    analyzers = iter_language_analyzers()
    for path, blob_sha, mode in blobs:
        if path_excluded(path, exclude_patterns):
            continue
        content = read_blob(repo, blob_sha)
        head = content[:8192]
        if mode == "120000":
            # Git symlink: the blob content IS the target path. Classify
            # as a distinct case (using "configuration" since it's
            # structural metadata, not source/data). Skip AST extraction.
            type_ = "configuration"
            lang = None
        else:
            type_ = classify(path, head)
            lang = language_of(path)
        # Filesystem times from the working tree. lstat (not stat) so a
        # symlink's own metadata is captured instead of its target's, and
        # so a dangling symlink doesn't raise. stat() doesn't bump atime,
        # so this is determinism-safe across consecutive runs.
        atime = mtime = ctime = None
        try:
            st = os.lstat(repo / path)
            atime, mtime, ctime = st.st_atime, st.st_mtime, st.st_ctime
        except (OSError, ValueError):
            pass

        rec = FileRecord(
            path=path, git_blob_sha=blob_sha,
            content_sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content), language=lang, type_=type_,
            phases=list(DEFAULT_PHASES[type_]) or ["runtime"],
            atime=atime, mtime=mtime, ctime=ctime,
            git_commit_time=commit_times.get(path),
        )
        # AST extraction via LanguageAnalyzer registry (first-match-wins).
        # Builtin analyzers are auto-registered at package import; their
        # `matches()` implements the same per-language gating as the
        # legacy if/elif chain.
        if type_ != "binary" and mode != "120000":
            for analyzer in analyzers:
                if analyzer.matches(rec, ctx):
                    rec.ast_summary, rec.extraction_errors = analyzer.extract(
                        rec, content, ctx,
                    )
                    break
        rec.phases = refine_phases(rec)
        records.append(rec)
        paths_set.add(rec.path)

    # Indices
    py_roots = detect_python_source_roots(records, read_path)
    by_module, by_suffix = build_python_module_index(records, py_roots)
    tsconfigs = load_tsconfigs(records, read_path)
    rust_crates = detect_rust_workspaces(records, read_path)
    go_module = detect_go_module(records, read_path)
    swift_modules = detect_swift_modules(records, read_path)
    dart_pkg_name = detect_dart_package_name(records, read_path)
    # Kotlin FQN index must be built after AST extraction is done (it reads
    # ast_summary.package + top_level_classes). That's already happened above.
    kotlin_fqn = build_kotlin_fqn_index(records, read_path)

    # Dependency manifests -> declared deps (needed early so we can match
    # external imports against them).
    dep_edges: set[DeclaresDependencyEdge] = set()
    for r in records:
        if r.type_ != "dependency_manifest":
            continue
        for pkg in declared_dependencies(r, read_path(r.path)):
            dep_edges.add(DeclaresDependencyEdge(r.path, pkg))
    declared_pkgs = {e.package_name for e in dep_edges}

    # Stash host-built indices on the ctx so registered ImportResolvers
    # (and any future plugins) can read them. The `host:` prefix
    # disambiguates from plugin-built entries in ctx.indices.
    ctx.indices["host:python_source_roots"] = py_roots
    ctx.indices["host:python_by_module"] = by_module
    ctx.indices["host:python_by_suffix"] = by_suffix
    ctx.indices["host:tsconfigs"] = tsconfigs
    ctx.indices["host:rust_crates"] = rust_crates
    ctx.indices["host:go_module"] = go_module
    ctx.indices["host:swift_modules"] = swift_modules
    ctx.indices["host:dart_pkg_name"] = dart_pkg_name
    ctx.indices["host:kotlin_fqn"] = kotlin_fqn
    ctx.indices["host:declared_pkgs"] = declared_pkgs

    import_edges: set[ImportEdge] = set()
    import_ext_edges: set[ImportExternalEdge] = set()

    # Import resolution via ImportResolver registry. First-match-wins.
    resolvers = iter_import_resolvers()
    for r in records:
        if r.ast_summary is None:
            continue
        result = None
        for resolver in resolvers:
            if resolver.matches(r, ctx):
                result = resolver.resolve(r, ctx)
                break
        if result is None:
            continue
        if result.annotations:
            ctx.resolver_annotations[r.path] = dict(result.annotations)
        for dst in result.in_repo:
            if dst != r.path:
                import_edges.add(ImportEdge(r.path, dst))
        for pkg in result.external:
            if pkg in declared_pkgs:
                import_ext_edges.add(ImportExternalEdge(r.path, pkg))

    # Harvest Kotlin prefix-matched provenance from resolver annotations.
    # The "prefix_matched" annotation key is the contract between the
    # KotlinResolver and the host's manifest builder.
    kotlin_prefix_matched: set[str] = set()
    for anns in ctx.resolver_annotations.values():
        kotlin_prefix_matched.update(anns.get("prefix_matched", []))

    pin_edges: set[PinsDependencyEdge] = set()
    for r in records:
        if r.type_ != "lockfile":
            continue
        for name, version in pinned_dependencies(r, read_path(r.path)):
            pin_edges.add(PinsDependencyEdge(r.path, name, version))

    # --- Extension hooks: RecordEnrichers + Aggregators ---
    # When no plugins are registered, both loops are no-ops and produce
    # zero observable change to the host's output.
    enrichers = iter_record_enrichers()
    if enrichers:
        for r in records:
            if r.type_ == "binary":
                continue
            content = read_path(r.path)
            for enricher in enrichers:
                enricher.enrich(r, content, ctx)
    for agg in iter_aggregators():
        ctx.indices[agg.name] = agg.run(ctx)

    return {
        "commit": commit, "records": records,
        "import_edges": sorted(import_edges, key=lambda e: (e.src_path, e.dst_path)),
        "import_ext_edges": sorted(import_ext_edges, key=lambda e: (e.src_path, e.package_name)),
        "dep_edges": sorted(dep_edges, key=lambda e: (e.manifest_path, e.package_name)),
        "pin_edges": sorted(pin_edges, key=lambda e: (e.lockfile_path, e.package_name, e.package_version)),
        # Pass rust_crates + paths_set so Rust integration tests under
        # ``tests/*.rs`` can fall back to use-analysis when filename
        # heuristics fail to match a subject by basename.
        "tests_edges": infer_tests_edges(
            records, rust_crates=rust_crates, paths_set=paths_set,
        ),
        "python_source_roots": py_roots,
        "rust_crates": rust_crates,
        "tsconfig_count": len(tsconfigs),
        "go_module": go_module,
        "swift_local_modules": list(swift_modules.get("local_modules", {}).keys()),
        "swift_product_modules": sorted(swift_modules.get("product_to_package", {}).keys()),
        "dart_package_name": dart_pkg_name,
        "kotlin_prefix_matched_packages": sorted(kotlin_prefix_matched),
        "exclude_patterns": exclude_patterns,
        "blob_by_path": blob_by_path,
        "repo": repo,
        "ctx": ctx,
    }
