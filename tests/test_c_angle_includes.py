"""Regression tests: angle-bracket #include resolution against in-repo files.

Confirmed on a real Linux-kernel run: ``resolve_c_includes`` routed *every*
angle-bracket include (``#include <linux/foo.h>``) straight to the
external/unresolved bucket, never checking whether the repository itself
contains a file ending in that path suffix (the kernel does:
``include/linux/foo.h``). Result at kernel scale: only 32,645 of 65,340
source files produced any ``cbm:imports`` edge — a fraction of the real
include graph.

Behavior contract pinned here:

1. An angle include resolves to an in-repo file when exactly ONE repo path
   ends with the included path suffix (``<linux/foo.h>`` -> the unique path
   ending in ``/linux/foo.h``).
2. Ambiguous suffixes (``<asm/io.h>`` matching several arch dirs) stay in
   the unresolved bucket — no guessing.
3. Genuinely external angle includes (``<stdio.h>``, no repo match) keep
   flowing to the unresolved bucket exactly as before.
4. Quoted-include behavior is byte-for-byte unchanged (exact relative path,
   then unique-basename fallback, ambiguous fallback silently dropped).

Run from the repo root:  python -m pytest tests/test_c_angle_includes.py
"""
from __future__ import annotations

import subprocess

from pathlib import Path

import pytest

from codebase_mapper.inspection.languages.c import resolve_c_includes


# ---------------------------------------------------------------------------
# Kernel-layout fixture (unit level)
# ---------------------------------------------------------------------------

# Mirrors the Linux tree shape: headers under include/, per-arch duplicates
# of the same suffix under arch/*/include/, and a driver TU next to a local
# header.
KERNEL_PATHS = {
    "include/linux/foo.h",
    "drivers/x/bar.c",
    "drivers/x/local.h",
    "arch/a/include/asm/io.h",
    "arch/b/include/asm/io.h",
}


def _summary(imports: list[dict]) -> dict:
    return {"language": "c", "imports": imports}


def _angle(source: str, lineno: int = 1) -> dict:
    return {"kind": "system_include", "source": source, "lineno": lineno}


def _quoted(source: str, lineno: int = 1) -> dict:
    return {"kind": "local_include", "source": source, "lineno": lineno}


# --- 1. angle -> in-repo on unique suffix match (the kernel-scale gap) -----


def test_angle_include_unique_suffix_resolves_in_repo():
    in_repo, unresolved = resolve_c_includes(
        "drivers/x/bar.c",
        _summary([_angle("linux/foo.h")]),
        KERNEL_PATHS,
    )
    assert in_repo == ["include/linux/foo.h"]
    # A resolved include must not ALSO be reported as unresolved.
    assert "linux/foo.h" not in unresolved


# --- 2. ambiguous suffix stays unresolved ----------------------------------


def test_angle_include_ambiguous_suffix_stays_unresolved():
    in_repo, unresolved = resolve_c_includes(
        "drivers/x/bar.c",
        _summary([_angle("asm/io.h")]),
        KERNEL_PATHS,
    )
    assert in_repo == []
    assert unresolved == ["asm/io.h"]


# --- 3. genuinely external angle include keeps flowing to unresolved -------


def test_angle_include_external_stays_unresolved():
    in_repo, unresolved = resolve_c_includes(
        "drivers/x/bar.c",
        _summary([_angle("stdio.h")]),
        KERNEL_PATHS,
    )
    assert in_repo == []
    assert unresolved == ["stdio.h"]


# --- combined: one TU exercising all three angle cases + a quoted one ------


def test_kernel_layout_combined():
    in_repo, unresolved = resolve_c_includes(
        "drivers/x/bar.c",
        _summary([
            _angle("linux/foo.h", 1),
            _angle("asm/io.h", 2),
            _angle("stdio.h", 3),
            _quoted("local.h", 4),
        ]),
        KERNEL_PATHS,
    )
    assert in_repo == ["drivers/x/local.h", "include/linux/foo.h"]
    assert unresolved == ["asm/io.h", "stdio.h"]


# ---------------------------------------------------------------------------
# 4. quoted-include behavior must not change at all
# ---------------------------------------------------------------------------


def test_quoted_exact_relative_path_unchanged():
    in_repo, unresolved = resolve_c_includes(
        "drivers/x/bar.c",
        _summary([_quoted("local.h")]),
        KERNEL_PATHS,
    )
    assert in_repo == ["drivers/x/local.h"]
    assert unresolved == []


def test_quoted_parent_traversal_unchanged():
    paths = {"src/core/main.c", "include/util.h"}
    in_repo, unresolved = resolve_c_includes(
        "src/core/main.c",
        _summary([_quoted("../../include/util.h")]),
        paths,
    )
    assert in_repo == ["include/util.h"]
    assert unresolved == []


