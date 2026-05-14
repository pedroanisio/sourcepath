"""LlmGraphWriter + LlmShapes — emit cbml4: triples + SHACL shapes.

Step 5 extends Step 4 with two more enrichment kinds. The writer now
covers:

  - ``cbml4:fileSummary*``       on cbm:File subjects (Step 4)
  - ``cbml4:conceptDescription*`` on skos:Concept subjects (Step 5)
  - ``cbml4:schemaPurpose*``     on cbm:File subjects (Step 5)

Each kind follows the same four-predicate convention:
``<kind>``, ``<kind>Model``, ``<kind>PromptSha``, ``<kind>GeneratedAt``.
Predicates remain at maxCount 1 with no minCount, so a bundle that
opted into only one kind still SHACL-validates.

The ``cbml4`` prefix is bound only when at least one triple is
emitted; on a no-op run, inventory.ttl gains no ``@prefix cbml4:``
line and Step 1's back-compat anchor holds.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, cast

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from codebase_mapper.shared_kernel.constants import CBMI_NS, CBML4, CBML4_NS
from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import file_iri

# Concept subjects come from the L3 concept-graph plugin; we mirror its
# IRI scheme here rather than importing the plugin to keep dependency
# direction one-way (L4 reads L3's index payload, not L3's modules).
import urllib.parse as _urllib_parse


def _concept_iri(canonical_form: str) -> URIRef:
    safe = _urllib_parse.quote(canonical_form, safe="")
    return URIRef(f"{CBMI_NS}concept/{safe}")

if TYPE_CHECKING:
    from codebase_mapper.shared_kernel.extensions import PipelineCtx


SH_NS = "http://www.w3.org/ns/shacl#"
SH = Namespace(SH_NS)

GRAPH_WRITER_NAME = "l4_30_graph"
SHAPES_NAME = "l4_30_shapes"


# ---------------------------------------------------------------------
# Graph writer
# ---------------------------------------------------------------------


# Per-kind predicate sets. Each tuple is
# (content_predicate, model_predicate, prompt_sha_predicate, generated_at_predicate).
# The graph writer iterates this table to keep emission code uniform
# across the three kinds (Step 5+).
_FILE_SUMMARY_PREDICATES = (
    CBML4.fileSummary,
    CBML4.fileSummaryModel,
    CBML4.fileSummaryPromptSha,
    CBML4.fileSummaryGeneratedAt,
)
_CONCEPT_DESCRIPTION_PREDICATES = (
    CBML4.conceptDescription,
    CBML4.conceptDescriptionModel,
    CBML4.conceptDescriptionPromptSha,
    CBML4.conceptDescriptionGeneratedAt,
)
_SCHEMA_PURPOSE_PREDICATES = (
    CBML4.schemaPurpose,
    CBML4.schemaPurposeModel,
    CBML4.schemaPurposePromptSha,
    CBML4.schemaPurposeGeneratedAt,
)


class LlmGraphWriter:
    """Walks ctx.scratch enrichment buckets and emits cbml4: triples
    on the correct subjects per kind:

      file_summary         → cbm:File           (cbmi:file/<path>)
      concept_description  → skos:Concept       (cbmi:concept/<canon>)
      schema_purpose       → cbm:File           (cbmi:file/<path>)

    Per-kind predicate naming convention is
    ``<kind>``, ``<kind>Model``, ``<kind>PromptSha``,
    ``<kind>GeneratedAt`` — uniform across all kinds so future ones
    slot in with one table entry plus one writer-loop iteration.
    """

    name = GRAPH_WRITER_NAME

    def contribute(self, g: Graph, ctx: "PipelineCtx") -> None:
        file_summaries = cast(
            dict, ctx.scratch.get("llm:file_summary", {})
        )
        concept_descs = cast(
            dict, ctx.scratch.get("llm:concept_description", {})
        )
        schema_purposes = cast(
            dict, ctx.scratch.get("llm:schema_purpose", {})
        )

        if not (file_summaries or concept_descs or schema_purposes):
            # No enrichments → no triples → no prefix binding. This
            # preserves Step 1's byte-equality back-compat anchor when
            # the plugin is registered but no scope is opted in.
            return

        g.bind("cbml4", CBML4)

        # Deterministic iteration order so re-emits over warm caches
        # produce byte-identical turtle.
        for path in sorted(file_summaries):
            _add_triples(g, file_iri(path), file_summaries[path],
                         _FILE_SUMMARY_PREDICATES)

        for name in sorted(concept_descs):
            _add_triples(g, _concept_iri(name), concept_descs[name],
                         _CONCEPT_DESCRIPTION_PREDICATES)

        for path in sorted(schema_purposes):
            _add_triples(g, file_iri(path), schema_purposes[path],
                         _SCHEMA_PURPOSE_PREDICATES)


def _add_triples(g: Graph, subject: URIRef, rec: dict,
                 preds: tuple[URIRef, URIRef, URIRef, URIRef]) -> None:
    """Emit one (subject, cbml4:<kind>*, value) group. ``rec`` carries
    ``text``, ``model``, ``prompt_sha``, ``generated_at`` produced by
    either the enricher (Step 3) or the aggregator (Step 5)."""
    p_text, p_model, p_sha, p_dt = preds
    text = rec.get("text")
    if not text:
        return
    g.add((subject, p_text, Literal(text)))
    model = rec.get("model")
    if model:
        g.add((subject, p_model, Literal(model)))
    prompt_sha = rec.get("prompt_sha")
    if prompt_sha:
        g.add((subject, p_sha, Literal(prompt_sha)))
    generated_at = rec.get("generated_at")
    if generated_at:
        g.add((subject, p_dt,
               Literal(generated_at, datatype=XSD.dateTime)))


# ---------------------------------------------------------------------
# SHACL shapes
# ---------------------------------------------------------------------


class LlmShapes:
    """Optional-cardinality shape declarations for every cbml4: predicate.

    Two node shapes:

      ``LlmFileShape``    targets cbm:File. Declares fileSummary*
                          (Step 4) and schemaPurpose* (Step 5)
                          predicates as optional.
      ``LlmConceptShape`` targets skos:Concept. Declares
                          conceptDescription* predicates as optional.

    Every predicate is ``maxCount 1`` with no ``minCount`` — files /
    concepts without an enrichment carry zero of these triples, ones
    with an enrichment carry exactly one. This is the back-compat
    contract Commitment #7 spelled out: bundles built without L4 (or
    with L4 unable to reach Ollama, or with only one scope opted in)
    still validate against these shapes.

    Shape contribution is unconditional — these declarations land in
    every bundle where the plugin is registered, regardless of whether
    triples were emitted. Step 1's back-compat verifier compares
    shapes.shacl.ttl using isomorphism modulo the cbml4: entries.
    """

    name = SHAPES_NAME

    def contribute(self, shapes: Graph) -> None:
        # Bind the cbml4 prefix on the shapes graph for readability.
        shapes.bind("cbml4", CBML4)

        from codebase_mapper.shared_kernel.constants import CBM

        # --- LlmFileShape: file_summary + schema_purpose -----------
        file_shape = URIRef(f"{CBML4_NS}LlmFileShape")
        shapes.add((file_shape, RDF.type, SH.NodeShape))
        shapes.add((file_shape, SH.targetClass, CBM.File))

        _add_optional_string(shapes, file_shape, CBML4.fileSummary)
        _add_optional_string(shapes, file_shape, CBML4.fileSummaryModel,
                             min_length=1)
        _add_optional_string(shapes, file_shape, CBML4.fileSummaryPromptSha,
                             pattern=r"^[a-f0-9]{64}$")
        _add_optional_datetime(shapes, file_shape, CBML4.fileSummaryGeneratedAt)

        _add_optional_string(shapes, file_shape, CBML4.schemaPurpose)
        _add_optional_string(shapes, file_shape, CBML4.schemaPurposeModel,
                             min_length=1)
        _add_optional_string(shapes, file_shape, CBML4.schemaPurposePromptSha,
                             pattern=r"^[a-f0-9]{64}$")
        _add_optional_datetime(shapes, file_shape,
                               CBML4.schemaPurposeGeneratedAt)

        # --- LlmConceptShape: concept_description -------------------
        concept_shape = URIRef(f"{CBML4_NS}LlmConceptShape")
        shapes.add((concept_shape, RDF.type, SH.NodeShape))
        # SKOS concept class — match the L3 plugin's targetClass.
        SKOS = URIRef("http://www.w3.org/2004/02/skos/core#Concept")
        shapes.add((concept_shape, SH.targetClass, SKOS))

        _add_optional_string(shapes, concept_shape,
                             CBML4.conceptDescription)
        _add_optional_string(shapes, concept_shape,
                             CBML4.conceptDescriptionModel, min_length=1)
        _add_optional_string(shapes, concept_shape,
                             CBML4.conceptDescriptionPromptSha,
                             pattern=r"^[a-f0-9]{64}$")
        _add_optional_datetime(shapes, concept_shape,
                               CBML4.conceptDescriptionGeneratedAt)


def _add_optional_string(g: Graph, parent: URIRef, path: URIRef, *,
                         min_length: int | None = None,
                         pattern: str | None = None) -> None:
    """Add an ``sh:property`` block: maxCount 1, datatype xsd:string,
    optional minLength + pattern. No ``minCount`` — the predicate is
    optional, by design."""
    key = f"{parent}|{path}|str|{min_length}|{pattern}"
    p_iri = URIRef(f"{CBML4_NS}_ps_{hashlib.sha1(key.encode()).hexdigest()[:16]}")
    g.add((parent, SH.property, p_iri))
    g.add((p_iri, SH.path, path))
    g.add((p_iri, SH.datatype, XSD.string))
    g.add((p_iri, SH.maxCount, Literal(1)))
    if min_length is not None:
        g.add((p_iri, SH.minLength, Literal(min_length)))
    if pattern is not None:
        g.add((p_iri, SH.pattern, Literal(pattern)))


def _add_optional_datetime(g: Graph, parent: URIRef, path: URIRef) -> None:
    """Add an optional xsd:dateTime predicate (maxCount 1, no minCount)."""
    key = f"{parent}|{path}|dt"
    p_iri = URIRef(f"{CBML4_NS}_ps_{hashlib.sha1(key.encode()).hexdigest()[:16]}")
    g.add((parent, SH.property, p_iri))
    g.add((p_iri, SH.path, path))
    g.add((p_iri, SH.datatype, XSD.dateTime))
    g.add((p_iri, SH.maxCount, Literal(1)))
