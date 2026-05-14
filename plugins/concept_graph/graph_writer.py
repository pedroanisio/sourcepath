"""ConceptGraphWriter and ConceptShapes — SKOS emission + cross-layer edges.

Triple structure per concept:

    cbmi:concept/<safe_name> a skos:Concept ;
        skos:prefLabel "user"@en ;
        skos:altLabel "User"@en, "users"@en ;
        skos:related cbmi:concept/account, cbmi:concept/service ;
        cbml3:occurrenceCount 14 ;
        cbml3:fileCount 7 ;
        cbml3:composedOf cbmi:concept/user, cbmi:concept/service ;   # compound only
        cbml3:embeddingRow 4 ;
        cbml3:embeddingArtifact "concepts_embeddings.npz" ;          # if computed
        # Curated-vocab-only (absent when concept is not in the bundled vocab):
        cbml3:conceptKind "domain-primitive" ;
        cbml3:broaderCollection cbmi:collection/intent_first_ontology .

When at least one concept carries `kind`, the writer also emits one
``skos:Collection`` per encountered kind:

    cbmi:collection/intent_first_ontology a skos:Collection ;
        skos:prefLabel "intent-first ontology"@en ;
        cbml3:conceptKindBacking "domain-primitive" ;
        skos:member cbmi:concept/behavior, cbmi:concept/intent, ... .

Plus per-file :lexicalizes edges:

    cbmi:file/<safe> cbml3:lexicalizes cbmi:concept/user, ... .

And, if L2 chunks are present, per-chunk :lexicalizes edges anchored from
each chunk's `symbol`/`parent_symbol`/path-derived terms.

The contributor never modifies host triples; it only adds.
"""
from __future__ import annotations

import hashlib
import urllib.parse
from typing import cast

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

from codebase_mapper.constants import CBM_NS, CBMI_NS
from codebase_mapper.rdf_emit import file_iri

from codebase_mapper.extensions import PipelineCtx


CBML3_NS = "https://codebase-mapper.example.org/cbml3#"
SKOS_NS = "http://www.w3.org/2004/02/skos/core#"
SH_NS = "http://www.w3.org/ns/shacl#"
# We don't want to hard-import L2's namespace here, so spell it out.
CBML2_NS = "https://codebase-mapper.example.org/cbml2#"

CBML3 = Namespace(CBML3_NS)
SKOS = Namespace(SKOS_NS)
SH = Namespace(SH_NS)
CBM = Namespace(CBM_NS)
CBML2 = Namespace(CBML2_NS)


def concept_iri(canonical_form: str) -> URIRef:
    safe = urllib.parse.quote(canonical_form, safe="")
    return URIRef(f"{CBMI_NS}concept/{safe}")


def chunk_iri_for(chunk_id: str) -> URIRef:
    safe = urllib.parse.quote(chunk_id, safe="")
    return URIRef(f"{CBMI_NS}chunk/{safe}")


def collection_iri(collection_tail: str) -> URIRef:
    safe = urllib.parse.quote(collection_tail, safe="")
    return URIRef(f"{CBMI_NS}collection/{safe}")


# Closed set of legal `cbml3:conceptKind` values. SHACL enforces the same
# set via `sh:in`. Keep in sync with codebase_mapper.vocab.loader.
CONCEPT_KIND_LITERALS: tuple[str, ...] = (
    "domain-primitive",
    "structural-primitive",
    "relational-primitive",
)

# Human-friendly labels for each kind's backing collection. The collection
# tail itself is determined by the curated YAML's `broader:` block.
_KIND_LABELS: dict[str, str] = {
    "domain-primitive":     "intent-first ontology",
    "structural-primitive": "code structure",
    "relational-primitive": "code relations",
}


