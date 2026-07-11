#!/usr/bin/env python3
"""verify_shell.py — first-class Shell support invariants.

Shell is a first-class language: detection + analyzer + import resolver +
symbol chunker + L4 summary gate + this suite. The analyzer is a
single-pass state-machine neutralizer (no new dependency) followed by a
brace-matched function scan, which is what makes shell parseable without a
grammar: `#` comments, 'single' and "double" quoted strings, and heredoc
bodies are blanked (length-preserving) BEFORE any structural scan.

Covered:
  1. Function AST: POSIX `name() {` and ksh/bash `function name {` forms are
     emitted with line/byte spans.
  2. The three shell hazards: a `#` inside a string is not a comment; an
     apostrophe inside a comment does not open a string; and a heredoc body
     containing `fake() { ... }` must NOT yield a phantom function or break
     brace matching for the enclosing function.
  3. resolve_shell_imports: `source path` and `. path` resolve in-repo;
     variable/absolute paths are external, not dropped.
  4. classify: *.sh → source_code; the shebang interpreter is recorded.
  5. L2 chunker emits one chunk per function (chunk kind 'function').
  6. Pipeline end-to-end: ast_summary, source edges, per-function chunks.
  7. First-class facets.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codebase_mapper.inspection.classify import classify
from codebase_mapper.inspection.languages.shell import (
    extract_shell_ast_summary,
    resolve_shell_imports,
)
from codebase_mapper.inspection.models import FileRecord
from codebase_mapper.inspection.pipeline import map_codebase
from codebase_mapper.shared_kernel.constants import LANG_BY_EXT
from codebase_mapper.shared_kernel.extensions import (
    PipelineCtx, iter_import_resolvers, iter_language_analyzers, reset_registries,
)

import plugins.chunks_embeddings as chunks_embeddings
import plugins.symbol_xrefs as symbol_xrefs


PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        if detail:
            for line in detail.splitlines()[:20]:
                print(f"        {line}")


def _init_git(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)


def _commit(target: Path) -> None:
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True)


def test_ast_extractor() -> None:
    src = Path("tests/fixtures/shell/scripts/deploy.sh").read_bytes()
    summary, errors = extract_shell_ast_summary(src, "deploy.sh")
    assert summary is not None
    check("ast: no errors", errors == [], f"errors={errors}")
    check("ast: language=shell", summary["language"] == "shell")
    check("ast: extraction_method=regex", summary["extraction_method"] == "regex")
    check("ast: shebang interpreter recorded", summary.get("interpreter") == "bash",
          f"interpreter={summary.get('interpreter')}")

    names = {it["name"] for it in summary["items"] if it["kind"] == "function"}
    check("ast: POSIX + `function` forms both found",
          {"log_info", "build", "deploy"} <= names, f"names={sorted(names)}")
    check("ast: heredoc body yields NO phantom function",
          "fake" not in names and "not_a_function" not in names, f"names={sorted(names)}")

    build = next((it for it in summary["items"] if it["name"] == "build"), None)
    check("ast: `function build` span survives its heredoc",
          build is not None and (build["line_end"] - build["line_start"]) >= 5,
          f"build={build}")
    ok_spans = all(it["line_end"] >= it["line_start"] and it["byte_end"] > it["byte_start"]
                   for it in summary["items"])
    check("ast: spans are non-degenerate", ok_spans)


def test_resolve_imports() -> None:
    src = Path("tests/fixtures/shell/scripts/deploy.sh").read_bytes()
    summary, _ = extract_shell_ast_summary(src, "scripts/deploy.sh")
    srcs = {imp["source"] for imp in summary["imports"]}
    check("imports: `source` and `.` forms captured",
          "./lib/common.sh" in srcs and "./lib/log.sh" in srcs, f"srcs={srcs}")

    paths = {"scripts/lib/common.sh", "scripts/lib/log.sh", "scripts/deploy.sh"}
    in_repo, external = resolve_shell_imports("scripts/deploy.sh", summary, paths)
    check("resolve: source ./lib/common.sh → in-repo",
          "scripts/lib/common.sh" in in_repo, f"in_repo={in_repo}")
    check("resolve: . ./lib/log.sh → in-repo",
          "scripts/lib/log.sh" in in_repo, f"in_repo={in_repo}")
    check("resolve: variable path → external, not dropped",
          any("HELPERS_DIR" in e or "extra.sh" in e for e in external),
          f"external={external}")


def test_classify() -> None:
    check("classify: deploy.sh → source_code",
          classify("scripts/deploy.sh", b"#!/usr/bin/env bash\n") == "source_code")


def test_first_class_facets() -> None:
    reset_registries()
    chunks_embeddings.register_all(chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()
    rec = FileRecord(path="x.sh", git_blob_sha="0" * 40, content_sha256="0" * 64,
                     size_bytes=1, language="shell", type_="source_code", phases=["build"])
    rec.ast_summary = {"items": []}
    ctx = PipelineCtx(repo=None, commit="", records=[rec], blob_by_path={},
                      mode_by_path={}, paths_set={rec.path}, read_path=lambda p: b"")
    check("facet: analyzer matches shell",
          any(a.matches(rec, ctx) for a in iter_language_analyzers()))
    check("facet: resolver matches shell",
          any(r.matches(rec, ctx) for r in iter_import_resolvers()))
    check("facet: shell extension-detected", "shell" in set(LANG_BY_EXT.values()))
    import re as _re
    src = Path("plugins/chunks_embeddings/chunker.py").read_text()
    check("facet: L2 chunker dispatches on shell",
          "shell" in set(_re.findall(r'record\.language\s*==\s*"([a-z-]+)"', src)))
    from plugins.llm_enrich.enricher import SUPPORTED_LANGUAGES
    check("facet: shell is L4-supported", "shell" in SUPPORTED_LANGUAGES)


def test_pipeline_end_to_end(repo: Path) -> None:
    reset_registries()
    chunks_embeddings.register_all(chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()
    mapped = map_codebase(repo.resolve(), "HEAD")

    sh = [r for r in mapped["records"] if r.language == "shell"]
    missing = [r.path for r in sh if r.ast_summary is None]
    check("pipeline: every shell file has ast_summary", not missing, f"missing={missing}")

    edges = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}
    check("pipeline: deploy.sh sources lib/common.sh",
          ("deploy.sh", "lib/common.sh") in edges, f"edges={edges}")
    check("pipeline: deploy.sh sources lib/log.sh",
          ("deploy.sh", "lib/log.sh") in edges, f"edges={edges}")

    chunks = mapped["ctx"].indices.get("l2_10_chunks", [])
    dc = [c for c in chunks if c["path"] == "deploy.sh"]
    symbols = {c["symbol"] for c in dc}
    check("pipeline: one chunk per function", len(dc) >= 3, f"n={len(dc)}")
    check("pipeline: build/deploy/log_info chunked",
          {"log_info", "build", "deploy"} <= symbols, f"symbols={symbols}")
    check("pipeline: shell chunks use the 'function' kind",
          {c["kind"] for c in dc} <= {"function"}, f"kinds={{c['kind'] for c in dc}}")


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--keep", action="store_true"); ap.parse_args()
    print("== Shell first-class verification ==")
    test_ast_extractor(); test_resolve_imports(); test_classify(); test_first_class_facets()
    src_root = Path("tests/fixtures/shell/scripts").resolve()
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / "scripts"
        shutil.copytree(src_root, scratch)
        _init_git(scratch); _commit(scratch)
        test_pipeline_end_to_end(scratch)
    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
