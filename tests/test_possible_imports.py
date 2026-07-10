"""E4 (error-free-mapping plan) — ambiguity becomes data; precision stays.

linux-v23 evidence: 40% of extracted includes stay unresolved, dominated by
multi-candidate angle includes (one <asm/io.h> per architecture). Three
mechanisms, all evidence-driven:

  1. compile_commands.json ``-I``/``-isystem`` roots resolve angle includes
     exactly (any C project that ships one);
  2. still-ambiguous includes become ``cbm:possibleImport`` candidate edges
     (schema v2) — hard ``cbm:imports`` stays 100% precise while recall is
     queryable instead of absent;
  3. the manifest counts the new tier, SHACL shapes cover it, and the
     bundle's vocabulary version records the schema change.

Run from the repo root:  python -m pytest tests/test_possible_imports.py
"""
from __future__ import annotations

import json
import subprocess

import pytest

from codebase_mapper.inspection.languages.c import (
    include_roots_from_compile_commands,
    resolve_c_includes,
)

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "1111111111 +0000",
    "GIT_COMMITTER_DATE": "1111111111 +0000",
    "PATH": "/usr/bin:/bin",
}

PATHS = {
    "src/user.c",
    "arch/x86/include/asm/io.h",
    "arch/arm/include/asm/io.h",
    "include/linux/fs.h",
}
SUMMARY = {"imports": [
    {"kind": "system_include", "source": "asm/io.h", "lineno": 1},
    {"kind": "system_include", "source": "linux/fs.h", "lineno": 2},
    {"kind": "system_include", "source": "stdio.h", "lineno": 3},
]}


def test_ambiguous_out_collects_candidates_hard_edges_stay_precise():
    ambiguous: dict[str, list[str]] = {}
    in_repo, external = resolve_c_includes(
        "src/user.c", SUMMARY, PATHS, ambiguous_out=ambiguous)
    assert in_repo == ["include/linux/fs.h"]      # unique suffix: hard edge
    assert "stdio.h" in external                   # truly external
    assert ambiguous == {"asm/io.h": ["arch/arm/include/asm/io.h",
                                      "arch/x86/include/asm/io.h"]}
    assert "asm/io.h" in external, \
        "an ambiguous include is still unresolved for the hard tier"


def test_include_roots_resolve_ambiguity_exactly():
    ambiguous: dict[str, list[str]] = {}
    in_repo, _ = resolve_c_includes(
        "src/user.c", SUMMARY, PATHS,
        include_roots=["arch/x86/include", "include"],
        ambiguous_out=ambiguous)
    assert "arch/x86/include/asm/io.h" in in_repo  # evidence beats ambiguity
    assert "asm/io.h" not in ambiguous


def test_compile_commands_parsing():
    cc = json.dumps([
        {"directory": "/build", "file": "src/user.c",
         "command": "cc -I include -Iarch/x86/include -isystem /usr/lib/x -c src/user.c"},
        {"directory": "/build", "file": "src/b.c",
         "arguments": ["cc", "-I", "include", "-o", "b.o", "src/b.c"]},
    ])
    roots = include_roots_from_compile_commands(cc)
    assert "include" in roots
    assert "arch/x86/include" in roots
    assert not any(r.startswith("/usr") for r in roots)  # in-repo roots only


def test_end_to_end_possible_import_tier(tmp_path):
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries

    repo = tmp_path / "repo"
    files = {
        "src/user.c": "#include <asm/io.h>\nint u(void) { return 0; }\n",
        "arch/x86/include/asm/io.h": "#define IO_X 1\n",
        "arch/arm/include/asm/io.h": "#define IO_A 1\n",
    }
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    mapped = map_codebase(repo, "HEAD")
    edges = mapped["possible_import_edges"]
    assert {(e.src_path, e.dst_path) for e in edges} == {
        ("src/user.c", "arch/arm/include/asm/io.h"),
        ("src/user.c", "arch/x86/include/asm/io.h"),
    }
    assert mapped["import_edges"] == []  # precision intact

    out = tmp_path / "bundle"
    manifest = emit("fixture", mapped, out, emit_blobs_flag=False)
    assert manifest["counts"]["possible_import_edges"] == 2
    assert manifest["vocabulary_version"] == "v2"
    assert manifest["shacl_self_check"]["conforms"] is True
    ttl = (out / "inventory.ttl").read_text()
    assert "possibleImport" in ttl
