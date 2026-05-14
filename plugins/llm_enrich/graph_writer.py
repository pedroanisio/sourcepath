"""LlmGraphWriter + LlmShapes — emit cbml4: triples + SHACL shapes.

Step 4 fills in the body. The writer reads enrichment records that
the enricher (Step 3) stashed on ``ctx.scratch["llm:file_summary"]``
and emits one triple group per ``cbm:File``. The shape contributor
declares each predicate at ``maxCount 1`` with no ``minCount`` —
the optional cardinality the plan committed to so bundles without
LLM enrichment still SHACL-validate.

Triple shape (one cbm:File with file_summary):

    cbmi:file/<safe>
        cbml4:fileSummary           "…" ;
        cbml4:fileSummaryModel      "qwen2.5-coder:7b" ;
        cbml4:fileSummaryPromptSha  "<hex>" ;
        cbml4:fileSummaryGeneratedAt "2026-05-14T…"^^xsd:dateTime .

A file without an enrichment has none of these predicates — same
shape as today's pre-L4 bundles. SHACL conforms either way.

The ``cbml4`` prefix is bound only when at least one triple is
emitted. On an empty run (no scopes opted in, or Ollama unreachable),
this writer is a no-op and the inventory.ttl carries no ``@prefix
cbml4:`` line — preserving Step 1's back-compat anchor.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, cast

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from codebase_mapper.constants import CBML4, CBML4_NS
from codebase_mapper.rdf_emit import file_iri

if TYPE_CHECKING:
    from codebase_mapper.extensions import PipelineCtx


SH_NS = "http://www.w3.org/ns/shacl#"
SH = Namespace(SH_NS)

GRAPH_WRITER_NAME = "l4_30_graph"
SHAPES_NAME = "l4_30_shapes"


# ---------------------------------------------------------------------
# Graph writer
# ---------------------------------------------------------------------


class LlmGraphWriter:
    """Walks ctx.scratch enrichment buckets and emits cbml4: triples
    on the corresponding cbm:File subjects.

    Per-kind predicate naming convention: ``<kind>`` for the content,
    ``<kind>Model``, ``<kind>PromptSha``, ``<kind>GeneratedAt`` for the
    provenance fields. Step 5 will reuse this convention for the
    concept_description + schema_purpose kinds.
    """

    name = GRAPH_WRITER_NAME

    def contribute(self, g: Graph, ctx: "PipelineCtx") -> None:
        file_summaries = cast(
            dict, ctx.scratch.get("llm:file_summary", {})
        )
        if not file_summaries:
            # No enrichments → no triples → no prefix binding. This
            # preserves Step 1's byte-equality back-compat anchor when
            # the plugin is registered but no scope is opted in.
            return

        g.bind("cbml4", CBML4)

        # Sort by path for deterministic emission. RDF graphs are
        # unordered sets, but the turtle serializer follows insertion
        # order for subject-blocks in some configurations; sorting
        # keeps re-emits byte-identical when the underlying records
        # are byte-identical (the warm-cache determinism story).
        for path in sorted(file_summaries):
            rec = file_summaries[path]
            subject = file_iri(path)
            _add_file_summary_triples(g, subject, rec)


def _add_file_summary_triples(g: Graph, subject: URIRef, rec: dict) -> None:
    """Emit one (subject, cbml4:fileSummary*, value) group. ``rec`` is
    the dict produced by the enricher: ``{text, model, prompt_sha,
    target_sha, generated_at, was_cache_hit, …}``. Missing optional
    fields are silently skipped — the SHACL shape allows it."""
    text = rec.get("text")
    if not text:
        # Defensive: a record with no text shouldn't have been stashed,
        # but if it slipped through, don't emit a useless empty triple.
        return
    g.add((subject, CBML4.fileSummary, Literal(text)))

    model = rec.get("model")
    if model:
        g.add((subject, CBML4.fileSummaryModel, Literal(model)))

    prompt_sha = rec.get("prompt_sha")
    if prompt_sha:
        g.add((subject, CBML4.fileSummaryPromptSha, Literal(prompt_sha)))

    generated_at = rec.get("generated_at")
    if generated_at:
        g.add((
            subject, CBML4.fileSummaryGeneratedAt,
            Literal(generated_at, datatype=XSD.dateTime),
        ))


# ---------------------------------------------------------------------
# SHACL shapes
# ---------------------------------------------------------------------


class LlmShapes:
    """Optional-cardinality shape declarations for every cbml4: predicate.

    The shape targets cbm:File. Every predicate is ``maxCount 1`` with
    no ``minCount`` — files without an enrichment carry zero of these
    triples, files with one carry exactly one. This is the back-compat
    contract Commitment #7 spelled out: bundles built without L4 (or
    with L4 unable to reach Ollama) still validate against this shape.

    The shape contribution is unconditional — it lands in every bundle
    where the plugin is registered, even if no triples are emitted. A
    SHACL shape declaring 'this field is optional' is harmless on a
    graph that doesn't carry the field. The Step 1 back-compat
    verifier accommodates this: it compares shapes.shacl.ttl modulo
    the L4 shape entries when the plugin is registered. (Actually it
    asserts byte equality. We need to revisit.)
    """

    name = SHAPES_NAME

    def contribute(self, shapes: Graph) -> None:
        # Bind the cbml4 prefix on the shapes graph too, so the
        # generated shapes.shacl.ttl is readable.
        shapes.bind("cbml4", CBML4)

        # We need a stable CBM_NS reference to declare the target
        # class. Importing here keeps the writer's import surface
        # minimal at module load.
        from codebase_mapper.constants import CBM

        file_shape = URIRef(f"{CBML4_NS}LlmFileSummaryShape")
        shapes.add((file_shape, RDF.type, SH.NodeShape))
        shapes.add((file_shape, SH.targetClass, CBM.File))

        _add_optional_string(shapes, file_shape, CBML4.fileSummary)
        _add_optional_string(shapes, file_shape, CBML4.fileSummaryModel,
                             min_length=1)
        _add_optional_string(shapes, file_shape, CBML4.fileSummaryPromptSha,
                             pattern=r"^[a-f0-9]{64}$")
        _add_optional_datetime(shapes, file_shape, CBML4.fileSummaryGeneratedAt)


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
