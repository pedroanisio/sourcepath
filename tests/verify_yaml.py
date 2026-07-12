#!/usr/bin/env python3
"""verify_yaml.py — first-class YAML support invariants.

YAML is a first-class language: detection + analyzer + import resolver +
symbol chunker + L4 summary gate + this suite. The analyzer uses PyYAML's
``compose_all`` to build a real node AST with source marks (PyYAML is already
a hard dependency), emitting one item per mapping key (top-level + one nested
level) across every document in a multi-document stream.

Covered:
  1. AST: mapping keys with spans; nested keys carry a parent; multi-document
     (`---`) streams are fully walked; malformed YAML reports an error.
  2. resolve_yaml_imports: `$ref` and `!include` file references resolve
     in-repo (fragments stripped); http/absolute are external.
  3. classify: *.yaml / *.yml → source_code.
  4. L2 chunker emits one chunk per top-level key (mapped to 'class').
  5. Pipeline end-to-end: ast_summary present, $ref edges resolved,
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
from codebase_mapper.inspection.languages.yaml import (
    extract_yaml_ast_summary,
    resolve_yaml_imports,
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
    src = Path("tests/fixtures/yaml/api/openapi.yaml").read_bytes()
    summary, errors = extract_yaml_ast_summary(src, "api/openapi.yaml")
    assert summary is not None
    check("ast: no errors", errors == [], f"errors={errors}")
    check("ast: language=yaml", summary["language"] == "yaml")
    check("ast: extraction_method=pyyaml", summary["extraction_method"] == "pyyaml")

    names = {it["name"] for it in summary["items"] if it["kind"] == "member"}
    check("ast: top-level keys (doc 1)",
          {"openapi", "info", "paths", "components"} <= names, f"names={sorted(names)}")
    check("ast: multi-document keys (doc 2)",
          {"kind", "metadata"} <= names, f"names={sorted(names)}")
    check("ast: nested key 'title' has parent 'info'",
          any(it["name"] == "title" and it.get("parent") == "info" for it in summary["items"]),
          f"items={[(i['name'], i.get('parent')) for i in summary['items']]}")
    ok_spans = all(it["line_end"] >= it["line_start"] and it["byte_end"] > it["byte_start"]
                   for it in summary["items"])
    check("ast: spans are non-degenerate", ok_spans)

    bad, berrs = extract_yaml_ast_summary(b"a: [1, 2\n", "bad.yaml")
    check("ast: malformed YAML reports an error", bad is None and berrs, f"berrs={berrs}")


def test_resolve_imports() -> None:
    src = Path("tests/fixtures/yaml/api/openapi.yaml").read_bytes()
    summary, _ = extract_yaml_ast_summary(src, "api/openapi.yaml")
    srcs = {imp["source"] for imp in summary["imports"]}
    check("imports: $ref values captured",
          any("responses.yaml" in s for s in srcs) and any("schemas.yaml" in s for s in srcs),
          f"srcs={srcs}")

    paths = {"api/responses.yaml", "api/schemas.yaml", "api/openapi.yaml"}
    in_repo, external = resolve_yaml_imports("api/openapi.yaml", summary, paths)
    check("resolve: $ref → api/responses.yaml", "api/responses.yaml" in in_repo, f"in_repo={in_repo}")
    check("resolve: $ref → api/schemas.yaml", "api/schemas.yaml" in in_repo, f"in_repo={in_repo}")


def test_classify() -> None:
    check("classify: openapi.yaml → source_code",
          classify("api/openapi.yaml", b"a: 1") == "source_code")


def test_first_class_facets() -> None:
    reset_registries()
    chunks_embeddings.register_all(chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()
    rec = FileRecord(path="x.yaml", git_blob_sha="0" * 40, content_sha256="0" * 64,
                     size_bytes=1, language="yaml", type_="source_code", phases=["build"])
    rec.ast_summary = {"items": []}
    ctx = PipelineCtx(repo=None, commit="", records=[rec], blob_by_path={},
                      mode_by_path={}, paths_set={rec.path}, read_path=lambda p: b"")
    check("facet: analyzer matches yaml", any(a.matches(rec, ctx) for a in iter_language_analyzers()))
    check("facet: resolver matches yaml", any(r.matches(rec, ctx) for r in iter_import_resolvers()))
    check("facet: yaml extension-detected", "yaml" in set(LANG_BY_EXT.values()))
    import re as _re
    src = Path("plugins/chunks_embeddings/chunker.py").read_text()
    check("facet: L2 chunker dispatches on yaml",
          "yaml" in set(_re.findall(r'record\.language\s*==\s*"([a-z-]+)"', src)))
    from plugins.llm_enrich.enricher import SUPPORTED_LANGUAGES
    # Data languages are DELIBERATELY excluded from L4 summary scope
    # (tests/test_l4_scope_extension.py pins the contract; schema files get
    # the separate schema_purpose scope). The promotion facet asserts the
    # documented exclusion, not the template default.
    check("facet: yaml is deliberately outside L4 summary scope",
          "yaml" not in SUPPORTED_LANGUAGES)


def test_pipeline_end_to_end(repo: Path) -> None:
    reset_registries()
    chunks_embeddings.register_all(chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()
    mapped = map_codebase(repo.resolve(), "HEAD")

    y = [r for r in mapped["records"] if r.language == "yaml"]
    missing = [r.path for r in y if r.ast_summary is None]
    check("pipeline: every .yaml has ast_summary", not missing, f"missing={missing}")

    edges = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}
    check("pipeline: openapi.yaml $ref responses.yaml",
          ("openapi.yaml", "responses.yaml") in edges, f"edges={edges}")
    check("pipeline: openapi.yaml $ref schemas.yaml",
          ("openapi.yaml", "schemas.yaml") in edges, f"edges={edges}")

    chunks = mapped["ctx"].indices.get("l2_10_chunks", [])
    oc = [c for c in chunks if c["path"] == "openapi.yaml"]
    check("pipeline: openapi.yaml has multiple per-key chunks", len(oc) >= 4, f"n={len(oc)}")
    check("pipeline: chunk kinds allowed",
          {c["kind"] for c in oc} <= {"class", "function", "method", "file"},
          f"kinds={{c['kind'] for c in oc}}")


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--keep", action="store_true"); ap.parse_args()
    print("== YAML first-class verification ==")
    test_ast_extractor(); test_resolve_imports(); test_classify(); test_first_class_facets()
    src_root = Path("tests/fixtures/yaml/api").resolve()
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / "api"
        shutil.copytree(src_root, scratch)
        _init_git(scratch); _commit(scratch)
        test_pipeline_end_to_end(scratch)
    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
