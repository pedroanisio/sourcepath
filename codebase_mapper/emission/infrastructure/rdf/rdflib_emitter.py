"""codebase_mapper.rdf_emit."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
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
from ....inspection.models import (
    DeclaresDependencyEdge,
    FileRecord,
    ImportEdge,
    ImportExternalEdge,
    PinsDependencyEdge,
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
    possible_import_edges: list = (),
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
    for e in possible_import_edges:
        # Disclosed candidates of an ambiguous include (plan E4): a separate
        # property so hard cbm:imports consumers keep 100% precision.
        g.add((file_iri(e.src_path), CBM.possibleImport, file_iri(e.dst_path)))
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

def build_shacl_graph() -> Graph:
    g = Graph()
    g.bind("sh", SH); g.bind("cbm", CBM)
    g.bind("cbmt", CBMT); g.bind("cbmp", CBMP); g.bind("xsd", XSD)

    def prop_uri(kwargs: dict) -> URIRef:
        key = "|".join(f"{k}={kwargs[k]}" for k in sorted(kwargs))
        return URIRef(f"{CBM_NS}_ps_{hashlib.sha1(key.encode()).hexdigest()[:16]}")

    def add_prop(parent: URIRef, **kwargs) -> URIRef:
        b = prop_uri(kwargs)
        g.add((parent, SH.property, b))
        for k, v in kwargs.items():
            g.add((b, URIRef(SH + k), v))
        return b

    file_shape = URIRef(f"{CBM_NS}FileShape")
    g.add((file_shape, RDF.type, SH.NodeShape))
    g.add((file_shape, SH.targetClass, CBM.File))

    add_prop(file_shape, path=CBM.path,
             minCount=Literal(1), maxCount=Literal(1), datatype=XSD.string)
    add_prop(file_shape, path=CBM.contentSha256,
             minCount=Literal(1), maxCount=Literal(1),
             datatype=XSD.hexBinary, pattern=Literal("^[0-9a-f]{64}$"))
    add_prop(file_shape, path=CBM.gitBlobSha,
             minCount=Literal(1), maxCount=Literal(1), datatype=XSD.string)
    add_prop(file_shape, path=CBM.sizeBytes,
             minCount=Literal(1), maxCount=Literal(1),
             datatype=XSD.integer, minInclusive=Literal(0))
    add_prop(file_shape, path=CBM.language,
             maxCount=Literal(1), datatype=XSD.string)
    # ast_summary is optional (only emitted when the analyzer produces one)
    # and serialized as a canonical JSON literal. Length is not bounded — some
    # analyzers emit large AST blobs.
    add_prop(file_shape, path=CBM.astSummary,
             maxCount=Literal(1), datatype=XSD.string)
    # extraction_errors is a list, recorded as one literal per error. No count
    # bound; xsd:string covers the analyzer-emitted message format.
    add_prop(file_shape, path=CBM.extractionError, datatype=XSD.string)
    # Dependency-manifest files declare external packages; lockfile records
    # pin them to specific releases. Both predicates are optional on
    # cbm:File (most files are neither manifest nor lockfile) but when
    # present the object class is constrained.
    add_prop(file_shape, path=CBM.declaresDependency,
             **{"class": CBM.ExternalPackage})
    add_prop(file_shape, path=CBM.pinsDependency,
             **{"class": CBM.PackageRelease})
    # Filesystem + git commit times are optional (None on a non-HEAD map);
    # when present, they're single xsd:dateTime literals.
    for pred in (CBM.atime, CBM.mtime, CBM.ctime, CBM.gitCommitTime):
        add_prop(file_shape, path=pred,
                 maxCount=Literal(1), datatype=XSD.dateTime)

    from rdflib.collection import Collection
    type_list = URIRef(f"{CBM_NS}_typeList")
    Collection(g, type_list, [type_iri(t) for t in TYPE_VOCABULARY])
    type_prop = URIRef(f"{CBM_NS}_typeProp")
    g.add((file_shape, SH.property, type_prop))
    g.add((type_prop, SH.path, CBM.type))
    g.add((type_prop, SH.minCount, Literal(1)))
    g.add((type_prop, SH.maxCount, Literal(1)))
    g.add((type_prop, URIRef(SH + "in"), type_list))

    phase_list = URIRef(f"{CBM_NS}_phaseList")
    Collection(g, phase_list, [phase_iri(p) for p in PHASE_VOCABULARY])
    phase_prop = URIRef(f"{CBM_NS}_phaseProp")
    g.add((file_shape, SH.property, phase_prop))
    g.add((phase_prop, SH.path, CBM.hasPhase))
    g.add((phase_prop, SH.minCount, Literal(1)))
    g.add((phase_prop, URIRef(SH + "in"), phase_list))

    imports_prop = URIRef(f"{CBM_NS}_importsProp")
    g.add((file_shape, SH.property, imports_prop))
    g.add((imports_prop, SH.path, CBM.imports))
    g.add((imports_prop, URIRef(SH + "class"), CBM.File))

    possible_imp_prop = URIRef(f"{CBM_NS}_possibleImportProp")
    g.add((file_shape, SH.property, possible_imp_prop))
    g.add((possible_imp_prop, SH.path, CBM.possibleImport))
    g.add((possible_imp_prop, URIRef(SH + "class"), CBM.File))

    imports_ext_prop = URIRef(f"{CBM_NS}_importsExtProp")
    g.add((file_shape, SH.property, imports_ext_prop))
    g.add((imports_ext_prop, SH.path, CBM.importsExternal))
    g.add((imports_ext_prop, URIRef(SH + "class"), CBM.ExternalPackage))

    tests_shape = URIRef(f"{CBM_NS}TestsSubjectShape")
    g.add((tests_shape, RDF.type, SH.NodeShape))
    g.add((tests_shape, URIRef(SH + "targetSubjectsOf"), CBM.tests))
    tests_type_prop = URIRef(f"{CBM_NS}_testsTypeProp")
    g.add((tests_shape, SH.property, tests_type_prop))
    g.add((tests_type_prop, SH.path, CBM.type))
    g.add((tests_type_prop, URIRef(SH + "hasValue"), type_iri("test_code")))

    repo_shape = URIRef(f"{CBM_NS}RepositoryShape")
    g.add((repo_shape, RDF.type, SH.NodeShape))
    g.add((repo_shape, SH.targetClass, CBM.Repository))
    add_prop(repo_shape, path=CBM.atCommit,
             minCount=Literal(1), maxCount=Literal(1),
             **{"class": CBM.Commit})
    # Repository → File edges. min_count=0 (an empty repo emits no files but
    # the Repository node is still valid); object class fixed to cbm:File.
    add_prop(repo_shape, path=CBM.hasFile,
             **{"class": CBM.File})

    # The Commit node carries its own SHA. Single value, hexBinary-shaped
    # (40 chars for SHA-1; the emitter currently writes the plain hex
    # string so we keep xsd:string and constrain the pattern).
    commit_shape = URIRef(f"{CBM_NS}CommitShape")
    g.add((commit_shape, RDF.type, SH.NodeShape))
    g.add((commit_shape, SH.targetClass, CBM.Commit))
    add_prop(commit_shape, path=CBM.commitSha,
             minCount=Literal(1), maxCount=Literal(1),
             datatype=XSD.string, pattern=Literal("^[0-9a-f]+$"))

    rel_shape = URIRef(f"{CBM_NS}PackageReleaseShape")
    g.add((rel_shape, RDF.type, SH.NodeShape))
    g.add((rel_shape, SH.targetClass, CBM.PackageRelease))
    add_prop(rel_shape, path=CBM.packageName,
             minCount=Literal(1), maxCount=Literal(1), datatype=XSD.string)
    add_prop(rel_shape, path=CBM.packageVersion,
             minCount=Literal(1), maxCount=Literal(1), datatype=XSD.string)
    add_prop(rel_shape, path=CBM.releaseOf,
             minCount=Literal(1), maxCount=Literal(1),
             **{"class": CBM.ExternalPackage})
    return g

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
