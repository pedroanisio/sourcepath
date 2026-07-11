#!/usr/bin/env python3
"""verify_css.py — first-class CSS/SCSS support invariants.

CSS (and the SCSS dialect) is a first-class language: detection + analyzer +
import resolver + symbol chunker + L4 summary gate + this suite. The analyzer
is a brace-scanning parser (no tree-sitter dependency) that extracts rules and
at-rules with line/byte spans, honouring the hazards:

  1. Rule/at-rule AST: selectors, ``@media``/``@supports``/``@keyframes``/
     ``@font-face`` blocks are emitted with spans; nested rules (SCSS, and
     rules inside ``@media``) carry a parent link.
  2. Declarations (``color: red;``) and ``/* */`` + SCSS ``//`` comments do
     NOT create phantom rules and their ``;`` never splits a block.
  3. resolve_css_imports handles ``@import`` / ``@import url(...)`` / ``@use``
     / ``@forward`` (incl. SCSS partial candidates); ``sass:`` and http(s)
     are external.
  4. classify: *.css / *.scss → source_code.
  5. L2 chunker emits one chunk per rule/at-rule (mapped to 'class').
  6. Pipeline end-to-end: ast_summary present, @import edges resolved,
     per-rule chunks produced.
  7. First-class facets for BOTH css and scss.
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
from codebase_mapper.inspection.languages.css import (
    extract_css_ast_summary,
    resolve_css_imports,
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
        print(f"  PASS  {name}")
        PASS += 1
    else:
        print(f"  FAIL  {name}")
        if detail:
            for line in detail.splitlines()[:20]:
                print(f"        {line}")
        FAIL += 1


def _init_git(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)


def _commit(target: Path) -> None:
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True)


def test_ast_extractor() -> None:
    src = Path("tests/fixtures/css/theme/main.css").read_bytes()
    summary, errors = extract_css_ast_summary(src, "main.css")
    assert summary is not None
    check("ast: no errors", errors == [], f"errors={errors}")
    check("ast: language=css", summary["language"] == "css")
    check("ast: extraction_method=regex", summary["extraction_method"] == "regex")

    by_kind: dict[str, list[dict]] = {}
    for it in summary["items"]:
        by_kind.setdefault(it["kind"], []).append(it)
    rule_names = {it["name"] for it in by_kind.get("rule", [])}
    check("ast: rules :root and .button", {":root", ".button"} <= rule_names,
          f"rules={rule_names}")
    check("ast: @media block emitted",
          any("@media" in it["name"] for it in by_kind.get("media", [])),
          f"media={by_kind.get('media')}")
    check("ast: @keyframes fade emitted",
          any(it["name"] == "fade" for it in by_kind.get("keyframes", [])),
          f"kf={by_kind.get('keyframes')}")
    # No phantom rule from a declaration line.
    check("ast: declarations do not become rules",
          not (rule_names & {"padding", "color", "--brand"}), f"rules={rule_names}")
    ok_spans = all(it["line_end"] >= it["line_start"] and it["byte_end"] > it["byte_start"]
                   for it in summary["items"])
    check("ast: spans are non-degenerate", ok_spans)


def test_scss() -> None:
    src = Path("tests/fixtures/css/theme/app.scss").read_bytes()
    summary, _ = extract_css_ast_summary(src, "app.scss")
    check("scss: language=scss", summary["language"] == "scss")
    rules = {it["name"]: it for it in summary["items"] if it["kind"] == "rule"}
    check("scss: nested .title has parent .card",
          ".title" in rules and rules[".title"].get("parent") == ".card",
          f"rules={ {k: v.get('parent') for k, v in rules.items()} }")
    srcs = {imp["source"] for imp in summary["imports"]}
    check("scss: @use and @import captured",
          {"sass:math", "base.css"} <= srcs, f"srcs={srcs}")


def test_resolve_imports() -> None:
    src = Path("tests/fixtures/css/theme/main.css").read_bytes()
    summary, _ = extract_css_ast_summary(src, "main.css")
    srcs = {imp["source"] for imp in summary["imports"]}
    check("imports: @import + url() captured",
          {"base.css", "vendor/reset.css"} <= srcs, f"srcs={srcs}")

    paths = {"base.css", "vendor/reset.css", "main.css"}
    in_repo, external = resolve_css_imports("main.css", summary, paths)
    check("resolve: base.css → in-repo", "base.css" in in_repo, f"in_repo={in_repo}")
    check("resolve: vendor/reset.css → in-repo",
          "vendor/reset.css" in in_repo, f"in_repo={in_repo}")
    check("resolve: https font import → external",
          any("fonts.example.com" in e for e in external), f"external={external}")


def test_classify() -> None:
    check("classify: main.css → source_code",
          classify("theme/main.css", b"a{}") == "source_code")
    check("classify: app.scss → source_code",
          classify("theme/app.scss", b"a{b:c}") == "source_code")


def test_first_class_facets() -> None:
    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()

    for lang in ("css", "scss"):
        rec = FileRecord(path=f"x.{lang}", git_blob_sha="0" * 40, content_sha256="0" * 64,
                         size_bytes=1, language=lang, type_="source_code",
                         phases=["runtime"])
        rec.ast_summary = {"items": []}
        ctx = PipelineCtx(repo=None, commit="", records=[rec], blob_by_path={},
                          mode_by_path={}, paths_set={rec.path}, read_path=lambda p: b"")
        check(f"facet: a LanguageAnalyzer matches {lang}",
              any(a.matches(rec, ctx) for a in iter_language_analyzers()))
        check(f"facet: an ImportResolver matches {lang}",
              any(r.matches(rec, ctx) for r in iter_import_resolvers()))
        check(f"facet: {lang} is L4-supported",
              __import__("plugins.llm_enrich.enricher", fromlist=["SUPPORTED_LANGUAGES"])
              .SUPPORTED_LANGUAGES.__contains__(lang))

    check("facet: css/scss extension-detected",
          {"css", "scss"} <= set(LANG_BY_EXT.values()))
    import re as _re
    src = Path("plugins/chunks_embeddings/chunker.py").read_text()
    grp = " ".join(_re.findall(r'record\.language\s+in\s+\(([^)]*)\)', src))
    dispatched = set(_re.findall(r'"([a-z-]+)"', grp))
    check("facet: L2 chunker dispatches on css + scss",
          {"css", "scss"} <= dispatched, f"dispatched={dispatched}")


def test_pipeline_end_to_end(repo: Path) -> None:
    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()
    mapped = map_codebase(repo.resolve(), "HEAD")

    css_records = [r for r in mapped["records"]
                   if r.language in ("css", "scss") and r.type_ == "source_code"]
    missing = [r.path for r in css_records if r.ast_summary is None]
    check("pipeline: every .css/.scss source has ast_summary", not missing, f"missing={missing}")

    edge_pairs = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}
    check("pipeline: main.css imports base.css",
          ("main.css", "base.css") in edge_pairs, f"edges={edge_pairs}")
    check("pipeline: main.css imports vendor/reset.css",
          ("main.css", "vendor/reset.css") in edge_pairs, f"edges={edge_pairs}")

    chunks = mapped["ctx"].indices.get("l2_10_chunks", [])
    main_chunks = [c for c in chunks if c["path"] == "main.css"]
    check("pipeline: main.css has multiple per-rule chunks",
          len(main_chunks) >= 4, f"n={len(main_chunks)}")
    check("pipeline: chunk kinds are the allowed set",
          {c["kind"] for c in main_chunks} <= {"class", "function", "method", "file"},
          f"kinds={{c['kind'] for c in main_chunks}}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    print("== CSS/SCSS first-class verification ==")
    test_ast_extractor()
    test_scss()
    test_resolve_imports()
    test_classify()
    test_first_class_facets()

    src_root = Path("tests/fixtures/css/theme").resolve()
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / "theme"
        shutil.copytree(src_root, scratch)
        _init_git(scratch)
        _commit(scratch)
        test_pipeline_end_to_end(scratch)
        if args.keep:
            keep = Path.cwd() / "_tmp" / "css-verify"
            shutil.rmtree(keep, ignore_errors=True)
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(scratch, keep)
            print(f"\n[--keep] fixture preserved at {keep}")

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
