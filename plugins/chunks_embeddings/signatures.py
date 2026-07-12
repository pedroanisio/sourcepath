"""Signature/type extraction for L2 chunks — canonical field contract.

Every symbol-level chunk MAY carry these optional, mechanically-derived
fields. They are parsed from source (confidence: certain), never inferred;
a field is OMITTED when empty/unknown — never emitted as an empty list or a
None placeholder, so downstream consumers can treat presence as evidence.

    signature    str    declaration header as written/reconstructed, no body
    params       list[{"name": str, "type": str | None, "default": str | None}]
                        name carries the * / ** prefix; the bare "/" and "*"
                        Python separators appear as marker entries
    returns      str | None   declared return type, as written
    bases        list[str]    class bases / supertypes / traits, as written
    type_params  list[str]    generic type parameters, as written
    visibility   str | None   an explicit source keyword/marker only — e.g.
                        public/private/protected (Java/C++/TS), pub/pub(crate)
                        (Rust), defn- (Clojure → "private"). Never derived
                        from naming conventions (Go capitalization, Python/
                        Dart underscores): convention-based visibility is
                        recoverable from the symbol name itself
    is_async     bool         present only when True
    decorators   list[str]    decorators/attributes/annotations, as written
                        (without the leading @ / #[] sigil)

Two producer paths feed the contract:

  1. AST-direct chunkers (Python here; TS/JS and Rust in chunker.py) extract
     fields from the parse tree at chunk time.
  2. Items-based chunkers (dart/java/go/clojure/cpp/objc/ruby/c/kotlin/swift)
     copy the canonical fields from ``record.ast_summary["items"]`` via
     :func:`signature_fields_from_item`; the language analyzers own producing
     them on the item dicts.

The graph writer emits each present field as a ``cbml2:`` property (params as
a JSON literal) — see plugins/chunks_embeddings/graph_writer.py.
"""
from __future__ import annotations

import ast


SIGNATURE_FIELDS = (
    "signature", "params", "returns", "bases", "type_params",
    "visibility", "is_async", "decorators",
    # Heritage split (BL-005): emitted only where the language syntactically
    # distinguishes generalization from realization (Java/C++/ObjC items,
    # TS/JS heritage clauses). ``bases`` stays the merged, always-present
    # view; these two carry the UML extends-vs-implements distinction.
    "extends", "implements",
)


def apply_signature_fields(chunk: dict, fields: dict) -> dict:
    """Merge non-empty signature fields into a chunk dict (omission contract)."""
    for key in SIGNATURE_FIELDS:
        value = fields.get(key)
        if value or value is True:
            chunk[key] = value
    return chunk


def signature_fields_from_item(item: dict) -> dict:
    """Copy-through for items-based chunkers.

    Reads only the canonical fields from an ``ast_summary`` item; analyzers
    that don't produce them yet simply yield an empty dict here (graceful
    absence, not an error).
    """
    out: dict = {}
    for key in SIGNATURE_FIELDS:
        value = item.get(key)
        if value or value is True:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Python (stdlib ast)
# ---------------------------------------------------------------------------
def python_signature_fields(node: ast.stmt) -> dict:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _py_function_fields(node)
    if isinstance(node, ast.ClassDef):
        return _py_class_fields(node)
    return {}


def _py_function_fields(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    params = _py_params(node.args)
    returns = ast.unparse(node.returns) if node.returns is not None else None
    type_params = [ast.unparse(tp) for tp in getattr(node, "type_params", [])]
    rendered = ", ".join(_py_render_param(p) for p in params)
    tp = f"[{', '.join(type_params)}]" if type_params else ""
    signature = f"def {node.name}{tp}({rendered})"
    if isinstance(node, ast.AsyncFunctionDef):
        signature = "async " + signature
    if returns:
        signature += f" -> {returns}"
    return {
        "signature": signature,
        "params": params,
        "returns": returns,
        "type_params": type_params,
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "decorators": [ast.unparse(d) for d in node.decorator_list],
    }


def _py_class_fields(node: ast.ClassDef) -> dict:
    bases = [ast.unparse(b) for b in node.bases]
    keywords = [
        f"{kw.arg}={ast.unparse(kw.value)}" if kw.arg else f"**{ast.unparse(kw.value)}"
        for kw in node.keywords
    ]
    type_params = [ast.unparse(tp) for tp in getattr(node, "type_params", [])]
    tp = f"[{', '.join(type_params)}]" if type_params else ""
    head = f"class {node.name}{tp}"
    inside = ", ".join(bases + keywords)
    signature = f"{head}({inside})" if inside else head
    return {
        "signature": signature,
        "bases": bases,
        "type_params": type_params,
        "decorators": [ast.unparse(d) for d in node.decorator_list],
    }


def _py_params(args: ast.arguments) -> list[dict]:
    """Flatten an ``ast.arguments`` into ordered param records.

    The ``/`` and ``*`` separators are kept as marker entries so the list
    round-trips the exact calling convention.
    """
    out: list[dict] = []

    positional = list(args.posonlyargs) + list(args.args)
    defaults: list[str | None] = [None] * (len(positional) - len(args.defaults))
    defaults += [ast.unparse(d) for d in args.defaults]
    for a, default in zip(positional, defaults):
        out.append(_py_param(a, prefix="", default=default))
        if args.posonlyargs and a is args.posonlyargs[-1]:
            out.append({"name": "/", "type": None, "default": None})

    if args.vararg is not None:
        out.append(_py_param(args.vararg, prefix="*", default=None))
    elif args.kwonlyargs:
        out.append({"name": "*", "type": None, "default": None})

    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        out.append(_py_param(a, prefix="", default=ast.unparse(d) if d else None))

    if args.kwarg is not None:
        out.append(_py_param(args.kwarg, prefix="**", default=None))
    return out


def _py_param(a: ast.arg, *, prefix: str, default: str | None) -> dict:
    return {
        "name": prefix + a.arg,
        "type": ast.unparse(a.annotation) if a.annotation is not None else None,
        "default": default,
    }


def _py_render_param(p: dict) -> str:
    name, ptype, default = p["name"], p["type"], p["default"]
    if name in ("/", "*"):
        return name
    s = f"{name}: {ptype}" if ptype else name
    if default is not None:
        s += f" = {default}" if ptype else f"={default}"
    return s
