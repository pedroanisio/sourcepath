#!/usr/bin/env python3
"""verify_sql.py — Tier-1 (first-class) SQL support invariants.

SQL is a first-class language per the TIOBE-top-50 goal ledger, which the
goal verifier defines mechanically as: detection + analyzer + import
resolver + symbol chunker + L4 summary gate + a verify/signatures test.
This suite exercises each of those, plus the SQL-specific hazards:

  1. AST extractor returns items with line/byte spans for tables, views,
     functions, procedures, triggers, and indexes.
  2. Dollar-quoted ($$...$$) and BEGIN/END bodies do NOT mis-split on the
     ';' terminators inside them.
  3. resolve_sql_imports handles \\i, \\ir, SOURCE, and @/@@ includes,
     resolving relative to the file's own directory.
  4. classify: *.sql → source_code.
  5. L2 chunker emits one chunk per SQL object (not a whole-file chunk),
     mapping object kinds to the allowed chunk kinds (class/function).
  6. Pipeline end-to-end: .sql files carry ast_summary, include edges are
     resolved, and per-object chunks are produced.
  7. First-class facets: the analyzer/resolver are registered, the chunker
     dispatches on 'sql', and 'sql' is L4-supported and extension-detected.

Exit code: 0 if all pass.
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
from codebase_mapper.inspection.languages.sql import (
    extract_sql_ast_summary,
    resolve_sql_imports,
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
    src = Path("tests/fixtures/sql/db/schema.sql").read_bytes()
    summary, errors = extract_sql_ast_summary(src, "db/schema.sql")
    assert summary is not None
    check("ast: no errors", errors == [], f"errors={errors}")
    check("ast: language=sql", summary["language"] == "sql")
    check("ast: extraction_method=regex", summary["extraction_method"] == "regex")

    by_kind: dict[str, set[str]] = {}
    for it in summary["items"]:
        by_kind.setdefault(it["kind"], set()).add(it["name"])
    check("ast: tables users + orders",
          {"users", "orders"} <= by_kind.get("table", set()), f"tables={by_kind.get('table')}")
    check("ast: view active_users",
          "active_users" in by_kind.get("view", set()), f"views={by_kind.get('view')}")
    check("ast: function user_count",
          "user_count" in by_kind.get("function", set()), f"funcs={by_kind.get('function')}")
    check("ast: index idx_orders_user",
          "idx_orders_user" in by_kind.get("index", set()), f"idx={by_kind.get('index')}")

    # Spans must be sane and monotonic per object.
    ok_spans = all(it["line_end"] >= it["line_start"] and it["byte_end"] > it["byte_start"]
                   for it in summary["items"])
    check("ast: spans are non-degenerate", ok_spans)

    # The dollar-quoted function body's inner ';' must not create a phantom item
    # nor truncate the function span before '$$ LANGUAGE sql;'.
    fn = next((it for it in summary["items"] if it["name"] == "user_count"), None)
    check("ast: function span covers dollar-quoted body",
          fn is not None and (fn["line_end"] - fn["line_start"]) >= 2,
          f"fn={fn}")

    # A plpgsql BEGIN/END procedure body with multiple ';' stays one item.
    proc_src = Path("tests/fixtures/sql/db/main.sql").read_bytes()
    psum, _ = extract_sql_ast_summary(proc_src, "db/main.sql")
    procs = {it["name"] for it in psum["items"] if it["kind"] == "procedure"}
    check("ast: procedure cleanup (BEGIN/END body)", "cleanup" in procs, f"procs={procs}")


def test_resolve_imports() -> None:
    src = Path("tests/fixtures/sql/db/main.sql").read_bytes()
    summary, _ = extract_sql_ast_summary(src, "db/main.sql")
    sources = {imp["source"] for imp in summary["imports"]}
    check("imports: \\i and \\ir directives captured",
          {"schema.sql", "sub/more.sql"} <= sources, f"sources={sources}")

    paths = {"db/schema.sql", "db/sub/more.sql", "db/main.sql"}
    in_repo, external = resolve_sql_imports("db/main.sql", summary, paths)
    check("resolve: \\i schema.sql → db/schema.sql",
          "db/schema.sql" in in_repo, f"in_repo={in_repo}")
    check("resolve: \\ir sub/more.sql → db/sub/more.sql",
          "db/sub/more.sql" in in_repo, f"in_repo={in_repo}")

    # Dialect variety: SOURCE (MySQL), @ and @@ (Oracle SQL*Plus).
    other = {
        "imports": [
            {"kind": "include", "source": "setup.sql", "lineno": 1},
            {"kind": "include", "source": "pkg/body.sql", "lineno": 2},
            {"kind": "include", "source": "/etc/absent.sql", "lineno": 3},
        ],
    }
    paths2 = {"db/setup.sql", "db/pkg/body.sql", "db/run.sql"}
    in_repo2, external2 = resolve_sql_imports("db/run.sql", other, paths2)
    check("resolve: SOURCE/@ relative includes resolve in-repo",
          {"db/setup.sql", "db/pkg/body.sql"} <= set(in_repo2), f"in_repo2={in_repo2}")
    check("resolve: unresolved include surfaced as external, not dropped",
          "/etc/absent.sql" in external2 or "absent.sql" in " ".join(external2),
          f"external2={external2}")


def test_classify() -> None:
    check("classify: schema.sql → source_code",
          classify("db/schema.sql", b"CREATE TABLE t (id int);") == "source_code")


def test_first_class_facets() -> None:
    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()

    rec = FileRecord(path="x.sql", git_blob_sha="0" * 40, content_sha256="0" * 64,
                     size_bytes=1, language="sql", type_="source_code",
                     phases=["runtime"])
    rec.ast_summary = {"items": []}
    ctx = PipelineCtx(repo=None, commit="", records=[rec], blob_by_path={},
                      mode_by_path={}, paths_set={rec.path}, read_path=lambda p: b"")

    analyzer_ok = any(a.matches(rec, ctx) for a in iter_language_analyzers())
    resolver_ok = any(r.matches(rec, ctx) for r in iter_import_resolvers())
    check("facet: a LanguageAnalyzer matches sql", analyzer_ok)
    check("facet: an ImportResolver matches sql", resolver_ok)
    check("facet: sql is extension-detected", "sql" in set(LANG_BY_EXT.values()))

    import re as _re
    chunker_src = Path("plugins/chunks_embeddings/chunker.py").read_text()
    dispatches = set(_re.findall(r'record\.language\s*==\s*"([a-z-]+)"', chunker_src))
    check("facet: L2 chunker dispatches on sql", "sql" in dispatches)

    from plugins.llm_enrich.enricher import SUPPORTED_LANGUAGES
    check("facet: sql is L4-supported", "sql" in SUPPORTED_LANGUAGES)


def test_pipeline_end_to_end(repo: Path) -> None:
    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()
    mapped = map_codebase(repo.resolve(), "HEAD")

    sql_records = [r for r in mapped["records"]
                   if r.language == "sql" and r.type_ == "source_code"]
    missing = [r.path for r in sql_records if r.ast_summary is None]
    check("pipeline: every .sql source has ast_summary", not missing, f"missing={missing}")

    edge_pairs = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}
    check("pipeline: main.sql includes schema.sql",
          ("db/main.sql", "db/schema.sql") in edge_pairs, f"edges={edge_pairs}")
    check("pipeline: main.sql includes sub/more.sql",
          ("db/main.sql", "db/sub/more.sql") in edge_pairs, f"edges={edge_pairs}")

    chunks = mapped["ctx"].indices.get("l2_10_chunks", [])
    schema_chunks = [c for c in chunks if c["path"] == "db/schema.sql"]
    symbols = {c["symbol"] for c in schema_chunks}
    kinds = {c["kind"] for c in schema_chunks}
    check("pipeline: schema.sql has multiple per-object chunks",
          len(schema_chunks) >= 4, f"n={len(schema_chunks)}")
    check("pipeline: users table + user_count function chunked",
          {"users", "user_count"} <= symbols, f"symbols={symbols}")
    check("pipeline: chunk kinds are the allowed set (class/function)",
          kinds <= {"class", "function", "method", "file"}, f"kinds={kinds}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    print("== SQL first-class verification ==")
    test_ast_extractor()
    test_resolve_imports()
    test_classify()
    test_first_class_facets()

    src_root = Path("tests/fixtures/sql").resolve()
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / "sqlrepo"
        shutil.copytree(src_root, scratch)
        _init_git(scratch)
        _commit(scratch)
        test_pipeline_end_to_end(scratch)
        if args.keep:
            keep = Path.cwd() / "_tmp" / "sql-verify"
            shutil.rmtree(keep, ignore_errors=True)
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(scratch, keep)
            print(f"\n[--keep] fixture preserved at {keep}")

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
