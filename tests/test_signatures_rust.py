"""TDD spec — Rust signature/type extraction on L2 chunks.

Contract (plugins/chunks_embeddings/signatures.py): optional fields
signature / params / returns / bases / type_params / visibility / is_async /
decorators, OMITTED when empty — never placeholders.

Conventions pinned here:
  * ``signature`` is the item header from the item start (attributes are
    separate sibling nodes, so they are naturally excluded) to (excluding)
    the body block or trailing ``;``, collapsed to a single line.
  * ``visibility`` is the visibility modifier as written (``pub``,
    ``pub(crate)``, ...).
  * Self params keep their exact form (``&self`` / ``&mut self`` / ``self``)
    with type None unless explicitly typed.
  * ``decorators`` are the attribute contents with the ``#[`` ``]`` sigil
    stripped (``derive(Debug, Clone)``, ``tokio::main``).
  * Traits: supertraits (``trait W: Draw + Resize``) go into ``bases``.
  * ``impl Trait for Type`` chunks (symbol = Type) get ``bases = [Trait]``.

Run: python -m pytest tests/test_signatures_rust.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.ts_setup import TS_AVAILABLE
from plugins.chunks_embeddings.chunker import _chunk_rust

pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")


def _by_symbol(chunks):
    return {(c["symbol"], c.get("parent_symbol")): c for c in chunks}


# ---------------------------------------------------------------------------
# functions and methods
# ---------------------------------------------------------------------------
def test_pub_async_method_full_fields():
    src = (
        b"pub struct Server;\n"
        b"impl Server {\n"
        b"    pub async fn serve(&self, addr: SocketAddr) -> io::Result<()> {\n"
        b"        Ok(())\n"
        b"    }\n"
        b"}\n"
    )
    by = _by_symbol(_chunk_rust(src, "m.rs"))
    m = by[("serve", "Server")]
    assert m["signature"] == (
        "pub async fn serve(&self, addr: SocketAddr) -> io::Result<()>"
    )
    assert m["visibility"] == "pub"
    assert m["is_async"] is True
    assert m["returns"] == "io::Result<()>"
    assert m["params"] == [
        {"name": "&self", "type": None, "default": None},
        {"name": "addr", "type": "SocketAddr", "default": None},
    ]


def test_generic_function_type_params_and_crate_visibility():
    src = (
        b"pub(crate) fn max<T: Ord>(a: T, b: T) -> T {\n"
        b"    if a > b { a } else { b }\n"
        b"}\n"
    )
    by = _by_symbol(_chunk_rust(src, "m.rs"))
    f = by[("max", None)]
    assert f["visibility"] == "pub(crate)"
    assert f["type_params"] == ["T: Ord"]
    assert f["returns"] == "T"
    assert f["signature"] == "pub(crate) fn max<T: Ord>(a: T, b: T) -> T"


def test_trait_signature_item_no_trailing_semicolon():
    src = (
        b"pub trait Widget: Draw + Resize {\n"
        b"    fn draw(&self);\n"
        b"}\n"
    )
    by = _by_symbol(_chunk_rust(src, "m.rs"))
    t = by[("Widget", None)]
    assert t["bases"] == ["Draw", "Resize"]
    assert t["visibility"] == "pub"
    assert t["signature"] == "pub trait Widget: Draw + Resize"
    d = by[("draw", "Widget")]
    assert d["signature"] == "fn draw(&self)"
    assert d["params"] == [{"name": "&self", "type": None, "default": None}]


# ---------------------------------------------------------------------------
# types, attributes, impls
# ---------------------------------------------------------------------------
def test_struct_attributes_become_decorators():
    src = (
        b"#[derive(Debug, Clone)]\n"
        b"#[serde(rename_all = \"camelCase\")]\n"
        b"pub struct Config {\n    pub port: u16,\n}\n"
    )
    by = _by_symbol(_chunk_rust(src, "m.rs"))
    s = by[("Config", None)]
    assert s["decorators"] == ['derive(Debug, Clone)',
                               'serde(rename_all = "camelCase")']
    assert s["visibility"] == "pub"
    assert s["signature"] == "pub struct Config"


def test_generic_struct_type_params():
    src = b"pub struct Pair<K, V> {\n    k: K,\n    v: V,\n}\n"
    s = _by_symbol(_chunk_rust(src, "m.rs"))[("Pair", None)]
    assert s["type_params"] == ["K", "V"]
    assert s["signature"] == "pub struct Pair<K, V>"


def test_trait_impl_records_trait_as_base():
    src = (
        b"impl Display for Config {\n"
        b"    fn fmt(&self, f: &mut Formatter) -> fmt::Result { todo!() }\n"
        b"}\n"
    )
    by = _by_symbol(_chunk_rust(src, "m.rs"))
    imp = by[("Config", None)]
    assert imp["bases"] == ["Display"]
    assert imp["signature"] == "impl Display for Config"
    m = by[("fmt", "Config")]
    assert m["params"] == [
        {"name": "&self", "type": None, "default": None},
        {"name": "f", "type": "&mut Formatter", "default": None},
    ]


def test_inherent_impl_has_no_bases():
    src = b"struct A;\nimpl A {\n    fn new() -> Self { A }\n}\n"
    imp = _by_symbol(_chunk_rust(src, "m.rs"))[("A", None)]
    assert "bases" not in imp
    assert imp["signature"] == "impl A"


# ---------------------------------------------------------------------------
# omission contract + span integrity
# ---------------------------------------------------------------------------
def test_bare_function_omits_empty_fields():
    src = b"fn main() {\n    run();\n}\n"
    c = _by_symbol(_chunk_rust(src, "m.rs"))[("main", None)]
    assert c["signature"] == "fn main()"
    for absent in ("params", "returns", "bases", "type_params", "visibility",
                   "is_async", "decorators"):
        assert absent not in c, f"{absent} must be omitted when empty"


def test_byte_spans_and_text_unchanged():
    src = b"#[derive(Debug)]\npub struct S;\nfn f() -> u8 { 1 }\n"
    for c in _chunk_rust(src, "m.rs"):
        assert src[c["byte_start"]:c["byte_end"]].decode() == c["text"]
