"""Extension surface for codebase_mapper.

This module is the *public* extension API for the host. Companion scripts
register plugins by importing this module and calling `register_*`.

Seven extension points:

    LanguageAnalyzer     — pre-extraction dispatch: extracts a per-file
                           AST summary. First-match-wins by `.matches()`.
    ImportResolver       — pre-extraction dispatch: resolves a record's
                           imports to in-repo paths and external packages.
                           First-match-wins by `.matches()`.
    RecordEnricher       — runs once per FileRecord, after AST extraction
                           and before aggregator phase.
    Aggregator           — runs once per pipeline, after all enrichers,
                           with full visibility of every record.
    GraphContributor     — runs against the inventory Graph after the host
                           has populated its own triples.
    ShapeContributor     — runs against the SHACL shapes Graph after the
                           host has populated its own shapes.
    ArtifactEmitter      — runs after the host has written its core
                           artifacts; returns a manifest fragment.

PipelineCtx is the typed shared-state object passed to every hook.

Registries are sorted by `.name` on iteration. Two reasons:
    (1) deterministic output across runs (essential for byte-identical
        artifact comparison in verification harnesses);
    (2) explicit load ordering across plugin layers (e.g. l2_20_embeddings
        runs before l3_20_concepts; the latter can then read the former's
        index entry).

This module is *additive*. When no plugins are registered (beyond the
built-in language analyzers and import resolvers that re-implement the
host's legacy dispatch chain), the host's behavior is equivalent to the
pre-extension version.

When adding an eighth, ninth, ... extension point in the future
(ManifestParser, LockfileParser, RoundtripChecker), follow the same
pattern: Protocol + module-level list + register_* function, and call the
registry from the relevant module's hot path with sort-by-name iteration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from rdflib import Graph

from .models import FileRecord


# ---------------------------------------------------------------------------
# PipelineCtx — the shared state object passed to every hook
# ---------------------------------------------------------------------------


@dataclass
class PipelineCtx:
    """Typed shared-state for the extension pipeline.

    Populated by the host before any plugin runs:
        repo, commit, records, blob_by_path, mode_by_path, paths_set,
        read_path.

    Used by plugins:
        indices             — Aggregator outputs land here, keyed by `.name`.
                              The host also stashes its own index objects
                              (tsconfigs, python module index, etc.) under
                              keys prefixed `host:` for analyzers/resolvers
                              to consume.
        scratch             — Free for cross-plugin handoff. Use sparingly;
                              prefer indices (which is the documented surface).
        resolver_annotations — Per-record annotations produced by
                              ImportResolvers (e.g. Kotlin's prefix-matched
                              packages). Map of path -> annotation_name -> list[str].
    """
    repo: Path
    commit: str
    records: list[FileRecord]
    blob_by_path: dict[str, str]
    mode_by_path: dict[str, str]
    paths_set: set[str]
    read_path: Callable[[str], bytes]
    indices: dict[str, object] = field(default_factory=dict)
    resolver_annotations: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    scratch: dict[str, object] = field(default_factory=dict)


@dataclass
class ResolveResult:
    """Return type of `ImportResolver.resolve`.

    in_repo:     paths in the repo that this record imports from
    external:    package names this record imports from outside the repo
    annotations: free-form per-record provenance (e.g. Kotlin's
                 prefix-matched packages). The host harvests these into
                 `ctx.resolver_annotations[record.path]`.
    """
    in_repo: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    annotations: dict[str, list[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class LanguageAnalyzer(Protocol):
    """Extracts a language-specific AST summary from a file's bytes.

    The host iterates registered analyzers in `.name` sort order; the
    first analyzer whose `matches()` returns True is used. This is
    *first-match-wins* semantics, mirroring the legacy `if lang == X`
    chain.

    Contract:
      - `matches()` must be cheap (no I/O, no parsing).
      - `extract()` returns `(ast_summary, extraction_errors)`. The
        ast_summary is `None` iff parsing failed; extraction_errors is
        always a list (possibly empty).
    """
    name: str
    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool: ...
    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]: ...


@runtime_checkable
class ImportResolver(Protocol):
    """Resolves a record's `ast_summary` imports to in-repo paths and
    external package names.

    Same first-match-wins semantics as LanguageAnalyzer. May read indices
    populated by the host (under `host:*` keys in `ctx.indices`) or by
    aggregators run earlier in the pipeline.

    Returns a `ResolveResult`. Provenance information that doesn't fit the
    `in_repo` / `external` shape goes in `annotations`.
    """
    name: str
    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool: ...
    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult: ...


@runtime_checkable
class RecordEnricher(Protocol):
    """Called once per FileRecord after the host's LanguageAnalyzer has
    populated `record.ast_summary`. The enricher may mutate the record
    (adding fields) or stash data in `ctx.scratch`.

    Contract:
      - Must be deterministic given the same input.
      - Must not mutate other records or `ctx.indices`.
      - Receives raw bytes; may ignore them if `record.ast_summary` is
        sufficient.
    """
    name: str
    def enrich(self, record: FileRecord, content: bytes, ctx: PipelineCtx) -> None: ...


@runtime_checkable
class Aggregator(Protocol):
    """Called once per pipeline run, after all enrichers, with full
    visibility of every record. Builds derived state (indexes, embeddings,
    concept graphs) and stores it in `ctx.indices[self.name]`.

    Contract:
      - Must be deterministic.
      - May read from `ctx.records`, `ctx.scratch`, and `ctx.indices`
        populated by aggregators with lexicographically smaller `.name`.
      - The return value is stored at `ctx.indices[self.name]`.
    """
    name: str
    def run(self, ctx: PipelineCtx) -> object: ...


@runtime_checkable
class GraphContributor(Protocol):
    """Called after the host has built `build_inventory_graph(...)`.
    Contributors add their own triples to the graph; they must not remove
    or rewrite the host's triples.
    """
    name: str
    def contribute(self, g: Graph, ctx: PipelineCtx) -> None: ...


@runtime_checkable
class ShapeContributor(Protocol):
    """Called after the host has built `build_shacl_graph()`. Contributors
    add SHACL shapes for the classes/properties their GraphContributor
    introduced.
    """
    name: str
    def contribute(self, shapes: Graph) -> None: ...


@runtime_checkable
class ArtifactEmitter(Protocol):
    """Called after the host has written its core artifacts and blobs.
    Returns a manifest fragment (a dict) merged into `run_manifest.json`
    under `extensions[self.name]`.

    Contract:
      - All emitted files must be byte-stable. The run-level timestamp
        lives in `run_manifest.json` only; emitters must not write
        timestamps into their own outputs (this is a determinism rule
        learned from the L2 prototype).
    """
    name: str
    def emit(self, out_dir: Path, ctx: PipelineCtx) -> dict: ...


# ---------------------------------------------------------------------------
# Registries — module-level lists, iterated in `.name` sort order
# ---------------------------------------------------------------------------


_LANGUAGE_ANALYZERS: list[LanguageAnalyzer] = []
_IMPORT_RESOLVERS: list[ImportResolver] = []
_RECORD_ENRICHERS: list[RecordEnricher] = []
_AGGREGATORS: list[Aggregator] = []
_GRAPH_CONTRIBUTORS: list[GraphContributor] = []
_SHAPE_CONTRIBUTORS: list[ShapeContributor] = []
_ARTIFACT_EMITTERS: list[ArtifactEmitter] = []


def register_language_analyzer(a: LanguageAnalyzer) -> None:
    _LANGUAGE_ANALYZERS.append(a)


def register_import_resolver(r: ImportResolver) -> None:
    _IMPORT_RESOLVERS.append(r)


def register_record_enricher(e: RecordEnricher) -> None:
    _RECORD_ENRICHERS.append(e)


def register_aggregator(a: Aggregator) -> None:
    _AGGREGATORS.append(a)


def register_graph_contributor(c: GraphContributor) -> None:
    _GRAPH_CONTRIBUTORS.append(c)


def register_shape_contributor(s: ShapeContributor) -> None:
    _SHAPE_CONTRIBUTORS.append(s)


def register_artifact_emitter(a: ArtifactEmitter) -> None:
    _ARTIFACT_EMITTERS.append(a)


def reset_registries() -> None:
    """Clear all registries, then re-register the built-in language
    analyzers and import resolvers so the host remains functional.

    Useful for tests; production callers that drive the host in-process
    should call this between distinct repo mappings.
    """
    _LANGUAGE_ANALYZERS.clear()
    _IMPORT_RESOLVERS.clear()
    _RECORD_ENRICHERS.clear()
    _AGGREGATORS.clear()
    _GRAPH_CONTRIBUTORS.clear()
    _SHAPE_CONTRIBUTORS.clear()
    _ARTIFACT_EMITTERS.clear()
    # Late import to avoid a circular dependency: _builtins imports from
    # the per-language modules, which depend on this package's __init__
    # having already executed.
    from ._builtins import register_builtins
    register_builtins()


# ---------------------------------------------------------------------------
# Iterators — used by the host's pipeline / emit / reconstruct modules
# ---------------------------------------------------------------------------


def iter_language_analyzers() -> list[LanguageAnalyzer]:
    return sorted(_LANGUAGE_ANALYZERS, key=lambda x: x.name)


def iter_import_resolvers() -> list[ImportResolver]:
    return sorted(_IMPORT_RESOLVERS, key=lambda x: x.name)


def iter_record_enrichers() -> list[RecordEnricher]:
    return sorted(_RECORD_ENRICHERS, key=lambda x: x.name)


def iter_aggregators() -> list[Aggregator]:
    return sorted(_AGGREGATORS, key=lambda x: x.name)


def iter_graph_contributors() -> list[GraphContributor]:
    return sorted(_GRAPH_CONTRIBUTORS, key=lambda x: x.name)


def iter_shape_contributors() -> list[ShapeContributor]:
    return sorted(_SHAPE_CONTRIBUTORS, key=lambda x: x.name)


def iter_artifact_emitters() -> list[ArtifactEmitter]:
    return sorted(_ARTIFACT_EMITTERS, key=lambda x: x.name)
