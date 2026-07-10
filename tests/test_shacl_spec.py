"""Canonical SHACL spec (drift refactor): one Pydantic model, one renderer.

The emitted ``shapes.shacl.ttl`` previously had five sources of truth — an
imperative constructor in the emitter plus four per-plugin ``_add_prop``
variants with mutually incompatible property-shape naming. This suite pins
the refactor to declarative ``NodeShapeSpec`` / ``PropertySpec`` models
(``codebase_mapper/shared_kernel/shacl_spec.py``) rendered by a single
function:

- semantic equality against ``tests/fixtures/shapes_golden.ttl`` — a
  snapshot of the pre-refactor graph (core + all four plugin contributors).
  Comparison is by constraint content, not node names: property-shape IRIs
  and ``sh:in`` list nodes are anonymous infrastructure and may differ;
- byte-determinism: rendering twice yields identical Turtle (the legacy
  concept-graph lists used fresh BNodes, so output churned per run);
- the Pydantic layer actually validates: malformed specs are rejected at
  construction time, not discovered as bad RDF downstream.

Run: uv run python -m pytest tests/test_shacl_spec.py
"""
from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "shapes_golden.ttl"

SH = "http://www.w3.org/ns/shacl#"


def constraint_model(g: Graph) -> dict[str, tuple]:
    """Reduce a shapes graph to its semantic content.

    For every named ``sh:NodeShape``: its targets, and for each
    ``sh:property`` the full set of constraint pairs. ``sh:in`` lists are
    resolved to their ordered member tuple so list-node naming (BNode vs
    deterministic IRI) cannot affect equality. Literals compare via n3()
    so datatypes stay significant.
    """
    shapes: dict[str, tuple] = {}
    for s in g.subjects(RDF.type, URIRef(SH + "NodeShape")):
        targets = tuple(sorted(
            (str(p), str(o))
            for p, o in g.predicate_objects(s)
            if str(p) in (SH + "targetClass", SH + "targetSubjectsOf")
        ))
        props = set()
        for ps in g.objects(s, URIRef(SH + "property")):
            constraints = []
            for p, o in g.predicate_objects(ps):
                if str(p) == SH + "in":
                    members = tuple(
                        m.n3() if isinstance(m, Literal) else str(m)
                        for m in Collection(g, o)
                    )
                    constraints.append(("sh:in", members))
                elif isinstance(o, Literal):
                    constraints.append((str(p), o.n3()))
                else:
                    constraints.append((str(p), str(o)))
            props.add(frozenset(constraints))
        shapes[str(s)] = (targets, frozenset(props))
    return shapes


def build_full_spec_graph() -> Graph:
    """Render core + all four plugin shape tiers, as emit_bundle would."""
    from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import (
        build_shacl_graph,
    )
    from plugins.chunks_embeddings.graph_writer import ChunkShapes
    from plugins.concept_graph.graph_writer import ConceptShapes
    from plugins.llm_enrich.graph_writer import LlmShapes
    from plugins.symbol_xrefs.graph_writer import XrefShapes

    g = build_shacl_graph()
    for contributor in (ChunkShapes(), XrefShapes(), ConceptShapes(),
                        LlmShapes()):
        contributor.contribute(g)
    return g


def test_spec_graph_semantically_equals_golden():
    golden = Graph()
    golden.parse(str(GOLDEN), format="turtle")
    current = build_full_spec_graph()

    want = constraint_model(golden)
    got = constraint_model(current)

    assert set(got) == set(want), (
        f"node-shape set drifted: only-golden={sorted(set(want) - set(got))} "
        f"only-current={sorted(set(got) - set(want))}"
    )
    for shape_iri in sorted(want):
        w_targets, w_props = want[shape_iri]
        g_targets, g_props = got[shape_iri]
        assert g_targets == w_targets, f"{shape_iri}: targets drifted"
        assert g_props == w_props, (
            f"{shape_iri}: constraints drifted:\n"
            f"  only-golden={sorted(map(sorted, w_props - g_props))}\n"
            f"  only-current={sorted(map(sorted, g_props - w_props))}"
        )


def test_render_is_byte_deterministic():
    a = build_full_spec_graph().serialize(format="turtle")
    b = build_full_spec_graph().serialize(format="turtle")
    assert a == b, "shapes rendering must be byte-stable across runs"


def test_property_spec_rejects_unknown_fields():
    from codebase_mapper.shared_kernel.shacl_spec import PropertySpec

    with pytest.raises(Exception):
        PropertySpec(path="https://example.org/p", dataype="typo")


def test_node_shape_requires_exactly_one_target():
    from codebase_mapper.shared_kernel.shacl_spec import (
        NodeShapeSpec, PropertySpec,
    )

    prop = PropertySpec(path="https://example.org/p")
    with pytest.raises(Exception):
        NodeShapeSpec(iri="https://example.org/S", properties=(prop,))
    with pytest.raises(Exception):
        NodeShapeSpec(
            iri="https://example.org/S",
            target_class="https://example.org/C",
            target_subjects_of="https://example.org/p",
            properties=(prop,),
        )


def test_sh_in_value_spaces_are_exclusive():
    from codebase_mapper.shared_kernel.shacl_spec import PropertySpec

    with pytest.raises(Exception):
        PropertySpec(
            path="https://example.org/p",
            in_literals=("a",),
            in_iris=("https://example.org/A",),
        )
