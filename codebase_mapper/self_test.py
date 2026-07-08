"""codebase_mapper.self_test."""
from __future__ import annotations

import json
import subprocess
import tempfile

from pathlib import Path
from rdflib import Graph
from rdflib import Literal
from rdflib import URIRef
from rdflib.namespace import RDF
from rdflib.namespace import XSD
from typing import Callable

from .emission.application.reconstruct import verify_roundtrip
from .emission.infrastructure.rdf.rdflib_emitter import (
    _plain,
    build_shacl_graph,
    file_iri,
    package_iri,
    phase_iri,
    type_iri,
)
from .shared_kernel.constants import CBM, CBMI_NS, CBMT_NS


def self_test() -> int:
    from pyshacl import validate
    fixture = Graph()
    fixture.bind("cbm", CBM)
    repo = URIRef(f"{CBMI_NS}repo/fixture")
    commit = URIRef(f"{CBMI_NS}commit/abc")
    fixture.add((commit, RDF.type, CBM.Commit))
    fixture.add((commit, CBM.commitSha, _plain("abc")))
    fixture.add((repo, RDF.type, CBM.Repository))
    fixture.add((repo, CBM.atCommit, commit))
    f1 = file_iri("a.py")
    f2 = file_iri("test_a.py")
    pkg = package_iri("requests")
    fixture.add((pkg, RDF.type, CBM.ExternalPackage))
    fixture.add((pkg, CBM.packageName, _plain("requests")))
    for f, t, ph in ((f1, "source_code", "runtime"), (f2, "test_code", "test")):
        fixture.add((f, RDF.type, CBM.File))
        fixture.add((repo, CBM.hasFile, f))
        fixture.add((f, CBM.path, _plain("a.py" if t == "source_code" else "test_a.py")))
        fixture.add((f, CBM.gitBlobSha, _plain("0" * 40)))
        fixture.add((f, CBM.contentSha256, Literal("a" * 64, datatype=XSD.hexBinary)))
        fixture.add((f, CBM.sizeBytes, Literal(10, datatype=XSD.integer)))
        fixture.add((f, CBM.language, _plain("python")))
        fixture.add((f, CBM.type, type_iri(t)))
        fixture.add((f, CBM.hasPhase, phase_iri(ph)))
    fixture.add((f2, CBM.tests, f1))
    fixture.add((f1, CBM.importsExternal, pkg))
    shapes = build_shacl_graph()

    # Mutators are called for effect; their return value (Graph, tuple of
    # Graphs, or None) is ignored, hence `object`.
    cases: list[tuple[str, bool, Callable[[Graph], object]]] = [
        ("control (untouched)", True, lambda g: None),
        ("drop contentSha256", False,
            lambda g: g.remove((f1, CBM.contentSha256, None))),
        ("invalid type IRI", False,
            lambda g: (g.remove((f1, CBM.type, None)),
                       g.add((f1, CBM.type, URIRef(f"{CBMT_NS}bogus"))))),
        ("zero phases", False,
            lambda g: g.remove((f1, CBM.hasPhase, None))),
        ("bad hex pattern", False,
            lambda g: (g.remove((f1, CBM.contentSha256, None)),
                       g.add((f1, CBM.contentSha256, Literal("ZZZZ", datatype=XSD.hexBinary))))),
        ("negative sizeBytes", False,
            lambda g: (g.remove((f1, CBM.sizeBytes, None)),
                       g.add((f1, CBM.sizeBytes, Literal(-1, datatype=XSD.integer))))),
        ("two type values", False,
            lambda g: g.add((f1, CBM.type, type_iri("documentation")))),
        ("tests subject not test_code", False,
            lambda g: (g.remove((f2, CBM.type, None)),
                       g.add((f2, CBM.type, type_iri("source_code"))))),
        ("imports to non-File", False,
            lambda g: g.add((f1, CBM.imports, URIRef("http://example.org/not-a-file")))),
        ("importsExternal to non-ExternalPackage", False,
            lambda g: g.add((f1, CBM.importsExternal, URIRef("http://example.org/not-a-pkg")))),
        ("repository without commit", False,
            lambda g: g.remove((repo, CBM.atCommit, None))),
        ("file without language is OK", True,
            lambda g: g.remove((f2, CBM.language, None))),
        ("two languages on one file", False,
            lambda g: g.add((f1, CBM.language, _plain("rust")))),
        ("hash longer than 64 chars", False,
            lambda g: (g.remove((f1, CBM.contentSha256, None)),
                       g.add((f1, CBM.contentSha256, Literal("a" * 65, datatype=XSD.hexBinary))))),
    ]
    print(f"{'case':<55} {'expected':<10} {'actual':<10} status")
    print("-" * 90)
    failed = 0
    for label, expected, mutator in cases:
        g = Graph()
        g += fixture
        mutator(g)
        actual, _, _ = validate(g, shacl_graph=shapes, inference="none")
        ok = bool(actual) == expected
        status = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"{label:<55} {str(expected):<10} {str(bool(actual)):<10} {status}")
    print()

    # Roundtrip property test on a synthetic in-memory fixture.
    print("=== roundtrip property test ===")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Build a tiny git repo on the fly.
        sub = td_path / "repo"
        sub.mkdir()
        (sub / "a.py").write_text("x = 1\n")
        (sub / "b.txt").write_bytes(b"\x00\x01binary-ish\xff")
        (sub / "sub").mkdir()
        (sub / "sub" / "c.md").write_text("# hello\n")
        subprocess.run(["git", "init", "-q"], cwd=sub, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "--allow-empty", "-q", "-m", "init"], cwd=sub, check=True)
        subprocess.run(["git", "add", "-A"], cwd=sub, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-q", "-m", "add"], cwd=sub, check=True)
        report = verify_roundtrip(sub, "HEAD")
        print(json.dumps({k: v for k, v in report.items() if k != "verification"},
                         indent=2, sort_keys=True))
        print(json.dumps(report["verification"], indent=2, sort_keys=True))
        if not report["roundtrip_ok"]:
            failed += 1
            print("FAIL: roundtrip identity broken")
        else:
            print("PASS: roundtrip identity preserved")
    print()
    print(f"failed: {failed} / {len(cases) + 1}")
    return 0 if failed == 0 else 1
