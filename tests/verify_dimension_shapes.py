#!/usr/bin/env python3
"""verify_dimension_shapes.py — regression suite for the Scope-A SHACL gate.

Locks the 2.0.5 silent-acceptance hardening of
``static/schemas/software_architecture_dimensions.ttl``. Before 2.0.5 the
gate accepted three error shapes without complaint:

  * arch:atScope was free text — absent or junk scope statements conformed;
  * arch:dominantValue coherence ("must be one of the used values") lived
    only in an rdfs:comment, so an unused dominant conformed;
  * ABox author identity lived in a header comment, so an analysis with no
    machine-readable provenance conformed.

Tests:
  1. The TBox itself conforms (data graph == shapes graph) and its
     owl:versionInfo carries the hardened revision.
  2. Both shipped fixture ABoxes (sqlite, airflow) conform against the
     shipped TBox — the constraints must not break conforming analyses.
  3. Control: a minimal hand-built ABox conforms.
  4. Injection: dropping arch:atScope → nonconforming, blamed on the
     atScope constraint.
  5. Injection: an unregistered altitude ("everywhere") → nonconforming.
  6. Injection: arch:dominantValue outside usesClassificationValue →
     nonconforming, blamed on DominantValueCoherenceShape's message.
  7. Injection: ontology header without dcterms:creator → nonconforming,
     blamed on AnalysisProvenanceShape's message.
  8. Witness for the pre-existing gates: confidence outside the enum and
     an unregistered classification value still fail.

Exit code: 0 if all pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rdflib
from pyshacl import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
TBOX = REPO_ROOT / "static" / "schemas" / "software_architecture_dimensions.ttl"
FIXTURES = [
    REPO_ROOT / "static" / "fixtures" / "sqlite_abox.ttl",
    REPO_ROOT / "static" / "fixtures" / "airflow_abox.ttl",
]

EXPECTED_VERSION_PREFIX = "2.0.5"

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        print(f"  PASS  {name}")
        PASS += 1
    else:
        print(f"  FAIL  {name}")
        if detail:
            for line in detail.splitlines()[:20]:
                print(f"        {line}")
        FAIL += 1


def shacl(abox_ttl: str | None = None, abox_path: Path | None = None) -> tuple[bool, str]:
    """Validate (TBox [+ ABox]) as data against the TBox as shapes."""
    data = rdflib.Graph()
    data.parse(TBOX.as_posix(), format="turtle")
    if abox_path is not None:
        data.parse(abox_path.as_posix(), format="turtle")
    if abox_ttl is not None:
        data.parse(data=abox_ttl, format="turtle")
    shapes = rdflib.Graph()
    shapes.parse(TBOX.as_posix(), format="turtle")
    conforms, _g, text = validate(
        data_graph=data,
        shacl_graph=shapes,
        inference="none",
        advanced=True,       # SHACL-SPARQL constraints
        meta_shacl=False,
        debug=False,
    )
    return conforms, text


ABOX_PREAMBLE = """\
@prefix arch: <https://w3id.org/arc4d3/software-architecture-dimensions#> .
@prefix tst:  <https://w3id.org/arc4d3/abox/shape-regression#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
"""

# Minimal conforming ABox: one system, one D01 application with a
# registered value, a coherent dominant, evidence, confidence, scope,
# and an ontology header disclosing its author.
VALID_ABOX = ABOX_PREAMBLE + """\
tst:Ontology a owl:Ontology ;
    dcterms:creator "verify_dimension_shapes.py synthetic fixture" .

tst:System a arch:ImplementedSoftwareSystem ;
    arch:systemName "shape-regression fixture system" .

tst:Ev a arch:EvidenceRecord ;
    arch:evidenceSummary "Synthetic evidence, long enough to satisfy minLength." .

tst:App a arch:DimensionApplication ;
    arch:classifiesSystem tst:System ;
    arch:appliesDimension arch:D01_DecompositionModel ;
    arch:usesClassificationValue arch:ByLayer ;
    arch:dominantValue arch:ByLayer ;
    arch:supportedByEvidence tst:Ev ;
    arch:atScope "system" ;
    arch:confidenceLevel "High" .
"""


def mutate(remove: str = "", replace: tuple[str, str] | None = None) -> str:
    out = VALID_ABOX
    if remove:
        assert remove in out, f"mutation target not in template: {remove!r}"
        out = out.replace(remove, "")
    if replace:
        old, new = replace
        assert old in out, f"mutation target not in template: {old!r}"
        out = out.replace(old, new)
    return out


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser().parse_args(argv)

    # --- 1. TBox self-conformance + version witness ---
    conforms, text = shacl()
    check("TBox conforms against its own shapes", conforms, text)

    tbox_graph = rdflib.Graph()
    tbox_graph.parse(TBOX.as_posix(), format="turtle")
    version = next(
        tbox_graph.objects(
            rdflib.URIRef(
                "https://w3id.org/arc4d3/software-architecture-dimensions#"
                "ScopeACoreSystemDimensionsOntology"
            ),
            rdflib.URIRef("http://www.w3.org/2002/07/owl#versionInfo"),
        ),
        None,
    )
    check(
        f"TBox owl:versionInfo is the hardened revision ({EXPECTED_VERSION_PREFIX}*)",
        version is not None and str(version).startswith(EXPECTED_VERSION_PREFIX),
        f"found: {version}",
    )

    # --- 2. Shipped fixture ABoxes still conform ---
    for fixture in FIXTURES:
        conforms, text = shacl(abox_path=fixture)
        check(f"fixture conforms: {fixture.name}", conforms, text)

    # --- 3. Control: the minimal synthetic ABox conforms ---
    conforms, text = shacl(VALID_ABOX)
    check("control: minimal synthetic ABox conforms", conforms, text)

    # --- 4..8. Injections: each must fail, blamed on the right constraint ---
    injections = [
        (
            "missing arch:atScope is rejected",
            mutate(remove='arch:atScope "system" ;\n    '),
            "must state its analytical altitude",
        ),
        (
            "unregistered altitude is rejected",
            mutate(replace=('arch:atScope "system"', 'arch:atScope "everywhere"')),
            "must state its analytical altitude",
        ),
        (
            "dominant value outside used values is rejected",
            mutate(replace=(
                "arch:dominantValue arch:ByLayer",
                "arch:dominantValue arch:ByFeature",
            )),
            "dominant classification value must be one of",
        ),
        (
            "ABox header without dcterms:creator is rejected",
            mutate(remove=' ;\n    dcterms:creator '
                          '"verify_dimension_shapes.py synthetic fixture"'),
            "disclose its author",
        ),
        (
            "witness: confidence outside the enum is rejected",
            mutate(replace=('arch:confidenceLevel "High"',
                            'arch:confidenceLevel "Certain"')),
            "must declare confidence as",
        ),
        (
            "witness: unregistered classification value is rejected",
            mutate(replace=(
                "arch:usesClassificationValue arch:ByLayer ;\n"
                "    arch:dominantValue arch:ByLayer",
                "arch:usesClassificationValue arch:DirectCallConnector ;\n"
                "    arch:dominantValue arch:DirectCallConnector",
            )),
            "must only use classification values that its applied dimension declares",
        ),
    ]
    for name, abox, expected_blame in injections:
        conforms, text = shacl(abox)
        if conforms:
            check(name, False, "expected nonconformance but the gate accepted it")
            continue
        check(
            name,
            expected_blame.lower() in text.lower(),
            f"nonconforming, but not blamed on the expected constraint.\n"
            f"expected message fragment: {expected_blame!r}\n{text}",
        )

    print()
    print(f"passed: {PASS}   failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
