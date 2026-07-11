#!/usr/bin/env python3
"""verify_html.py — first-class HTML support invariants.

HTML is a first-class language: detection + analyzer + import resolver +
symbol chunker + L4 summary gate + this suite. The analyzer is a
stack-based element parser (no tree-sitter dependency) that builds an
element tree with line/byte spans, honouring void elements, raw-text
elements (<script>/<style>), comments, and the doctype.

Covered:
  1. Element AST: structural/landmark elements and any element with an id
     are emitted with sane spans and a parent link.
  2. Raw-text hazard: markup-looking text inside <style>/<script> and
     comments is NOT parsed as elements and does not corrupt the tree.
  3. resolve_html_imports: <link href>, <script src>, <a href>, <img src>
     relative refs resolve in-repo; http(s)/anchor/data refs are external.
  4. classify: *.html → source_code.
  5. L2 chunker emits per-element chunks (mapped to the 'class' chunk kind).
  6. Pipeline end-to-end: ast_summary present, include/link edges resolved,
     per-element chunks produced.
  7. First-class facets: analyzer/resolver registered, chunker dispatches
     'html', 'html' is L4-supported and extension-detected.
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
from codebase_mapper.inspection.languages.html import (
    extract_html_ast_summary,
    resolve_html_imports,
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
    src = Path("tests/fixtures/html/site/index.html").read_bytes()
    summary, errors = extract_html_ast_summary(src, "index.html")
    assert summary is not None
    check("ast: no errors", errors == [], f"errors={errors}")
    check("ast: language=html", summary["language"] == "html")
    check("ast: extraction_method=regex", summary["extraction_method"] == "regex")

    tags = [it["tag"] for it in summary["items"]]
    for t in ("html", "head", "body", "header", "nav", "main", "section", "footer"):
        check(f"ast: structural <{t}> emitted", t in tags, f"tags={sorted(set(tags))}")
    ids = {it.get("id") for it in summary["items"] if it.get("id")}
    check("ast: id'd elements emitted (top/content/hero)",
          {"top", "content", "hero"} <= ids, f"ids={ids}")
    check("ast: <script>/<style> emitted",
          {"script", "style"} <= set(tags), f"tags={sorted(set(tags))}")

    # Raw-text hazard: the '<div>' inside the <style> comment must not appear.
    check("ast: no phantom element from raw-text/comment",
          "div" not in tags, f"tags={sorted(set(tags))}")

    # Parent links: the #hero section is inside <main>; nav inside header.
    hero = next((it for it in summary["items"] if it.get("id") == "hero"), None)
    check("ast: parent link (section#hero → main)",
          hero is not None and hero.get("parent") == "main", f"hero={hero}")

    ok_spans = all(it["line_end"] >= it["line_start"] and it["byte_end"] > it["byte_start"]
                   for it in summary["items"])
    check("ast: spans are non-degenerate", ok_spans)


def test_resolve_imports() -> None:
    src = Path("tests/fixtures/html/site/index.html").read_bytes()
    summary, _ = extract_html_ast_summary(src, "site/index.html")
    srcs = {imp["source"] for imp in summary["imports"]}
    check("imports: link/script/anchor/img captured",
          {"styles/app.css", "js/app.js", "about.html", "img/logo.png"} <= srcs,
          f"srcs={srcs}")
    check("imports: external URLs captured too",
          any(s.startswith("http") for s in srcs), f"srcs={srcs}")

    paths = {"site/styles/app.css", "site/js/app.js", "site/about.html",
             "site/img/logo.png", "site/index.html"}
    in_repo, external = resolve_html_imports("site/index.html", summary, paths)
    check("resolve: styles/app.css → in-repo",
          "site/styles/app.css" in in_repo, f"in_repo={in_repo}")
    check("resolve: js/app.js → in-repo",
          "site/js/app.js" in in_repo, f"in_repo={in_repo}")
    check("resolve: about.html → in-repo",
          "site/about.html" in in_repo, f"in_repo={in_repo}")
    check("resolve: https CDN → external, not in-repo",
          any("cdn.example.com" in e or "example.com" in e for e in external)
          and not any("cdn.example.com" in p for p in in_repo),
          f"external={external}")


def test_classify() -> None:
    check("classify: index.html → source_code",
          classify("site/index.html", b"<html></html>") == "source_code")


def test_first_class_facets() -> None:
    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()

    rec = FileRecord(path="x.html", git_blob_sha="0" * 40, content_sha256="0" * 64,
                     size_bytes=1, language="html", type_="source_code",
                     phases=["runtime"])
    rec.ast_summary = {"items": []}
    ctx = PipelineCtx(repo=None, commit="", records=[rec], blob_by_path={},
                      mode_by_path={}, paths_set={rec.path}, read_path=lambda p: b"")
    check("facet: a LanguageAnalyzer matches html",
          any(a.matches(rec, ctx) for a in iter_language_analyzers()))
    check("facet: an ImportResolver matches html",
          any(r.matches(rec, ctx) for r in iter_import_resolvers()))
    check("facet: html is extension-detected", "html" in set(LANG_BY_EXT.values()))

    import re as _re
    src = Path("plugins/chunks_embeddings/chunker.py").read_text()
    dispatches = set(_re.findall(r'record\.language\s*==\s*"([a-z-]+)"', src))
    dispatches |= set(_re.findall(r'record\.language\s+in\s+\(([^)]*)\)', src) and
                      _re.findall(r'"([a-z-]+)"', " ".join(_re.findall(
                          r'record\.language\s+in\s+\(([^)]*)\)', src))))
    check("facet: L2 chunker dispatches on html", "html" in dispatches)

    from plugins.llm_enrich.enricher import SUPPORTED_LANGUAGES
    check("facet: html is L4-supported", "html" in SUPPORTED_LANGUAGES)


def test_pipeline_end_to_end(repo: Path) -> None:
    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()
    mapped = map_codebase(repo.resolve(), "HEAD")

    html_records = [r for r in mapped["records"]
                    if r.language == "html" and r.type_ == "source_code"]
    missing = [r.path for r in html_records if r.ast_summary is None]
    check("pipeline: every .html source has ast_summary", not missing, f"missing={missing}")

    edge_pairs = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}
    check("pipeline: index.html links styles/app.css",
          ("index.html", "styles/app.css") in edge_pairs, f"edges={edge_pairs}")
    check("pipeline: index.html scripts js/app.js",
          ("index.html", "js/app.js") in edge_pairs, f"edges={edge_pairs}")
    check("pipeline: index.html anchors about.html",
          ("index.html", "about.html") in edge_pairs, f"edges={edge_pairs}")

    chunks = mapped["ctx"].indices.get("l2_10_chunks", [])
    idx_chunks = [c for c in chunks if c["path"] == "index.html"]
    check("pipeline: index.html has multiple per-element chunks",
          len(idx_chunks) >= 4, f"n={len(idx_chunks)}")
    check("pipeline: chunk kinds are the allowed set",
          {c["kind"] for c in idx_chunks} <= {"class", "function", "method", "file"},
          f"kinds={{c['kind'] for c in idx_chunks}}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    print("== HTML first-class verification ==")
    test_ast_extractor()
    test_resolve_imports()
    test_classify()
    test_first_class_facets()

    src_root = Path("tests/fixtures/html/site").resolve()
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / "site"
        shutil.copytree(src_root, scratch)
        _init_git(scratch)
        _commit(scratch)
        test_pipeline_end_to_end(scratch)
        if args.keep:
            keep = Path.cwd() / "_tmp" / "html-verify"
            shutil.rmtree(keep, ignore_errors=True)
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(scratch, keep)
            print(f"\n[--keep] fixture preserved at {keep}")

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
