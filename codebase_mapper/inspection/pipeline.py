"""codebase_mapper.pipeline."""
from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


from .classify import classify, language_of, path_excluded, read_repo_ignore, refine_phases
from ..shared_kernel.constants import DEFAULT_PHASES
from ..shared_kernel.extensions import (
    PipelineCtx, iter_aggregators, iter_import_resolvers,
    iter_language_analyzers, iter_record_enrichers,
)
from .git_plumbing import (
    BlobReader, is_shallow_repository, list_commit_times, list_tree, read_blob,
    resolve_commit,
)
from .languages.c import build_c_include_index
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
from .macro_neutralize import MacroTable, harvest_macros

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


def _workers_from_env(var: str, default: int) -> int:
    """Resolve a worker-count knob. Garbage or non-positive values
    degrade to 1 (serial) with a logged warning — a bad knob must never
    fail a mapping run, only slow it down (loudly)."""
    raw = os.environ.get(var, "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        _log.warning("%s=%r is not an integer; running that pass serially",
                     var, raw)
        return 1
    if n < 1:
        _log.warning("%s=%d is not positive; running that pass serially",
                     var, n)
        return 1
    return n


def _extract_workers() -> int:
    """AST-extraction threads: ``$CBM_EXTRACT_WORKERS``, default every
    core — the pass is CPU-bound and tree-sitter releases the GIL
    during parse."""
    return _workers_from_env("CBM_EXTRACT_WORKERS", os.cpu_count() or 1)


def _enrich_workers() -> int:
    """Record-enricher threads: ``$CBM_ENRICH_WORKERS``, default 4 —
    the pass is I/O-bound (LLM HTTP calls) and a modest fan-out keeps
    the inference server busy instead of idle between requests."""
    return _workers_from_env("CBM_ENRICH_WORKERS", 4)


