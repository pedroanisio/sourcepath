"""F19 — emit must survive analyzer output nested beyond the recursion limit.

A TypeScript file whose full-body CST nests deeper than Python's recursion
ceiling killed emit() at the last step of a completed run
(``json.dumps(r.ast_summary)`` in rdflib_emitter.py raised RecursionError
after L4 had already finished). The contract: serialize normally when
possible, retry under a raised ceiling, and only then replace the offending
field with a disclosed omission stub — and the run manifest must carry the
degradation (PALS's Law: dropped data is disclosed, never silent).

Run from the repo root:  python -m pytest tests/test_deep_ast_summary_emit.py
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from codebase_mapper.shared_kernel.json_safety import (
    DEEP_NESTING_LIMIT,
    OMISSION_MARKER,
    dump_ast_summary,
)

_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "1111111111 +0000",
    "GIT_COMMITTER_DATE": "1111111111 +0000",
    "PATH": "/usr/bin:/bin",
}


def _nested(depth: int):
    d = {"leaf": 1}
    for _ in range(depth):
        d = {"n": d}
    return d


def test_normal_summary_roundtrips_untruncated():
    text, truncated = dump_ast_summary({"items": [], "language": "c"})
    assert not truncated
    assert json.loads(text) == {"items": [], "language": "c"}


def test_moderately_deep_summary_survives_via_raised_ceiling():
    depth = sys.getrecursionlimit() + 500  # over default, under DEEP limit
    text, truncated = dump_ast_summary({"cst_json": _nested(depth)})
    assert not truncated
    assert len(text) > depth  # the full structure was serialized


def test_absurdly_deep_field_is_stubbed_with_disclosure():
    deep = {"cst_json": _nested(DEEP_NESTING_LIMIT + 5_000),
            "language": "typescript", "imports": [{"source": "./x"}]}
    text, truncated = dump_ast_summary(deep)
    assert truncated
    doc = json.loads(text)
    assert doc["cst_json"] == {"omitted": OMISSION_MARKER}
    # the shallow fields survive untouched
    assert doc["language"] == "typescript"
    assert doc["imports"] == [{"source": "./x"}]
    # the interpreter's ceiling is restored
    assert sys.getrecursionlimit() < DEEP_NESTING_LIMIT


def test_emit_survives_and_discloses_deep_record(tmp_path):
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries

    r = tmp_path / "repo"
    r.mkdir()
    (r / "a.ts").write_text("export const x = 1;\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "one"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True,
                       capture_output=True, env=_ENV)
    reset_registries()
    mapped = map_codebase(r, "HEAD")
    rec = next(x for x in mapped["records"] if x.path == "a.ts")
    rec.ast_summary = {"cst_json": _nested(DEEP_NESTING_LIMIT + 5_000),
                       "language": "typescript"}

    manifest = emit("fixture", mapped, tmp_path / "bundle",
                    emit_blobs_flag=False, validate_shacl=False)

    entry = next(d for d in manifest["degradations"]
                 if d["reason"] == "ast_summary_depth_truncated")
    assert entry["component"] == "emission"
    assert entry["affected_files"] == 1
    assert "a.ts" in entry["paths_sample"]
    # the graph carries the stubbed summary, not nothing
    ttl = (tmp_path / "bundle" / "inventory.ttl").read_text()
    assert OMISSION_MARKER in ttl
