"""TDD spec — C signature/type extraction on ast_summary items.

Contract under test (see plugins/chunks_embeddings/signatures.py): the C
analyzer puts the canonical optional signature fields directly on each
``ast_summary["items"]`` dict, where the items-based chunker copies them
onto chunks via ``signature_fields_from_item``.

    signature    str    declaration header as written, up to (excluding) the
                        body ``{`` for a definition, or the trailing ``;`` for
                        a prototype / struct-only / typedef declaration
    params       list[{name, type, default}]   ``type`` is the parameter's
                        declared type as written (pointer stars / array
                        brackets included via byte-splicing the name back
                        out); ``default`` is always None — C has no
                        parameter defaults; an unnamed parameter (``void``,
                        or a prototype with no parameter name) carries name ""
    returns      str | None   the function's return type as written,
                        including pointer decoration attached to the
                        declarator (``char *``)
    bases        NEVER SET — C has no inheritance
    type_params  NEVER SET — C has no generics
    visibility   NEVER SET — C has no visibility keywords (static linkage is
                        a storage-class concern, not a class member modifier,
                        and there is no per-symbol equivalent to track)
    is_async     NEVER SET — no async in C
    decorators   NEVER SET — no decorator/attribute syntax modeled here

kind ∈ {"function", "struct", "union", "enum", "typedef"}. A struct/union/enum
introduced (with or without a name) inside a ``typedef`` is a single item
under its alias name — never double-counted alongside a separate typedef item.

Run: python -m pytest tests/test_signatures_c.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.languages.c import extract_c_ast_summary
from codebase_mapper.ts_setup import TS_AVAILABLE


pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")

NEVER_SET = ("bases", "type_params", "visibility", "is_async", "decorators")


def _items(src: bytes) -> dict[str, dict]:
    summary, errors = extract_c_ast_summary(src, "m.c")
    assert summary is not None and not errors
    return {i["name"]: i for i in summary["items"]}


# ---------------------------------------------------------------------------
# functions
# ---------------------------------------------------------------------------
def test_function_definition_params_and_returns():
    src = b"int add(int a, int b) {\n    return a + b;\n}\n"
    it = _items(src)["add"]
    assert it["kind"] == "function"
    assert it["signature"] == "int add(int a, int b)"
    assert it["params"] == [
        {"name": "a", "type": "int", "default": None},
        {"name": "b", "type": "int", "default": None},
    ]
    assert it["returns"] == "int"
    for absent in NEVER_SET:
        assert absent not in it


def test_function_prototype_no_body_still_captured():
    src = b"int add(int a, int b);\n"
    it = _items(src)["add"]
    assert it["kind"] == "function"
    assert it["signature"] == "int add(int a, int b)"
    assert it["returns"] == "int"


def test_pointer_return_type_included_in_returns_and_signature():
    src = b"char *make(void) {\n    return 0;\n}\n"
    it = _items(src)["make"]
    assert it["returns"] == "char *"
    assert it["signature"] == "char *make(void)"
    assert "params" not in it


def test_pointer_and_array_params_preserve_declarator_decoration():
    src = b"void nop(const char *name, int arr[10]);\n"
    it = _items(src)["nop"]
    assert it["params"] == [
        {"name": "name", "type": "const char *", "default": None},
        {"name": "arr", "type": "int [10]", "default": None},
    ]


def test_variadic_parameter():
    src = b"int sum(int first, ...);\n"
    it = _items(src)["sum"]
    assert it["params"] == [
        {"name": "first", "type": "int", "default": None},
        {"name": "", "type": "...", "default": None},
    ]


def test_void_only_parameter_list_is_empty_params():
    src = b"void nop(void);\n"
    it = _items(src)["nop"]
    assert "params" not in it


# ---------------------------------------------------------------------------
# struct / union / enum
# ---------------------------------------------------------------------------
def test_plain_named_struct():
    src = b"struct Named {\n    int a;\n};\n"
    it = _items(src)["Named"]
    assert it["kind"] == "struct"
    assert it["signature"] == "struct Named"
    for absent in NEVER_SET:
        assert absent not in it


def test_named_union():
    src = b"union U {\n    int i;\n    float f;\n};\n"
    it = _items(src)["U"]
    assert it["kind"] == "union"
    assert it["signature"] == "union U"


def test_named_enum():
    src = b"enum Color { RED, GREEN };\n"
    it = _items(src)["Color"]
    assert it["kind"] == "enum"
    assert it["signature"] == "enum Color"


# ---------------------------------------------------------------------------
# typedef
# ---------------------------------------------------------------------------
def test_typedef_anonymous_struct_single_item_under_alias():
    src = b"typedef struct {\n    int x;\n    int y;\n} Point;\n"
    items = _items(src)
    assert list(items.keys()) == ["Point"]
    it = items["Point"]
    assert it["kind"] == "struct"


def test_typedef_named_struct_single_item_no_duplicate():
    src = b"typedef struct Named2 {\n    int y;\n} Named2;\n"
    items = _items(src)
    assert list(items.keys()) == ["Named2"]
    assert items["Named2"]["kind"] == "struct"


def test_plain_type_alias():
    src = b"typedef unsigned long ULong;\n"
    it = _items(src)["ULong"]
    assert it["kind"] == "typedef"
    assert it["signature"] == "typedef unsigned long ULong"
    for absent in ("params", "returns") + NEVER_SET:
        assert absent not in it


# ---------------------------------------------------------------------------
# omission contract + invariants of the item shape
# ---------------------------------------------------------------------------
C_SRC = (
    b"#include <stdio.h>\n\n"
    b"struct Point {\n    int x;\n    int y;\n};\n\n"
    b"int add(int a, int b) {\n"
    b"    return a + b;\n"
    b"}\n"
)


def test_never_set_fields_are_never_present():
    for name, it in _items(C_SRC).items():
        for key in NEVER_SET:
            assert key not in it, f"{key} must never be set on C item {name!r}"


def test_existing_item_fields_present():
    items = _items(C_SRC)
    for it in items.values():
        for key in ("kind", "name", "line_start", "line_end",
                    "byte_start", "byte_end"):
            assert key in it
    add = items["add"]
    assert C_SRC[add["byte_start"]:add["byte_end"]].decode().startswith(
        "int add(int a, int b) {"
    )
