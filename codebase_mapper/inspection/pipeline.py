"""codebase_mapper.pipeline."""
from __future__ import annotations

import hashlib
import logging
import os
import sys

from pathlib import Path


from .classify import classify, language_of, path_excluded, read_repo_ignore, refine_phases
from ..shared_kernel.constants import DEFAULT_PHASES
from ..shared_kernel.extensions import (
    PipelineCtx, iter_aggregators, iter_import_resolvers,
    iter_language_analyzers, iter_record_enrichers,
)
from .git_plumbing import list_commit_times, list_tree, read_blob, resolve_commit
from .languages.cpp import build_cpp_symbol_index, refine_cpp_header_languages
from .languages.dart import detect_dart_package_name, detect_dart_packages
from .languages.objc import build_objc_symbol_index, refine_objc_header_languages
from .languages.go import detect_go_module
from .languages.java import (
    build_java_fqn_index, build_java_package_index, detect_java_source_roots,
)
from .languages.kotlin import build_kotlin_fqn_index
from .languages.python import build_python_module_index, detect_python_source_roots
from .languages.rust import detect_rust_workspaces
from .languages.swift import detect_swift_modules
from .languages.tsjs import load_tsconfigs
from .lockfiles import pinned_dependencies
from .manifests import declared_dependencies
from .models import (
    DeclaresDependencyEdge,
    FileRecord,
    ImportEdge,
    ImportExternalEdge,
    PinsDependencyEdge,
)
from .tests_edges import infer_tests_edges

_log = logging.getLogger(__name__)

# Target number of _progress() update lines per pass, independent of how
# many items that pass has. Picked so a few-hundred-file repo visibly
# skips items (obviously throttled, not "every other one") while a 94K-file
# repo still gets a scannable status trickle instead of one line per file.
_PROGRESS_LINES = 50


def _phase(msg: str) -> None:
    """One-line banner marking entry into a map_codebase() phase. Cheap
    sub-passes (index building, import resolution) get this instead of
    per-item progress — they're function calls over already-parsed data,
    not the raw per-file parsing work that dominates wall-clock time on a
    large repo."""
    print(f"[host] {msg}", file=sys.stderr)


