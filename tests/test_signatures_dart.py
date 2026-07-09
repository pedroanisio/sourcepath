"""TDD spec — Dart signature/type extraction on ast_summary items.

Contract under test (see plugins/chunks_embeddings/signatures.py): the Dart
analyzer puts the canonical optional signature fields directly on each
``ast_summary["items"]`` dict, where the items-based chunker copies them
onto chunks via ``signature_fields_from_item``.

Dart has no maintained tree-sitter grammar (see the module docstring), so
this extractor is regex-based; the fields below are reconstructed from the
same capture groups (``mods``, ``ret``, ``generics``, ``rest``) the item
extractor already computes for spans — no new grammar dependency.

    signature    str    declaration header as written, up to (excluding) the
                        body ``{``/``;``/``=>``
    params       list[{name, type, default}]   the ``required``/optional-
                        positional (``[...]``) / named (``{...}``) grouping
                        markers are stripped before splitting — a param's
                        ``name`` is its trailing identifier as written
                        (``this.foo`` shorthand kept intact); ``type`` is
                        everything before it
    returns      str | None   declared return type, as written (from the
                        ``ret`` capture group)
    bases        list[str]    class items only: ``extends``/``with``/
                        ``implements`` clauses merged into one list, in
                        source order
    type_params  list[str]    generic type parameters, as written (from the
                        ``generics`` capture group)
    is_async     True only for ``async``/``async*`` — mechanically detected
                        from the explicit keyword between the parameter list
                        and the body
    visibility   NEVER SET — Dart has no visibility keywords; leading-
                        underscore is a naming convention, explicitly out of
                        scope per signatures.py's own contract
    decorators   NEVER SET — annotation (``@override`` etc.) extraction is
                        out of scope for this delivery (documented
                        limitation, not fabricated absence)

Run: python -m pytest tests/test_signatures_dart.py
"""
from __future__ import annotations

from codebase_mapper.inspection.languages.dart import extract_dart_ast_summary


NEVER_SET = ("visibility", "decorators")


def _items(src: bytes) -> dict[str, dict]:
    summary, errors = extract_dart_ast_summary(src, "m.dart")
    assert summary is not None and not errors
    out: dict[str, dict] = {}
    for i in summary["items"]:
        out.setdefault(i["name"], i)
    return out


# ---------------------------------------------------------------------------
# top-level functions
# ---------------------------------------------------------------------------
def test_bare_function_omits_empty_fields():
    src = b"void run() {}\n"
    it = _items(src)["run"]
    assert it["signature"] == "void run()"
    assert it["returns"] == "void"  # void is a real written return type
    for absent in ("params", "bases", "type_params", "is_async") + NEVER_SET:
        assert absent not in it


def test_function_params_default_and_return_type():
    src = b'String greet(String msg, {int times = 1}) {\n  return msg;\n}\n'
    it = _items(src)["greet"]
    assert it["signature"] == "String greet(String msg, {int times = 1})"
    assert it["params"] == [
        {"name": "msg", "type": "String", "default": None},
        {"name": "times", "type": "int", "default": "1"},
    ]
    assert it["returns"] == "String"


def test_generic_function_type_params():
    src = b"T identity<T>(T x) {\n  return x;\n}\n"
    it = _items(src)["identity"]
    assert it["type_params"] == ["T"]


def test_async_function_is_async():
    src = b"Future<void> fetch() async {\n}\n"
    it = _items(src)["fetch"]
    assert it["is_async"] is True


def test_non_async_function_omits_is_async():
    src = b"void sync_() {}\n"
    it = _items(src)["sync_"]
    assert "is_async" not in it


def test_arrow_body_function_signature_excludes_body():
    src = b"int square(int x) => x * x;\n"
    it = _items(src)["square"]
    assert it["signature"] == "int square(int x)"


# ---------------------------------------------------------------------------
# classes
# ---------------------------------------------------------------------------
def test_class_with_extends_with_implements_merged_into_bases():
    src = b"class Dog extends Animal with Named implements Comparable {\n}\n"
    it = _items(src)["Dog"]
    assert it["kind"] == "class"
    assert it["bases"] == ["Animal", "Named", "Comparable"]


def test_class_without_clauses_omits_bases():
    src = b"class Plain {\n}\n"
    it = _items(src)["Plain"]
    assert "bases" not in it


# ---------------------------------------------------------------------------
# methods / constructors
# ---------------------------------------------------------------------------
def test_method_has_class_parent_and_params():
    src = (
        b"class Greeter {\n"
        b"  String hello(String name) {\n"
        b"    return name;\n"
        b"  }\n"
        b"}\n"
    )
    it = _items(src)["hello"]
    assert it["kind"] == "method"
    assert it["parent"] == "Greeter"
    assert it["signature"] == "String hello(String name)"
    assert it["params"] == [{"name": "name", "type": "String", "default": None}]
    assert it["returns"] == "String"


def test_constructor_this_shorthand_param_kept_as_written():
    src = (
        b"class Point {\n"
        b"  final int x;\n"
        b"  Point(this.x);\n"
        b"}\n"
    )
    summary, errors = extract_dart_ast_summary(src, "m.dart")
    assert summary is not None and not errors
    it = next(i for i in summary["items"] if i["kind"] == "constructor")
    assert it["name"] == "Point"
    assert it["params"] == [{"name": "this.x", "type": None, "default": None}]


def test_optional_positional_params_bracket_stripped():
    src = (
        b"class C {\n"
        b"  void run(int a, [int b = 2]) {\n"
        b"  }\n"
        b"}\n"
    )
    it = _items(src)["run"]
    assert it["params"] == [
        {"name": "a", "type": "int", "default": None},
        {"name": "b", "type": "int", "default": "2"},
    ]


# ---------------------------------------------------------------------------
# omission contract
# ---------------------------------------------------------------------------
def test_never_set_fields_are_never_present():
    src = (
        b"class Greeter {\n"
        b"  String hello(String name) {\n"
        b"    return name;\n"
        b"  }\n"
        b"}\n"
    )
    for name, it in _items(src).items():
        for key in NEVER_SET:
            assert key not in it, f"{key} must never be set on Dart item {name!r}"
