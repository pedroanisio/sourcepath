"""TDD spec — TypeScript/JavaScript signature/type extraction on L2 chunks.

Contract (plugins/chunks_embeddings/signatures.py): optional fields
signature / params / returns / bases / type_params / visibility / is_async /
decorators, OMITTED when empty — never placeholders.

Conventions pinned here:
  * ``signature`` is the inner declaration header (without the ``export``
    keyword), from the declaration start to (excluding) the body, collapsed
    to a single line. Arrow-function chunks use the declarator text up to and
    including the ``=>``.
  * Param names are as written: rest params keep the ``...`` prefix and
    optional params keep the ``?`` suffix (no data loss; the plain name is
    trivially recoverable).
  * ``returns`` drops the leading ``:`` of the type annotation.
  * ``visibility`` is the TS accessibility keyword only (public/private/
    protected); ES ``#private`` naming stays in the name.

Run: python -m pytest tests/test_signatures_tsjs.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.ts_setup import TS_AVAILABLE
from plugins.chunks_embeddings.chunker import _chunk_tsjs

pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")


def _by_symbol(chunks):
    return {c["symbol"]: c for c in chunks}


# ---------------------------------------------------------------------------
# functions (TypeScript)
# ---------------------------------------------------------------------------
def test_ts_annotated_function_signature_params_returns():
    src = (
        b"export async function fetchUser(id: number, opts: Options = {}):"
        b" Promise<User> {\n  return get(id, opts);\n}\n"
    )
    c = _by_symbol(_chunk_tsjs(src, "m.ts"))["fetchUser"]
    assert c["signature"] == (
        "async function fetchUser(id: number, opts: Options = {}): Promise<User>"
    )
    assert c["returns"] == "Promise<User>"
    assert c["is_async"] is True
    assert c["params"] == [
        {"name": "id", "type": "number", "default": None},
        {"name": "opts", "type": "Options", "default": "{}"},
    ]


def test_ts_rest_and_optional_params_as_written():
    src = b"function f(x?: number, ...rest: string[]) {}\n"
    c = _by_symbol(_chunk_tsjs(src, "m.ts"))["f"]
    assert c["params"] == [
        {"name": "x?", "type": "number", "default": None},
        {"name": "...rest", "type": "string[]", "default": None},
    ]
    assert "returns" not in c


def test_ts_generic_function_type_params():
    src = b"function pick<T, K extends keyof T>(obj: T, key: K): T[K] { return obj[key]; }\n"
    c = _by_symbol(_chunk_tsjs(src, "m.ts"))["pick"]
    assert c["type_params"] == ["T", "K extends keyof T"]
    assert c["returns"] == "T[K]"


# ---------------------------------------------------------------------------
# classes and methods (TypeScript)
# ---------------------------------------------------------------------------
def test_ts_class_heritage_and_method_visibility():
    src = (
        b"export class Repo extends Base implements Store<User> {\n"
        b"  private async load(id: number): Promise<void> {}\n"
        b"  public get(key: string): User | null { return null; }\n"
        b"}\n"
    )
    by = _by_symbol(_chunk_tsjs(src, "m.ts"))
    cls = by["Repo"]
    assert cls["bases"] == ["Base", "Store<User>"]
    assert cls["signature"] == "class Repo extends Base implements Store<User>"
    load = by["load"]
    assert load["parent_symbol"] == "Repo"
    assert load["visibility"] == "private"
    assert load["is_async"] is True
    assert load["signature"] == "private async load(id: number): Promise<void>"
    get = by["get"]
    assert get["visibility"] == "public"
    assert get["returns"] == "User | null"


def test_ts_generic_class_type_params_no_bases():
    src = b"class Box<T> { value: T; take(): T { return this.value; } }\n"
    cls = _by_symbol(_chunk_tsjs(src, "m.ts"))["Box"]
    assert cls["type_params"] == ["T"]
    assert "bases" not in cls
    assert cls["signature"] == "class Box<T>"


# ---------------------------------------------------------------------------
# arrow functions
# ---------------------------------------------------------------------------
def test_arrow_function_signature_up_to_arrow():
    src = b"export const sum = async (a: number, b: number): number => a + b;\n"
    c = _by_symbol(_chunk_tsjs(src, "m.ts"))["sum"]
    assert c["signature"] == "const sum = async (a: number, b: number): number =>"
    assert c["is_async"] is True
    assert c["returns"] == "number"
    assert c["params"] == [
        {"name": "a", "type": "number", "default": None},
        {"name": "b", "type": "number", "default": None},
    ]


# ---------------------------------------------------------------------------
# plain JavaScript + omission contract
# ---------------------------------------------------------------------------
def test_js_untyped_function_params_have_none_types():
    src = b"function run(x, y = 3) { return x + y; }\n"
    c = _by_symbol(_chunk_tsjs(src, "m.js"))["run"]
    assert c["signature"] == "function run(x, y = 3)"
    assert c["params"] == [
        {"name": "x", "type": None, "default": None},
        {"name": "y", "type": None, "default": "3"},
    ]
    for absent in ("returns", "bases", "type_params", "visibility",
                   "is_async", "decorators"):
        assert absent not in c, f"{absent} must be omitted when empty"


def test_byte_spans_and_text_unchanged():
    src = b"export function a() { return 1; }\nfunction b() { return 2; }\n"
    for c in _chunk_tsjs(src, "m.ts"):
        assert src[c["byte_start"]:c["byte_end"]].decode() == c["text"]
