"""Canonical SHACL shape model — one Pydantic spec, one renderer.

``shapes.shacl.ttl`` previously had five sources of truth: an imperative
constructor in the RDF emitter plus four per-plugin ``_add_prop`` variants,
each with its own property-shape hashing scheme. This module replaces all
of them. The emitter and every plugin shape contributor now *declare* their
shapes as :class:`NodeShapeSpec` / :class:`PropertySpec` instances —
validated at construction time — and :func:`render_shapes` is the single
code path that turns specs into RDF.

Renderer guarantees:

- **Deterministic naming.** Anonymous property shapes get
  ``<ns>_ps_<sha1(shape-iri + canonical spec dump)[:16]>`` IRIs; ``sh:in``
  lists get named chain nodes derived from their property shape. No BNodes
  are emitted, so serialized output is byte-stable across runs (the legacy
  concept-graph lists were fresh BNodes and churned every emit).
- **Human-named nodes survive.** Specs may pin explicit local names
  (``name=``, ``list_name=``) for the property shapes and enum lists that
  reports and humans grep for (``_typeProp``, ``_kindList``, …).

Guarded by ``tests/test_shacl_spec.py`` (semantic equality against the
pre-refactor golden snapshot + byte-determinism) and, transitively, by
``tests/verify_shape_coverage.py`` and the emit-time SHACL self-check.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

from pydantic import BaseModel, ConfigDict, model_validator
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF

SH_NS = "http://www.w3.org/ns/shacl#"


def _sh(term: str) -> URIRef:
    return URIRef(SH_NS + term)


class PropertySpec(BaseModel):
    """One ``sh:property`` block: a path plus its constraint set.

    IRI-valued fields (``path``, ``datatype``, ``klass``, ``has_value``,
    ``in_iris`` members) are absolute IRI strings; ``in_literals`` members
    become plain string literals. ``name``/``list_name`` pin the local name
    of the property-shape node / ``sh:in`` list head within the parent
    shape's namespace; when absent the renderer derives deterministic
    ``_ps_``-prefixed names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    name: str | None = None
    datatype: str | None = None
    klass: str | None = None
    min_count: int | None = None
    max_count: int | None = None
    min_inclusive: int | None = None
    min_length: int | None = None
    pattern: str | None = None
    has_value: str | None = None
    in_literals: tuple[str, ...] | None = None
    in_iris: tuple[str, ...] | None = None
    list_name: str | None = None

    @model_validator(mode="after")
    def _validate_sh_in(self) -> "PropertySpec":
        if self.in_literals is not None and self.in_iris is not None:
            raise ValueError(
                "sh:in takes one value space: in_literals or in_iris")
        if self.list_name is not None and (
                self.in_literals is None and self.in_iris is None):
            raise ValueError("list_name given without an sh:in value list")
        return self


class NodeShapeSpec(BaseModel):
    """One ``sh:NodeShape``: an IRI, exactly one target, its properties."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    iri: str
    target_class: str | None = None
    target_subjects_of: str | None = None
    properties: tuple[PropertySpec, ...] = ()

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "NodeShapeSpec":
        if (self.target_class is None) == (self.target_subjects_of is None):
            raise ValueError(
                "exactly one of target_class / target_subjects_of required")
        return self


def _namespace_of(iri: str) -> str:
    """The namespace prefix of an absolute IRI (through its last # or /)."""
    return iri[: max(iri.rfind("#"), iri.rfind("/")) + 1]


def _prop_iri(shape: NodeShapeSpec, prop: PropertySpec) -> URIRef:
    ns = _namespace_of(shape.iri)
    if prop.name is not None:
        return URIRef(f"{ns}{prop.name}")
    dump = prop.model_dump(exclude_none=True)
    key = f"{shape.iri}|" + "|".join(
        f"{k}={dump[k]}" for k in sorted(dump))
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return URIRef(f"{ns}_ps_{digest}")


def _render_list(g: Graph, head: URIRef,
                 members: tuple[URIRef | Literal, ...]) -> None:
    """An RDF list with named, deterministic chain nodes (no BNodes)."""
    nodes: list[URIRef] = [head] + [
        URIRef(f"{head}-{i}") for i in range(1, len(members))
    ]
    for i, member in enumerate(members):
        g.add((nodes[i], RDF.first, member))
        rest = nodes[i + 1] if i + 1 < len(members) else RDF.nil
        g.add((nodes[i], RDF.rest, rest))


def render_shapes(g: Graph, specs: Iterable[NodeShapeSpec], *,
                  bind: dict[str, str] | None = None) -> Graph:
    """Render every spec into ``g``; the only spec→RDF code path."""
    g.bind("sh", SH_NS)
    for prefix, ns in (bind or {}).items():
        g.bind(prefix, ns)

    for shape in specs:
        s = URIRef(shape.iri)
        g.add((s, RDF.type, _sh("NodeShape")))
        if shape.target_class is not None:
            g.add((s, _sh("targetClass"), URIRef(shape.target_class)))
        else:
            assert shape.target_subjects_of is not None  # model invariant
            g.add((s, _sh("targetSubjectsOf"),
                   URIRef(shape.target_subjects_of)))

        for prop in shape.properties:
            p = _prop_iri(shape, prop)
            g.add((s, _sh("property"), p))
            g.add((p, _sh("path"), URIRef(prop.path)))
            if prop.datatype is not None:
                g.add((p, _sh("datatype"), URIRef(prop.datatype)))
            if prop.klass is not None:
                g.add((p, _sh("class"), URIRef(prop.klass)))
            if prop.min_count is not None:
                g.add((p, _sh("minCount"), Literal(prop.min_count)))
            if prop.max_count is not None:
                g.add((p, _sh("maxCount"), Literal(prop.max_count)))
            if prop.min_inclusive is not None:
                g.add((p, _sh("minInclusive"), Literal(prop.min_inclusive)))
            if prop.min_length is not None:
                g.add((p, _sh("minLength"), Literal(prop.min_length)))
            if prop.pattern is not None:
                g.add((p, _sh("pattern"), Literal(prop.pattern)))
            if prop.has_value is not None:
                g.add((p, _sh("hasValue"), URIRef(prop.has_value)))
            members: tuple[URIRef | Literal, ...] | None = None
            if prop.in_literals is not None:
                members = tuple(Literal(v) for v in prop.in_literals)
            elif prop.in_iris is not None:
                members = tuple(URIRef(v) for v in prop.in_iris)
            if members is not None:
                ns = _namespace_of(shape.iri)
                head = (URIRef(f"{ns}{prop.list_name}")
                        if prop.list_name is not None
                        else URIRef(f"{p}_list"))
                g.add((p, _sh("in"), head))
                _render_list(g, head, members)
    return g
