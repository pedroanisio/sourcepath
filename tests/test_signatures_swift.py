"""TDD spec — Swift signature/type extraction on ast_summary items.

Contract under test (see plugins/chunks_embeddings/signatures.py): the Swift
analyzer puts the canonical optional signature fields directly on each
``ast_summary["items"]`` dict, where the items-based chunker copies them
onto chunks via ``signature_fields_from_item``.

    signature    str    ``func name(params) -> Ret`` / ``class Name: Base``
                        as written, up to (excluding) the body
    params       list[{name, type, default}]   ``name`` is the *internal*
                        parameter name (the one used in the function body) —
                        an external argument label, when present, is dropped;
                        type/default are as written
    returns      str | None   declared return type, as written
    bases        list[str]    class/struct/enum items only: each inheritance
                        specifier's type as written
    visibility   "private" | "protected" | "public" | "internal" | "fileprivate"
                        — an explicit modifier keyword only
    is_async     True only for an ``async`` function — never derived
    type_params  list[str]    generic type parameters, as written
    decorators   NEVER SET — attribute extraction (``@objc`` etc.) is out of
                        scope for this delivery (documented limitation, not
                        fabricated absence)

kind ∈ {"function" (top-level), "method" (member of a class/struct/enum/
protocol), "class"} — ``class``, ``struct``, and ``enum`` all normalize to
item kind ``"class"`` (they share one grammar node), matching the Ruby
class/module precedent.

Run: python -m pytest tests/test_signatures_swift.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.languages.swift import extract_swift_ast_summary
from codebase_mapper.ts_setup import TS_AVAILABLE


pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")

NEVER_SET = ("decorators",)


def _items(src: bytes) -> dict[str, dict]:
    summary, errors = extract_swift_ast_summary(src, "m.swift")
    assert summary is not None and not errors
    return {i["name"]: i for i in summary["items"]}


# ---------------------------------------------------------------------------
# top-level functions
# ---------------------------------------------------------------------------
def test_bare_function_omits_empty_fields():
    src = b"func run() {}\n"
    it = _items(src)["run"]
    assert it["kind"] == "function"
    assert it["signature"] == "func run()"
    for absent in ("params", "returns", "bases", "visibility",
                   "type_params", "is_async") + NEVER_SET:
        assert absent not in it


def test_external_label_dropped_default_and_return_type():
    src = b'func greet(_ msg: String, times: Int = 1) -> String {\n    return msg\n}\n'
    it = _items(src)["greet"]
    assert it["signature"] == "func greet(_ msg: String, times: Int = 1) -> String"
    assert it["params"] == [
        {"name": "msg", "type": "String", "default": None},
        {"name": "times", "type": "Int", "default": "1"},
    ]
    assert it["returns"] == "String"


def test_generic_function_type_params():
    src = b"func generic<T>(x: T) -> T {\n    return x\n}\n"
    it = _items(src)["generic"]
    assert it["type_params"] == ["T"]
    assert it["signature"] == "func generic<T>(x: T) -> T"


def test_async_function_is_async():
    src = b"func fetch() async -> String {\n    return \"\"\n}\n"
    it = _items(src)["fetch"]
    assert it["is_async"] is True


def test_non_async_function_omits_is_async():
    src = b"func sync() {}\n"
    it = _items(src)["sync"]
    assert "is_async" not in it


# ---------------------------------------------------------------------------
# class / struct / enum / protocol
# ---------------------------------------------------------------------------
def test_class_with_bases():
    src = b"class Bar: Base, Comparable {\n}\n"
    it = _items(src)["Bar"]
    assert it["kind"] == "class"
    assert it["signature"] == "class Bar: Base, Comparable"
    assert it["bases"] == ["Base", "Comparable"]


def test_class_without_bases_omits_bases():
    src = b"class Plain {\n}\n"
    it = _items(src)["Plain"]
    assert "bases" not in it


def test_struct_is_kind_class():
    src = b"struct Point {\n    var x: Int\n}\n"
    it = _items(src)["Point"]
    assert it["kind"] == "class"
    assert it["signature"] == "struct Point"


def test_enum_is_kind_class():
    src = b"enum Color {\n    case red, green\n}\n"
    it = _items(src)["Color"]
    assert it["kind"] == "class"
    assert it["signature"] == "enum Color"


def test_protocol_is_kind_class_with_method_members():
    src = b"protocol Speaker {\n    func speak() -> String\n}\n"
    items = _items(src)
    assert items["Speaker"]["kind"] == "class"
    speak = items["speak"]
    assert speak["kind"] == "method"
    assert speak["parent"] == "Speaker"
    assert speak["signature"] == "func speak() -> String"


def test_class_method_has_class_parent():
    src = b"class Bar {\n    private func secret() {}\n}\n"
    it = _items(src)["secret"]
    assert it["kind"] == "method"
    assert it["parent"] == "Bar"
    assert it["visibility"] == "private"


# ---------------------------------------------------------------------------
# omission contract + invariants of the item shape
# ---------------------------------------------------------------------------
SWIFT_SRC = (
    b"import Foundation\n\n"
    b"class Greeter {\n"
    b"    func hello() -> String {\n"
    b'        return "hi"\n'
    b"    }\n"
    b"}\n"
)


def test_never_set_fields_are_never_present():
    for name, it in _items(SWIFT_SRC).items():
        for key in NEVER_SET:
            assert key not in it, f"{key} must never be set on Swift item {name!r}"


def test_existing_item_fields_present():
    items = _items(SWIFT_SRC)
    for it in items.values():
        for key in ("kind", "name", "parent", "line_start", "line_end",
                    "byte_start", "byte_end"):
            assert key in it
    hello = items["hello"]
    assert hello["kind"] == "method" and hello["parent"] == "Greeter"
    assert SWIFT_SRC[hello["byte_start"]:hello["byte_end"]].decode().startswith(
        "func hello() -> String"
    )
