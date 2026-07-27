"""Streaming canonical JSON-LD writer (error-free-mapping E8).

The rdflib path holds the whole document plus a sorted copy in memory — the
step class that failed kernel-scale emits (flaws F9/F19). This writer reuses
the fast Turtle path's canonicalization spine (N-Triples → external
byte-sort) and emits the document node by node: peak memory is one subject
group, not the graph.

Byte contract: the output is exactly the legacy path's bytes —
``graph.serialize(format="json-ld", auto_compact=True)`` followed by the
documented canonical sort and ``json.dumps(indent=2, sort_keys=True)`` —
verified by byte-equality tests on fixture graphs
(tests/test_streaming_jsonld.py). Compaction rules replicated:

  * ``@context`` = every namespace bound on the graph;
  * IRIs compact against the longest matching namespace;
  * ``rdf:type`` becomes ``@type`` (string, or a sorted list);
  * plain literals are JSON strings; xsd:integer/boolean/double are native
    JSON; any other datatype is ``{"@type": ..., "@value": ...}``;
    language literals are ``{"@language": ..., "@value": ...}``;
  * multi-valued properties are arrays sorted by the canonical key
    (objects by @id, then scalars).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess

from pathlib import Path

from rdflib import Graph

_log = logging.getLogger(__name__)

_XSD = "http://www.w3.org/2001/XMLSchema#"
_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

_NT_LINE = re.compile(
    r"^<(?P<s>[^>]*)> <(?P<p>[^>]*)> "
    r"(?:<(?P<o_iri>[^>]*)>"
    r'|"(?P<o_lit>(?:[^"\\]|\\.)*)"'
    r"(?:\^\^<(?P<dt>[^>]*)>|@(?P<lang>[A-Za-z0-9-]+))?)"
    r" \.$"
)

_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t",
            "b": "\b", "f": "\f"}


def _unescape(lit: str) -> str:
    out: list[str] = []
    i, n = 0, len(lit)
    while i < n:
        c = lit[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        e = lit[i + 1]
        if e in _ESCAPES:
            out.append(_ESCAPES[e])
            i += 2
        elif e == "u":
            out.append(chr(int(lit[i + 2:i + 6], 16)))
            i += 6
        elif e == "U":
            out.append(chr(int(lit[i + 2:i + 10], 16)))
            i += 10
        else:  # unknown escape — keep verbatim, never crash the emit
            out.append(e)
            i += 2
    return "".join(out)


def _canonical_sort(node):
    if isinstance(node, dict):
        return {k: _canonical_sort(v) for k, v in sorted(node.items())}
    if isinstance(node, list):
        items = [_canonical_sort(x) for x in node]

        def key(x):
            if isinstance(x, dict):
                return (0, x.get("@id", ""), json.dumps(x, sort_keys=True))
            return (1, str(x))
        return sorted(items, key=key)
    return node


class _Compactor:
    def __init__(self, namespaces):
        # longest-prefix-first so nested namespaces compact correctly
        self.ns = sorted(((str(url), prefix) for prefix, url in namespaces),
                         key=lambda x: -len(x[0]))

    def compact(self, iri: str) -> str:
        for url, prefix in self.ns:
            if iri.startswith(url) and len(iri) > len(url):
                return f"{prefix}:{iri[len(url):]}"
        return iri


def _object_value(m: re.Match, compact) -> object:
    if m.group("o_iri") is not None:
        return {"@id": compact(m.group("o_iri"))}
    value = _unescape(m.group("o_lit"))
    dt = m.group("dt")
    lang = m.group("lang")
    if lang:
        return {"@language": lang, "@value": value}
    if dt is None or dt == _XSD + "string":
        return value
    if dt == _XSD + "integer":
        return int(value)
    if dt == _XSD + "boolean":
        return value == "true"
    if dt in (_XSD + "double", _XSD + "decimal"):
        return float(value)
    return {"@type": compact(dt), "@value": value}


def _node_from_group(subject_iri: str, preds: dict, compact) -> dict:
    node: dict = {"@id": compact(subject_iri)}
    for pred, objs in preds.items():
        if pred == _RDF_TYPE:
            types = sorted(o["@id"] for o in objs)
            node["@type"] = types[0] if len(types) == 1 else types
            continue
        key = compact(pred)
        node[key] = objs[0] if len(objs) == 1 else objs
    return _canonical_sort(node)


def _dump_shifted(obj, shift: str) -> str:
    """json.dumps(indent=2) with every continuation line shifted, matching
    the layout (and default ASCII escaping) json.dumps produces when the
    object nests at that depth."""
    return json.dumps(obj, indent=2, sort_keys=True).replace("\n", "\n" + shift)


def write_jsonld_streaming(graph: Graph, dest: Path) -> str:
    """Write ``graph`` to ``dest`` as canonical JSON-LD. Returns the engine
    used: ``"streaming"`` or ``"rdflib"`` (blank-node fallback — rdflib
    bnode labels are process-random and would break determinism through
    the N-Triples intermediate)."""
    from .fast_serializer import _has_bnodes  # same guard as the TTL path
    if _has_bnodes(graph):
        _log.warning("graph contains blank nodes — JSON-LD via rdflib fallback")
        return _write_jsonld_rdflib(graph, dest)

    # rdflib's JSON-LD serializer omits the reserved `xml` binding from the
    # context (it is not usable as a JSON-LD term); mirror that exactly.
    namespaces = [(p, u) for p, u in graph.namespaces() if p != "xml"]
    compact = _Compactor(namespaces).compact
    context = {prefix: str(url) for prefix, url in namespaces}

    nt_tmp = dest.with_name(dest.name + ".tmp.nt")
    nt_sorted = dest.with_name(dest.name + ".tmp.sorted.nt")
    try:
        graph.serialize(destination=str(nt_tmp), format="nt", encoding="utf-8")
        subprocess.run(
            ["sort", "-o", str(nt_sorted), "-T", str(dest.parent), str(nt_tmp)],
            check=True, env={**os.environ, "LC_ALL": "C"},
        )
        with open(nt_sorted, encoding="utf-8") as nt, \
                open(dest, "w", encoding="utf-8") as out:
            out.write('{\n  "@context": ')
            out.write(_dump_shifted(context, "  "))
            out.write(',\n  "@graph": [\n')

            current: str | None = None
            preds: dict[str, list] = {}
            first = True

            def flush():
                nonlocal first
                if current is None:
                    return
                node = _node_from_group(current, preds, compact)
                if not first:
                    out.write(",\n")
                out.write("    ")
                out.write(_dump_shifted(node, "    "))
                first = False

            for line in nt:
                line = line.rstrip("\n")
                if not line:
                    continue
                m = _NT_LINE.match(line)
                if m is None:
                    raise ValueError(f"unparseable N-Triples line: {line[:120]}")
                subj = m.group("s")
                if subj != current:
                    flush()
                    current, preds = subj, {}
                preds.setdefault(m.group("p"), []).append(
                    _object_value(m, compact))
            flush()
            out.write("\n  ]\n}" if current is not None else "]\n}")
            out.write("\n")
    finally:
        nt_tmp.unlink(missing_ok=True)
        nt_sorted.unlink(missing_ok=True)
    return "streaming"


def _write_jsonld_rdflib(graph: Graph, dest: Path) -> str:
    data = graph.serialize(format="json-ld", auto_compact=True,
                           indent=2, sort_keys=True)
    doc = _canonical_sort(json.loads(data))
    dest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return "rdflib"
