"""TDD spec — Ruby signature/type extraction on ast_summary items.

Contract under test (see plugins/chunks_embeddings/signatures.py): the Ruby
analyzer puts the canonical optional signature fields directly on each
``ast_summary["items"]`` dict, where the items-based chunker copies them
onto chunks via ``signature_fields_from_item``.

    signature    str    ``def name(params)`` / ``def self.name(params)`` /
                        ``class Name < Base`` / ``module Name`` as written,
                        up to (excluding) the body
    params       list[{name, type, default}]   type is always None — Ruby is
                        untyped; splat/double-splat/block params carry the
                        */**/ & prefix on the name; keyword params keep the
                        bare name (no ``:``)
    bases        list[str]    class items only: the superclass (if any)
                        followed by ``include``d module names, as written —
                        both are part of the method-resolution order, so both
                        count as subtyping surface (unlike Go struct embedding)
    visibility   "private" | "protected", set only when an explicit bare
                        ``private``/``protected`` statement precedes the
                        method in its enclosing class/module body — NEVER
                        derived from naming conventions
    type_params  NEVER SET — Ruby has no generics
    is_async     NEVER SET — Ruby has no async methods
    decorators   NEVER SET — Ruby has no decorator syntax

Fields are OMITTED when empty/unknown — never emitted as empty lists or None
placeholders.

Run: python -m pytest tests/test_signatures_ruby.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.languages.ruby import extract_ruby_ast_summary
from codebase_mapper.ts_setup import TS_AVAILABLE


pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")

NEVER_SET = ("type_params", "is_async", "decorators")


def _items(src: bytes) -> dict[str, dict]:
    summary, errors = extract_ruby_ast_summary(src, "m.rb")
    assert summary is not None and not errors
    return {i["name"]: i for i in summary["items"]}


# ---------------------------------------------------------------------------
# methods
# ---------------------------------------------------------------------------
def test_bare_method_omits_empty_fields():
    src = b"def run\nend\n"
    it = _items(src)["run"]
    assert it["signature"] == "def run"
    for absent in ("params", "returns", "bases", "visibility") + NEVER_SET:
        assert absent not in it, f"{absent} must be omitted when empty"


def test_method_params_with_default_splat_kwarg_and_block():
    src = b"def go(name, age = 10, *rest, key:, opt: 1, **kw, &blk)\nend\n"
    it = _items(src)["go"]
    assert it["params"] == [
        {"name": "name", "type": None, "default": None},
        {"name": "age", "type": None, "default": "10"},
        {"name": "*rest", "type": None, "default": None},
        {"name": "key", "type": None, "default": None},
        {"name": "opt", "type": None, "default": "1"},
        {"name": "**kw", "type": None, "default": None},
        {"name": "&blk", "type": None, "default": None},
    ]
    assert it["signature"] == "def go(name, age = 10, *rest, key:, opt: 1, **kw, &blk)"


def test_singleton_method_is_kind_method_with_self_signature():
    src = b"class C\n  def self.create(x)\n  end\nend\n"
    it = _items(src)["create"]
    assert it["kind"] == "method"
    assert it["parent"] == "C"
    assert it["signature"] == "def self.create(x)"
    assert it["params"] == [{"name": "x", "type": None, "default": None}]


# ---------------------------------------------------------------------------
# classes / modules
# ---------------------------------------------------------------------------
def test_class_with_superclass_and_includes():
    src = (
        b"class Bar < Base\n"
        b"  include Comparable\n"
        b"  include Enumerable\n"
        b"end\n"
    )
    it = _items(src)["Bar"]
    assert it["kind"] == "class"
    assert it["signature"] == "class Bar < Base"
    assert it["bases"] == ["Base", "Comparable", "Enumerable"]


def test_class_without_superclass_omits_bases_unless_include():
    src = b"class Plain\nend\n"
    it = _items(src)["Plain"]
    assert it["signature"] == "class Plain"
    assert "bases" not in it


def test_module_is_kind_class():
    src = b"module Foo\nend\n"
    it = _items(src)["Foo"]
    assert it["kind"] == "class"
    assert it["signature"] == "module Foo"


# ---------------------------------------------------------------------------
# visibility
# ---------------------------------------------------------------------------
def test_private_statement_marks_following_methods():
    src = (
        b"class C\n"
        b"  def pub1\n  end\n"
        b"  private\n"
        b"  def secret\n  end\n"
        b"  def secret2\n  end\n"
        b"end\n"
    )
    items = _items(src)
    assert "visibility" not in items["pub1"]
    assert items["secret"]["visibility"] == "private"
    assert items["secret2"]["visibility"] == "private"


def test_protected_then_public_resets_visibility():
    src = (
        b"class C\n"
        b"  protected\n"
        b"  def guarded\n  end\n"
        b"  public\n"
        b"  def open_method\n  end\n"
        b"end\n"
    )
    items = _items(src)
    assert items["guarded"]["visibility"] == "protected"
    assert "visibility" not in items["open_method"]


# ---------------------------------------------------------------------------
# nested methods carry their enclosing class as parent
# ---------------------------------------------------------------------------
def test_method_nested_in_module_has_module_parent():
    src = b"module Foo\n  def helper\n  end\nend\n"
    it = _items(src)["helper"]
    assert it["kind"] == "method"
    assert it["parent"] == "Foo"


# ---------------------------------------------------------------------------
# omission contract + invariants of the existing item shape
# ---------------------------------------------------------------------------
RUBY_SRC = (
    b"class Greeter < Base\n"
    b"  def initialize(name)\n"
    b"    @name = name\n"
    b"  end\n\n"
    b"  def hello\n"
    b'    "hi #{@name}"\n'
    b"  end\n"
    b"end\n"
)


def test_never_set_fields_are_never_present():
    for name, it in _items(RUBY_SRC).items():
        for key in NEVER_SET:
            assert key not in it, f"{key} must never be set on Ruby item {name!r}"


def test_existing_item_fields_unchanged():
    items = _items(RUBY_SRC)
    for it in items.values():
        for key in ("kind", "name", "parent", "line_start", "line_end",
                    "byte_start", "byte_end"):
            assert key in it
    hello = items["hello"]
    assert hello["kind"] == "method" and hello["parent"] == "Greeter"
    assert RUBY_SRC[hello["byte_start"]:hello["byte_end"]].decode().startswith(
        "def hello"
    )
