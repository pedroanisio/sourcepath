#!/usr/bin/env python3
"""verify_json.py — first-class JSON support invariants.

JSON is a first-class language: detection + analyzer + import resolver +
symbol chunker + L4 summary gate + this suite. The analyzer is a
hand-written recursive-descent parser (stdlib only, no new dependency) that
builds a value AST with line/byte spans and emits one item per object
member (top-level and one nested level), so the L2 chunker produces a chunk
per structural key.

Covered:
  1. AST: object members with value_type and spans; nested members carry a
     parent key; invalid JSON reports an error rather than crashing.
  2. resolve_json_imports: `$ref` and `extends` file references resolve
     in-repo (JSON-pointer fragments stripped); http/absolute are external.
  3. classify: *.json → source_code.
  4. L2 chunker emits one chunk per top-level key (mapped to 'class').
  5. Pipeline end-to-end: ast_summary present, $ref/extends edges resolved,
     per-key chunks produced.
  6. First-class facets.
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
from codebase_mapper.inspection.languages.json import (
    extract_json_ast_summary,
    resolve_json_imports,
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
    src = Path("tests/fixtures/json/cfg/app.json").read_bytes()
    summary, errors = extract_json_ast_summary(src, "cfg/app.json")
    assert summary is not None
    check("ast: no errors", errors == [], f"errors={errors}")
    check("ast: language=json", summary["language"] == "json")
    check("ast: extraction_method=recursive-descent",
          summary["extraction_method"] == "recursive-descent")

    members = {it["name"]: it for it in summary["items"] if it["kind"] == "member"}
    check("ast: top-level keys present",
          {"name", "version", "extends", "settings", "schema"} <= set(members),
          f"keys={sorted(members)}")
    check("ast: value_type recorded (settings=object, name=string)",
          members.get("settings", {}).get("value_type") == "object"
          and members.get("name", {}).get("value_type") == "string",
          f"settings={members.get('settings')} name={members.get('name')}")
    check("ast: nested member 'debug' has parent 'settings'",
          any(it["name"] == "debug" and it.get("parent") == "settings"
              for it in summary["items"]),
          f"items={[(i['name'], i.get('parent')) for i in summary['items']]}")
    ok_spans = all(it["line_end"] >= it["line_start"] and it["byte_end"] > it["byte_start"]
                   for it in summary["items"])
    check("ast: spans are non-degenerate", ok_spans)

    bad, berrs = extract_json_ast_summary(b'{ "a": }', "bad.json")
    check("ast: invalid JSON reports an error", bad is None and berrs, f"berrs={berrs}")


def test_resolve_imports() -> None:
    src = Path("tests/fixtures/json/cfg/app.json").read_bytes()
    summary, _ = extract_json_ast_summary(src, "cfg/app.json")
    srcs = {imp["source"] for imp in summary["imports"]}
    check("imports: extends + $ref captured",
          "./base.json" in srcs and any("schema.json" in s for s in srcs),
          f"srcs={srcs}")

    paths = {"cfg/base.json", "cfg/schema.json", "cfg/app.json"}
    in_repo, external = resolve_json_imports("cfg/app.json", summary, paths)
    check("resolve: extends → cfg/base.json", "cfg/base.json" in in_repo, f"in_repo={in_repo}")
    check("resolve: $ref → cfg/schema.json (fragment stripped)",
          "cfg/schema.json" in in_repo, f"in_repo={in_repo}")


def test_classify() -> None:
    check("classify: app.json → source_code",
          classify("cfg/app.json", b"{}") == "source_code")


def test_first_class_facets() -> None:
    reset_registries()
    chunks_embeddings.register_all(chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()
    rec = FileRecord(path="x.json", git_blob_sha="0" * 40, content_sha256="0" * 64,
                     size_bytes=1, language="json", type_="source_code", phases=["build"])
    rec.ast_summary = {"items": []}
    ctx = PipelineCtx(repo=None, commit="", records=[rec], blob_by_path={},
                      mode_by_path={}, paths_set={rec.path}, read_path=lambda p: b"")
    check("facet: analyzer matches json", any(a.matches(rec, ctx) for a in iter_language_analyzers()))
    check("facet: resolver matches json", any(r.matches(rec, ctx) for r in iter_import_resolvers()))
    check("facet: json extension-detected", "json" in set(LANG_BY_EXT.values()))
    import re as _re
    src = Path("plugins/chunks_embeddings/chunker.py").read_text()
    check("facet: L2 chunker dispatches on json",
          "json" in set(_re.findall(r'record\.language\s*==\s*"([a-z-]+)"', src)))
    from plugins.llm_enrich.enricher import SUPPORTED_LANGUAGES
    check("facet: json is L4-supported", "json" in SUPPORTED_LANGUAGES)


def test_pipeline_end_to_end(repo: Path) -> None:
    reset_registries()
    chunks_embeddings.register_all(chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()
    mapped = map_codebase(repo.resolve(), "HEAD")

    j = [r for r in mapped["records"] if r.language == "json"]
    missing = [r.path for r in j if r.ast_summary is None]
    check("pipeline: every .json has ast_summary", not missing, f"missing={missing}")

    edges = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}
    check("pipeline: app.json extends base.json", ("app.json", "base.json") in edges, f"edges={edges}")
    check("pipeline: app.json $ref schema.json", ("app.json", "schema.json") in edges, f"edges={edges}")

    chunks = mapped["ctx"].indices.get("l2_10_chunks", [])
    app_chunks = [c for c in chunks if c["path"] == "app.json"]
    check("pipeline: app.json has multiple per-key chunks", len(app_chunks) >= 3, f"n={len(app_chunks)}")
    check("pipeline: chunk kinds allowed",
          {c["kind"] for c in app_chunks} <= {"class", "function", "method", "file"},
          f"kinds={{c['kind'] for c in app_chunks}}")


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--keep", action="store_true"); ap.parse_args()
    print("== JSON first-class verification ==")
    test_ast_extractor(); test_resolve_imports(); test_classify(); test_first_class_facets()
    src_root = Path("tests/fixtures/json/cfg").resolve()
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / "cfg"
        shutil.copytree(src_root, scratch)
        _init_git(scratch); _commit(scratch)
        test_pipeline_end_to_end(scratch)
    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
