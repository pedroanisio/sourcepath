"""Built-in language analyzers and import resolvers.

Each wrapper class is a thin shim over the existing per-language
extract_*_ast_summary / resolve_*_imports function. The wrappers preserve
exact behavior of the legacy dispatch chain.

Naming convention: analyzers are named `lang_<language>`, resolvers are
named `resolve_<language>`. Both are unique by .name so sort order is
deterministic, and the dispatch is first-match-wins by `.matches()`.

These get auto-registered at package import via codebase_mapper.__init__,
and re-registered after `reset_registries()` is called.
"""
from __future__ import annotations

from .extensions import (
    LanguageAnalyzer, ImportResolver, PipelineCtx, ResolveResult,
    register_language_analyzer, register_import_resolver,
)
from .languages.c import extract_c_ast_summary, resolve_c_includes
from .languages.dart import extract_dart_ast_summary, resolve_dart_imports
from .languages.go import (
    extract_go_ast_summary, go_package_root, resolve_go_imports,
)
from .languages.kotlin import (
    extract_kotlin_ast_summary, resolve_kotlin_imports,
)
from .languages.python import (
    extract_python_ast_summary, resolve_python_imports,
)
from .languages.ruby import extract_ruby_ast_summary, resolve_ruby_imports
from .languages.rust import extract_rust_ast_summary, resolve_rust_imports
from .languages.swift import extract_swift_ast_summary, resolve_swift_imports
from .languages.tsjs import (
    extract_tsjs_ast_summary, resolve_tsjs_import, tsjs_bare_package_root,
)
from .models import FileRecord
from .ts_setup import TS_AVAILABLE, _ts_grammar_for


# ---------------------------------------------------------------------------
# LanguageAnalyzer wrappers
# ---------------------------------------------------------------------------


class PythonAnalyzer:
    name = "lang_python"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "python"

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_python_ast_summary(content, record.path)


class TsJsAnalyzer:
    name = "lang_tsjs"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language in ("typescript", "javascript") and TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        grammar = _ts_grammar_for(record.path)
        if not grammar:
            return None, []
        return extract_tsjs_ast_summary(content, record.path, grammar)


class RustAnalyzer:
    name = "lang_rust"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "rust" and TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_rust_ast_summary(content, record.path)


class RubyAnalyzer:
    name = "lang_ruby"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "ruby" and TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_ruby_ast_summary(content, record.path)


class GoAnalyzer:
    name = "lang_go"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "go" and TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_go_ast_summary(content, record.path)


class CAnalyzer:
    name = "lang_c"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "c" and TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_c_ast_summary(content, record.path)


class KotlinAnalyzer:
    name = "lang_kotlin"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "kotlin" and TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_kotlin_ast_summary(content, record.path)


class SwiftAnalyzer:
    name = "lang_swift"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "swift" and TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_swift_ast_summary(content, record.path)


class DartAnalyzer:
    name = "lang_dart"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "dart"

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_dart_ast_summary(content, record.path)


# ---------------------------------------------------------------------------
# ImportResolver wrappers
# ---------------------------------------------------------------------------


class PythonResolver:
    name = "resolve_python"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "python" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        py_roots = ctx.indices["host:python_source_roots"]
        by_module = ctx.indices["host:python_by_module"]
        by_suffix = ctx.indices["host:python_by_suffix"]
        in_repo, external = resolve_python_imports(
            record.path, record.ast_summary, py_roots, by_module, by_suffix,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class TsJsResolver:
    name = "resolve_tsjs"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return (record.language in ("typescript", "javascript")
                and record.ast_summary is not None)

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        tsconfigs = ctx.indices["host:tsconfigs"]
        paths_set = ctx.paths_set
        in_repo: list[str] = []
        external: list[str] = []
        for imp in record.ast_summary.get("imports", []):
            spec = imp["source"]
            dst = resolve_tsjs_import(record.path, spec, paths_set, tsconfigs)
            if dst:
                in_repo.append(dst)
            else:
                pkg = tsjs_bare_package_root(spec)
                if pkg:
                    external.append(pkg)
        return ResolveResult(in_repo=in_repo, external=external)


class RustResolver:
    name = "resolve_rust"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "rust" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_rust_imports(
            record.path, record.ast_summary,
            ctx.indices["host:rust_crates"], ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class RubyResolver:
    name = "resolve_ruby"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "ruby" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_ruby_imports(
            record.path, record.ast_summary, ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class GoResolver:
    name = "resolve_go"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "go" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_go_imports(
            record.path, record.ast_summary,
            ctx.indices["host:go_module"], ctx.paths_set,
        )
        external = [go_package_root(u) for u in external]
        return ResolveResult(in_repo=list(in_repo), external=external)


class CResolver:
    name = "resolve_c"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "c" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_c_includes(
            record.path, record.ast_summary, ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class KotlinResolver:
    name = "resolve_kotlin"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "kotlin" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external, prefix_matched = resolve_kotlin_imports(
            record.path, record.ast_summary,
            ctx.indices["host:kotlin_fqn"],
            ctx.indices["host:declared_pkgs"],
        )
        return ResolveResult(
            in_repo=list(in_repo),
            external=list(external),
            annotations={"prefix_matched": list(prefix_matched)},
        )


class SwiftResolver:
    name = "resolve_swift"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "swift" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_swift_imports(
            record.path, record.ast_summary,
            ctx.indices["host:swift_modules"], ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class DartResolver:
    name = "resolve_dart"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "dart" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_dart_imports(
            record.path, record.ast_summary,
            ctx.indices["host:dart_pkg_name"], ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


_BUILTIN_ANALYZERS = (
    CAnalyzer, DartAnalyzer, GoAnalyzer, KotlinAnalyzer, PythonAnalyzer,
    RubyAnalyzer, RustAnalyzer, SwiftAnalyzer, TsJsAnalyzer,
)
_BUILTIN_RESOLVERS = (
    CResolver, DartResolver, GoResolver, KotlinResolver, PythonResolver,
    RubyResolver, RustResolver, SwiftResolver, TsJsResolver,
)


def register_builtins() -> None:
    """Register every built-in LanguageAnalyzer and ImportResolver with
    the host's extension registries. Called from `__init__.py` at package
    import time, and from `reset_registries()` after clearing.
    """
    for cls in _BUILTIN_ANALYZERS:
        register_language_analyzer(cls())
    for cls in _BUILTIN_RESOLVERS:
        register_import_resolver(cls())
