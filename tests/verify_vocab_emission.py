#!/usr/bin/env python3
"""verify_vocab_emission.py — Stage 3 acceptance test for L3 vocab RDF.

The Stage-4 plugin wiring is not in place yet, so we can't exercise this
end-to-end via the real pipeline. Instead, we synthesize a `concepts`
index with `kind` + `broader` fields set on a subset of entries, drive
ConceptGraphWriter directly, and assert:

  1. Concepts WITH `kind`:
     - emit cbml3:conceptKind with the literal value
     - emit cbml3:broaderCollection pointing at the right skos:Collection
  2. Concepts WITHOUT `kind`:
     - do NOT emit cbml3:conceptKind nor cbml3:broaderCollection
     - remain valid skos:Concept nodes (back-compat with pre-vocab bundles)
  3. Exactly one skos:Collection is emitted per encountered kind, with:
     - rdf:type skos:Collection
     - skos:prefLabel (lang="en")
     - cbml3:conceptKindBacking matching the kind
     - skos:member for every concept of that kind
  4. The shapes graph contributed by ConceptShapes SHACL-validates the
     emitted concept+collection graph.
  5. A concept carrying an unknown `kind` value raises ValueError at
     emission time (defense in depth — loader catches it earlier).
  6. Pre-vocab default path: when no concept carries `kind`, no
     skos:Collection nodes and no cbml3:conceptKind triples appear.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass, field
from typing import cast

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF
from pyshacl import validate as shacl_validate

from plugins.concept_graph.graph_writer import (
    CBML3,
    CBML3_NS,
    CONCEPT_KIND_LITERALS,
    ConceptGraphWriter,
    ConceptShapes,
    SKOS,
    collection_iri,
    concept_iri,
)


@dataclass
class _StubCtx:
    """Minimal stand-in for PipelineCtx. Only `indices` is read by the
    writer when we omit L2 (no chunks)."""
    indices: dict = field(default_factory=dict)


def _fixture_concepts(with_kind: bool) -> dict:
    """Three concepts, two of them carrying `kind`+`broader` when requested.

    `behavior` is the canonical positive case; `intent` shares its kind so
    we exercise the multi-member collection path; `frobnicator` is the
    uncurated tail that must keep validating as a plain skos:Concept.
    """
    base = {
        "behavior": {
            "label": "behavior", "alt_labels": ["Behavior", "behaviour"],
            "components": [], "frequency": 5, "file_count": 3,
            "embedding_row": None,
        },
        "intent": {
            "label": "intent", "alt_labels": [],
            "components": [], "frequency": 3, "file_count": 2,
            "embedding_row": None,
        },
        "frobnicator": {
            "label": "frobnicator", "alt_labels": [],
            "components": [], "frequency": 1, "file_count": 1,
            "embedding_row": None,
        },
    }
    if with_kind:
        base["behavior"]["kind"] = "domain-primitive"
        base["behavior"]["broader"] = "intent_first_ontology"
        base["intent"]["kind"] = "domain-primitive"
        base["intent"]["broader"] = "intent_first_ontology"
        # frobnicator stays unkinded
    return {
        "concepts": base,
        "per_path_concepts": {},
        "cooccurrence": [],
        "concept_embeddings": None,
        "concept_embedding_ids": None,
    }


def _emit(with_kind: bool) -> Graph:
    g = Graph()
    ctx = _StubCtx(indices={"l3_20_concepts": _fixture_concepts(with_kind)})
    ConceptGraphWriter().contribute(g, cast(object, ctx))  # type: ignore[arg-type]
    return g


def _shapes_graph() -> Graph:
    g = Graph()
    ConceptShapes().contribute(g)
    return g


def test_kind_triples_emitted() -> None:
    g = _emit(with_kind=True)
    b_iri = concept_iri("behavior")
    coll_iri = collection_iri("intent_first_ontology")

    assert (b_iri, CBML3.conceptKind, Literal("domain-primitive")) in g
    assert (b_iri, CBML3.broaderCollection, coll_iri) in g

    i_iri = concept_iri("intent")
    assert (i_iri, CBML3.conceptKind, Literal("domain-primitive")) in g
    assert (i_iri, CBML3.broaderCollection, coll_iri) in g


def test_unkinded_concept_has_no_typing_triples() -> None:
    g = _emit(with_kind=True)
    f_iri = concept_iri("frobnicator")
    # It must still be a skos:Concept.
    assert (f_iri, RDF.type, SKOS.Concept) in g
    # But carry no kind/broader.
    kinds = list(g.objects(f_iri, CBML3.conceptKind))
    broaders = list(g.objects(f_iri, CBML3.broaderCollection))
    assert kinds == [], f"unkinded concept got conceptKind: {kinds}"
    assert broaders == [], (
        f"unkinded concept got broaderCollection: {broaders}"
    )


def test_collection_emitted_once_per_kind() -> None:
    g = _emit(with_kind=True)
    coll_iri = collection_iri("intent_first_ontology")
    assert (coll_iri, RDF.type, SKOS.Collection) in g
    # prefLabel present with the human-readable kind label.
    labels = list(g.objects(coll_iri, SKOS.prefLabel))
    assert len(labels) == 1
    assert str(labels[0]) == "intent-first ontology"
    # conceptKindBacking matches.
    assert (coll_iri, CBML3.conceptKindBacking,
            Literal("domain-primitive")) in g
    # Both kinded concepts are members; frobnicator is not.
    members = set(g.objects(coll_iri, SKOS.member))
    assert members == {concept_iri("behavior"), concept_iri("intent")}, (
        f"unexpected members: {members}"
    )
    # No second collection for kinds that didn't appear.
    other_colls = [s for s in g.subjects(RDF.type, SKOS.Collection)
                   if s != coll_iri]
    assert not other_colls, f"unexpected extra collections: {other_colls}"


def test_unkinded_default_path_emits_no_typing() -> None:
    """Bundles built before Stage 4 (no kinds in concept dicts) must
    behave exactly like today: no conceptKind, no broaderCollection,
    no skos:Collection nodes."""
    g = _emit(with_kind=False)
    kinds = list(g.subjects(CBML3.conceptKind, None))
    broaders = list(g.subjects(CBML3.broaderCollection, None))
    colls = list(g.subjects(RDF.type, SKOS.Collection))
    assert not kinds, f"unexpected conceptKind subjects: {kinds}"
    assert not broaders, f"unexpected broaderCollection subjects: {broaders}"
    assert not colls, f"unexpected skos:Collection nodes: {colls}"


def test_shacl_validates_kinded_emission() -> None:
    data = _emit(with_kind=True)
    shapes = _shapes_graph()
    conforms, _, report = shacl_validate(
        data, shacl_graph=shapes, inference="none",
        meta_shacl=False, advanced=False, debug=False,
    )
    assert conforms, f"SHACL violations:\n{report}"


def test_shacl_rejects_unknown_kind_value() -> None:
    """If a bad value somehow reached the graph (e.g., a future bug
    bypasses the writer's guard), the SHACL `sh:in` constraint must
    catch it."""
    g = Graph()
    b_iri = concept_iri("rogue")
    g.add((b_iri, RDF.type, SKOS.Concept))
    g.add((b_iri, SKOS.prefLabel, Literal("rogue", lang="en")))
    # Satisfy the required counts so the only failure is `sh:in`.
    g.add((b_iri, CBML3.occurrenceCount,
           Literal(1, datatype=URIRef("http://www.w3.org/2001/XMLSchema#integer"))))
    g.add((b_iri, CBML3.fileCount,
           Literal(1, datatype=URIRef("http://www.w3.org/2001/XMLSchema#integer"))))
    g.add((b_iri, CBML3.conceptKind, Literal("not-a-real-kind")))

    shapes = _shapes_graph()
    conforms, _, report = shacl_validate(
        g, shacl_graph=shapes, inference="none",
        meta_shacl=False, advanced=False, debug=False,
    )
    assert not conforms, "SHACL should have rejected the rogue kind value"
    assert "conceptKind" in report or "not-a-real-kind" in report, (
        f"expected violation to mention conceptKind/value; got:\n{report}"
    )


def test_writer_rejects_unknown_kind_value() -> None:
    """Defense in depth: even before SHACL runs, the writer raises."""
    bad = _fixture_concepts(with_kind=True)
    bad["concepts"]["behavior"]["kind"] = "bogus-kind"
    g = Graph()
    ctx = _StubCtx(indices={"l3_20_concepts": bad})
    try:
        ConceptGraphWriter().contribute(g, cast(object, ctx))  # type: ignore[arg-type]
    except ValueError as e:
        assert "bogus-kind" in str(e), str(e)
        return
    raise AssertionError("writer should have raised on unknown kind")


def test_kind_literal_set_matches_loader() -> None:
    """The writer's allowed-values tuple and the loader's accepted set
    must stay in lockstep. (If you add a fourth kind to one, you must
    add it to the other.)"""
    from codebase_mapper.vocab.loader import _CONCEPT_KINDS  # type: ignore
    assert set(CONCEPT_KIND_LITERALS) == set(_CONCEPT_KINDS), (
        f"drift: writer={set(CONCEPT_KIND_LITERALS)} "
        f"loader={set(_CONCEPT_KINDS)}"
    )


def main() -> int:
    tests = [
        test_kind_triples_emitted,
        test_unkinded_concept_has_no_typing_triples,
        test_collection_emitted_once_per_kind,
        test_unkinded_default_path_emits_no_typing,
        test_shacl_validates_kinded_emission,
        test_shacl_rejects_unknown_kind_value,
        test_writer_rejects_unknown_kind_value,
        test_kind_literal_set_matches_loader,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    if failures:
        print(f"\n{failures}/{len(tests)} test(s) failed")
        return 1
    print(f"\n{len(tests)} test(s) passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
