"""Extension surface for codebase_mapper shared-kernel hooks."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from rdflib import Graph

from ..inspection.models import FileRecord


@dataclass
class PipelineCtx:
    """Typed shared-state for the extension pipeline."""

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
    """Return type of `ImportResolver.resolve`."""

    in_repo: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    annotations: dict[str, list[str]] = field(default_factory=dict)


@runtime_checkable
class LanguageAnalyzer(Protocol):
    name: str
    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool: ...
    def extract(self, record: FileRecord, content: bytes,
                ctx: PipelineCtx) -> tuple[dict | None, list[str]]: ...


@runtime_checkable
class ImportResolver(Protocol):
    name: str
    def matches(self, record: FileRecord, ctx: PipelineCtx) -> bool: ...
    def resolve(self, record: FileRecord, ctx: PipelineCtx) -> ResolveResult: ...


@runtime_checkable
class RecordEnricher(Protocol):
    name: str
    def enrich(self, record: FileRecord, content: bytes, ctx: PipelineCtx) -> None: ...


@runtime_checkable
class Aggregator(Protocol):
    name: str
    def run(self, ctx: PipelineCtx) -> object: ...


@runtime_checkable
class GraphContributor(Protocol):
    name: str
    def contribute(self, g: Graph, ctx: PipelineCtx) -> None: ...


@runtime_checkable
class ShapeContributor(Protocol):
    name: str
    def contribute(self, shapes: Graph) -> None: ...


@runtime_checkable
class ArtifactEmitter(Protocol):
    name: str
    def emit(self, out_dir: Path, ctx: PipelineCtx) -> dict: ...


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
    """Clear all registries, then re-register built-in analyzers and resolvers."""

    _LANGUAGE_ANALYZERS.clear()
    _IMPORT_RESOLVERS.clear()
    _RECORD_ENRICHERS.clear()
    _AGGREGATORS.clear()
    _GRAPH_CONTRIBUTORS.clear()
    _SHAPE_CONTRIBUTORS.clear()
    _ARTIFACT_EMITTERS.clear()
    from ..inspection._builtins import register_builtins
    register_builtins()


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
