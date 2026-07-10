"""Fast structural validation — a mini-engine for OUR SHACL subset (E8).

pySHACL is the reference implementation and stays available; at tens of
millions of triples it costs hours, so every-emit validation needs a fast
path. This engine is *driven by the real shapes graph* (never a hand-copied
rule list): it interprets exactly the constraint vocabulary the bundled
shapes use —

    sh:targetClass · sh:targetSubjectsOf · sh:property · sh:path ·
    sh:minCount · sh:maxCount · sh:datatype · sh:class · sh:in ·
    sh:hasValue · sh:pattern · sh:minInclusive

— and RAISES on anything else (``UnsupportedShaclFeature``), so a shape
evolution that outruns the engine fails loudly instead of passing silently.
Equivalence with pyshacl is pinned by tests/test_fast_shacl.py on healthy
and seeded-violation fixtures (PALS's Law: the fast gate ships only with
that proof).
"""
from __future__ import annotations

import re

from collections import defaultdict

from rdflib import RDF, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import SH, XSD


class UnsupportedShaclFeature(Exception):
    """A shape uses a constraint this engine does not implement."""


_SUPPORTED_PROP_KEYS = {
    SH.path, SH.minCount, SH.maxCount, SH.datatype, SH.hasValue,
    SH.pattern, SH.minInclusive,
    URIRef(str(SH) + "class"), URIRef(str(SH) + "in"),
}
_SUPPORTED_SHAPE_KEYS = {
    RDF.type, SH.targetClass, URIRef(str(SH) + "targetSubjectsOf"),
    SH.property,
}


def _parse_shapes(shapes: Graph) -> list[dict]:
    """Shape graph → executable constraint list; raises on the unknown."""
    parsed = []
    for shape in shapes.subjects(RDF.type, SH.NodeShape):
        for p, _o in shapes.predicate_objects(shape):
            if p not in _SUPPORTED_SHAPE_KEYS:
                raise UnsupportedShaclFeature(f"{shape}: {p}")
        target_class = shapes.value(shape, SH.targetClass)
        target_subjects_of = shapes.value(
            shape, URIRef(str(SH) + "targetSubjectsOf"))
        props = []
        for prop in shapes.objects(shape, SH.property):
            spec: dict = {}
            for p, o in shapes.predicate_objects(prop):
                if p not in _SUPPORTED_PROP_KEYS:
                    raise UnsupportedShaclFeature(f"{prop}: {p}")
                if p == SH.path:
                    spec["path"] = o
                elif p == SH.minCount:
                    spec["min"] = int(o)
                elif p == SH.maxCount:
                    spec["max"] = int(o)
                elif p == SH.datatype:
                    spec["datatype"] = o
                elif p == SH.hasValue:
                    spec["has_value"] = o
                elif p == SH.pattern:
                    spec["pattern"] = re.compile(str(o))
                elif p == SH.minInclusive:
                    spec["min_inclusive"] = o.toPython()
                elif p == URIRef(str(SH) + "class"):
                    spec["cls"] = o
                elif p == URIRef(str(SH) + "in"):
                    spec["in"] = set(Collection(shapes, o))
            if "path" not in spec:
                raise UnsupportedShaclFeature(f"{prop}: property without sh:path")
            props.append(spec)
        parsed.append({
            "shape": shape,
            "target_class": target_class,
            "target_subjects_of": target_subjects_of,
            "props": props,
        })
    return parsed


def validate_fast(data: Graph, shapes: Graph) -> tuple[bool, list[str]]:
    """Validate ``data`` against ``shapes``. Returns (conforms, violations)
    where each violation is one human-readable line naming the focus node,
    the path, and the broken constraint."""
    parsed = _parse_shapes(shapes)
    violations: list[str] = []

    # one pass over the data: type index + per-subject predicate objects
    types: dict = defaultdict(set)
    by_subject: dict = defaultdict(lambda: defaultdict(list))
    for s, p, o in data:
        if p == RDF.type:
            types[s].add(o)
        by_subject[s][p].append(o)

    for entry in parsed:
        if entry["target_class"] is not None:
            focus = [s for s, ts in types.items()
                     if entry["target_class"] in ts]
        elif entry["target_subjects_of"] is not None:
            pred = entry["target_subjects_of"]
            focus = [s for s, po in by_subject.items() if pred in po]
        else:
            continue  # untargeted shape constrains nothing
        for node in focus:
            po = by_subject.get(node, {})
            for spec in entry["props"]:
                values = po.get(spec["path"], [])
                n = len(values)
                path = spec["path"]
                if "min" in spec and n < spec["min"]:
                    violations.append(
                        f"{node} {path}: {n} value(s) < minCount {spec['min']}")
                if "max" in spec and n > spec["max"]:
                    violations.append(
                        f"{node} {path}: {n} value(s) > maxCount {spec['max']}")
                if "datatype" in spec:
                    for v in values:
                        dt = v.datatype if isinstance(v, Literal) else None
                        # xsd:string is the implicit datatype of a plain literal
                        if dt is None and isinstance(v, Literal) \
                                and spec["datatype"] == XSD.string:
                            continue
                        if dt != spec["datatype"]:
                            violations.append(
                                f"{node} {path}: datatype {dt} != {spec['datatype']}")
                if "in" in spec:
                    for v in values:
                        if v not in spec["in"]:
                            violations.append(
                                f"{node} {path}: {v} not in allowed set")
                if "has_value" in spec and spec["has_value"] not in values:
                    violations.append(
                        f"{node} {path}: required value {spec['has_value']} absent")
                if "cls" in spec:
                    for v in values:
                        if spec["cls"] not in types.get(v, set()):
                            violations.append(
                                f"{node} {path}: {v} is not a {spec['cls']}")
                if "pattern" in spec:
                    for v in values:
                        if not spec["pattern"].search(str(v)):
                            violations.append(
                                f"{node} {path}: {str(v)[:60]!r} fails pattern "
                                f"{spec['pattern'].pattern}")
                if "min_inclusive" in spec:
                    for v in values:
                        try:
                            ok = v.toPython() >= spec["min_inclusive"]
                        except (AttributeError, TypeError):
                            ok = False
                        if not ok:
                            violations.append(
                                f"{node} {path}: {v} < minInclusive "
                                f"{spec['min_inclusive']}")
    return not violations, violations
