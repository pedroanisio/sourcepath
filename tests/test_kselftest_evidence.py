"""F7/F17 — kernel-style test evidence.

The Linux bundle typed only 626 of ~5,161+ kselftest files as test_code and
derived 139 tests edges, because:

- ``classify`` recognizes path components ``tests/test/__tests__/spec`` but
  not ``selftests`` (kselftest lives at ``tools/testing/selftests/``), and
  its ``*_test`` stem rule covers ``.cpp`` but not ``.c`` (KUnit convention:
  ``*_test.c`` / ``*_kunit.c`` / ``test_*.c``);
- ``infer_tests_edges`` had only the stem heuristic (plus a Rust-only
  ``use``-analysis fallback), so a test whose name mirrors no subject file
  produced zero evidence. The typed-import derivation (test-typed file →
  source-typed import target) generalizes the Rust fallback to every
  language and becomes the canonical number all reports cite.

Run from the repo root:  python -m pytest tests/test_kselftest_evidence.py
"""
from __future__ import annotations

import pytest

from codebase_mapper.inspection.classify import classify
from codebase_mapper.inspection.models import FileRecord, ImportEdge, TestsEdge
from codebase_mapper.inspection.tests_edges import infer_tests_edges


# ---------------------------------------------------------------- classify

C_HEAD = b"#include <stdio.h>\nint main(void) { return 0; }\n"
SH_HEAD = b"#!/bin/sh\nexit 0\n"


@pytest.mark.parametrize("path,head", [
    ("tools/testing/selftests/net/netfilter/rpath.sh", SH_HEAD),
    ("tools/testing/selftests/cgroup/test_memcontrol.c", C_HEAD),
    ("tools/testing/selftests/kvm/lib/kvm_util.c", C_HEAD),
    ("lib/test_bitmap.c", C_HEAD),              # kernel test_*.c convention
    ("drivers/base/power/domain_test.c", C_HEAD),   # KUnit *_test.c
    ("net/sunrpc/auth_gss/gss_krb5_test.c", C_HEAD),
    ("lib/cmdline_kunit.c", C_HEAD),            # KUnit *_kunit.c
    ("sound/soc/topology-test.c", C_HEAD),      # KUnit *-test.c
])
def test_kernel_test_files_classify_as_test_code(path, head):
    assert classify(path, head) == "test_code"


@pytest.mark.parametrize("path,head", [
    ("drivers/net/latest.c", C_HEAD),     # 'test' substring is not a marker
    ("kernel/protest.c", C_HEAD),         # *test without separator
    ("drivers/misc/attest.c", C_HEAD),
    ("crypto/testmgr.c", C_HEAD),         # test-prefix without underscore
    ("fs/ext4/inode.c", C_HEAD),
])
def test_lookalike_paths_stay_source_code(path, head):
    assert classify(path, head) == "source_code"


# ------------------------------------------------------- typed-import edges

def _rec(path: str, type_: str, language: str = "c") -> FileRecord:
    return FileRecord(
        path=path, git_blob_sha="0" * 40, content_sha256="0" * 64,
        size_bytes=1, language=language, type_=type_, phases=["runtime"],
    )


def test_typed_import_fallback_produces_edges_for_stemless_tests():
    records = [
        _rec("tools/testing/selftests/mm/uffd_stress.c", "test_code"),
        _rec("mm/userfaultfd.c", "source_code"),
        _rec("tools/testing/selftests/kselftest_harness.h", "test_code"),
    ]
    import_edges = [
        # test → source: this IS the evidence
        ImportEdge("tools/testing/selftests/mm/uffd_stress.c",
                   "mm/userfaultfd.c"),
        # test → test infrastructure: excluded from evidence
        ImportEdge("tools/testing/selftests/mm/uffd_stress.c",
                   "tools/testing/selftests/kselftest_harness.h"),
    ]
    edges = infer_tests_edges(records, import_edges=import_edges)
    assert TestsEdge("tools/testing/selftests/mm/uffd_stress.c",
                     "mm/userfaultfd.c") in edges
    assert all(e.subject_path != "tools/testing/selftests/kselftest_harness.h"
               for e in edges), "test-infrastructure targets are not subjects"


def test_typed_import_fallback_does_not_fire_when_stem_matched():
    records = [
        _rec("pkg/tests/test_foo.py", "test_code", "python"),
        _rec("pkg/foo.py", "source_code", "python"),
        _rec("pkg/bar.py", "source_code", "python"),
    ]
    import_edges = [
        # test imports a helper too — stem heuristic already found the
        # subject, so the fallback must not dilute the edge set.
        ImportEdge("pkg/tests/test_foo.py", "pkg/foo.py"),
        ImportEdge("pkg/tests/test_foo.py", "pkg/bar.py"),
    ]
    edges = infer_tests_edges(records, import_edges=import_edges)
    assert edges == [TestsEdge("pkg/tests/test_foo.py", "pkg/foo.py")]


def test_no_import_edges_means_old_behavior():
    records = [
        _rec("pkg/tests/test_foo.py", "test_code", "python"),
        _rec("pkg/foo.py", "source_code", "python"),
    ]
    edges = infer_tests_edges(records)
    assert edges == [TestsEdge("pkg/tests/test_foo.py", "pkg/foo.py")]
