"""E9 golden corpus — every confirmed flaw family, end-to-end, forever.

Each case builds a minimal git repo exhibiting one historical flaw family
(flaw map F1–F20 / plan E1–E5), runs the real pipeline (map → emit →
verify-bundle), and asserts the exact numbers the fix guarantees. A future
regression fails here before it ships. New flaws add a case red-first.

Run from the repo root:  python -m pytest tests/test_golden_corpus.py
"""
from __future__ import annotations

import json
import subprocess

import pytest

from codebase_mapper.verification.bundle_gate import Budgets, check_bundle

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "1111111111 +0000",
    "GIT_COMMITTER_DATE": "1111111111 +0000",
    "PATH": "/usr/bin:/bin",
}

OCTAVE_M = "% GNU Octave script\nfunction r = f(x)\n  r = x + 1;\nendfunction\n"
PLAIN_C_HEADER = ("#ifndef _FS_H\n#define _FS_H\n"
                  "struct inode;\nextern int register_fs(void);\n#endif\n")
MACRO_PATTERNS_H = (
    "#define __iomem __attribute__((noderef))\n"
    "#define __maybe_unused __attribute__((unused))\n"
    "#define for_each_set_bit(bit, addr, size) \\\n"
    "\tfor ((bit) = 0; (bit) < (size); (bit)++)\n")
MACRO_PATTERNS_C = (
    '#include "patterns.h"\n'
    "static int rd(void *ctx, unsigned int reg, unsigned int *val)\n"
    "{\n\tvoid __iomem *base = ctx;\n\treturn 0;\n}\n"
    "static void __maybe_unused walk(unsigned long *mask)\n"
    "{\n\tint pair;\n\tfor_each_set_bit(pair, mask, 4) {\n\t\trd(0, pair, 0);\n\t}\n}\n")


def _build(tmp_path, files: dict[str, str]):
    """files → git repo → mapped bundle. Returns (bundle_dir, manifest)."""
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries

    repo = tmp_path / "repo"
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    mapped = map_codebase(repo, "HEAD")
    out = tmp_path / "bundle"
    manifest = emit("corpus", mapped, out, emit_blobs_flag=True)
    return out, manifest


def _gate(bundle, **kw):
    return check_bundle(bundle, Budgets(), **kw)


# ---- F1: the lone Octave .m must not poison header languages ----------

def test_corpus_octave_m(tmp_path):
    bundle, man = _build(tmp_path, {
        "tools/check.m": OCTAVE_M,
        "src/main.c": "int main(void) { return 0; }\n",
        "include/fs.h": PLAIN_C_HEADER,
    })
    langs = man["files_by_language"]
    assert langs.get("matlab") == 1
    assert "objective-c" not in langs
    assert langs.get("c") == 2
    assert _gate(bundle) == []


# ---- F20: incidental C++ tooling must not claim plain-C headers -------

def test_corpus_incidental_cpp(tmp_path):
    bundle, man = _build(tmp_path, {
        "tools/plugin.cpp": "namespace t { int f() { return 1; } }\n",
        "src/a.c": "int a(void) { return 0; }\n",
        "include/fs.h": PLAIN_C_HEADER,
    })
    langs = man["files_by_language"]
    assert langs.get("cpp") == 1
    assert langs.get("c") == 2, "plain-C header must stay c"
    assert _gate(bundle) == []


# ---- F7/F17: kselftest layout yields test files AND evidence ----------

def test_corpus_kselftest_layout(tmp_path):
    bundle, man = _build(tmp_path, {
        "mm/userfaultfd.c": "int handle_userfault(void) { return 0; }\n",
        "tools/testing/selftests/mm/uffd_stress.c":
            '#include "../../../../mm/userfaultfd.c"\n'
            "int main(void) { return handle_userfault(); }\n",
    })
    assert man["files_by_type"].get("test_code") == 1
    assert man["counts"]["tests_edges"] >= 1
    assert _gate(bundle) == []


# ---- E1: the three macro patterns parse clean via neutralization ------

def test_corpus_macro_patterns(tmp_path):
    bundle, man = _build(tmp_path, {
        "include/patterns.h": MACRO_PATTERNS_H,
        "drivers/user.c": MACRO_PATTERNS_C,
    })
    totals = man["ast_coverage"]["totals"]
    assert totals["files_with_parse_errors"] == 0, \
        "macro neutralization must clear the three kernel patterns"
    assert _gate(bundle) == []


# ---- E3: macro-only header is not a silent zero ------------------------

def test_corpus_macro_only_header(tmp_path):
    bundle, man = _build(tmp_path, {
        "include/irqs.h": ("#define IRQ_BASE 16\n#define NR_IRQS 64\n"
                           "#define CAT(x, y) x##y\n"),
        "src/a.c": '#include "../include/irqs.h"\nint a(void) { return NR_IRQS; }\n',
    })
    totals = man["ast_coverage"]["totals"]
    assert totals["silent_zero_symbol_files"] == 0
    assert totals["symbols_extracted"] >= 4  # 3 macros + 1 function
    assert _gate(bundle) == []


# ---- E2: the seven unlanguaged families all carry languages ------------

def test_corpus_unlanguaged_families(tmp_path):
    bundle, man = _build(tmp_path, {
        "config/a.yaml": "key: 1\n",
        "docs/b.rst": "Title\n=====\n",
        "boot/c.dts": "/dts-v1/;\n/ {\n\tcpus {\n\t};\n};\n",
        "Makefile": "all: build\nbuild:\n\ttrue\n",
        "drivers/Kconfig": "config FOO\n\tbool \"foo\"\n",
        "arch/entry.S": "start:\n\tret\n",
        "data/d.json": "{}\n",
        "notes/e.txt": "note\n",
        "src/a.c": "int a(void) { return 0; }\n",
    })
    langs = man["files_by_language"]
    assert "(none)" not in langs, f"unlanguaged files remain: {langs}"
    for lang in ("yaml", "restructuredtext", "devicetree", "make",
                 "kconfig", "asm", "json", "text"):
        assert langs.get(lang) == 1, (lang, langs)
    assert _gate(bundle) == []


# ---- F19: a deep CST cannot kill emit ----------------------------------

def test_corpus_deep_cst(tmp_path):
    deep = "(" * 3000 + "1" + ")" * 3000
    bundle, man = _build(tmp_path, {
        "src/deep.ts": f"export const x = {deep};\n",
        "src/ok.ts": "export const y = 1;\n",
    })
    # emit survived; any depth truncation is disclosed, not silent
    assert (bundle / "run_manifest.json").is_file()
    for d in man["degradations"]:
        assert d["component"] in {"emission"}, d


# ---- E5: local working trees are not shallow — full provenance --------

def test_corpus_provenance_healthy(tmp_path):
    bundle, man = _build(tmp_path, {"src/a.py": "x = 1\n"})
    assert man["degradations"] == []
    assert _gate(bundle) == []