def test_quoted_unique_basename_fallback_unchanged():
    # No relative hit from src/main.c; unique basename elsewhere resolves.
    paths = {"src/main.c", "include/util.h"}
    in_repo, unresolved = resolve_c_includes(
        "src/main.c",
        _summary([_quoted("util.h")]),
        paths,
    )
    assert in_repo == ["include/util.h"]
    assert unresolved == []


def test_quoted_ambiguous_basename_fallback_dropped_unchanged():
    # Two candidate basenames, no relative hit: dropped silently — the
    # quoted include appears in NEITHER bucket (pre-existing behavior).
    paths = {"src/main.c", "include/util.h", "lib/util.h"}
    in_repo, unresolved = resolve_c_includes(
        "src/main.c",
        _summary([_quoted("util.h")]),
        paths,
    )
    assert in_repo == []
    assert unresolved == []


def test_quoted_relative_hit_wins_over_fallback_unchanged():
    # Relative sibling exists AND another dir shares the basename: the
    # exact relative path must win, exactly as before.
    paths = {"src/main.c", "src/util.h", "lib/util.h"}
    in_repo, unresolved = resolve_c_includes(
        "src/main.c",
        _summary([_quoted("util.h")]),
        paths,
    )
    assert in_repo == ["src/util.h"]
    assert unresolved == []


# ---------------------------------------------------------------------------
# Prebuilt-index call path (performance contract surface)
# ---------------------------------------------------------------------------


def test_prebuilt_index_path_matches_default_path():
    # The once-per-repo index and the build-on-the-fly fallback must be
    # behaviorally identical. Import is deferred so the contract tests
    # above still collect (and fail red, not error) on unmodified code.
    from codebase_mapper.inspection.languages.c import build_c_include_index

    summary = _summary([
        _angle("linux/foo.h"), _angle("asm/io.h"), _angle("stdio.h"),
        _quoted("local.h"),
    ])
    index = build_c_include_index(KERNEL_PATHS)
    with_index = resolve_c_includes(
        "drivers/x/bar.c", summary, KERNEL_PATHS, index)
    without_index = resolve_c_includes(
        "drivers/x/bar.c", summary, KERNEL_PATHS)
    assert with_index == without_index
    assert with_index == (
        ["drivers/x/local.h", "include/linux/foo.h"],
        ["asm/io.h", "stdio.h"],
    )


# ---------------------------------------------------------------------------
# End-to-end: map_codebase over a kernel-layout git repo -> cbm:imports edges
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True)


def _make_kernel_repo(root: Path) -> Path:
    repo = root / "kernelish"
    files = {
        "include/linux/foo.h": "#ifndef FOO_H\n#define FOO_H\nint foo(void);\n#endif\n",
        "drivers/x/local.h": "#ifndef LOCAL_H\n#define LOCAL_H\nint local(void);\n#endif\n",
        "arch/a/include/asm/io.h": "#ifndef IO_A_H\n#define IO_A_H\n#endif\n",
        "arch/b/include/asm/io.h": "#ifndef IO_B_H\n#define IO_B_H\n#endif\n",
        "drivers/x/bar.c": (
            "#include <linux/foo.h>\n"
            "#include <asm/io.h>\n"
            "#include <stdio.h>\n"
            '#include "local.h"\n'
            "\n"
            "int bar(void) { return foo() + local(); }\n"
        ),
    }
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "init"], repo)
    return repo


@pytest.fixture()
def kernel_repo(tmp_path: Path) -> Path:
    return _make_kernel_repo(tmp_path)


def _ts_available() -> bool:
    from codebase_mapper.ts_setup import TS_AVAILABLE
    return TS_AVAILABLE


@pytest.mark.skipif(not _ts_available(), reason="tree-sitter unavailable")
def test_pipeline_emits_angle_include_edges(kernel_repo: Path):
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries

    reset_registries()
    mapped = map_codebase(kernel_repo.resolve(), "HEAD")
    edges = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}

    # THE regression: angle include with a unique in-repo suffix match.
    assert ("drivers/x/bar.c", "include/linux/foo.h") in edges, (
        f"angle-include edge missing; edges={sorted(edges)}"
    )
    # Quoted include still resolves relative to the including file.
    assert ("drivers/x/bar.c", "drivers/x/local.h") in edges
    # Ambiguous <asm/io.h> (two arch dirs) must NOT produce an edge.
    assert ("drivers/x/bar.c", "arch/a/include/asm/io.h") not in edges
    assert ("drivers/x/bar.c", "arch/b/include/asm/io.h") not in edges
    # <stdio.h> has no repo match: no in-repo edge may reference it.
    assert not any("stdio" in dst for _src, dst in edges)
