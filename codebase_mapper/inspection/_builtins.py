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

from ..shared_kernel.extensions import (
    LanguageAnalyzer, ImportResolver, PipelineCtx, ResolveResult,
    register_language_analyzer, register_import_resolver,
)
from .languages.c import (
    build_c_include_index, extract_c_ast_summary, resolve_c_includes,
)
from .languages.cfml import (
    CFML_TS_AVAILABLE, extract_cfml_ast_summary, resolve_cfml_imports,
)
from .languages.clojure import (
    extract_clojure_ast_summary, resolve_clojure_imports,
)
from .languages.cobol import (
    extract_cobol_ast_summary, resolve_cobol_imports,
)
from .languages.lightweight import (
    extract_asm_summary, extract_devicetree_summary,
    extract_kconfig_summary, extract_make_summary,
)
from .languages.cpp import extract_cpp_ast_summary
from .languages.css import extract_css_ast_summary, resolve_css_imports
from .languages.dart import extract_dart_ast_summary, resolve_dart_imports
from .languages.go import (
    extract_go_ast_summary, go_package_root, resolve_go_imports,
)
from .languages.html import extract_html_ast_summary, resolve_html_imports
from .languages.json import extract_json_ast_summary, resolve_json_imports
from .languages.yaml import extract_yaml_ast_summary, resolve_yaml_imports
from .languages.java import (
    extract_java_ast_summary, resolve_java_imports,
)
from .languages.kotlin import (
    extract_kotlin_ast_summary, resolve_kotlin_imports,
)
from .languages.objc import (
    OBJC_LANGUAGE_TAGS, extract_objc_ast_summary, resolve_objc_includes,
)
from .languages.php import (
    extract_php_ast_summary, parse_composer_psr4, resolve_php_imports,
)
from .languages.python import (
    extract_python_ast_summary, resolve_python_imports,
)
from .languages.ruby import extract_ruby_ast_summary, resolve_ruby_imports
from .languages.rust import extract_rust_ast_summary, resolve_rust_imports
from .languages.shell import extract_shell_ast_summary, resolve_shell_imports
from .languages.sql import extract_sql_ast_summary, resolve_sql_imports
from .languages.swift import extract_swift_ast_summary, resolve_swift_imports
from .languages.tsjs import (
    extract_tsjs_ast_summary, resolve_tsjs_import, tsjs_bare_package_root,
)
from .models import FileRecord
from ..ts_setup import TS_AVAILABLE, _ts_grammar_for


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


class JavaAnalyzer:
    name = "lang_java"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "java" and TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_java_ast_summary(content, record.path)


class ObjcAnalyzer:
    """Handles both ``.m`` (objective-c) and ``.mm`` (objective-cpp).

    tree-sitter-objc parses both dialects: for ``.mm`` files the ObjC
    superstructure (``@interface``/``@implementation``/methods) is
    recovered intact; the C++ method bodies may produce non-fatal
    ``parse_errors_present`` diagnostics that we surface in
    ``extraction_errors`` for downstream visibility.
    """
    name = "lang_objc"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language in OBJC_LANGUAGE_TAGS and TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_objc_ast_summary(content, record.path)


class CAnalyzer:
    name = "lang_c"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "c" and TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_c_ast_summary(
            content, record.path,
            macro_table=ctx.scratch.get("macro_table"),
        )


class CfmlAnalyzer:
    """CFML — tag syntax and cfscript, via the ``tree-sitter-cfml`` wheel.

    The grammar package is imported by ``languages/cfml.py`` itself (not
    ts_setup's all-or-nothing block), so ``matches`` gates on the module's
    own availability flag rather than the global ``TS_AVAILABLE``.
    """
    name = "lang_cfml"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "cfml" and CFML_TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_cfml_ast_summary(content, record.path)


class CobolAnalyzer:
    name = "lang_cobol"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        # Column-aware regex reader (no tree-sitter), like Dart / Clojure.
        return record.language == "cobol"

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_cobol_ast_summary(content, record.path)


