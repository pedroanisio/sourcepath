"""TDD spec — Clojure signature/type extraction on ast_summary items.

Contract under test (see plugins/chunks_embeddings/signatures.py): the
Clojure analyzer enriches each ``items`` entry with the optional canonical
signature fields, which the items-based chunker copies onto L2 chunks via
``signature_fields_from_item``. All values are parsed from source by the
self-contained s-expression reader (no tree-sitter), never inferred, and
OMITTED when empty/unknown — never emitted as placeholders.

Clojure mapping conventions pinned here:

    signature    header form, no body, whitespace-collapsed to one line —
                 e.g. "(defn fetch [url opts])". MULTI-ARITY functions list
                 every arity vector wrapped in parens, in source order:
                 "(defn fetch ([url]) ([url opts]))".
    params       from the argument vector, names as written; a rest arg is a
                 single entry named "& args"; destructuring forms keep their
                 source text as the name. type is None unless a ^Type hint
                 precedes the param (then the hint text, without the ^);
                 default is always None (Clojure has no default args).
                 MULTI-ARITY: params come from the arity vector with the
                 MOST parameters.
    returns      only when a ^Type hint directly precedes the single-arity
                 arg vector; omitted for multi-arity functions.
    visibility   "private" for defn- or a ^:private marker — explicit source
                 markers only; else omitted.
    bases        defrecord/deftype only: implemented protocols/interfaces,
                 as written.
    type_params / is_async / decorators — never produced for Clojure.

Run: python -m pytest tests/test_signatures_clojure.py
"""
from __future__ import annotations

from types import SimpleNamespace

from codebase_mapper.inspection.languages.clojure import extract_clojure_ast_summary
from plugins.chunks_embeddings.chunker import _chunk_clojure

ABSENT_WHEN_EMPTY = ("signature", "params", "returns", "bases", "type_params",
                     "visibility", "is_async", "decorators")


def _items_by_name(src: bytes) -> dict[str, dict]:
    summary, errors = extract_clojure_ast_summary(src, "core.clj")
    assert summary is not None and errors == []
    return {it["name"]: it for it in summary["items"]}


# ---------------------------------------------------------------------------
# defn / defn- / defmacro
# ---------------------------------------------------------------------------
def test_plain_defn_signature_and_params():
    src = (
        b"(defn greet\n"
        b"  \"Say hello.\"\n"
        b"  [name greeting]\n"
        b"  (str greeting \" \" name))\n"
    )
    it = _items_by_name(src)["greet"]
    assert it["signature"] == "(defn greet [name greeting])"
    assert it["params"] == [
        {"name": "name", "type": None, "default": None},
        {"name": "greeting", "type": None, "default": None},
    ]
    for absent in ("returns", "bases", "type_params", "visibility",
                   "is_async", "decorators"):
        assert absent not in it, f"{absent} must be omitted when empty"


def test_defn_existing_item_fields_unchanged():
    src = b"(defn greet [name] name)\n"
    it = _items_by_name(src)["greet"]
    assert it["kind"] == "function"
    assert it["parent"] is None
    assert it["line_start"] == 1 and it["line_end"] == 1
    assert src[it["byte_start"]:it["byte_end"]] == b"(defn greet [name] name)"


def test_defn_private_visibility():
    src = b"(defn- helper [x] (inc x))\n"
    it = _items_by_name(src)["helper"]
    assert it["visibility"] == "private"
    assert it["signature"] == "(defn- helper [x])"


def test_defn_caret_private_metadata_visibility():
    src = b"(defn ^:private hidden [x] x)\n"
    it = _items_by_name(src)["hidden"]
    assert it["visibility"] == "private"
    assert it["signature"] == "(defn hidden [x])"


def test_multi_arity_defn_lists_every_arity_and_takes_widest_params():
    """Multi-arity convention: ``signature`` lists every arity vector in
    source order, each wrapped in parens; ``params`` come from the arity
    with the MOST parameters."""
    src = (
        b"(defn fetch\n"
        b"  ([url] (fetch url {}))\n"
        b"  ([url opts] (http-get url opts)))\n"
    )
    it = _items_by_name(src)["fetch"]
    assert it["signature"] == "(defn fetch ([url]) ([url opts]))"
    assert it["params"] == [
        {"name": "url", "type": None, "default": None},
        {"name": "opts", "type": None, "default": None},
    ]
    assert "returns" not in it


def test_rest_args_merge_into_single_param():
    src = b"(defn total [x & xs] (apply + x xs))\n"
    it = _items_by_name(src)["total"]
    assert it["signature"] == "(defn total [x & xs])"
    assert it["params"] == [
        {"name": "x", "type": None, "default": None},
        {"name": "& xs", "type": None, "default": None},
    ]


