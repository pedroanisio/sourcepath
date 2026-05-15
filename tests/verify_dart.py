#!/usr/bin/env python3
"""verify_dart.py — Tier-1 Dart support invariants.

Covers (each maps to a SPEC §1 invariant):

  1. AST extractor returns items with line/byte spans for every
     top-level decl, every method, and getters/setters/constructors.
  2. Multi-package monorepo: ``detect_dart_packages`` finds both
     packages and ``dart_package_for_path`` resolves to the nearest.
  3. ``resolve_dart_imports`` handles ``package:``, ``dart:``, relative,
     ``part``, and cross-package sibling imports.
  4. Generated Dart files (``*.g.dart``) classify as ``generated``.
  5. ``*_test.dart`` classifies as ``test_code`` even outside ``test/``.
  6. tests_edges links ``foo_test.dart`` to ``foo.dart``.
  7. L2 chunker emits one chunk per Dart class / function / method
     (not a single whole-file chunk).
  8. Symbol-xref resolver emits ``calls`` and ``subclassOf`` edges
     between Dart chunks.

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

from codebase_mapper.inspection.classify import classify, language_of
from codebase_mapper.inspection.languages.dart import (
    dart_package_for_path,
    detect_dart_packages,
    extract_dart_ast_summary,
    resolve_dart_imports,
)
from codebase_mapper.inspection.models import FileRecord
from codebase_mapper.inspection.pipeline import map_codebase
from codebase_mapper.inspection.tests_edges import infer_tests_edges
from codebase_mapper.shared_kernel.extensions import reset_registries

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
    src = Path("tests/fixtures/dart/single_pkg/lib/animals.dart").read_bytes()
    summary, errors = extract_dart_ast_summary(src, "lib/animals.dart")
    assert summary is not None
    check("ast: no errors", errors == [], f"errors={errors}")
    check("ast: language=dart", summary["language"] == "dart")
    check("ast: extraction_method=regex", summary["extraction_method"] == "regex")
    classes = set(summary["top_level_classes"])
    funcs = set(summary["top_level_functions"])
    check("ast: detects Animal, Dog, Eats", {"Animal", "Dog", "Eats"} <= classes,
          f"classes={classes}")
    check("ast: detects bark / randomBetween", {"bark", "randomBetween"} <= funcs,
          f"funcs={funcs}")
    items_by_kind: dict[str, list[dict]] = {}
    for it in summary["items"]:
        items_by_kind.setdefault(it["kind"], []).append(it)
    check("ast: emits method items", any(
        it["name"] == "speak" and it["parent"] == "Dog"
        for it in items_by_kind.get("method", [])
    ))
    check("ast: emits getter items", any(
        it["name"] == "name" and it["parent"] == "Dog"
        for it in items_by_kind.get("getter", [])
    ))
    check("ast: emits setter items", any(
        it["name"] == "rename" and it["parent"] == "Dog"
        for it in items_by_kind.get("setter", [])
    ))
    check("ast: emits constructor item (default)", any(
        it["name"] == "Dog" and it["parent"] == "Dog"
        for it in items_by_kind.get("constructor", [])
    ))
    check("ast: emits constructor item (factory)", any(
        "Dog.puppy" == it["name"] and it["parent"] == "Dog"
        for it in items_by_kind.get("constructor", [])
    ))
    # Spans must be sane.
    for it in summary["items"]:
        assert it["line_end"] >= it["line_start"], f"bad span: {it}"
        assert it["byte_end"] > it["byte_start"], f"bad span: {it}"
    check("ast: spans are non-degenerate", True)

    # Conditional import test.
    raw_cond = (
        "import 'foo.dart'\n"
        "  if (dart.library.html) 'foo_web.dart'\n"
        "  if (dart.library.io) 'foo_io.dart';\n"
        "\n"
        "void m() {}\n"
    )
    s, _ = extract_dart_ast_summary(raw_cond.encode("utf-8"), "x.dart")
    src_set = {imp["source"] for imp in s["imports"]}
    check("ast: conditional imports captured",
          src_set == {"foo.dart", "foo_web.dart", "foo_io.dart"},
          f"src_set={src_set}")
    cond_only = [imp for imp in s["imports"] if imp.get("conditional")]
    check("ast: conditional branches flagged",
          len(cond_only) == 2,
          f"conditional={cond_only}")

    # part / part of test.
    raw_part = (
        "library greeter;\n"
        "part 'greeter_io.dart';\n"
    )
    s, _ = extract_dart_ast_summary(raw_part.encode("utf-8"), "g.dart")
    check("ast: part directive captured",
          any(p["kind"] == "part" and p["source"] == "greeter_io.dart"
              for p in s["parts"]))

    raw_part_of = "part of 'greeter.dart';\n"
    s, _ = extract_dart_ast_summary(raw_part_of.encode("utf-8"), "g_io.dart")
    check("ast: part_of (string) captured",
          any(p["kind"] == "part_of" and p["source"] == "greeter.dart"
              for p in s["parts"]))

    raw_part_of_lib = "part of greeter;\n"
    s, _ = extract_dart_ast_summary(raw_part_of_lib.encode("utf-8"), "g_io.dart")
    check("ast: part_of (library id) captured",
          any(p["kind"] == "part_of_library" and p["source"] == "greeter"
              for p in s["parts"]))


def test_multi_package_detection(repo: Path) -> None:
    """detect_dart_packages walks every pubspec.yaml."""
    # Build records by hand from the monorepo fixture.
    fixture = repo / "monorepo"
    records: list[FileRecord] = []
    for p in fixture.rglob("pubspec.yaml"):
        rel = p.relative_to(fixture).as_posix()
        records.append(FileRecord(
            path=rel, git_blob_sha="", content_sha256="",
            size_bytes=p.stat().st_size,
            language=None, type_="dependency_manifest",
            phases=["build"],
        ))

    def read(rel: str) -> bytes:
        return (fixture / rel).read_bytes()

    pkgs = detect_dart_packages(records, read)
    check("multi-pkg: detected both packages",
          set(pkgs.keys()) == {"packages/app", "packages/core"},
          f"pkgs={pkgs}")
    check("multi-pkg: names map correctly",
          pkgs.get("packages/app") == "app"
          and pkgs.get("packages/core") == "core",
          f"pkgs={pkgs}")
    check("multi-pkg: enclosing package for app/lib/main.dart",
          dart_package_for_path("packages/app/lib/main.dart", pkgs)
          == ("packages/app", "app"))
    check("multi-pkg: enclosing package for core/lib/greeter.dart",
          dart_package_for_path("packages/core/lib/greeter.dart", pkgs)
          == ("packages/core", "core"))


def test_resolve_imports() -> None:
    summary = {
        "imports": [
            {"kind": "import", "source": "package:core/greeter.dart", "lineno": 1},
            {"kind": "import", "source": "dart:async", "lineno": 2},
            {"kind": "import", "source": "../widgets/button.dart", "lineno": 3},
        ],
        "parts": [
            {"kind": "part", "source": "main_io.dart", "lineno": 4},
        ],
    }
    paths = {
        "packages/core/lib/greeter.dart",
        "packages/app/lib/main_io.dart",
        "packages/app/widgets/button.dart",
    }
    in_repo, external = resolve_dart_imports(
        "packages/app/lib/main.dart", summary,
        {"packages/app": "app", "packages/core": "core"}, paths,
    )
    check("resolve: package:core/greeter.dart → in-repo",
          "packages/core/lib/greeter.dart" in in_repo, f"in_repo={in_repo}")
    check("resolve: ../widgets/button.dart → in-repo",
          "packages/app/widgets/button.dart" in in_repo)
    check("resolve: part main_io.dart → in-repo",
          "packages/app/lib/main_io.dart" in in_repo)
    check("resolve: dart:async → external",
          "dart:async" in external, f"external={external}")
    # Scalar fallback for legacy host:dart_pkg_name.
    in_repo2, _ = resolve_dart_imports(
        "lib/x.dart",
        {"imports": [{"kind": "import", "source": "package:single_pkg/y.dart",
                      "lineno": 1}]},
        "single_pkg",
        {"lib/y.dart"},
    )
    check("resolve: legacy scalar still works",
          "lib/y.dart" in in_repo2, f"in_repo2={in_repo2}")


def test_classify_generated() -> None:
    check("classify: *.g.dart → generated",
          classify("lib/foo.g.dart", b"// gen") == "generated")
    check("classify: *.freezed.dart → generated",
          classify("lib/foo.freezed.dart", b"// gen") == "generated")
    check("classify: *.mocks.dart → generated",
          classify("test/foo.mocks.dart", b"// gen") == "generated")
    check("classify: *.pb.dart → generated",
          classify("lib/foo.pb.dart", b"// gen") == "generated")
    check("classify: regular .dart NOT generated",
          classify("lib/foo.dart", b"void m(){}") == "source_code")


def test_classify_test_dart() -> None:
    # Outside test/ dir: rule fires via the explicit *_test.dart pattern.
    check("classify: foo_test.dart → test_code (no test/ dir)",
          classify("lib/foo_test.dart", b"void main(){}") == "test_code")
    # Inside test/: the generic 'test in parts' rule fires.
    check("classify: test/foo_test.dart → test_code",
          classify("test/foo_test.dart", b"void main(){}") == "test_code")


def test_tests_edges_dart() -> None:
    records = [
        FileRecord(path="lib/animals.dart", git_blob_sha="", content_sha256="",
                   size_bytes=10, language="dart", type_="source_code",
                   phases=["runtime"]),
        FileRecord(path="test/animals_test.dart", git_blob_sha="",
                   content_sha256="", size_bytes=10, language="dart",
                   type_="test_code", phases=["test"]),
    ]
    edges = infer_tests_edges(records)
    check("tests_edges: foo_test.dart → foo.dart",
          any(e.test_path == "test/animals_test.dart"
              and e.subject_path == "lib/animals.dart"
              for e in edges),
          f"edges={edges}")


def test_pipeline_end_to_end(repo: Path) -> None:
    """Run full L1 + L2 + xref on the single_pkg fixture and assert
    chunks, edges, and host indices are populated."""
    fixture = repo / "single_pkg"

    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(dimension=64),
    )
    symbol_xrefs.register_all()
    mapped = map_codebase(fixture.resolve(), "HEAD")

    # Host index check.
    pkgs = mapped["dart_packages"]
    check("pipeline: host:dart_packages found",
          pkgs == {"": "single_pkg"}, f"pkgs={pkgs}")
    check("pipeline: legacy host:dart_pkg_name still set",
          mapped["dart_package_name"] == "single_pkg")

    # AST coverage: every Dart source/test file must have ast_summary.
    dart_records = [
        r for r in mapped["records"]
        if r.language == "dart" and r.type_ in {"source_code", "test_code"}
    ]
    missing_ast = [r.path for r in dart_records if r.ast_summary is None]
    check("pipeline: every Dart source/test has ast_summary",
          not missing_ast, f"missing={missing_ast}")

    # Codegen file must be classified as generated, not source_code.
    gen_rec = next(
        (r for r in mapped["records"]
         if r.path == "lib/codegen_example.g.dart"), None,
    )
    check("pipeline: codegen file classified as generated",
          gen_rec is not None and gen_rec.type_ == "generated",
          f"type_={gen_rec.type_ if gen_rec else 'missing'}")

    # Import edges: animals_test → animals, animals → sounds.
    edge_pairs = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}
    check("pipeline: animals.dart imports sounds.dart",
          ("lib/animals.dart", "lib/sounds.dart") in edge_pairs,
          f"edges={edge_pairs}")
    check("pipeline: animals_test.dart imports animals.dart",
          ("test/animals_test.dart", "lib/animals.dart") in edge_pairs,
          f"edges={edge_pairs}")

    # tests_edges
    te_pairs = {(e.test_path, e.subject_path) for e in mapped["tests_edges"]}
    check("pipeline: tests_edges links animals_test → animals",
          ("test/animals_test.dart", "lib/animals.dart") in te_pairs,
          f"te={te_pairs}")

    # L2 chunks: must have per-symbol chunks for animals.dart, not just a
    # single whole-file chunk.
    chunks = mapped["ctx"].indices.get("l2_10_chunks", [])
    animals_chunks = [c for c in chunks if c["path"] == "lib/animals.dart"]
    kinds = {c["kind"] for c in animals_chunks}
    symbols = {c["symbol"] for c in animals_chunks}
    check("pipeline: animals.dart has multiple chunks",
          len(animals_chunks) >= 5, f"n={len(animals_chunks)}")
    check("pipeline: chunks include class + method + function",
          kinds >= {"class", "method", "function"},
          f"kinds={kinds}")
    check("pipeline: bark function chunked",
          "bark" in symbols, f"symbols={symbols}")
    check("pipeline: Dog class chunked",
          "Dog" in symbols, f"symbols={symbols}")
    check("pipeline: Dog.speak method chunked",
          any(c["symbol"] == "speak" and c["parent_symbol"] == "Dog"
              for c in animals_chunks))
    check("pipeline: Dog getter 'name' chunked",
          any(c["symbol"] == "get name" and c["parent_symbol"] == "Dog"
              for c in animals_chunks))
    check("pipeline: Dog setter 'rename' chunked",
          any(c["symbol"] == "set rename" and c["parent_symbol"] == "Dog"
              for c in animals_chunks))

    # Symbol xrefs.
    xrefs = mapped["ctx"].indices.get("l3_10_xrefs", {})
    edges = xrefs.get("edges", [])
    by_kind = xrefs.get("by_kind", {})
    by_lang = xrefs.get("by_language", {})
    check("pipeline: xrefs emitted edges for dart",
          by_lang.get("dart", {}).get("resolved", 0) > 0,
          f"by_lang={by_lang}")
    check("pipeline: xrefs include 'calls' kind",
          by_kind.get("calls", 0) > 0, f"by_kind={by_kind}")
    check("pipeline: xrefs include 'subclassOf' kind",
          by_kind.get("subclassOf", 0) > 0, f"by_kind={by_kind}")

    # bark() called from Dog.speak — explicit edge check.
    bark_call_edge = any(
        "lib/animals.dart#function:bark:" in e.dst_chunk_id
        and "lib/animals.dart#method:Dog.speak:" in e.src_chunk_id
        and e.kind == "calls"
        for e in edges
    )
    check("pipeline: Dog.speak --calls--> bark", bark_call_edge,
          detail="\n".join(
              f"{e.src_chunk_id} -{e.kind}-> {e.dst_chunk_id}" for e in edges
          ))

    # Dog extends Animal subclassOf edge.
    sub_edge = any(
        "lib/animals.dart#class:Dog:" in e.src_chunk_id
        and "lib/animals.dart#class:Animal:" in e.dst_chunk_id
        and e.kind == "subclassOf"
        for e in edges
    )
    check("pipeline: Dog --subclassOf--> Animal", sub_edge,
          detail="\n".join(
              f"{e.src_chunk_id} -{e.kind}-> {e.dst_chunk_id}" for e in edges
          ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    print("== Dart Tier-1 verification ==")

    test_ast_extractor()
    test_resolve_imports()
    test_classify_generated()
    test_classify_test_dart()
    test_tests_edges_dart()

    # The pipeline + multi-package tests need real git repos.
    src_root = Path("tests/fixtures/dart").resolve()
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        single = scratch / "single_pkg"
        shutil.copytree(src_root / "single_pkg", single)
        _init_git(single)
        _commit(single)

        mono = scratch / "monorepo"
        shutil.copytree(src_root / "monorepo", mono)
        _init_git(mono)
        _commit(mono)

        test_multi_package_detection(scratch)
        test_pipeline_end_to_end(scratch)

        if args.keep:
            keep = Path.cwd() / "_tmp" / "dart-verify"
            keep.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(keep, ignore_errors=True)
            shutil.copytree(scratch, keep)
            print(f"\n[--keep] fixture preserved at {keep}")

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