def _progress(tag: str, i: int, total: int, label: str) -> None:
    """Throttled per-item progress line to stderr, for map_codebase()'s two
    genuinely expensive per-file passes (classify+build, AST extraction).

    Always fires on the first and last item, so even a small repo shows
    start and completion; otherwise capped at roughly ``_PROGRESS_LINES``
    updates for the whole pass, regardless of ``total`` — a 250-file repo
    and a 94K-file repo both get a manageable, scannable number of lines,
    not one line per file (which would itself add measurable print/flush
    overhead at the high end and just be noise at the low end).
    """
    if total <= 0:
        return
    interval = max(1, total // _PROGRESS_LINES)
    if i == 1 or i == total or i % interval == 0:
        print(f"[host] {tag}  {i}/{total}  {label}", file=sys.stderr)


def _safe_extract(analyzer, record, content, ctx):
    """Run ``analyzer.extract`` with any failure contained to this one record.

    A mapping run must not be aborted by a single pathological file. A
    deeply-nested source can still overflow a recursive helper into
    ``RecursionError`` (and any analyzer may raise on malformed input); either
    way the failure is recorded in the record's ``extraction_errors`` and no
    summary is returned, so the file degrades gracefully and the run continues.
    The drop is logged, never silent (PALS's Law).
    """
    name = getattr(analyzer, "name", type(analyzer).__name__)
    try:
        return analyzer.extract(record, content, ctx)
    except RecursionError:
        _log.warning("extract recursion overflow on %s (%s); skipping file",
                     record.path, name)
        return None, ["extract_recursion_error"]
    except Exception as e:  # noqa: BLE001 — untrusted input shape; never abort the run
        _log.warning("extract failed on %s (%s): %s: %s",
                     record.path, name, type(e).__name__, e)
        return None, [f"extract_failed: {type(e).__name__}: {e}"]


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
    # Pass 1: classify + build records (no AST yet). We need the full
    # set of files (and their languages) before we can run language
    # refinements like ``refine_cpp_header_languages`` — which re-tags
    # ``.h`` files as C++ when they live in a directory containing any
    # C++ source. Running this before AST extraction means each header
    # gets parsed by the correct analyzer.
    content_by_path: dict[str, bytes] = {}
    mode_skip_extraction: set[str] = set()
    n_blobs = len(blobs)
    for i, (path, blob_sha, mode) in enumerate(blobs, 1):
        _progress("classify", i, n_blobs, path)
        if path_excluded(path, exclude_patterns):
            continue
        content = read_blob(repo, blob_sha)
        content_by_path[path] = content
        head = content[:8192]
        if mode == "120000":
            type_ = "configuration"
            lang = None
            mode_skip_extraction.add(path)
        else:
            type_ = classify(path, head)
            lang = language_of(path)
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
        records.append(rec)
        paths_set.add(rec.path)

    # Pass 1.5: cross-file language refinement. ``.h`` files get
    # re-tagged based on cross-file evidence so the right analyzer
    # handles them:
    #
    #   * ObjC retag runs first — ``Foo.h`` next to ``Foo.m`` is ObjC.
    #     Apple's convention is universal across iOS/macOS code; pure-C
    #     repos are unaffected (the retag is a no-op when no ``.m``
    #     files exist).
    #   * C++ retag runs second — picks up any remaining ``.h`` files
    #     in C++ projects (the cpp grammar is a superset of C and
    #     parses C correctly, so this is safe).
    #
    # New refinements (sibling-aware language decisions across files)
    # belong here, before AST extraction.
    refine_objc_header_languages(records)
    refine_cpp_header_languages(records)

    # Pass 2: AST extraction. Analyzer ``matches()`` reads ``rec.language``,
    # so the refinement above must be visible here. Usually the most
    # expensive pass on a large repo — real per-language parsing, not a
    # filtered/dict-building sub-index — hence its own progress line.
    n_records = len(records)
    for i, rec in enumerate(records, 1):
        _progress("extract", i, n_records, rec.path)
        if rec.type_ == "binary" or rec.path in mode_skip_extraction:
            rec.phases = refine_phases(rec)
            continue
        content = content_by_path[rec.path]
        for analyzer in analyzers:
            if analyzer.matches(rec, ctx):
                rec.ast_summary, rec.extraction_errors = _safe_extract(
                    analyzer, rec, content, ctx,
                )
                break
        rec.phases = refine_phases(rec)

    # Indices
    _phase(f"building language indices ({n_records} records)")
    py_roots = detect_python_source_roots(records, read_path)
    by_module, by_suffix = build_python_module_index(records, py_roots)
    tsconfigs = load_tsconfigs(records, read_path)
    rust_crates = detect_rust_workspaces(records, read_path)
    go_module = detect_go_module(records, read_path)
    swift_modules = detect_swift_modules(records, read_path)
    dart_packages = detect_dart_packages(records, read_path)
    # Back-compat scalar: the shallowest package name. Kept so any code
    # still reading host:dart_pkg_name continues to work; new callers
    # should consume host:dart_packages (multi-package map).
    dart_pkg_name = detect_dart_package_name(records, read_path)
    # Kotlin FQN index must be built after AST extraction is done (it reads
    # ast_summary.package + top_level_classes). That's already happened above.
    kotlin_fqn = build_kotlin_fqn_index(records, read_path)
    # Java FQN + package indices follow the same pattern as Kotlin: read
    # the per-file AST summary, fold into ``pkg.ClassName → path`` and
    # ``pkg → [paths]``. The source-root list anchors Maven-layout repos.
    java_fqn = build_java_fqn_index(records)
    java_packages = build_java_package_index(records)
    java_source_roots = detect_java_source_roots(records)
    # C++ symbol index: top-level class/struct/function name → list of
    # defining files. The xref resolver uses it for ``new Foo()`` and
    # ``Foo::bar()`` receiver binding.
    cpp_symbols = build_cpp_symbol_index(records)
    # Objective-C symbol index: class/protocol/category-host name →
    # defining files. Categories register under both their full name
    # ``NSString(Greet)`` and their host class ``NSString`` so receiver
    # references like ``[NSString …]`` bind even when only a category
    # was imported.
    objc_symbols = build_objc_symbol_index(records)

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
    ctx.indices["host:dart_packages"] = dart_packages
    ctx.indices["host:kotlin_fqn"] = kotlin_fqn
    ctx.indices["host:java_fqn"] = java_fqn
    ctx.indices["host:java_packages"] = java_packages
    ctx.indices["host:java_source_roots"] = java_source_roots
    ctx.indices["host:cpp_symbols"] = cpp_symbols
    ctx.indices["host:objc_symbols"] = objc_symbols
    ctx.indices["host:declared_pkgs"] = declared_pkgs

    import_edges: set[ImportEdge] = set()
    import_ext_edges: set[ImportExternalEdge] = set()

    # Import resolution via ImportResolver registry. First-match-wins.
    _phase("resolving imports")
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
    # zero observable change to the host's output. Per-item detail for
    # this loop is each enricher's own responsibility (e.g. L4's
    # plugins/llm_enrich/ prints "[L4] file_summary ..." per file) — the
    # host only announces that the phase started.
    enrichers = iter_record_enrichers()
    if enrichers:
        _phase(f"running {len(enrichers)} record enricher(s)")
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
        "dart_packages": dart_packages,
        "java_source_roots": java_source_roots,
        "kotlin_prefix_matched_packages": sorted(kotlin_prefix_matched),
        "exclude_patterns": exclude_patterns,
        "blob_by_path": blob_by_path,
        "repo": repo,
        "ctx": ctx,
    }