class CppAnalyzer:
    name = "lang_cpp"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "cpp" and TS_AVAILABLE

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_cpp_ast_summary(content, record.path)


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


class ClojureAnalyzer:
    name = "lang_clojure"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        # Pure-Python s-expr reader (no tree-sitter), like the Python analyzer.
        return record.language == "clojure"

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_clojure_ast_summary(content, record.path)


class SqlAnalyzer:
    name = "lang_sql"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        # Disciplined regex extractor (no tree-sitter), like Dart / COBOL.
        return record.language == "sql"

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_sql_ast_summary(content, record.path)


class HtmlAnalyzer:
    name = "lang_html"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        # Stack-based element parser (no tree-sitter), like Dart / SQL.
        return record.language == "html"

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_html_ast_summary(content, record.path)


class CssAnalyzer:
    """CSS and the SCSS dialect share one brace-scan extractor."""
    name = "lang_css"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language in ("css", "scss")

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_css_ast_summary(content, record.path)


class JsonAnalyzer:
    """JSON via a hand-written recursive-descent AST parser (stdlib only)."""
    name = "lang_json"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "json"

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_json_ast_summary(content, record.path)


class YamlAnalyzer:
    """YAML via PyYAML's compose_all node AST (multi-document aware)."""
    name = "lang_yaml"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "yaml"

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_yaml_ast_summary(content, record.path)


class ShellAnalyzer:
    """Shell via a state-machine neutralizer + brace-matched function scan."""
    name = "lang_shell"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "shell"

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_shell_ast_summary(content, record.path)


class PhpAnalyzer:
    """PHP via a state-machine neutralizer + brace-matched declaration scan."""
    name = "lang_php"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "php"

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return extract_php_ast_summary(content, record.path)


# ---------------------------------------------------------------------------
# ImportResolver wrappers
# ---------------------------------------------------------------------------


def _summary(record: FileRecord) -> dict:
    """Non-None ast_summary; resolve() only runs after matches() gated on it.

    Raising (rather than returning {}) keeps a pipeline-contract violation
    loud instead of silently resolving zero imports.
    """
    summary = record.ast_summary
    if summary is None:
        raise ValueError(f"{record.path}: resolve() called without ast_summary")
    return summary


