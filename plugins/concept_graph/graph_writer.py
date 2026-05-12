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
        cbml3:embeddingArtifact "concepts_embeddings.npz" .          # if computed

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
            # canonicalize, intersect with the global concept set.
            from .splitter import split_identifier
            from .concepts import canonicalize
            for c in l2_chunks:
                symbols: list[str] = []
                if c.get("symbol") and c["symbol"] != "<file>":
                    symbols.append(c["symbol"])
                if c.get("parent_symbol"):
                    symbols.append(c["parent_symbol"])
                concept_names: set[str] = set()
                for sym in symbols:
                    for tok in split_identifier(sym):
                        cn = canonicalize(tok)
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


def _add_prop(g: Graph, parent: URIRef, path: URIRef, *,
              datatype: URIRef | None = None,
              klass: URIRef | None = None,
              min_count: int | None = None,
              max_count: int | None = None,
              min_inclusive: int | None = None,
              pattern: str | None = None) -> None:
    key = f"{parent}|{path}|{datatype}|{klass}|{min_count}|{max_count}|{min_inclusive}|{pattern}"
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