def test_type_hinted_param_and_return_hint():
    src = b"(defn parse ^Long [^String s] (Long/parseLong s))\n"
    it = _items_by_name(src)["parse"]
    assert it["returns"] == "Long"
    assert it["params"] == [{"name": "s", "type": "String", "default": None}]
    assert it["signature"] == "(defn parse ^Long [^String s])"


def test_destructuring_param_kept_as_written():
    src = b"(defn init [{:keys [host port]}] (str host port))\n"
    it = _items_by_name(src)["init"]
    assert it["params"] == [
        {"name": "{:keys [host port]}", "type": None, "default": None},
    ]


def test_multiline_arg_vector_collapsed_in_signature():
    src = b"(defn wide\n  [alpha\n   beta]\n  alpha)\n"
    it = _items_by_name(src)["wide"]
    assert it["signature"] == "(defn wide [alpha beta])"


def test_defmacro_treated_like_defn():
    src = b"(defmacro unless [test & body] `(if (not ~test) (do ~@body)))\n"
    it = _items_by_name(src)["unless"]
    assert it["signature"] == "(defmacro unless [test & body])"
    assert it["params"] == [
        {"name": "test", "type": None, "default": None},
        {"name": "& body", "type": None, "default": None},
    ]


def test_defmethod_skips_dispatch_value():
    src = (
        b"(defmethod area :circle [shape] (* 3 (:r shape)))\n"
        b"(defmethod convert [Kilometers Miles] [quantity] quantity)\n"
    )
    by = _items_by_name(src)
    circle = by["area"]
    assert circle["signature"] == "(defmethod area :circle [shape])"
    assert circle["params"] == [{"name": "shape", "type": None, "default": None}]
    conv = by["convert"]
    assert conv["signature"] == "(defmethod convert [Kilometers Miles] [quantity])"
    assert conv["params"] == [{"name": "quantity", "type": None, "default": None}]


# ---------------------------------------------------------------------------
# def / defrecord / deftype / defprotocol
# ---------------------------------------------------------------------------
def test_def_caret_private_visibility_only():
    src = b"(def ^:private secret 42)\n"
    it = _items_by_name(src)["secret"]
    assert it["visibility"] == "private"
    for absent in ("signature", "params", "returns", "bases",
                   "type_params", "is_async", "decorators"):
        assert absent not in it, f"{absent} must be omitted on a def var"


def test_defrecord_fields_as_params_and_protocols_as_bases():
    src = (
        b"(defrecord Point [x y]\n"
        b"  Shape\n"
        b"  (area [this] (* x y)))\n"
    )
    it = _items_by_name(src)["Point"]
    assert it["params"] == [
        {"name": "x", "type": None, "default": None},
        {"name": "y", "type": None, "default": None},
    ]
    assert it["bases"] == ["Shape"]
    assert it["signature"] == "(defrecord Point [x y] Shape)"


def test_deftype_without_protocols_omits_bases():
    src = b"(deftype Cell [v])\n"
    it = _items_by_name(src)["Cell"]
    assert it["params"] == [{"name": "v", "type": None, "default": None}]
    assert "bases" not in it
    assert it["signature"] == "(deftype Cell [v])"


def test_omission_contract_on_var_ns_and_protocol_items():
    src = (
        b"(ns app.core)\n"
        b"(def config {:port 8080})\n"
        b"(defprotocol Shape\n"
        b"  (area [this]))\n"
    )
    by = _items_by_name(src)
    # protocol methods are not itemized today; the protocol item stays bare
    for name in ("app.core", "config", "Shape"):
        for absent in ABSENT_WHEN_EMPTY:
            assert absent not in by[name], (
                f"{absent} must be absent on {name}, not a placeholder")


# ---------------------------------------------------------------------------
# copy-through onto chunks (items-based chunker path)
# ---------------------------------------------------------------------------
def test_fields_reach_clojure_chunks():
    src = b"(ns app.core)\n\n(defn- helper [x & rest] (inc x))\n"
    summary, errors = extract_clojure_ast_summary(src, "core.clj")
    assert errors == []
    rec = SimpleNamespace(ast_summary=summary, path="core.clj")
    chunks = {c["symbol"]: c for c in _chunk_clojure(src, rec)}
    c = chunks["helper"]
    assert c["signature"] == "(defn- helper [x & rest])"
    assert c["visibility"] == "private"
    assert c["params"] == [
        {"name": "x", "type": None, "default": None},
        {"name": "& rest", "type": None, "default": None},
    ]
