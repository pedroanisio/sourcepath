"""codebase_mapper.rdf_emit."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from typing import Sequence
from urllib.parse import quote

from rdflib import Graph
from rdflib import Literal
from rdflib import URIRef
from rdflib.namespace import OWL
from rdflib.namespace import RDF
from rdflib.namespace import RDFS
from rdflib.namespace import XSD


def _iso_utc(ts: float) -> str:
    """Format a Unix timestamp as a UTC xsd:dateTime literal.

    Preserves microsecond precision when the source has any; trims the
    trailing zeros otherwise so equal seconds serialize identically
    regardless of how the value was constructed.
    """
    d = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc)
    s = d.isoformat(timespec="microseconds").replace("+00:00", "Z")
    # "2026-05-12T10:00:00.000000Z" -> "2026-05-12T10:00:00Z" when sub-second is 0
    return s.replace(".000000Z", "Z")

from ....shared_kernel.constants import CBM, CBMI, CBMI_NS, CBMP, CBMP_NS, CBMT, CBMT_NS, CBM_NS, PHASE_VOCABULARY, SH, SPDX_CORE_NS, SPDX_SOFTWARE_NS, TYPE_VOCABULARY
from ....shared_kernel.json_safety import dump_ast_summary
from ....shared_kernel.shacl_spec import NodeShapeSpec, PropertySpec, render_shapes
from ....inspection.models import (
    DeclaresDependencyEdge,
    FileRecord,
    ImportEdge,
    ImportExternalEdge,
    PinsDependencyEdge,
    PossibleImportEdge,
    TestsEdge,
)


def file_iri(path: str) -> URIRef:
    return URIRef(f"{CBMI_NS}file/{quote(path, safe='')}")

def package_iri(name: str) -> URIRef:
    safe = re.sub(r"[^A-Za-z0-9._@/-]", "_", name).replace("/", "%2F")
    return URIRef(f"{CBMI_NS}pkg/{safe}")

def release_iri(name: str, version: str) -> URIRef:
    safe_n = re.sub(r"[^A-Za-z0-9._@/-]", "_", name).replace("/", "%2F")
    safe_v = re.sub(r"[^A-Za-z0-9._-]", "_", version)
    return URIRef(f"{CBMI_NS}release/{safe_n}@{safe_v}")

def type_iri(t: str) -> URIRef:
    return URIRef(f"{CBMT_NS}{t}")

def phase_iri(p: str) -> URIRef:
    return URIRef(f"{CBMP_NS}{p}")

def _plain(s: str) -> Literal:
    return Literal(s)

def build_inventory_graph(
    repo_iri: URIRef, commit_sha: str, records: list[FileRecord],
    import_edges: list[ImportEdge], import_ext_edges: list[ImportExternalEdge],
    dep_edges: list[DeclaresDependencyEdge], pin_edges: list[PinsDependencyEdge],
    tests_edges: list[TestsEdge],
    truncated_ast_paths: list[str] | None = None,
    possible_import_edges: Sequence[PossibleImportEdge] = (),
) -> Graph:
    g = Graph()
    g.bind("cbm", CBM); g.bind("cbmt", CBMT); g.bind("cbmp", CBMP)
    g.bind("cbmi", CBMI); g.bind("xsd", XSD)

    commit = URIRef(f"{CBMI_NS}commit/{commit_sha}")
    g.add((commit, RDF.type, CBM.Commit))
    g.add((commit, CBM.commitSha, _plain(commit_sha)))
    g.add((repo_iri, RDF.type, CBM.Repository))
    g.add((repo_iri, CBM.atCommit, commit))

    for r in records:
        f = file_iri(r.path)
        g.add((f, RDF.type, CBM.File))
        g.add((repo_iri, CBM.hasFile, f))
        g.add((f, CBM.path, _plain(r.path)))
        g.add((f, CBM.gitBlobSha, _plain(r.git_blob_sha)))
        g.add((f, CBM.contentSha256, Literal(r.content_sha256, datatype=XSD.hexBinary)))
        g.add((f, CBM.sizeBytes, Literal(r.size_bytes, datatype=XSD.integer)))
        if r.language is not None:
            g.add((f, CBM.language, _plain(r.language)))
        g.add((f, CBM.type, type_iri(r.type_)))
        for ph in r.phases:
            g.add((f, CBM.hasPhase, phase_iri(ph)))
        if r.ast_summary is not None:
            # A CST deeper than the recursion ceiling must not kill the
            # emit at the last step of a completed run (flaw F19); an
            # out-nested field is stubbed with a disclosed marker and the
            # path is reported so emit() can register the degradation.
            text, was_truncated = dump_ast_summary(r.ast_summary)
            g.add((f, CBM.astSummary, _plain(text)))
            if was_truncated and truncated_ast_paths is not None:
                truncated_ast_paths.append(r.path)
        for err in r.extraction_errors:
            g.add((f, CBM.extractionError, _plain(err)))
        if r.atime is not None:
            g.add((f, CBM.atime, Literal(_iso_utc(r.atime), datatype=XSD.dateTime)))
        if r.mtime is not None:
            g.add((f, CBM.mtime, Literal(_iso_utc(r.mtime), datatype=XSD.dateTime)))
        if r.ctime is not None:
            g.add((f, CBM.ctime, Literal(_iso_utc(r.ctime), datatype=XSD.dateTime)))
        if r.git_commit_time is not None:
            g.add((f, CBM.gitCommitTime,
                   Literal(_iso_utc(r.git_commit_time), datatype=XSD.dateTime)))

    for e in import_edges:
        g.add((file_iri(e.src_path), CBM.imports, file_iri(e.dst_path)))
    for pe_edge in possible_import_edges:
        # Disclosed candidates of an ambiguous include (plan E4): a separate
        # property so hard cbm:imports consumers keep 100% precision.
        g.add((file_iri(pe_edge.src_path), CBM.possibleImport,
               file_iri(pe_edge.dst_path)))
    for te in tests_edges:
        g.add((file_iri(te.test_path), CBM.tests, file_iri(te.subject_path)))

    declared: set[str] = set()
    for de in dep_edges:
        declared.add(de.package_name)
        g.add((file_iri(de.manifest_path), CBM.declaresDependency, package_iri(de.package_name)))
    for xe in import_ext_edges:
        # Always emit; the package node may not be declared in any manifest
        # (still useful for downstream queries).
        g.add((file_iri(xe.src_path), CBM.importsExternal, package_iri(xe.package_name)))
        declared.add(xe.package_name)
    for pkg in sorted(declared):
        g.add((package_iri(pkg), RDF.type, CBM.ExternalPackage))
        g.add((package_iri(pkg), CBM.packageName, _plain(pkg)))

    pinned: set[tuple[str, str]] = set()
    for pe in pin_edges:
        pinned.add((pe.package_name, pe.package_version))
        g.add((file_iri(pe.lockfile_path), CBM.pinsDependency,
               release_iri(pe.package_name, pe.package_version)))
    for name, version in sorted(pinned):
        rn = release_iri(name, version)
        g.add((rn, RDF.type, CBM.PackageRelease))
        g.add((rn, CBM.packageName, _plain(name)))
        g.add((rn, CBM.packageVersion, _plain(version)))
        g.add((rn, CBM.releaseOf, package_iri(name)))
        g.add((package_iri(name), RDF.type, CBM.ExternalPackage))
        g.add((package_iri(name), CBM.packageName, _plain(name)))

    for t in TYPE_VOCABULARY:
        g.add((type_iri(t), RDF.type, CBM.FileType))
    for p in PHASE_VOCABULARY:
        g.add((phase_iri(p), RDF.type, CBM.Phase))
    return g

def _core_shape_specs() -> tuple[NodeShapeSpec, ...]:
    """The canonical model of every core (cbm:) node shape.

    This declaration — not the emitted shapes.shacl.ttl — is the source of
    truth for the L1 validation contract. Plugins declare their tiers the
    same way in their graph_writer modules; render_shapes() is the only
    spec→RDF code path.
    """
    xsd_string = str(XSD.string)
    xsd_datetime = str(XSD.dateTime)

    file_shape = NodeShapeSpec(
        iri=f"{CBM_NS}FileShape", target_class=str(CBM.File), properties=(
            PropertySpec(path=str(CBM.path), datatype=xsd_string, min_count=1, max_count=1),
            PropertySpec(path=str(CBM.contentSha256), min_count=1, max_count=1,
                         datatype=str(XSD.hexBinary),
                         pattern="^[0-9a-f]{64}$"),
            PropertySpec(path=str(CBM.gitBlobSha), datatype=xsd_string,
                         min_count=1, max_count=1),
            PropertySpec(path=str(CBM.sizeBytes), min_count=1, max_count=1,
                         datatype=str(XSD.integer), min_inclusive=0),
            PropertySpec(path=str(CBM.language), datatype=xsd_string,
                         max_count=1),
            # ast_summary is optional (only emitted when the analyzer
            # produces one), a canonical JSON literal of unbounded length.
            PropertySpec(path=str(CBM.astSummary), datatype=xsd_string,
                         max_count=1),
            # extraction_errors: one literal per error, no count bound.
            PropertySpec(path=str(CBM.extractionError),
                         datatype=xsd_string),
            # Manifest/lockfile edges are optional on cbm:File but their
            # object class is constrained when present.
            PropertySpec(path=str(CBM.declaresDependency),
                         klass=str(CBM.ExternalPackage)),
            PropertySpec(path=str(CBM.pinsDependency),
                         klass=str(CBM.PackageRelease)),
            # Filesystem + git commit times: optional single dateTimes
            # (None on a non-HEAD map / shallow clone).
            PropertySpec(path=str(CBM.atime), datatype=xsd_datetime,
                         max_count=1),
            PropertySpec(path=str(CBM.mtime), datatype=xsd_datetime,
                         max_count=1),
            PropertySpec(path=str(CBM.ctime), datatype=xsd_datetime,
                         max_count=1),
            PropertySpec(path=str(CBM.gitCommitTime),
                         datatype=xsd_datetime, max_count=1),
            PropertySpec(path=str(CBM.type), name="_typeProp",
                         list_name="_typeList", min_count=1, max_count=1,
                         in_iris=tuple(str(type_iri(t))
                                       for t in TYPE_VOCABULARY)),
            PropertySpec(path=str(CBM.hasPhase), name="_phaseProp",
                         list_name="_phaseList", min_count=1,
                         in_iris=tuple(str(phase_iri(p))
                                       for p in PHASE_VOCABULARY)),
            PropertySpec(path=str(CBM.imports), name="_importsProp",
                         klass=str(CBM.File)),
            PropertySpec(path=str(CBM.possibleImport),
                         name="_possibleImportProp", klass=str(CBM.File)),
            PropertySpec(path=str(CBM.importsExternal),
                         name="_importsExtProp",
                         klass=str(CBM.ExternalPackage)),
        ))

    tests_shape = NodeShapeSpec(
        iri=f"{CBM_NS}TestsSubjectShape",
        target_subjects_of=str(CBM.tests), properties=(
            PropertySpec(path=str(CBM.type), name="_testsTypeProp",
                         has_value=str(type_iri("test_code"))),
        ))

    repo_shape = NodeShapeSpec(
        iri=f"{CBM_NS}RepositoryShape", target_class=str(CBM.Repository),
        properties=(
            PropertySpec(path=str(CBM.atCommit), klass=str(CBM.Commit),
                         min_count=1, max_count=1),
            # Repository → File edges. No minCount (an empty repo emits no
            # files but the Repository node is still valid).
            PropertySpec(path=str(CBM.hasFile), klass=str(CBM.File)),
        ))

    # The Commit node carries its own SHA. The emitter writes the plain hex
    # string, so xsd:string with a hex pattern rather than xsd:hexBinary.
    commit_shape = NodeShapeSpec(
        iri=f"{CBM_NS}CommitShape", target_class=str(CBM.Commit),
        properties=(
            PropertySpec(path=str(CBM.commitSha), datatype=xsd_string,
                         pattern="^[0-9a-f]+$", min_count=1, max_count=1),
        ))

    release_shape = NodeShapeSpec(
        iri=f"{CBM_NS}PackageReleaseShape",
        target_class=str(CBM.PackageRelease), properties=(
            PropertySpec(path=str(CBM.packageName), datatype=xsd_string,
                         min_count=1, max_count=1),
            PropertySpec(path=str(CBM.packageVersion),
                         datatype=xsd_string, min_count=1, max_count=1),
            PropertySpec(path=str(CBM.releaseOf),
                         klass=str(CBM.ExternalPackage), min_count=1, max_count=1),
        ))

    return (file_shape, tests_shape, repo_shape, commit_shape,
            release_shape)


CORE_SHAPE_SPECS = _core_shape_specs()


def build_shacl_graph() -> Graph:
    return render_shapes(
        Graph(), CORE_SHAPE_SPECS,
        bind={"cbm": CBM_NS, "cbmt": CBMT_NS, "cbmp": CBMP_NS,
              "xsd": str(XSD)})

def build_ontology_mapping_graph() -> Graph:
    """A small RDFS/OWL document mapping cbm: terms to SPDX 3.0.1.

    Emitted alongside inventory but not used in SHACL validation. Consumers
    that understand SPDX can use these triples; others can ignore them.
    """
    g = Graph()
    g.bind("cbm", CBM); g.bind("rdfs", RDFS); g.bind("owl", OWL)
    spdx_file = URIRef(SPDX_SOFTWARE_NS + "File")
    spdx_pkg = URIRef(SPDX_SOFTWARE_NS + "Package")
    spdx_hash = URIRef(SPDX_CORE_NS + "Hash")
    g.add((CBM.File, OWL.equivalentClass, spdx_file))
    g.add((CBM.ExternalPackage, RDFS.subClassOf, spdx_pkg))
    # contentSha256 carries the same data SPDX expresses as a Hash with algorithm=sha256.
    g.add((CBM.contentSha256, RDFS.comment,
           _plain("SHA-256 of file content; equivalent to SPDX Core/Hash with algorithm=sha256.")))
    g.add((CBM.contentSha256, RDFS.seeAlso, spdx_hash))
    return g
