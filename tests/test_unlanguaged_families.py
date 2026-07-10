"""E2 (error-free-mapping plan) — the seven unlanguaged families get languages.

linux-v23 evidence: 29,012 files (31%) carried no language; the measured
histogram is seven families (yaml 5,665 · rst 4,011 · devicetree 6,708 ·
Makefile 3,196 · Kconfig 1,829 · asm 1,346 · json 1,072). Tier 1 assigns
languages (census correctness needs no parser); asm/devicetree/kconfig/make
also get line-oriented extractors so their symbols exist and coverage stays
honest. Data/doc languages must NOT flip test-fixture files to test_code.

Run from the repo root:  python -m pytest tests/test_unlanguaged_families.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.classify import classify, language_of
from codebase_mapper.inspection.languages.lightweight import (
    extract_asm_summary,
    extract_devicetree_summary,
    extract_kconfig_summary,
    extract_make_summary,
)


@pytest.mark.parametrize("path,lang", [
    ("Documentation/devicetree/bindings/net/renesas.yaml", "yaml"),
    ("scripts/spelling.json", "json"),
    ("Documentation/admin-guide/index.rst", "restructuredtext"),
    ("Documentation/atomic_t.txt", "text"),
    ("arch/x86/entry/entry_64.S", "asm"),
    ("arch/alpha/lib/udiv.s", "asm"),
    ("arch/arm/boot/dts/allwinner/sun4i-a10.dts", "devicetree"),
    ("arch/arm/boot/dts/allwinner/sunxi.dtsi", "devicetree"),
    ("drivers/net/Makefile", "make"),
    ("scripts/Makefile.build", "make"),
    ("scripts/Kbuild.include", "make"),
    ("drivers/gpu/Kconfig", "kconfig"),
    ("init/Kconfig.suspend", "kconfig"),
])
def test_family_language_assignment(path, lang):
    assert language_of(path) == lang


def test_data_languages_do_not_become_test_code():
    # a YAML fixture inside a tests dir stays configuration, not test_code
    assert classify("pkg/tests/fixtures/case1.yaml", b"key: 1\n") != "test_code"
    assert classify("pkg/tests/notes.txt", b"notes\n") != "test_code"
    # code languages in tests dirs still do
    assert classify("pkg/tests/helper.py", b"x = 1\n") == "test_code"


# ---------------------------------------------------------- extractors

def test_asm_symbols():
    src = (b"/* comment */\n"
           b".globl memcpy\n"
           b"memcpy:\n"
           b"\tret\n"
           b"ENTRY(strcpy)\n"
           b"SYM_FUNC_START(memset)\n"
           b".include \"macros.s\"\n")
    s, errors = extract_asm_summary(src, "arch/x86/lib/x.S")
    assert errors == []
    names = {(i["kind"], i["name"]) for i in s["items"]}
    assert ("label", "memcpy") in names
    assert ("function", "strcpy") in names
    assert ("function", "memset") in names
    assert any(imp["source"] == "macros.s" for imp in s["imports"])


def test_kconfig_symbols_and_depends():
    src = (b"menu \"Drivers\"\n"
           b"config USB_SUPPORT\n"
           b"\tbool \"USB support\"\n"
           b"\tdepends on HAS_IOMEM\n"
           b"\tselect NLS\n"
           b"menuconfig SND\n"
           b"\ttristate \"Sound\"\n"
           b"source \"drivers/usb/Kconfig\"\n"
           b"endmenu\n")
    s, errors = extract_kconfig_summary(src, "drivers/Kconfig")
    assert errors == []
    names = {(i["kind"], i["name"]) for i in s["items"]}
    assert ("config", "USB_SUPPORT") in names
    assert ("config", "SND") in names
    sources = {imp["source"] for imp in s["imports"]}
    assert "drivers/usb/Kconfig" in sources


def test_devicetree_nodes():
    src = (b"/dts-v1/;\n"
           b"/ {\n"
           b"\tcompatible = \"allwinner,sun4i-a10\";\n"
           b"\tcpus {\n"
           b"\t\tcpu@0 { };\n"
           b"\t};\n"
           b"\tuart0: serial@1c28000 { };\n"
           b"};\n"
           b'#include "sunxi-common.dtsi"\n')
    s, errors = extract_devicetree_summary(src, "a.dts")
    assert errors == []
    names = {(i["kind"], i["name"]) for i in s["items"]}
    assert ("node", "cpus") in names
    assert ("node", "cpu@0") in names
    assert ("node", "uart0: serial@1c28000") in names
    assert any(imp["source"] == "sunxi-common.dtsi" for imp in s["imports"])


def test_make_targets():
    src = (b"obj-$(CONFIG_USB) += usb.o\n"
           b"all: build\n"
           b"build:\n"
           b"\t$(CC) -o out main.c\n"
           b".PHONY: clean\n"
           b"clean:\n"
           b"\trm -f out\n"
           b"include scripts/Makefile.lib\n")
    s, errors = extract_make_summary(src, "Makefile")
    assert errors == []
    names = {i["name"] for i in s["items"] if i["kind"] == "target"}
    assert {"all", "build", "clean"} <= names
    assert ".PHONY" not in names
    assert any(imp["source"] == "scripts/Makefile.lib" for imp in s["imports"])


def test_analyzers_are_registered():
    from codebase_mapper.shared_kernel.extensions import (
        iter_language_analyzers, reset_registries,
    )
    reset_registries()

    class _R:
        def __init__(self, path, language):
            self.path = path
            self.language = language
            self.type_ = "source_code"

    langs_covered = set()
    for a in iter_language_analyzers():
        for lang in ("asm", "kconfig", "devicetree", "make"):
            if a.matches(_R("x", lang), None):
                langs_covered.add(lang)
    assert langs_covered == {"asm", "kconfig", "devicetree", "make"}
