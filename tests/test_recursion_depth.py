"""Regression tests: deeply-nested sources must not crash extraction.

A recursive Python walk over a tree-sitter CST took one frame per depth level,
so a deeply-nested file (a generated/macro-expanded Chromium Obj-C++ unit, in
the field) overflowed the default recursion limit (~1000) with a
``RecursionError`` *inside* ``analyzer.extract`` — and because the pipeline did
not contain extractor failures, it aborted the entire mapping run.

This pins both halves of the fix:
  * the whole-tree CST walkers/builders are iterative (no Python frame ceiling),
    so a deeply-nested file extracts instead of raising; and
  * the pipeline contains any extractor failure (``_safe_extract``), so one
    pathological file degrades into a recorded error rather than aborting.

Run from the repo root:  uv run python -m pytest tests/test_recursion_depth.py
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from codebase_mapper.inspection.languages._treewalk import (
    find_named_descendant,
    iter_named_pre_order,
)
from codebase_mapper.inspection.pipeline import _safe_extract
from codebase_mapper.ts_setup import TS_AVAILABLE

# Depth comfortably past the default recursion limit (~1000): the old recursive
# walk overflowed here; the iterative walk does not care.
DEPTH = 5000


# ---------------------------------------------------------------------------
# Iterative traversal primitives (no tree-sitter needed)
# ---------------------------------------------------------------------------


class _N:
    """Minimal tree-sitter-Node stand-in for the duck-typed walkers."""

    def __init__(self, type_, children=(), is_named=True):
        self.type = type_
        self.children = list(children)
        self.is_named = is_named


def test_iter_named_pre_order_matches_recursive_order():
    leaf_a, leaf_b, leaf_c = _N("a"), _N("b"), _N("c")
    mid = _N("mid", [leaf_b, leaf_c])
    root = _N("root", [leaf_a, mid])
    assert [n.type for n in iter_named_pre_order(root)] == ["root", "a", "mid", "b", "c"]


def test_iter_named_pre_order_skips_anonymous_children():
    named, anon = _N("kept"), _N(";", is_named=False)
    root = _N("root", [anon, named])
    assert [n.type for n in iter_named_pre_order(root)] == ["root", "kept"]


def test_iter_named_pre_order_prunes_subtree():
    pruned = _N("stop", [_N("hidden")])
    root = _N("root", [pruned, _N("seen")])
    visited = [n.type for n in iter_named_pre_order(root, descend=lambda n: n.type != "stop")]
    assert visited == ["root", "stop", "seen"]  # "hidden" never visited


def test_iter_named_pre_order_handles_pathological_depth():
    node = _N("leaf")
    for _ in range(DEPTH):
        node = _N("nest", [node])
    # A recursive walk would RecursionError here; the iterative one must not.
    assert sum(1 for _ in iter_named_pre_order(node)) == DEPTH + 1


def test_find_named_descendant():
    target = _N("want", [_N("x")])
    root = _N("root", [_N("a"), _N("b", [target])])
    assert find_named_descendant(root, {"want"}) is target
    assert find_named_descendant(root, {"missing"}) is None


# ---------------------------------------------------------------------------
# Pipeline containment — one failing file must not abort the run
# ---------------------------------------------------------------------------


class _RaisingAnalyzer:
    name = "boom"

    def extract(self, rec, content, ctx):
        raise RecursionError("maximum recursion depth exceeded")


class _OtherRaisingAnalyzer:
    name = "boom2"

    def extract(self, rec, content, ctx):
        raise ValueError("malformed node")


class _GoodAnalyzer:
    name = "ok"

    def extract(self, rec, content, ctx):
        return {"language": "x"}, ["a_warning"]


def test_safe_extract_contains_recursion_error():
    summary, errors = _safe_extract(_RaisingAnalyzer(), SimpleNamespace(path="f"), b"", None)
    assert summary is None
    assert any("recursion" in e.lower() for e in errors)


def test_safe_extract_contains_any_exception():
    summary, errors = _safe_extract(_OtherRaisingAnalyzer(), SimpleNamespace(path="f"), b"", None)
    assert summary is None
    assert errors and "ValueError" in errors[0]


def test_safe_extract_passes_through_success():
    summary, errors = _safe_extract(_GoodAnalyzer(), SimpleNamespace(path="f"), b"", None)
    assert summary == {"language": "x"}
    assert errors == ["a_warning"]


# ---------------------------------------------------------------------------
# Whole-tree CST walkers/builders on a deeply-nested real file (needs tree-sitter)
# ---------------------------------------------------------------------------


def _deep_expr(open_: str = "(", close: str = ")") -> str:
    """A 5000-deep parenthesized expression — exercises every whole-tree walker
    that descends into a function body."""
    return open_ * DEPTH + "1" + close * DEPTH


@pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")
@pytest.mark.parametrize(
    "extractor_path,filename,source",
    [
        ("objc", "deep.m", f"static int f(void) {{ return {_deep_expr()}; }}\n"),
        ("cpp", "deep.cpp", f"int f() {{ return {_deep_expr()}; }}\n"),
        ("java", "Deep.java", f"class Deep {{ int f() {{ return {_deep_expr()}; }} }}\n"),
        ("rust", "deep.rs", f"fn f() -> i32 {{ {_deep_expr()} }}\n"),
        ("tsjs", "deep.ts", f"const x = {_deep_expr()};\n"),
    ],
)
def test_deeply_nested_file_extracts_without_recursion_error(extractor_path, filename, source):
    content = source.encode("utf-8")
    if extractor_path == "objc":
        from codebase_mapper.inspection.languages.objc import extract_objc_ast_summary
        summary, _errors = extract_objc_ast_summary(content, filename)
    elif extractor_path == "cpp":
        from codebase_mapper.inspection.languages.cpp import extract_cpp_ast_summary
        summary, _errors = extract_cpp_ast_summary(content, filename)
    elif extractor_path == "java":
        from codebase_mapper.inspection.languages.java import extract_java_ast_summary
        summary, _errors = extract_java_ast_summary(content, filename)
    elif extractor_path == "rust":
        from codebase_mapper.inspection.languages.rust import extract_rust_ast_summary
        summary, _errors = extract_rust_ast_summary(content, filename)
    else:
        from codebase_mapper.inspection.languages.tsjs import extract_tsjs_ast_summary
        summary, _errors = extract_tsjs_ast_summary(content, filename, "typescript")
    # The point is that the call returns at all (no RecursionError); a valid
    # summary dict is the expected, stronger outcome.
    assert summary is not None


@pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter not available")
def test_deep_tsjs_file_round_trips_byte_exact():
    """The iterative CST builder must preserve byte-exact regenerate even on a
    pathologically deep file."""
    from codebase_mapper.inspection.languages.tsjs import (
        extract_tsjs_ast_summary,
        regenerate_tsjs_source,
    )

    source = f"const x = {_deep_expr('[', ']')};\n"
    content = source.encode("utf-8")
    summary, _errors = extract_tsjs_ast_summary(content, "deep.ts", "typescript")
    assert summary is not None and summary.get("cst_json") is not None
    assert regenerate_tsjs_source(summary) == source
