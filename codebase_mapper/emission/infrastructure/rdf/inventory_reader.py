"""rdflib → canonical schema bridge for inventory.ttl.

Parses an inventory graph (as emitted by ``build_inventory_graph``)
into the typed ``InventoryGraph`` model defined in
``emission/domain/inventory_schema.py``. Node content is assembled into
plain payload dicts and handed to ``model_validate`` — Pydantic is the
validation boundary, so a graph that violates the mirrored shape
constraints (missing required literal, bad pattern, dangling edge)
raises ``pydantic.ValidationError`` here, before any consumer touches
it.

IRIs are resolved to natural keys by reading the target node's own
literals (``cbm:path`` / ``cbm:packageName`` / name+version), never by
un-quoting the IRI string — the literal is the authoritative value.
"""
from __future__ import annotations

from typing import Any

from rdflib import Graph
from rdflib.term import Node
from rdflib.namespace import RDF

from ....shared_kernel.constants import CBM
from ...domain.inventory_schema import InventoryGraph


def _one(g: Graph, s: Node, p: Node) -> str | None:
    v = g.value(s, p)
    return None if v is None else str(v)


def _many(g: Graph, s: Node, p: Node) -> list[str]:
    return sorted(str(o) for o in g.objects(s, p))


def _targets(g: Graph, s: Node, p: Node, key_of: dict[Node, str]) -> list[str]:
    """Edge objects resolved to natural keys; an unmapped target keeps
    its raw IRI so InventoryGraph's integrity validator names it."""
    return sorted(key_of.get(o, str(o)) for o in g.objects(s, p))


def _iri_leaf(iri: str | None) -> str | None:
    """cbmt:/cbmp: vocabulary IRI → its vocabulary term."""
    return None if iri is None else iri.rsplit("#", 1)[-1]


def read_inventory(g: Graph) -> InventoryGraph:
    """Build a validated ``InventoryGraph`` from a parsed inventory graph.

    Raises ``ValueError`` when the graph lacks its Repository/Commit
    spine, and ``pydantic.ValidationError`` when node content violates
    the canonical schema.
    """
    repo = g.value(predicate=RDF.type, object=CBM.Repository)
    if repo is None:
        raise ValueError("no cbm:Repository node in graph")
    commit_node = g.value(repo, CBM.atCommit)
    if commit_node is None:
        raise ValueError("cbm:Repository has no cbm:atCommit edge")

    # Natural-key lookups for edge targets. Files keyed by IRI → path;
    # packages by IRI → name; releases by IRI → name@version.
    path_of = {s: str(g.value(s, CBM.path))
               for s in g.subjects(RDF.type, CBM.File)}
    pkg_of = {s: str(g.value(s, CBM.packageName))
              for s in g.subjects(RDF.type, CBM.ExternalPackage)}
    release_of = {
        s: f"{g.value(s, CBM.packageName)}@{g.value(s, CBM.packageVersion)}"
        for s in g.subjects(RDF.type, CBM.PackageRelease)}

    files: list[dict[str, Any]] = []
    for s in sorted(path_of, key=lambda n: path_of[n]):
        files.append({
            "path": path_of[s],
            "git_blob_sha": _one(g, s, CBM.gitBlobSha),
            "content_sha256": _one(g, s, CBM.contentSha256),
            "size_bytes": _one(g, s, CBM.sizeBytes),
            "type": _iri_leaf(_one(g, s, CBM.type)),
            "phases": sorted(
                str(_iri_leaf(str(o))) for o in g.objects(s, CBM.hasPhase)),
            "language": _one(g, s, CBM.language),
            "ast_summary": _one(g, s, CBM.astSummary),
            "extraction_errors": _many(g, s, CBM.extractionError),
            "atime": _one(g, s, CBM.atime),
            "mtime": _one(g, s, CBM.mtime),
            "ctime": _one(g, s, CBM.ctime),
            "git_commit_time": _one(g, s, CBM.gitCommitTime),
            "imports": _targets(g, s, CBM.imports, path_of),
            "possible_imports": _targets(g, s, CBM.possibleImport, path_of),
            "tests": _targets(g, s, CBM.tests, path_of),
            "imports_external": _targets(g, s, CBM.importsExternal, pkg_of),
            "declares_dependencies": _targets(
                g, s, CBM.declaresDependency, pkg_of),
            "pins_dependencies": _targets(
                g, s, CBM.pinsDependency, release_of),
        })

    payload: dict[str, Any] = {
        "repository_iri": str(repo),
        "commit": {"commit_sha": _one(g, commit_node, CBM.commitSha)},
        "files": files,
        "external_packages": [
            {"package_name": name} for name in sorted(pkg_of.values())],
        "package_releases": [
            {
                "package_name": str(g.value(s, CBM.packageName)),
                "package_version": str(g.value(s, CBM.packageVersion)),
                "release_of": _targets(g, s, CBM.releaseOf, pkg_of)[0]
                if list(g.objects(s, CBM.releaseOf)) else None,
            }
            for s in sorted(release_of, key=lambda n: release_of[n])],
    }
    return InventoryGraph.model_validate(payload)