class ConceptGraphWriter:
    name = "l3_30_graph"

    def contribute(self, g: Graph, ctx: PipelineCtx) -> None:
        g.bind("cbml3", CBML3)
        g.bind("skos", SKOS)

        idx = cast(dict, ctx.indices.get("l3_20_concepts") or {})
        concepts: dict[str, dict] = idx.get("concepts", {})
        per_path: dict[str, list[str]] = idx.get("per_path_concepts", {})
        cooccurrence = idx.get("cooccurrence", [])

        # --- Concept nodes ---
        # Tracks which curated kinds appeared, so we can emit a
        # skos:Collection per kind after the per-concept loop. Maps
        # collection_tail -> (kind_literal, [member concept iris]).
        kind_collections: dict[str, tuple[str, list[URIRef]]] = {}

        for canon, meta in sorted(concepts.items()):
            ciri = concept_iri(canon)
            g.add((ciri, RDF.type, SKOS.Concept))
            g.add((ciri, SKOS.prefLabel, Literal(meta["label"], lang="en")))
            for alt in meta.get("alt_labels", []):
                if alt and alt != meta["label"]:
                    g.add((ciri, SKOS.altLabel, Literal(alt, lang="en")))
            g.add((ciri, CBML3.occurrenceCount,
                   Literal(meta["frequency"], datatype=XSD.integer)))
            g.add((ciri, CBML3.fileCount,
                   Literal(meta["file_count"], datatype=XSD.integer)))
            for comp in meta.get("components", []):
                if comp in concepts:
                    g.add((ciri, CBML3.composedOf, concept_iri(comp)))
            row = meta.get("embedding_row")
            if row is not None:
                g.add((ciri, CBML3.embeddingRow,
                       Literal(row, datatype=XSD.integer)))
                g.add((ciri, CBML3.embeddingArtifact,
                       Literal("concepts_embeddings.npz")))

            # Stage 3: curated-vocab-only typing. Both fields are optional —
            # only concepts matched against the bundled vocab carry them.
            # Stage 4 populates them; the writer is forward-compatible.
            kind = meta.get("kind")
            broader = meta.get("broader")
            if kind is not None:
                if kind not in CONCEPT_KIND_LITERALS:
                    # Loader should have already rejected this; defend
                    # in depth so a hand-edited bundle dict fails loudly.
                    raise ValueError(
                        f"concept {canon!r}: unknown conceptKind {kind!r}; "
                        f"expected one of {CONCEPT_KIND_LITERALS}"
                    )
                g.add((ciri, CBML3.conceptKind, Literal(kind)))
                if broader:
                    coll_iri = collection_iri(broader)
                    g.add((ciri, CBML3.broaderCollection, coll_iri))
                    bucket = kind_collections.setdefault(
                        broader, (kind, []),
                    )
                    # Same kind always for a given broader; guard anyway.
                    if bucket[0] != kind:
                        raise ValueError(
                            f"broader collection {broader!r} reused under "
                            f"conflicting kinds: {bucket[0]!r} vs {kind!r}"
                        )
                    bucket[1].append(ciri)

        # --- Per-kind skos:Collection nodes (only when populated) ---
        for tail, (kind, members) in sorted(kind_collections.items()):
            coll_iri = collection_iri(tail)
            g.add((coll_iri, RDF.type, SKOS.Collection))
            g.add((coll_iri, SKOS.prefLabel,
                   Literal(_KIND_LABELS.get(kind, tail), lang="en")))
            g.add((coll_iri, CBML3.conceptKindBacking, Literal(kind)))
            for m in members:
                g.add((coll_iri, SKOS.member, m))

        # --- skos:related from co-occurrence (symmetric, but RDF is
        # directed; we emit both directions to be lookup-friendly) ---
        for a, b, _count in cooccurrence:
            if a in concepts and b in concepts:
                g.add((concept_iri(a), SKOS.related, concept_iri(b)))
                g.add((concept_iri(b), SKOS.related, concept_iri(a)))

        # --- File-to-concept :lexicalizes edges ---
        for path, concept_list in sorted(per_path.items()):
            firi = file_iri(path)
            for cn in concept_list:
                if cn in concepts:
                    g.add((firi, CBML3.lexicalizes, concept_iri(cn)))

        # --- Chunk-to-concept edges, only if L2 chunks present ---
        l2_chunks = cast(list, ctx.indices.get("l2_10_chunks") or [])
        if l2_chunks:
            # Compute concepts per chunk from its symbol(s) + parent_symbol,
            # canonicalize, intersect with the global concept set. The
            # vocab must match the aggregator's so chunk-anchoring agrees
            # with file-anchoring on alias collapses; the aggregator
            # stashes its resolved vocab on ctx.scratch.
            from .splitter import split_identifier
            from .concepts import canonicalize
            vocab = ctx.scratch.get("l3:resolved_vocab")
            for c in l2_chunks:
                symbols: list[str] = []
                if c.get("symbol") and c["symbol"] != "<file>":
                    symbols.append(c["symbol"])
                if c.get("parent_symbol"):
                    symbols.append(c["parent_symbol"])
                concept_names: set[str] = set()
                for sym in symbols:
                    for tok in split_identifier(sym):
                        cn = canonicalize(tok, vocab)
                        if cn and cn in concepts:
                            concept_names.add(cn)
                if not concept_names:
                    continue
                ciri_chunk = chunk_iri_for(c["chunk_id"])
                for cn in sorted(concept_names):
                    g.add((ciri_chunk, CBML3.lexicalizes, concept_iri(cn)))


