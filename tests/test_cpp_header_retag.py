"""F20 — the C++ header retag must not claim plain-C headers repo-wide.

The linux-v23 verification showed the F1 defect had a twin: with the objc
retag fixed, `refine_cpp_header_languages`' project-wide rule claimed the
same ~13.5K kernel headers as cpp — 246 genuine C++ files under tools/
armed pass 2, which retags every `.h` lacking a sibling `.c` with no
per-header evidence at all. Same fix shape as F1: the project-wide rule
now requires the header's own content to carry C++ markers; the sibling
rule (co-resident C++ source) is unchanged.

Run from the repo root:  python -m pytest tests/test_cpp_header_retag.py
"""
from __future__ import annotations

from codebase_mapper.inspection.languages.cpp import refine_cpp_header_languages
from codebase_mapper.inspection.models import FileRecord


def _rec(path: str, language: str) -> FileRecord:
    return FileRecord(
        path=path, git_blob_sha="0" * 40, content_sha256="0" * 64,
        size_bytes=1, language=language, type_="source_code",
        phases=["runtime"],
    )


C_HEADER = b"#ifndef _LINUX_FS_H\n#define _LINUX_FS_H\nstruct inode;\nint register_filesystem(struct file_system_type *);\n#endif\n"
CPP_HEADER = b"#pragma once\nnamespace gcc {\ntemplate <typename T>\nclass pass_manager {\n public:\n  virtual void run();\n};\n}\n"


def _refine(records, contents):
    refine_cpp_header_languages(records, lambda p: contents.get(p, b""))


def test_kernel_shape_plain_c_headers_stay_c():
    """246 cpp files in tools/ must not flip .c-less C headers repo-wide."""
    records = [
        _rec("tools/gcc-plugins/plugin.cpp", "cpp"),
        _rec("fs/inode.c", "c"),
        _rec("include/linux/fs.h", "c"),        # no sibling .c, pure C content
        _rec("include/uapi/linux/bpf.h", "c"),
    ]
    _refine(records, {
        "include/linux/fs.h": C_HEADER,
        "include/uapi/linux/bpf.h": C_HEADER,
    })
    assert [r.language for r in records] == ["cpp", "c", "c", "c"]


def test_projectwide_rule_fires_on_cpp_content_evidence():
    """include/ vs src/ split: a header that IS C++ still gets retagged."""
    records = [
        _rec("src/pass.cpp", "cpp"),
        _rec("include/pass_manager.h", "c"),
    ]
    _refine(records, {"include/pass_manager.h": CPP_HEADER})
    assert records[1].language == "cpp"


def test_sibling_rule_needs_no_content_evidence():
    """Co-resident C++ source stays sufficient (existing behavior)."""
    records = [
        _rec("src/foo.cpp", "cpp"),
        _rec("src/foo.h", "c"),
    ]
    _refine(records, {"src/foo.h": C_HEADER})
    assert records[1].language == "cpp"


def test_pure_c_repo_untouched():
    records = [_rec("fs/inode.c", "c"), _rec("include/fs.h", "c")]
    _refine(records, {"include/fs.h": CPP_HEADER})
    assert [r.language for r in records] == ["c", "c"]


def test_backcompat_without_content_accessor():
    """No accessor = no content evidence = project-wide rule stays put."""
    records = [
        _rec("tools/x.cpp", "cpp"),
        _rec("include/y.h", "c"),
    ]
    refine_cpp_header_languages(records)
    assert records[1].language == "c"
