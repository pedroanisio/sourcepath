"""TDD spec — Java canonical signature fields on ``ast_summary`` items.

Contract under test (see plugins/chunks_embeddings/signatures.py): the Java
analyzer enriches every item it emits with the optional, mechanically-derived
fields

    signature    str    declaration header up to (excluding) the body ``{``
                        (or the terminating ``;`` for bodyless methods),
                        single-line-collapsed, without annotations
    params       list[{name, type, default}]   default is always None in Java;
                        varargs keep ``...`` in the type as written
    returns      str | None   declared return type (constructors: omitted)
    bases        list[str]    [extends] + implements, as written in source
    type_params  list[str]    generic type parameters, as written
    visibility   str | None   explicit public/private/protected keyword only —
                        package-private members carry NO visibility field
    is_async     bool         never true for Java — always omitted
    decorators   list[str]    annotations, as written, without the leading ``@``

Fields are OMITTED when empty/unknown — never emitted as empty lists or None
placeholders. The pre-existing item fields consumed by the xref resolver
(kind, name, parent, line/byte spans, extends, implements) must be preserved
exactly; the canonical fields are ADDED alongside.

Run: python -m pytest tests/test_signatures_java.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.languages.java import extract_java_ast_summary
from codebase_mapper.ts_setup import TS_AVAILABLE

pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")


CANONICAL_FIELDS = ("signature", "params", "returns", "bases", "type_params",
                    "visibility", "is_async", "decorators")


def _items(src: bytes, path: str = "com/example/Sample.java") -> dict:
    summary, errors = extract_java_ast_summary(src, path)
    assert summary is not None
    assert "parse_errors_present" not in errors
    return {(it["kind"], it["name"]): it for it in summary["items"]}


# ---------------------------------------------------------------------------
# classes
# ---------------------------------------------------------------------------
def test_public_class_extends_implements_annotations():
    src = (
        b"package com.example;\n"
        b"\n"
        b"@Entity\n"
        b'@Table(name = "users")\n'
        b"public class User extends Base implements Serializable, Comparable<User> {\n"
        b"}\n"
    )
    c = _items(src)[("class", "User")]
    assert c["signature"] == (
        "public class User extends Base implements Serializable, Comparable<User>"
    )
    assert c["bases"] == ["Base", "Serializable", "Comparable<User>"]
    assert c["visibility"] == "public"
    assert c["decorators"] == ["Entity", 'Table(name = "users")']
    for absent in ("params", "returns", "type_params", "is_async"):
        assert absent not in c, f"{absent} must be omitted when empty"
    # Pre-existing xref-consumed fields are preserved, not replaced.
    assert c["parent"] is None
    assert c["extends"] == "Base"
    assert "Serializable" in c["implements"]
    for span in ("line_start", "line_end", "byte_start", "byte_end"):
        assert span in c


def test_bare_class_omits_all_empty_fields():
    src = b"class Plain {\n}\n"
    c = _items(src)[("class", "Plain")]
    assert c["signature"] == "class Plain"
    for absent in ("params", "returns", "bases", "type_params",
                   "visibility", "is_async", "decorators"):
        assert absent not in c, f"{absent} must be omitted when empty"


def test_enum_implements_and_visibility():
    src = (
        b"public enum Color implements Printable {\n"
        b"    RED;\n"
        b"    public void print() { }\n"
        b"}\n"
    )
    by = _items(src)
    e = by[("enum", "Color")]
    assert e["signature"] == "public enum Color implements Printable"
    assert e["bases"] == ["Printable"]
    assert e["visibility"] == "public"
    m = by[("method", "print")]
    assert m["signature"] == "public void print()"
    assert m["returns"] == "void"
    assert m["visibility"] == "public"
    assert "params" not in m


# ---------------------------------------------------------------------------
# methods
# ---------------------------------------------------------------------------
def test_generic_method_params_returns_visibility_type_params():
    src = (
        b"public class Util {\n"
        b"    protected static <T extends Comparable<T>> T max(List<T> items, T fallback) {\n"
        b"        return fallback;\n"
        b"    }\n"
        b"}\n"
    )
    m = _items(src)[("method", "max")]
    assert m["signature"] == (
        "protected static <T extends Comparable<T>> T max(List<T> items, T fallback)"
    )
    assert m["params"] == [
        {"name": "items", "type": "List<T>", "default": None},
        {"name": "fallback", "type": "T", "default": None},
    ]
    assert m["returns"] == "T"
    assert m["visibility"] == "protected"
    assert m["type_params"] == ["T extends Comparable<T>"]
    for absent in ("bases", "decorators", "is_async"):
        assert absent not in m


def test_multiline_header_collapsed_to_single_line():
    src = (
        b"public class Svc {\n"
        b"    public Map<String, List<Integer>> lookup(String key,\n"
        b"                                              int limit) {\n"
        b"        return null;\n"
        b"    }\n"
        b"}\n"
    )
    m = _items(src)[("method", "lookup")]
    assert m["signature"] == "public Map<String, List<Integer>> lookup(String key, int limit)"
    assert m["returns"] == "Map<String, List<Integer>>"
    assert m["params"] == [
        {"name": "key", "type": "String", "default": None},
        {"name": "limit", "type": "int", "default": None},
    ]


def test_annotations_become_decorators_and_leave_the_signature():
    src = (
        b"public class Handler {\n"
        b"    @Override\n"
        b'    @SuppressWarnings("unchecked")\n'
        b"    public String render() { return null; }\n"
        b"}\n"
    )
    m = _items(src)[("method", "render")]
    assert m["decorators"] == ["Override", 'SuppressWarnings("unchecked")']
    assert m["signature"] == "public String render()"


# ---------------------------------------------------------------------------
# constructors
# ---------------------------------------------------------------------------
def test_constructor_varargs_and_no_returns():
    src = (
        b"public class Point {\n"
        b"    public Point(int x, int... rest) { }\n"
        b"}\n"
    )
    c = _items(src)[("constructor", "Point")]
    assert c["signature"] == "public Point(int x, int... rest)"
    assert c["params"] == [
        {"name": "x", "type": "int", "default": None},
        {"name": "rest", "type": "int...", "default": None},
    ]
    assert c["visibility"] == "public"
    assert "returns" not in c, "constructors carry no returns field"
    assert c["parent"] == "Point"


# ---------------------------------------------------------------------------
# interfaces
# ---------------------------------------------------------------------------
def test_interface_extends_list_and_bodyless_method():
    src = (
        b"interface Store<K, V> extends AutoCloseable, Iterable<V> {\n"
        b"    V get(K key);\n"
        b"}\n"
    )
    by = _items(src)
    i = by[("interface", "Store")]
    assert i["signature"] == "interface Store<K, V> extends AutoCloseable, Iterable<V>"
    assert i["bases"] == ["AutoCloseable", "Iterable<V>"]
    assert i["type_params"] == ["K", "V"]
    assert "visibility" not in i, "package-private: no explicit keyword, no field"
    m = by[("method", "get")]
    assert m["signature"] == "V get(K key)", "trailing ';' must not leak in"
    assert m["returns"] == "V"
    assert m["params"] == [{"name": "key", "type": "K", "default": None}]
    assert "visibility" not in m


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------
def test_record_components_populate_params():
    src = (
        b"public record Point(int x, int y) implements Shape {\n"
        b"}\n"
    )
    r = _items(src)[("record", "Point")]
    assert r["signature"] == "public record Point(int x, int y) implements Shape"
    assert r["params"] == [
        {"name": "x", "type": "int", "default": None},
        {"name": "y", "type": "int", "default": None},
    ]
    assert r["bases"] == ["Shape"]
    assert r["visibility"] == "public"
    assert "returns" not in r


# ---------------------------------------------------------------------------
# global invariants
# ---------------------------------------------------------------------------
def test_visibility_only_from_explicit_keywords_and_is_async_never_set():
    src = (
        b"class Helper {\n"
        b"    void run() { }\n"
        b"    private int count() { return 0; }\n"
        b"}\n"
    )
    by = _items(src)
    assert "visibility" not in by[("class", "Helper")]
    assert "visibility" not in by[("method", "run")]
    assert by[("method", "run")]["signature"] == "void run()"
    assert by[("method", "count")]["visibility"] == "private"
    for item in by.values():
        assert "is_async" not in item, "is_async is never true for Java"
        assert "signature" in item