def _run_extraction(records, contents, analyzers, ctx, skip_extraction,
                    workers):
    """Pass 2: AST extraction over every record.

    Each file's bytes are popped from ``contents`` at the moment they are
    consumed, so peak memory is bounded by in-flight files rather than
    repository size. With ``workers > 1`` records extract concurrently:
    analyzer ``extract`` implementations are pure per-file functions (no
    shared ctx mutation) and tree-sitter releases the GIL during parse,
    so threads deliver multi-core wins without pickling records across
    processes. Each task mutates only its own record — output is
    identical to a serial run regardless of worker count.
    """
    total = len(records)
    progress_lock = threading.Lock()
    done = 0

    def one(rec):
        nonlocal done
        content = contents.pop(rec.path, None)
        with progress_lock:
            done += 1
            _progress("extract", done, total, rec.path)
        if rec.type_ == "binary" or rec.path in skip_extraction:
            rec.phases = refine_phases(rec)
            return
        if content is None:
            content = ctx.read_path(rec.path)
        for analyzer in analyzers:
            if analyzer.matches(rec, ctx):
                rec.ast_summary, rec.extraction_errors = _safe_extract(
                    analyzer, rec, content, ctx,
                )
                break
        rec.phases = refine_phases(rec)

    if workers <= 1:
        for rec in records:
            one(rec)
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # list() drains the iterator so any task exception propagates,
        # matching serial behavior (extract errors themselves are already
        # contained per-record by _safe_extract).
        list(pool.map(one, records))


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
    # Shallow clones (repo_source defaults to `--depth 1` for remotes) have
    # no usable history: `git log` would attribute every path to the lone
    # parentless tip, stamping every file with one fabricated commit time.
    # Omit the fact instead, and record the degradation below once the
    # affected record count is known.
    shallow = is_shallow_repository(repo)
    commit_times = {} if shallow else list_commit_times(repo, commit)

    records: list[FileRecord] = []
    # Build a stub PipelineCtx now so LanguageAnalyzers receive a ctx with
    # `records` (still growing), `read_path`, and `paths_set` references.
    # The full ctx (with all `host:*` indices) is populated later, before
    # ImportResolvers run.
    paths_set: set[str] = set()

    # One persistent `git cat-file --batch` process serves every blob
    # read in the run (classify pass + read_path consumers). Previously
    # each read spawned its own subprocess — at Linux-kernel scale that
    # was two process spawns per file. The reader lives as long as the
    # ctx that closes over it.
    blob_reader = BlobReader(repo)

    def read_path(p: str) -> bytes:
        return blob_reader.read(blob_by_path[p])

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
        content = blob_reader.read(blob_sha)
        content_by_path[path] = content
        head = content[:8192]
        if mode == "120000":
            type_ = "configuration"
            lang = None
            mode_skip_extraction.add(path)
        else:
            type_ = classify(path, head)
            lang = language_of(path, head)
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

    if shallow:
        # Shared degradation contract: manifest plumbing reads
        # ctx.scratch["degradations"] and surfaces each entry in the run
        # manifest. Every mapped record lacks git_commit_time here, so the
        # affected count is the full record count.
        _log.warning(
            "shallow clone: omitting git_commit_time for %d file(s); "
            "history required to derive it is not present", len(records),
        )
        ctx.scratch.setdefault("degradations", []).append({
            "component": "git_provenance",
            "reason": "shallow_clone_no_history",
            "affected_files": len(records),
        })

    # Pass 1.5: cross-file language refinement. ``.h`` files get
    # re-tagged based on cross-file evidence so the right analyzer
    # handles them:
    #
    #   * ObjC retag runs first — ``Foo.h`` next to ``Foo.m`` is ObjC.
    #     Apple's convention is universal across iOS/macOS code; pure-C
    #     repos are unaffected (the retag is a no-op when no ``.m``
    #     files exist, and its project-wide fallback only fires on
    #     headers whose own content carries ObjC markers).
    #   * C++ retag runs second — picks up any remaining ``.h`` files
    #     in C++ projects (the cpp grammar is a superset of C and
    #     parses C correctly, so this is safe).
    #
    # New refinements (sibling-aware language decisions across files)
    # belong here, before AST extraction.
    refine_objc_header_languages(records, content_by_path.__getitem__)
    refine_cpp_header_languages(records, content_by_path.__getitem__)

    # Pass 1.6: harvest the repo's own #define classification so C-family
    # extraction can retry a failing parse against a byte-preserving
    # neutralized buffer (error-free-mapping E1). The table is repo-derived
    # evidence — no hardcoded macro lists.
    macro_table = MacroTable()
    for r in records:
        if r.language in ("c", "cpp", "objective-c") and r.type_ in (
                "source_code", "test_code"):
            c_src = content_by_path.get(r.path)
            if c_src and b"#" in c_src:
                harvest_macros(c_src, macro_table)
    ctx.scratch["macro_table"] = macro_table

    # Build-evidence include roots (plan E4): a shipped compilation database
    # names the compiler's real -I roots, resolving angle includes exactly.
    cc = content_by_path.get("compile_commands.json")
    if cc:
        from .languages.c import include_roots_from_compile_commands
        roots = include_roots_from_compile_commands(
            cc.decode("utf-8", "replace"))
        if roots:
            ctx.indices["host:c_include_roots"] = roots

    # Pass 2: AST extraction. Analyzer ``matches()`` reads ``rec.language``,
    # so the refinement above must be visible here. Usually the most
    # expensive pass on a large repo — real per-language parsing, not a
    # filtered/dict-building sub-index — hence its own progress line.
    # Runs on _extract_workers() threads and consumes content_by_path
    # as it goes, so the whole-repo byte map does not outlive this pass.
    n_records = len(records)
    _run_extraction(records, content_by_path, analyzers, ctx,
                    mode_skip_extraction, _extract_workers())

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
    # C/C++ basename index: final path component → all repo paths ending
    # in it. Powers quoted-include basename fallback AND angle-include
    # unique path-suffix resolution (#include <linux/foo.h> →
    # include/linux/foo.h). Built exactly once per run — per-include
    # scans of paths_set are O(files × includes) and infeasible at
    # kernel scale (~95k files).
    c_basename_index = build_c_include_index(ctx.paths_set)

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
    ctx.indices["host:c_basename_index"] = c_basename_index
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
        serial = [e for e in enrichers
                  if not getattr(e, "parallel_safe", False)]
        par = [e for e in enrichers if getattr(e, "parallel_safe", False)]
        # Per-record enricher order is the sorted registry order. The
        # parallel-safe group may only be hoisted into its own pass when
        # that preserves the order (every parallel enricher sorts after
        # every serial one — true for the shipped plugins: l2_* < l4_*).
        # Otherwise: the exact serial path, old behavior.
        order_preserved = enrichers == serial + par
        workers = _enrich_workers() if par and order_preserved else 1

        def run_group(group, r):
            if r.type_ == "binary":
                return
            content = read_path(r.path)
            for enricher in group:
                enricher.enrich(r, content, ctx)

        if workers <= 1:
            for r in records:
                run_group(enrichers, r)
        else:
            for r in records:
                run_group(serial, r)
            # I/O-bound (LLM calls): thread fan-out keeps the inference
            # server saturated instead of one request in flight at a time.
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(lambda r: run_group(par, r), records))
    for agg in iter_aggregators():
        ctx.indices[agg.name] = agg.run(ctx)

    return {
        "commit": commit, "records": records,
        "import_edges": sorted(import_edges, key=lambda e: (e.src_path, e.dst_path)),
        "import_ext_edges": sorted(import_ext_edges, key=lambda e: (e.src_path, e.package_name)),
        # Disclosed multi-candidate include resolution (plan E4).
        "possible_import_edges": sorted(
            ctx.scratch.get("possible_import_edges", set()),
            key=lambda e: (e.src_path, e.dst_path)),
        "dep_edges": sorted(dep_edges, key=lambda e: (e.manifest_path, e.package_name)),
        "pin_edges": sorted(pin_edges, key=lambda e: (e.lockfile_path, e.package_name, e.package_version)),
        # Pass rust_crates + paths_set so Rust integration tests under
        # ``tests/*.rs`` can fall back to use-analysis when filename
        # heuristics fail to match a subject by basename, and the resolved
        # import edges so any test whose name mirrors no subject still
        # yields typed-import evidence (kselftest-style suites — F17).
        "tests_edges": infer_tests_edges(
            records, rust_crates=rust_crates, paths_set=paths_set,
            import_edges=list(import_edges),
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
