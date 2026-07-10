"""E3 (error-free-mapping plan) — macro/extern-only headers must yield symbols.

linux-v23 evidence: 7,380 "silent zero-symbol" files — the C extractor's
walker covers functions, aggregates, and typedefs, so a kernel-style header
consisting of #defines and extern declarations parses cleanly to zero items.
Macros ARE the kernel's API surface; a zero-symbol file must be an anomaly
with an explicit reason, not a silent census hole.

Run from the repo root:  python -m pytest tests/test_c_symbol_completeness.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.ts_setup import TS_AVAILABLE
from codebase_mapper.inspection.languages.c import extract_c_ast_summary

pytestmark = pytest.mark.skipif(not TS_AVAILABLE, reason="tree-sitter absent")

MACRO_ONLY_HEADER = b"""\
#ifndef _MACHVEC_IMPL_H
#define _MACHVEC_IMPL_H

#include <linux/init.h>

#define IRQ_BASE        16
#define NR_IRQS         (IRQ_BASE + 48)
#define CAT1(x, y)      x##y
#define DO_DEFAULT_RTC  .rtc_boot_cpu_only = 0

extern struct alpha_machine_vector alpha_mv;
extern int alpha_using_srm;

#endif
"""


def _items(content: bytes):
    summary, errors = extract_c_ast_summary(content, "x.h")
    assert summary is not None
    return summary


def test_object_macros_become_symbols():
    s = _items(MACRO_ONLY_HEADER)
    macros = {i["name"] for i in s["items"] if i["kind"] == "macro"}
    # include-guard defines are still macros — mechanically true, no filtering
    assert {"IRQ_BASE", "NR_IRQS", "_MACHVEC_IMPL_H"} <= macros


def test_function_like_macros_become_symbols():
    s = _items(MACRO_ONLY_HEADER)
    m = next(i for i in s["items"] if i["name"] == "CAT1")
    assert m["kind"] == "macro"
    assert m["line_start"] >= 1 and m["byte_end"] > m["byte_start"]


def test_extern_variable_declarations_become_symbols():
    s = _items(MACRO_ONLY_HEADER)
    variables = {i["name"] for i in s["items"] if i["kind"] == "variable"}
    assert {"alpha_mv", "alpha_using_srm"} <= variables


def test_macro_only_header_is_not_zero_symbol():
    s = _items(MACRO_ONLY_HEADER)
    assert len(s["items"]) >= 6


def test_genuinely_empty_file_carries_reason():
    s = _items(b"/* nothing but a comment */\n")
    assert s["items"] == []
    assert s["zero_symbol_reason"] == "no_declarations_found"


def test_existing_extraction_unchanged():
    s = _items(b"struct foo { int x; };\nint bar(void) { return 0; }\n"
               b"typedef unsigned int u32_t;\n")
    kinds = {(i["kind"], i["name"]) for i in s["items"]}
    assert ("struct", "foo") in kinds
    assert ("function", "bar") in kinds
    assert ("typedef", "u32_t") in kinds
    assert "zero_symbol_reason" not in s


def test_variable_signature_excludes_initializer():
    """A kernel-style data table must not embed its megabyte initializer in
    the item signature — one such literal blew pyoxigraph's 16 MiB buffer
    at emit (observed live on sound/usb/quirks-table.h-shaped tables)."""
    big_init = ", ".join(str(i) for i in range(20_000))
    src = ("static const struct usb_device_id quirks_table[] = { "
           + big_init + " };\n").encode()
    s = _items(src)
    item = next(i for i in s["items"] if i["name"] == "quirks_table")
    assert item["kind"] == "variable"
    assert len(item["signature"]) < 200, len(item["signature"])
    assert "19999" not in item["signature"]
    # spans still cover the whole declaration (chunking needs them)
    assert item["byte_end"] > len(src) - 10
