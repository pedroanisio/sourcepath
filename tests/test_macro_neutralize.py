"""E1 (error-free-mapping plan) — macro harvest + byte-preserving neutralization.

linux-v23 evidence: 49% of C source files carry parse errors, median 4 error
nodes, and every sampled failure is an unexpanded macro that alters C's
token grammar: annotation macros between type and declarator
(`void __iomem *base`), iterator macros (`for_each_set_bit(...) {`), and
digit-leading pasted identifiers (`1000baseX_Full`). The repo's own
`#define`s classify the macros — no hardcoded lists — and neutralization is
byte-length-preserving so every node span stays valid against the original
content.

Run from the repo root:  python -m pytest tests/test_macro_neutralize.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.macro_neutralize import (
    MacroTable,
    harvest_macros,
    neutralize,
)
from codebase_mapper.ts_setup import TS_AVAILABLE

DEFINES = b"""\
#define __iomem __attribute__((noderef))
#define __maybe_unused __attribute__((unused))
#define __init
#define for_each_set_bit(bit, addr, size) \\
\tfor ((bit) = 0; (bit) < (size); (bit)++)
#define NR_CPUS 64
#define min(a, b) ((a) < (b) ? (a) : (b))
"""


def _table() -> MacroTable:
    t = MacroTable()
    harvest_macros(DEFINES, t)
    return t


# ------------------------------------------------------------- harvest

def test_harvest_classifies_by_define_body():
    t = _table()
    assert t.annotations >= {"__iomem", "__maybe_unused", "__init"}
    assert "for_each_set_bit" in t.iterators
    # constants / expression macros are left alone
    assert "NR_CPUS" not in t.annotations | t.iterators
    assert "min" not in t.annotations | t.iterators


# -------------------------------------------------------- neutralization

def test_annotation_macro_spaced_out_byte_preserving():
    src = b"static void write_reg(void __iomem *base, u64 v) { }\n"
    out = neutralize(src, _table())
    assert len(out) == len(src)
    assert b"__iomem" not in out
    assert out.replace(b" ", b"") == src.replace(b"__iomem", b"").replace(b" ", b"")


def test_iterator_macro_becomes_while_byte_preserving():
    src = b"void f(unsigned long *m) { int b; for_each_set_bit(b, m, 8) { g(b); } }\n"
    out = neutralize(src, _table())
    assert len(out) == len(src)
    assert b"while" in out
    assert b"for_each_set_bit" not in out


def test_strings_and_comments_are_never_touched():
    src = (b'const char *s = "for_each_set_bit(__iomem)";\n'
           b"/* __iomem in a comment */\n"
           b"// __maybe_unused too\n")
    out = neutralize(src, _table())
    assert out == src


def test_no_table_hits_returns_content_unchanged():
    src = b"int add(int a, int b) { return a + b; }\n"
    assert neutralize(src, _table()) == src


# ------------------------------------------------- end-to-end extraction

pytestmark_ts = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter absent")


@pytestmark_ts
def test_kernel_patterns_parse_clean_after_neutralization():
    from codebase_mapper.ts_setup import _TS_LANGS, _ts_setup, ts
    _ts_setup()
    parser = ts.Parser(_TS_LANGS["c"])
    src = (
        b"static int dfl_read(void *context, unsigned int reg,\n"
        b"\t\t    unsigned int *val)\n"
        b"{\n"
        b"\tvoid __iomem *base = context;\n"
        b"\treturn 0;\n"
        b"}\n"
        b"static void __maybe_unused walk(unsigned long *mask)\n"
        b"{\n"
        b"\tint pair;\n"
        b"\tfor_each_set_bit(pair, mask, 4) {\n"
        b"\t\tdfl_read(0, pair, 0);\n"
        b"\t}\n"
        b"}\n"
    )
    assert parser.parse(src).root_node.has_error, "fixture must trip vanilla grammar"
    out = neutralize(src, _table())
    assert len(out) == len(src)
    assert not parser.parse(out).root_node.has_error


@pytestmark_ts
def test_analyzer_retries_with_neutralization_and_discloses():
    """extract_c_ast_summary retries a failing parse against the
    neutralized buffer when a macro table is supplied, keeps the better
    parse, and records provenance."""
    from codebase_mapper.inspection.languages.c import extract_c_ast_summary
    src = b"void probe(void __iomem *base) { }\n"
    table = _table()
    summary, errors = extract_c_ast_summary(src, "x.c", macro_table=table)
    assert "parse_errors_present" not in errors
    assert summary["parse_buffer"] == "macro_neutralized"
    assert any(i["name"] == "probe" and i["kind"] == "function"
               for i in summary["items"])
    # clean files never pay the retry nor carry the flag
    clean, errs = extract_c_ast_summary(b"int x(void) { return 1; }\n", "y.c",
                                        macro_table=table)
    assert "parse_buffer" not in clean and errs == []
