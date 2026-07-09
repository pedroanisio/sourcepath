"""TDD spec — C++ signature/type extraction on ast_summary items.

Contract under test (see plugins/chunks_embeddings/signatures.py): the C++
analyzer puts the canonical optional signature fields directly on each
``ast_summary["items"]`` dict, where the items-based chunker copies them
onto chunks via ``signature_fields_from_item``.

    signature    str    declaration header as written, up to (excluding) the
                        body ``{`` (or the member-initializer-list start for
                        a constructor) / trailing ``;`` for a prototype
    params       list[{name, type, default}]   ``type`` is reconstructed by
                        splicing the name identifier back out of the
                        parameter's full text (pointer/reference/const
                        decoration preserved exactly as written); ``default``
                        is present only for an ``optional_parameter_declaration``
    returns      str | None   the function's return type as written,
                        including pointer/reference decoration attached to
                        the declarator
    bases        list[str]    class/struct/union items only: ``extends`` +
                        ``implements`` merged into one list, in source order
                        — additive alongside those existing fields (relied on
                        by ``plugins/symbol_xrefs/cpp_resolver.py``), not a
                        replacement
    visibility   "public" | "private" | "protected" — set only after an
                        explicit ``access_specifier`` label has appeared in
                        the enclosing class body; members before the first
                        label get no visibility field (never defaulted from
                        the class/struct/union keyword, matching the
                        explicit-marker-only precedent used elsewhere)
    type_params  list[str]    a top-level ``template<...>`` parameter list,
                        as written — never set for non-template items
    is_async     NEVER SET — C++ has no async function declarator
    decorators   NEVER SET — attribute ([[nodiscard]] etc.) extraction is out
                        of scope for this delivery (documented limitation,
                        not fabricated absence)

Run: python -m pytest tests/test_signatures_cpp.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.languages.cpp import extract_cpp_ast_summary
from codebase_mapper.ts_setup import TS_AVAILABLE


pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")

NEVER_SET = ("is_async", "decorators")


def _items(src: bytes) -> list[dict]:
    summary, errors = extract_cpp_ast_summary(src, "m.cpp")
    assert summary is not None and not errors
    return summary["items"]


def _by_name(src: bytes) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for it in _items(src):
        out.setdefault(it["name"], it)
    return out


# ---------------------------------------------------------------------------
# free functions
# ---------------------------------------------------------------------------
def test_function_params_and_returns():
    src = b"int add(int a, int b) {\n    return a + b;\n}\n"
    it = _by_name(src)["add"]
    assert it["signature"] == "int add(int a, int b)"
    assert it["params"] == [
        {"name": "a", "type": "int", "default": None},
        {"name": "b", "type": "int", "default": None},
    ]
    assert it["returns"] == "int"
    for absent in NEVER_SET:
        assert absent not in it


def test_bare_function_omits_empty_fields():
    src = b"void run() {}\n"
    it = _by_name(src)["run"]
    assert it["signature"] == "void run()"
    assert it["returns"] == "void"  # void is a real written return type
    for absent in ("params", "bases", "visibility", "type_params") + NEVER_SET:
        assert absent not in it


def test_pointer_and_reference_params_and_defaults():
    src = (
        b"void speak(const std::string& msg, int times = 1) {\n"
        b"}\n"
    )
    it = _by_name(src)["speak"]
    assert it["params"] == [
        {"name": "msg", "type": "const std::string&", "default": None},
        {"name": "times", "type": "int", "default": "1"},
    ]


def test_pointer_return_type_included_in_returns():
    src = b"char *make() {\n    return 0;\n}\n"
    it = _by_name(src)["make"]
    assert it["returns"] == "char *"
    assert it["signature"] == "char *make()"


# ---------------------------------------------------------------------------
# class / struct / template
# ---------------------------------------------------------------------------
def test_class_bases_merge_extends_and_implements():
    src = b"class Dog : public Animal, public Runnable {\n};\n"
    it = _by_name(src)["Dog"]
    assert it["kind"] == "class"
    assert it["extends"] == "Animal"
    assert it["implements"] == ["Runnable"]
    assert it["bases"] == ["Animal", "Runnable"]


def test_class_without_bases_omits_bases():
    src = b"class Plain {\n};\n"
    it = _by_name(src)["Plain"]
    assert "bases" not in it


def test_template_class_type_params():
    src = b"template<typename T, int N>\nclass Box {\n};\n"
    it = _by_name(src)["Box"]
    assert it["type_params"] == ["typename T", "int N"]


def test_template_function_type_params():
    src = b"template<typename T>\nT identity(T x) {\n    return x;\n}\n"
    it = _by_name(src)["identity"]
    assert it["type_params"] == ["typename T"]


# ---------------------------------------------------------------------------
# class members: constructor / destructor / visibility
# ---------------------------------------------------------------------------
def test_constructor_and_destructor_kinds():
    src = (
        b"class Box {\n"
        b"public:\n"
        b"    Box(int val) {}\n"
        b"    virtual ~Box() {}\n"
        b"};\n"
    )
    items = _by_name(src)
    assert items["Box"]["kind"] == "class"
    ctor = next(i for i in _items(src) if i["kind"] == "constructor")
    dtor = next(i for i in _items(src) if i["kind"] == "destructor")
    assert ctor["name"] == "Box" and ctor["parent"] == "Box"
    assert dtor["name"] == "~Box" and dtor["parent"] == "Box"
    assert ctor["params"] == [{"name": "val", "type": "int", "default": None}]


def test_member_visibility_after_explicit_label():
    src = (
        b"class C {\n"
        b"public:\n"
        b"    void pub1() {}\n"
        b"private:\n"
        b"    void secret() {}\n"
        b"};\n"
    )
    items = _by_name(src)
    assert items["pub1"]["visibility"] == "public"
    assert items["secret"]["visibility"] == "private"


def test_member_before_any_label_has_no_visibility():
    src = b"class C {\n    void implicit() {}\n};\n"
    it = _by_name(src)["implicit"]
    assert "visibility" not in it


# ---------------------------------------------------------------------------
# out-of-class method definitions
# ---------------------------------------------------------------------------
def test_out_of_class_method_definition_has_signature_and_params():
    src = b"int Dog::helper(int x) {\n    return x;\n}\n"
    it = _by_name(src)["helper"]
    assert it["kind"] == "method"
    assert it["parent"] == "Dog"
    assert it["signature"] == "int Dog::helper(int x)"
    assert it["params"] == [{"name": "x", "type": "int", "default": None}]
    assert it["returns"] == "int"


# ---------------------------------------------------------------------------
# omission contract
# ---------------------------------------------------------------------------
def test_never_set_fields_are_never_present():
    src = (
        b"class Dog : public Animal {\n"
        b"public:\n"
        b"    void speak(int times) {}\n"
        b"};\n"
        b"int add(int a, int b) { return a + b; }\n"
    )
    for it in _items(src):
        for key in NEVER_SET:
            assert key not in it, f"{key} must never be set on C++ item {it['name']!r}"