class PythonResolver:
    name = "resolve_python"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "python" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        py_roots = ctx.indices["host:python_source_roots"]
        by_module = ctx.indices["host:python_by_module"]
        by_suffix = ctx.indices["host:python_by_suffix"]
        declared = ctx.indices.get("host:declared_pkgs", set())
        in_repo, external = resolve_python_imports(
            record.path, _summary(record), py_roots, by_module, by_suffix,
            declared_external=declared,
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
        for imp in _summary(record).get("imports", []):
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
            record.path, _summary(record),
            ctx.indices["host:rust_crates"], ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class RubyResolver:
    name = "resolve_ruby"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "ruby" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_ruby_imports(
            record.path, _summary(record), ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class GoResolver:
    name = "resolve_go"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "go" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_go_imports(
            record.path, _summary(record),
            ctx.indices["host:go_module"], ctx.paths_set,
        )
        external = [go_package_root(u) for u in external]
        return ResolveResult(in_repo=list(in_repo), external=external)


def _c_basename_index(ctx: PipelineCtx) -> dict[str, list[str]]:
    """Once-per-repo basename index shared by the C and C++ resolvers.

    The host pipeline builds it in its index phase; this fallback lazily
    builds-and-stashes it for contexts that bypass that phase (bare
    PipelineCtx in tests / plugin hosts), so it is still constructed at
    most once per ctx — never per file or per include (hard performance
    constraint at kernel scale, see ``build_c_include_index``).
    """
    index = ctx.indices.get("host:c_basename_index")
    if index is None:
        index = build_c_include_index(ctx.paths_set)
        ctx.indices["host:c_basename_index"] = index
    return index


def _resolve_c_family(record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
    """Shared C/C++ include resolution with the E4 additions: build-evidence
    include roots (host:c_include_roots) sharpen the hard tier; ambiguous
    angle includes land on ctx.scratch["possible_import_edges"] as disclosed
    candidate edges instead of vanishing."""
    from .models import PossibleImportEdge

    ambiguous: dict[str, list[str]] = {}
    in_repo, external = resolve_c_includes(
        record.path, _summary(record), ctx.paths_set,
        _c_basename_index(ctx),
        include_roots=ctx.indices.get("host:c_include_roots"),
        ambiguous_out=ambiguous,
    )
    if ambiguous:
        bucket = ctx.scratch.setdefault("possible_import_edges", set())
        for candidates in ambiguous.values():
            for dst in candidates:
                bucket.add(PossibleImportEdge(record.path, dst))
    return ResolveResult(in_repo=list(in_repo), external=list(external))


class CResolver:
    name = "resolve_c"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "c" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        return _resolve_c_family(record, ctx)


class CfmlResolver:
    name = "resolve_cfml"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "cfml" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_cfml_imports(
            record.path, _summary(record), ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class CobolResolver:
    name = "resolve_cobol"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "cobol" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_cobol_imports(
            record.path, _summary(record), ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class CppResolver:
    name = "resolve_cpp"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "cpp" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        # C++ #include resolution is structurally identical to C's;
        # share the implementation (and the once-per-repo basename
        # index). Future C++20 `import std;` / `import :module;` is out
        # of scope.
        return _resolve_c_family(record, ctx)


class KotlinResolver:
    name = "resolve_kotlin"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "kotlin" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external, prefix_matched = resolve_kotlin_imports(
            record.path, _summary(record),
            ctx.indices["host:kotlin_fqn"],
            ctx.indices["host:declared_pkgs"],
        )
        return ResolveResult(
            in_repo=list(in_repo),
            external=list(external),
            annotations={"prefix_matched": list(prefix_matched)},
        )


class JavaResolver:
    name = "resolve_java"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "java" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external, prefix_matched = resolve_java_imports(
            record.path, _summary(record),
            ctx.indices["host:java_fqn"],
            ctx.indices["host:java_packages"],
            ctx.indices["host:declared_pkgs"],
        )
        return ResolveResult(
            in_repo=list(in_repo),
            external=list(external),
            annotations={"prefix_matched": list(prefix_matched)},
        )


class ObjcResolver:
    name = "resolve_objc"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return (record.language in OBJC_LANGUAGE_TAGS
                and record.ast_summary is not None)

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_objc_includes(
            record.path, _summary(record), ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class SwiftResolver:
    name = "resolve_swift"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "swift" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_swift_imports(
            record.path, _summary(record),
            ctx.indices["host:swift_modules"], ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class DartResolver:
    name = "resolve_dart"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "dart" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        # Prefer the multi-package map; fall back to the legacy scalar so
        # bundles built before Tier-1 promotion still resolve correctly.
        packages = ctx.indices.get("host:dart_packages")
        if not packages:
            packages = ctx.indices.get("host:dart_pkg_name")
        in_repo, external = resolve_dart_imports(
            record.path, _summary(record),
            packages, ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class ClojureResolver:
    name = "resolve_clojure"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "clojure" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_clojure_imports(
            record.path, _summary(record), ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class SqlResolver:
    name = "resolve_sql"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "sql" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_sql_imports(
            record.path, _summary(record), ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class HtmlResolver:
    name = "resolve_html"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "html" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_html_imports(
            record.path, _summary(record), ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class CssResolver:
    name = "resolve_css"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language in ("css", "scss") and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_css_imports(
            record.path, _summary(record), ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class JsonResolver:
    name = "resolve_json"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "json" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_json_imports(
            record.path, _summary(record), ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class YamlResolver:
    name = "resolve_yaml"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "yaml" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_yaml_imports(
            record.path, _summary(record), ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


class ShellResolver:
    name = "resolve_shell"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "shell" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_shell_imports(
            record.path, _summary(record), ctx.paths_set,
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


def _php_psr4_index(ctx: PipelineCtx) -> dict[str, str]:
    """Once-per-repo composer.json PSR-4 autoload map, built lazily and stashed
    on ``ctx`` — mirrors ``_c_basename_index``: never rebuilt per file, and
    still constructed for bare PipelineCtx contexts that skip the host's index
    phase. Prefix dirs are anchored at the composer.json's own directory, so a
    monorepo with per-package composer files resolves correctly.
    """
    index = ctx.indices.get("host:php_psr4")
    if index is None:
        index = {}
        for path in sorted(ctx.paths_set):
            if path != "composer.json" and not path.endswith("/composer.json"):
                continue
            try:
                psr4 = parse_composer_psr4(ctx.read_path(path))
            except Exception:
                continue
            base = path[: -len("composer.json")]   # '' at root, 'pkg/' otherwise
            for prefix, directory in psr4.items():
                index[prefix] = base + directory
        ctx.indices["host:php_psr4"] = index
    return index


class PhpResolver:
    name = "resolve_php"

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == "php" and record.ast_summary is not None

    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult:
        in_repo, external = resolve_php_imports(
            record.path, _summary(record), ctx.paths_set, _php_psr4_index(ctx),
        )
        return ResolveResult(in_repo=list(in_repo), external=list(external))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class _LightweightAnalyzer:
    """Shared shape for the E2 line-oriented extractors — no tree-sitter
    dependency, so ``matches`` never gates on TS_AVAILABLE."""
    language = ""
    _extract = None

    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool:
        return record.language == self.language

    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]:
        return type(self)._extract(content, record.path)


class AsmAnalyzer(_LightweightAnalyzer):
    name = "lang_asm"
    language = "asm"
    _extract = staticmethod(extract_asm_summary)


class KconfigAnalyzer(_LightweightAnalyzer):
    name = "lang_kconfig"
    language = "kconfig"
    _extract = staticmethod(extract_kconfig_summary)


class DevicetreeAnalyzer(_LightweightAnalyzer):
    name = "lang_devicetree"
    language = "devicetree"
    _extract = staticmethod(extract_devicetree_summary)


class MakeAnalyzer(_LightweightAnalyzer):
    name = "lang_make"
    language = "make"
    _extract = staticmethod(extract_make_summary)


_BUILTIN_ANALYZERS = (
    AsmAnalyzer, CAnalyzer, CfmlAnalyzer, ClojureAnalyzer, CobolAnalyzer,
    CppAnalyzer, CssAnalyzer, DartAnalyzer, DevicetreeAnalyzer, GoAnalyzer, HtmlAnalyzer,
    JavaAnalyzer, JsonAnalyzer,
    KconfigAnalyzer, KotlinAnalyzer, MakeAnalyzer, ObjcAnalyzer, PhpAnalyzer,
    PythonAnalyzer, RubyAnalyzer, RustAnalyzer, ShellAnalyzer, SqlAnalyzer,
    SwiftAnalyzer, TsJsAnalyzer, YamlAnalyzer,
)
_BUILTIN_RESOLVERS = (
    CResolver, CfmlResolver, ClojureResolver, CobolResolver, CppResolver,
    CssResolver,
    DartResolver, GoResolver, HtmlResolver, JavaResolver, JsonResolver,
    KotlinResolver, ObjcResolver, PhpResolver,
    PythonResolver, RubyResolver, RustResolver, ShellResolver, SqlResolver,
    SwiftResolver, TsJsResolver, YamlResolver,
)


def register_builtins() -> None:
    """Register every built-in LanguageAnalyzer and ImportResolver with
    the host's extension registries. Called from `__init__.py` at package
    import time, and from `reset_registries()` after clearing.
    """
    for analyzer_cls in _BUILTIN_ANALYZERS:
        register_language_analyzer(analyzer_cls())
    for resolver_cls in _BUILTIN_RESOLVERS:
        register_import_resolver(resolver_cls())