class ConceptShapes:
    name = "l3_30_shapes"

    def contribute(self, shapes: Graph) -> None:
        shapes.bind("cbml3", CBML3)
        shapes.bind("skos", SKOS)
        shapes.bind("sh", SH)

        concept_shape = URIRef(f"{CBML3_NS}ConceptShape")
        shapes.add((concept_shape, RDF.type, SH.NodeShape))
        shapes.add((concept_shape, SH.targetClass, SKOS.Concept))

        _add_prop(shapes, concept_shape, SKOS.prefLabel,
                  min_count=1, max_count=1)
        _add_prop(shapes, concept_shape, CBML3.occurrenceCount,
                  datatype=XSD.integer, min_inclusive=1,
                  min_count=1, max_count=1)
        _add_prop(shapes, concept_shape, CBML3.fileCount,
                  datatype=XSD.integer, min_inclusive=1,
                  min_count=1, max_count=1)
        _add_prop(shapes, concept_shape, CBML3.composedOf,
                  klass=SKOS.Concept)   # any number; targets must be Concept
        _add_prop(shapes, concept_shape, SKOS.related,
                  klass=SKOS.Concept)
        _add_prop(shapes, concept_shape, CBML3.embeddingRow,
                  datatype=XSD.integer, min_inclusive=0, max_count=1)
        # `cbml3:embeddingArtifact` is the filename of the matrix the
        # `embeddingRow` indexes into. Optional (only present when an
        # embedding row was computed), single free-form string literal.
        _add_prop(shapes, concept_shape, CBML3.embeddingArtifact,
                  datatype=XSD.string, max_count=1)

        # Stage 3: curated-vocab typing. Both predicates are optional
        # (max_count=1, no min) — concepts not in the bundled vocab are
        # still valid skos:Concept nodes.
        _add_prop(shapes, concept_shape, CBML3.conceptKind,
                  datatype=XSD.string, max_count=1,
                  in_values=CONCEPT_KIND_LITERALS)
        _add_prop(shapes, concept_shape, CBML3.broaderCollection,
                  klass=SKOS.Collection, max_count=1)

        # Stage 3: per-kind collection node shape. A collection is emitted
        # only when at least one concept carries a `kind`; this shape
        # validates its required predicates.
        collection_shape = URIRef(f"{CBML3_NS}KindCollectionShape")
        shapes.add((collection_shape, RDF.type, SH.NodeShape))
        shapes.add((collection_shape, SH.targetClass, SKOS.Collection))

        _add_prop(shapes, collection_shape, SKOS.prefLabel,
                  min_count=1, max_count=1)
        _add_prop(shapes, collection_shape, CBML3.conceptKindBacking,
                  datatype=XSD.string, min_count=1, max_count=1,
                  in_values=CONCEPT_KIND_LITERALS)
        _add_prop(shapes, collection_shape, SKOS.member,
                  klass=SKOS.Concept, min_count=1)

        # `cbml3:lexicalizes` connects cbm:File AND cbml2:Chunk subjects to
        # skos:Concept objects. We don't own either subject's NodeShape
        # (cbm:File lives in the host, cbml2:Chunk in the L2 plugin), so
        # target via sh:targetSubjectsOf — every node that has *any*
        # `cbml3:lexicalizes` edge must point to a skos:Concept. This
        # closes the gap without cross-plugin shape ownership.
        lex_shape = URIRef(f"{CBML3_NS}LexicalizesShape")
        shapes.add((lex_shape, RDF.type, SH.NodeShape))
        shapes.add((lex_shape, URIRef(SH_NS + "targetSubjectsOf"),
                    CBML3.lexicalizes))
        _add_prop(shapes, lex_shape, CBML3.lexicalizes, klass=SKOS.Concept)


def _add_prop(g: Graph, parent: URIRef, path: URIRef, *,
              datatype: URIRef | None = None,
              klass: URIRef | None = None,
              min_count: int | None = None,
              max_count: int | None = None,
              min_inclusive: int | None = None,
              pattern: str | None = None,
              in_values: tuple[str, ...] | None = None) -> None:
    key = (f"{parent}|{path}|{datatype}|{klass}|{min_count}|{max_count}"
           f"|{min_inclusive}|{pattern}|{in_values}")
    p_iri = URIRef(f"{CBML3_NS}_ps_{hashlib.sha1(key.encode()).hexdigest()[:16]}")
    g.add((parent, SH.property, p_iri))
    g.add((p_iri, SH.path, path))
    if datatype is not None:
        g.add((p_iri, SH.datatype, datatype))
    if klass is not None:
        g.add((p_iri, SH["class"], klass))
    if min_count is not None:
        g.add((p_iri, SH.minCount, Literal(min_count)))
    if max_count is not None:
        g.add((p_iri, SH.maxCount, Literal(max_count)))
    if min_inclusive is not None:
        g.add((p_iri, SH.minInclusive, Literal(min_inclusive)))
    if pattern is not None:
        g.add((p_iri, SH.pattern, Literal(pattern)))
    if in_values is not None:
        # `sh:in` takes an RDF list. Build it as a fresh blank-list head.
        from rdflib import BNode
        from rdflib.namespace import RDF as _RDF
        head: URIRef | BNode = BNode()
        g.add((p_iri, SH["in"], head))
        nodes: list[URIRef | BNode] = [head]
        for _ in range(len(in_values) - 1):
            nodes.append(BNode())
        for i, val in enumerate(in_values):
            g.add((nodes[i], _RDF.first, Literal(val)))
            if i + 1 < len(in_values):
                g.add((nodes[i], _RDF.rest, nodes[i + 1]))
            else:
                g.add((nodes[i], _RDF.rest, _RDF.nil))
