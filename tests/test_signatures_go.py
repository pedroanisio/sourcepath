"""TDD spec — Go signature/type extraction on ast_summary items.

Contract under test (see plugins/chunks_embeddings/signatures.py): the Go
analyzer puts the canonical optional signature fields directly on each
``ast_summary["items"]`` dict, where the items-based chunker copies them
onto chunks via ``signature_fields_from_item``.

    signature    str    declaration header as written, up to (excluding) the
                        body ``{``, whitespace-collapsed to one line; methods
                        include the receiver as written
    params       list[{name, type, default}]   grouped params (``a, b int``)
                        expand to one entry per name; variadic keeps the
                        ``...`` prefix on the type; default is always None
    returns      str | None   result type as written — a single type or the
                        parenthesized tuple (e.g. ``(int, error)``)
    bases        list[str]    interface items only: embedded interfaces as
                        written; struct embedding is composition, never bases
    type_params  list[str]    generic type parameters, as written
    visibility   NEVER SET — Go has no visibility keywords; capitalization
                        conventions are recoverable from the name itself
    is_async     NEVER SET — no async functions in Go
    decorators   NEVER SET — no decorators in Go

Fields are OMITTED when empty/unknown — never emitted as empty lists or None
placeholders. Existing item fields (kind, name, parent, line/byte spans) are
untouched: the signature fields are additive.

Run: python -m pytest tests/test_signatures_go.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.languages.go import extract_go_ast_summary
from codebase_mapper.ts_setup import TS_AVAILABLE


pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")

NEVER_SET = ("visibility", "is_async", "decorators")


def _items(src: bytes) -> dict[str, dict]:
    summary, errors = extract_go_ast_summary(src, "m.go")
    assert summary is not None and not errors
    return {i["name"]: i for i in summary["items"]}


# ---------------------------------------------------------------------------
# functions
# ---------------------------------------------------------------------------
def test_function_grouped_variadic_params_and_tuple_returns():
    src = b"package p\n\nfunc Sum(a, b int, tail ...string) (int, error) { return 0, nil }\n"
    it = _items(src)["Sum"]
    assert it["signature"] == "func Sum(a, b int, tail ...string) (int, error)"
    assert it["params"] == [
        {"name": "a", "type": "int", "default": None},
        {"name": "b", "type": "int", "default": None},
        {"name": "tail", "type": "...string", "default": None},
    ]
    assert it["returns"] == "(int, error)"
    assert "type_params" not in it
    assert "bases" not in it


def test_bare_function_omits_empty_fields():
    src = b"package p\n\nfunc main() { run() }\n"
    it = _items(src)["main"]
    assert it["signature"] == "func main()"
    for absent in ("params", "returns", "bases", "type_params") + NEVER_SET:
        assert absent not in it, f"{absent} must be omitted when empty"


def test_unnamed_params_get_empty_name():
    src = b"package p\n\nfunc Nop(int, string) {}\n"
    it = _items(src)["Nop"]
    assert it["params"] == [
        {"name": "", "type": "int", "default": None},
        {"name": "", "type": "string", "default": None},
    ]


def test_multiline_header_collapses_to_single_line():
    src = (
        b"package p\n\n"
        b"func Long(\n"
        b"\ta int,\n"
        b"\tb string,\n"
        b") error {\n"
        b"\treturn nil\n"
        b"}\n"
    )
    it = _items(src)["Long"]
    assert it["signature"] == "func Long( a int, b string, ) error"
    assert "\n" not in it["signature"]


# ---------------------------------------------------------------------------
# methods
# ---------------------------------------------------------------------------
def test_method_pointer_receiver_in_signature():
    src = b"package p\n\nfunc (s *Server) Serve(addr string) error { return nil }\n"
    it = _items(src)["Serve"]
    assert it["kind"] == "method"
    assert it["parent"] == "Server"
    assert it["signature"] == "func (s *Server) Serve(addr string) error"
    assert it["params"] == [{"name": "addr", "type": "string", "default": None}]
    assert it["returns"] == "error"


# ---------------------------------------------------------------------------
# generics
# ---------------------------------------------------------------------------
def test_generic_function_type_params():
    src = (
        b"package p\n\n"
        b"func Map[K comparable, V any](m map[K]V, f func(V) V) map[K]V { return m }\n"
    )
    it = _items(src)["Map"]
    assert it["type_params"] == ["K comparable", "V any"]
    assert it["signature"] == "func Map[K comparable, V any](m map[K]V, f func(V) V) map[K]V"
    assert it["params"] == [
        {"name": "m", "type": "map[K]V", "default": None},
        {"name": "f", "type": "func(V) V", "default": None},
    ]
    assert it["returns"] == "map[K]V"


def test_generic_struct_type_params():
    src = b"package p\n\ntype Pair[K comparable, V any] struct {\n\tkey K\n\tval V\n}\n"
    it = _items(src)["Pair"]
    assert it["kind"] == "struct"
    assert it["type_params"] == ["K comparable", "V any"]
    assert it["signature"] == "type Pair[K comparable, V any] struct"


# ---------------------------------------------------------------------------
# interfaces and structs
# ---------------------------------------------------------------------------
def test_interface_embedded_interfaces_are_bases():
    src = (
        b"package p\n\n"
        b"type Store interface {\n"
        b"\tio.Reader\n"
        b"\tCloser\n"
        b"\tGet(key string) (string, bool)\n"
        b"}\n"
    )
    it = _items(src)["Store"]
    assert it["kind"] == "interface"
    assert it["signature"] == "type Store interface"
    assert it["bases"] == ["io.Reader", "Closer"]


def test_struct_embedding_is_not_bases():
    src = b"package p\n\ntype Server struct {\n\tBase\n\tName string\n}\n"
    it = _items(src)["Server"]
    assert it["kind"] == "struct"
    assert it["signature"] == "type Server struct"
    assert "bases" not in it, "struct embedding is composition, not subtyping"


def test_plain_type_declaration_signature():
    src = b"package p\n\ntype ID int\n"
    it = _items(src)["ID"]
    assert it["kind"] == "type"
    assert it["signature"] == "type ID int"
    for absent in ("params", "returns", "bases", "type_params"):
        assert absent not in it


# ---------------------------------------------------------------------------
# omission contract + invariants of the existing item shape
# ---------------------------------------------------------------------------
GO_SRC = (
    b"package p\n\n"
    b'import "fmt"\n\n'
    b"type Greeter struct {\n\tName string\n}\n\n"
    b"type Speaker interface {\n\tSpeak() string\n}\n\n"
    b"func (g *Greeter) Hello() string {\n"
    b'\treturn fmt.Sprintf("hi %s", g.Name)\n'
    b"}\n\n"
    b"func main() {\n"
    b'\tfmt.Println("x")\n'
    b"}\n"
)


def test_never_set_fields_are_never_present():
    for name, it in _items(GO_SRC).items():
        for key in NEVER_SET:
            assert key not in it, f"{key} must never be set on Go item {name!r}"


def test_existing_item_fields_unchanged():
    items = _items(GO_SRC)
    for it in items.values():
        for key in ("kind", "name", "parent", "line_start", "line_end",
                    "byte_start", "byte_end"):
            assert key in it
    hello = items["Hello"]
    assert hello["kind"] == "method" and hello["parent"] == "Greeter"
    # Byte spans still address the declaration source exactly.
    assert GO_SRC[hello["byte_start"]:hello["byte_end"]].decode().startswith(
        "func (g *Greeter) Hello() string {"
    )
