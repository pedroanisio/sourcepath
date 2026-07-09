"""TDD spec — Python signature/type extraction on L2 chunks (Tier 2, delivery 3).

Contract under test (see plugins/chunks_embeddings/signatures.py):
every symbol-level chunk MAY carry the optional, mechanically-derived fields

    signature    str                       declaration header, reconstructed
    params       list[{name, type, default}]   type/default are None when absent
    returns      str | None
    bases        list[str]                 class bases (as written in source)
    type_params  list[str]                 PEP 695 type parameters
    visibility   str | None                not applicable to Python (never set)
    is_async     bool                      present only when True
    decorators   list[str]                 as written, without leading '@'

Fields are OMITTED from the chunk dict when empty/unknown — never emitted as
empty lists or None placeholders. All values are parsed from source via
``ast`` (confidence: certain), never inferred.

Run: python -m pytest tests/test_signatures_python.py
"""
from __future__ import annotations

import sys

import pytest

from plugins.chunks_embeddings.chunker import _chunk_python


def _by_symbol(chunks):
    return {c["symbol"]: c for c in chunks}


# ---------------------------------------------------------------------------
# functions
# ---------------------------------------------------------------------------
def test_annotated_function_signature_params_returns():
    src = (
        b"def score(user: User, weight: float = 1.0, *args: int, "
        b"flag: bool = False, **extra: str) -> Score:\n"
        b"    return Score()\n"
    )
    c = _by_symbol(_chunk_python(src, "m.py"))["score"]
    assert c["signature"] == (
        "def score(user: User, weight: float = 1.0, *args: int, "
        "flag: bool = False, **extra: str) -> Score"
    )
    assert c["returns"] == "Score"
    assert c["params"] == [
        {"name": "user", "type": "User", "default": None},
        {"name": "weight", "type": "float", "default": "1.0"},
        {"name": "*args", "type": "int", "default": None},
        {"name": "flag", "type": "bool", "default": "False"},
        {"name": "**extra", "type": "str", "default": None},
    ]


def test_bare_function_omits_empty_fields():
    src = b"def run(x, y):\n    return x\n"
    c = _by_symbol(_chunk_python(src, "m.py"))["run"]
    assert c["signature"] == "def run(x, y)"
    assert c["params"] == [
        {"name": "x", "type": None, "default": None},
        {"name": "y", "type": None, "default": None},
    ]
    for absent in ("returns", "bases", "type_params", "decorators",
                   "is_async", "visibility"):
        assert absent not in c, f"{absent} must be omitted when empty"


def test_async_function_marked_and_signature_prefixed():
    src = b"async def fetch(url: str) -> bytes:\n    ...\n"
    c = _by_symbol(_chunk_python(src, "m.py"))["fetch"]
    assert c["is_async"] is True
    assert c["signature"].startswith("async def fetch(")


def test_positional_only_params_preserved():
    src = b"def f(a, /, b, *, c):\n    ...\n"
    c = _by_symbol(_chunk_python(src, "m.py"))["f"]
    names = [p["name"] for p in c["params"]]
    assert names == ["a", "/", "b", "*", "c"]


def test_decorators_recorded_without_at_sign():
    src = (
        b"@app.route('/x')\n"
        b"@functools.cache\n"
        b"def handler():\n    ...\n"
    )
    c = _by_symbol(_chunk_python(src, "m.py"))["handler"]
    assert c["decorators"] == ["app.route('/x')", "functools.cache"]


# ---------------------------------------------------------------------------
# classes
# ---------------------------------------------------------------------------
def test_class_bases_and_signature():
    src = (
        b"class Repo(Base, Generic[T], metaclass=ABCMeta):\n"
        b"    def get(self, key: str) -> T | None:\n"
        b"        ...\n"
    )
    by = _by_symbol(_chunk_python(src, "m.py"))
    cls = by["Repo"]
    assert cls["bases"] == ["Base", "Generic[T]"]
    assert cls["signature"] == "class Repo(Base, Generic[T], metaclass=ABCMeta)"
    # method chunk carries its own signature and parent linkage
    m = by["get"]
    assert m["parent_symbol"] == "Repo"
    assert m["signature"] == "def get(self, key: str) -> T | None"
    assert m["returns"] == "T | None"


def test_plain_class_omits_bases():
    src = b"class Plain:\n    pass\n"
    c = _by_symbol(_chunk_python(src, "m.py"))["Plain"]
    assert c["signature"] == "class Plain"
    assert "bases" not in c


@pytest.mark.skipif(sys.version_info < (3, 12),
                    reason="PEP 695 type-parameter syntax needs the 3.12+ "
                           "stdlib ast parser; on older interpreters the "
                           "chunker's documented SyntaxError fallback "
                           "(whole-file chunk) applies instead")
def test_pep695_type_params():
    src = b"class Box[T]:\n    pass\n"
    c = _by_symbol(_chunk_python(src, "m.py"))["Box"]
    assert c["type_params"] == ["T"]
    assert c["signature"] == "class Box[T]"


# ---------------------------------------------------------------------------
# invariants of the existing chunk contract (must not regress)
# ---------------------------------------------------------------------------
def test_byte_spans_unaffected_by_signature_extraction():
    src = b"def a():\n    return 1\n\ndef b():\n    return 2\n"
    chunks = _chunk_python(src, "m.py")
    for c in chunks:
        assert src[c["byte_start"]:c["byte_end"]].decode() == c["text"]
