"""TDD spec — Kotlin signature/type extraction on ast_summary items.

Contract under test (see plugins/chunks_embeddings/signatures.py): the Kotlin
analyzer puts the canonical optional signature fields directly on each
``ast_summary["items"]`` dict, where the items-based chunker copies them
onto chunks via ``signature_fields_from_item``.

    signature    str    ``fun name(params): Ret`` / ``class Name : Base`` as
                        written, up to (excluding) the body
    params       list[{name, type, default}]   type/default are as written;
                        a ``vararg`` parameter's name is prefixed ``"vararg "``
    returns      str | None   declared return type, as written
    bases        list[str]    class items only: each delegation specifier's
                        type as written (constructor-call parens excluded)
    visibility   "private" | "protected" | "public" | "internal" — an
                        explicit modifier keyword only
    is_async     True only for a ``suspend fun`` — Kotlin's async marker
    type_params  list[str]    generic type parameters, as written
    decorators   NEVER SET — annotation extraction is out of scope for this
                        delivery (known limitation, not fabricated absence)

kind ∈ {"function" (top-level), "method" (class/object member), "class"
(class/interface/object)}.

Run: python -m pytest tests/test_signatures_kotlin.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.languages.kotlin import extract_kotlin_ast_summary
from codebase_mapper.ts_setup import TS_AVAILABLE


pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")

NEVER_SET = ("decorators",)


def _items(src: bytes) -> dict[str, dict]:
    summary, errors = extract_kotlin_ast_summary(src, "m.kt")
    assert summary is not None and not errors
    return {i["name"]: i for i in summary["items"]}


# ---------------------------------------------------------------------------
# top-level functions
# ---------------------------------------------------------------------------
def test_bare_function_omits_empty_fields():
    src = b"fun run() {}\n"
    it = _items(src)["run"]
    assert it["kind"] == "function"
    assert it["signature"] == "fun run()"
    for absent in ("params", "returns", "bases", "visibility", "type_params") + NEVER_SET:
        assert absent not in it


def test_function_default_and_return_type():
    src = b'fun greet(msg: String = "hi"): String {\n    return msg\n}\n'
    it = _items(src)["greet"]
    assert it["signature"] == 'fun greet(msg: String = "hi"): String'
    assert it["params"] == [{"name": "msg", "type": "String", "default": '"hi"'}]
    assert it["returns"] == "String"


def test_vararg_parameter_is_prefixed():
    src = b"fun sum(vararg nums: Int): Int { return 0 }\n"
    it = _items(src)["sum"]
    assert it["params"] == [{"name": "vararg nums", "type": "Int", "default": None}]


def test_generic_function_type_params():
    src = b"fun <T> identity(x: T): T = x\n"
    it = _items(src)["identity"]
    assert it["type_params"] == ["T"]
    assert it["signature"] == "fun <T> identity(x: T): T"


# ---------------------------------------------------------------------------
# classes / interfaces / objects
# ---------------------------------------------------------------------------
def test_class_with_superclass_and_interface_bases():
    src = b"class Bar : Base(), Comparable<Bar> {\n}\n"
    it = _items(src)["Bar"]
    assert it["kind"] == "class"
    assert it["signature"] == "class Bar : Base(), Comparable<Bar>"
    assert it["bases"] == ["Base", "Comparable<Bar>"]


def test_class_without_bases_omits_bases():
    src = b"class Plain {\n}\n"
    it = _items(src)["Plain"]
    assert "bases" not in it


def test_interface_is_kind_class():
    src = b"interface Speaker {\n    fun speak(): String\n}\n"
    items = _items(src)
    assert items["Speaker"]["kind"] == "class"
    speak = items["speak"]
    assert speak["kind"] == "method"
    assert speak["parent"] == "Speaker"
    assert speak["signature"] == "fun speak(): String"
    for absent in ("params",):
        assert absent not in speak


def test_object_declaration_is_kind_class_with_method_members():
    src = b"object Singleton {\n    fun instanceMethod() {}\n}\n"
    items = _items(src)
    assert items["Singleton"]["kind"] == "class"
    assert items["instanceMethod"]["kind"] == "method"
    assert items["instanceMethod"]["parent"] == "Singleton"


# ---------------------------------------------------------------------------
# visibility / suspend
# ---------------------------------------------------------------------------
def test_private_function_visibility():
    src = b"class C {\n    private fun secret() {}\n}\n"
    it = _items(src)["secret"]
    assert it["visibility"] == "private"


def test_public_default_omits_visibility():
    src = b"class C {\n    fun open() {}\n}\n"
    it = _items(src)["open"]
    assert "visibility" not in it


def test_suspend_function_is_async():
    src = b"class C {\n    suspend fun asyncOne() {}\n}\n"
    it = _items(src)["asyncOne"]
    assert it["is_async"] is True


def test_non_suspend_function_omits_is_async():
    src = b"class C {\n    fun sync() {}\n}\n"
    it = _items(src)["sync"]
    assert "is_async" not in it


# ---------------------------------------------------------------------------
# omission contract + invariants of the item shape
# ---------------------------------------------------------------------------
KOTLIN_SRC = (
    b"package com.example\n\n"
    b"class Greeter(val name: String) {\n"
    b"    fun hello(): String {\n"
    b'        return "hi " + name\n'
    b"    }\n"
    b"}\n"
)


def test_never_set_fields_are_never_present():
    for name, it in _items(KOTLIN_SRC).items():
        for key in NEVER_SET:
            assert key not in it, f"{key} must never be set on Kotlin item {name!r}"


def test_existing_item_fields_present():
    items = _items(KOTLIN_SRC)
    for it in items.values():
        for key in ("kind", "name", "parent", "line_start", "line_end",
                    "byte_start", "byte_end"):
            assert key in it
    hello = items["hello"]
    assert hello["kind"] == "method" and hello["parent"] == "Greeter"
    assert KOTLIN_SRC[hello["byte_start"]:hello["byte_end"]].decode().startswith(
        "fun hello(): String"
    )
