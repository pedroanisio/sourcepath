"""F8 — parse-error diagnostics must be quantified, not a bare boolean.

On the Linux kernel, tree-sitter's ``root_node.has_error`` flagged 28,581 of
49,569 C files ``parse_errors_present`` — one recovery node anywhere flags
the whole file, so the marker cannot distinguish "one GCC-extension hiccup"
from "half the file failed to parse". The fix keeps the marker (backward
compatible) and adds a ``parse_error_nodes:<N>`` diagnostic counting
ERROR/missing nodes, which coverage aggregates per language.

Run from the repo root:  python -m pytest tests/test_parse_error_granularity.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.ts_setup import TS_AVAILABLE, parse_error_diagnostics
from codebase_mapper.inspection.coverage import (
    aggregate_coverage,
    parse_error_node_count,
)

pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter absent")

CLEAN_C = b"int add(int a, int b) { return a + b; }\n"
# Genuinely malformed: unbalanced parens/braces and stray tokens in two
# separate regions, so the tree carries multiple ERROR nodes.
BROKEN_C = (
    b"int broken( { return 0; }\n"
    b"@@ stray tokens @@\n"
    b"void g(void) { if (a b c } ;\n"
)


def _parse_c(content: bytes):
    from codebase_mapper.ts_setup import _TS_LANGS, _ts_setup, ts
    _ts_setup()
    parser = ts.Parser(_TS_LANGS["c"])
    return parser.parse(content)


def test_clean_parse_yields_no_diagnostics():
    tree = _parse_c(CLEAN_C)
    assert parse_error_diagnostics(tree.root_node) == []


def test_broken_parse_yields_marker_and_count():
    tree = _parse_c(BROKEN_C)
    diags = parse_error_diagnostics(tree.root_node)
    assert "parse_errors_present" in diags
    counts = [d for d in diags if d.startswith("parse_error_nodes:")]
    assert len(counts) == 1
    n = int(counts[0].split(":", 1)[1])
    assert n >= 1


def test_analyzer_emits_quantified_diagnostics():
    from codebase_mapper.inspection.languages.c import extract_c_ast_summary
    summary, errors = extract_c_ast_summary(BROKEN_C, "x.c")
    assert summary is not None
    assert "parse_errors_present" in errors
    assert any(e.startswith("parse_error_nodes:") for e in errors)
    summary, errors = extract_c_ast_summary(CLEAN_C, "y.c")
    assert errors == []


def test_parse_error_node_count_reader():
    assert parse_error_node_count(["parse_errors_present",
                                   "parse_error_nodes:37"]) == 37
    assert parse_error_node_count(["parse_errors_present"]) == 0
    assert parse_error_node_count([]) == 0
    assert parse_error_node_count(["parse_error_nodes:junk:3"]) == 0


def test_coverage_aggregates_parse_error_nodes():
    class R:
        def __init__(self, path, errors):
            self.path = path
            self.language = "c"
            self.type_ = "source_code"
            self.extraction_errors = errors
            self.ast_summary = {"items": [], "imports": []}

    records = [
        R("a.c", ["parse_errors_present", "parse_error_nodes:3"]),
        R("b.c", ["parse_errors_present", "parse_error_nodes:40"]),
        R("c.c", []),
    ]
    cov = aggregate_coverage(records)
    assert cov["by_language"]["c"]["files_with_parse_errors"] == 2
    assert cov["by_language"]["c"]["parse_error_nodes"] == 43
    assert cov["totals"]["parse_error_nodes"] == 43
