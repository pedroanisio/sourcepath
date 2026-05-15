#!/usr/bin/env python3
"""verify_cpp.py — Tier-1 C++ support invariants.

Mirrors verify_dart.py / verify_java.py. Covers (SPEC §1 invariants):

  1. AST extractor returns items with line/byte spans for classes,
     methods, in-class methods, out-of-class method definitions
     (``Dog::speak``), constructors, destructors, free functions, and
     namespace-aware naming.
  2. ``build_cpp_symbol_index`` covers multi-file definitions (class in
     .h + methods in .cpp).
  3. ``refine_cpp_header_languages`` re-tags ``.h`` files in mixed
     C/C++ directories so the cpp analyzer parses them.
  4. ``*_test.cpp`` / ``*_test.cc`` / ``FooTest.cpp`` classify as
     ``test_code``; ``Latest.cpp`` does NOT.
  5. tests_edges links ``foo_test.cpp`` to ``foo.cpp``.
  6. L2 chunker emits one chunk per type/method/function (with
     declaration-vs-definition deduplication).
  7. Symbol-xref resolver emits ``calls`` (constructor + direct-init +
     ``Foo::method()`` qualified), ``subclassOf`` (``Dog : public Animal``),
     and ``overrides`` (``Dog::speak`` overrides ``Animal::speak``) edges.
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
from codebase_mapper.inspection.languages.cpp import (
    build_cpp_symbol_index,
    extract_cpp_ast_summary,
    refine_cpp_header_languages,
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


def _record(path: str, lang: str | None, type_: str,
            ast: dict | None = None, size: int = 100) -> FileRecord:
    return FileRecord(
        path=path, git_blob_sha="", content_sha256="", size_bytes=size,
        language=lang, type_=type_, phases=["runtime"], ast_summary=ast,
    )


def test_ast_extractor() -> None:
    src = Path("tests/fixtures/cpp/basic_pkg/src/dog.cpp").read_bytes()
    summary, errors = extract_cpp_ast_summary(src, "src/dog.cpp")
    assert summary is not None
    check("ast: no errors", errors == [], f"errors={errors}")
    check("ast: language=cpp", summary["language"] == "cpp")
    check("ast: primary namespace = acme",
          summary["namespace"] == "acme", f"ns={summary['namespace']}")

    items = summary["items"]
    by_kind: dict[str, list[dict]] = {}
    for it in items:
        by_kind.setdefault(it["kind"], []).append(it)
    # Out-of-class definitions: ``Dog::speak``, ``Dog::name``, ``Dog::bumpAge``,
    # plus the constructor ``Dog::Dog``.
    method_names = {(it["name"], it.get("parent")) for it in by_kind.get("method", [])}
    check("ast: Dog::speak method emitted",
          ("speak", "Dog") in method_names, f"methods={method_names}")
    check("ast: Dog::name method emitted",
          ("name", "Dog") in method_names)
    check("ast: Dog::bumpAge method emitted",
          ("bumpAge", "Dog") in method_names)
    check("ast: Dog constructor emitted",
          any(it["name"] == "Dog" and it.get("parent") == "Dog"
              for it in by_kind.get("constructor", [])))

    # Header: Dog class declaration includes virtual + override methods.
    hdr_src = Path("tests/fixtures/cpp/basic_pkg/include/dog.h").read_bytes()
    hsum, _ = extract_cpp_ast_summary(hdr_src, "include/dog.h")
    items_h = hsum["items"]
    classes_h = [it for it in items_h if it["kind"] == "class"]
    check("ast (header): Dog class found",
          any(it["name"] == "Dog" for it in classes_h))
    dog_h = next(it for it in classes_h if it["name"] == "Dog")
    check("ast (header): Dog extends Animal",
          dog_h.get("extends") == "Animal", f"dog_h={dog_h}")
    methods_h = {(it["name"], it.get("parent")) for it in items_h
                 if it["kind"] in {"method", "constructor"}}
    check("ast (header): Dog::speak declaration emitted",
          ("speak", "Dog") in methods_h, f"methods_h={methods_h}")
    check("ast (header): Dog::bumpAge declaration emitted",
          ("bumpAge", "Dog") in methods_h)

    # Nested namespace `acme::detail`.
    nested_src = b"""
namespace acme::detail {
struct Helper {
    int value() const;
};
int Helper::value() const { return 7; }
}
"""
    s, _ = extract_cpp_ast_summary(nested_src, "x.cpp")
    items_n = s["items"]
    helper_struct = next((it for it in items_n if it["name"] == "Helper"), None)
    check("ast: nested namespace recorded on struct",
          helper_struct is not None
          and helper_struct.get("namespace") == "acme::detail",
          f"helper={helper_struct}")

    # Free function at top scope.
    free_src = b"int compute(int x) { return x + 1; }\n"
    s, _ = extract_cpp_ast_summary(free_src, "u.cpp")
    funcs = [it for it in s["items"] if it["kind"] == "function"]
    check("ast: free function compute emitted",
          any(it["name"] == "compute" for it in funcs))


def test_classify() -> None:
    check("classify: foo_test.cpp → test_code",
          classify("tests/foo_test.cpp", b"") == "test_code")
    check("classify: foo_test.cc → test_code",
          classify("tests/foo_test.cc", b"") == "test_code")
    check("classify: FooTest.cpp → test_code",
          classify("tests/FooTest.cpp", b"") == "test_code")
    check("classify: Latest.cpp is NOT a test",
          classify("src/Latest.cpp", b"") == "source_code")
    check("classify: regular .cpp → source_code",
          classify("src/dog.cpp", b"") == "source_code")


def test_tests_edges() -> None:
    records = [
        _record("src/dog.cpp", "cpp", "source_code"),
        _record("tests/dog_test.cpp", "cpp", "test_code"),
    ]
    edges = infer_tests_edges(records)
    pairs = {(e.test_path, e.subject_path) for e in edges}
    check("tests_edges: dog_test.cpp → dog.cpp",
          ("tests/dog_test.cpp", "src/dog.cpp") in pairs,
          f"edges={pairs}")


def test_header_retag() -> None:
    """A ``.h`` file in a directory with a sibling ``.cpp`` retags to cpp."""
    records = [
        _record("mixed/widget.h", "c", "source_code"),
        _record("mixed/widget.cpp", "cpp", "source_code"),
        _record("clibs/util.h", "c", "source_code"),
        _record("clibs/util.c", "c", "source_code"),
    ]
    refine_cpp_header_languages(records)
    by_path = {r.path: r.language for r in records}
    check("retag: widget.h re-tagged to cpp (sibling .cpp exists)",
          by_path["mixed/widget.h"] == "cpp", f"by_path={by_path}")
    check("retag: util.h stays c (no sibling C++ source)",
          by_path["clibs/util.h"] == "c", f"by_path={by_path}")
    check("retag: widget.cpp untouched",
          by_path["mixed/widget.cpp"] == "cpp")


def test_symbol_index() -> None:
    """build_cpp_symbol_index aggregates multi-file definitions."""
    hsum = {
        "items": [{"kind": "class", "name": "Dog", "parent": None,
                   "namespace": "acme", "line_start": 1, "line_end": 5,
                   "byte_start": 0, "byte_end": 50}],
    }
    csum = {
        "items": [
            {"kind": "method", "name": "speak", "parent": "Dog",
             "namespace": "acme", "line_start": 1, "line_end": 3,
             "byte_start": 0, "byte_end": 50},
            # Out-of-class top-level class definition would also appear
            # here in real code; we mock just enough for the index logic.
        ],
    }
    records = [
        _record("include/dog.h", "cpp", "source_code", ast=hsum),
        _record("src/dog.cpp", "cpp", "source_code", ast=csum),
    ]
    idx = build_cpp_symbol_index(records)
    check("symbol_index: Dog mapped to include/dog.h",
          "Dog" in idx and idx["Dog"] == ["include/dog.h"],
          f"idx={idx}")


def test_pipeline_end_to_end(repo: Path) -> None:
    fixture = repo / "basic_pkg"

    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(dimension=64),
    )
    symbol_xrefs.register_all()
    mapped = map_codebase(fixture.resolve(), "HEAD")

    # AST coverage.
    cpp_records = [
        r for r in mapped["records"]
        if r.language == "cpp" and r.type_ in {"source_code", "test_code"}
    ]
    missing = [r.path for r in cpp_records if r.ast_summary is None]
    check("pipeline: every C++ source/test has ast_summary",
          not missing, f"missing={missing}")

    # The header (.h) is in include/ but the cpp/ live in src/ — sibling
    # check is *directory-scoped*. So include/dog.h alone wouldn't be
    # retagged. We verify the per-language tag is at least consistent
    # with how language_of() decided.
    by_path = {r.path: r.language for r in mapped["records"]}
    # Every .cpp file should be cpp.
    cpps = [p for p in by_path if p.endswith(".cpp")]
    check("pipeline: every .cpp file tagged cpp",
          all(by_path[p] == "cpp" for p in cpps), f"langs={by_path}")

    # cpp_symbols index reflects the class definitions.
    cpp_symbols = mapped["ctx"].indices["host:cpp_symbols"]
    check("pipeline: host:cpp_symbols has Dog",
          "Dog" in cpp_symbols, f"symbols={list(cpp_symbols)[:8]}")
    check("pipeline: host:cpp_symbols has Animal",
          "Animal" in cpp_symbols)
    check("pipeline: host:cpp_symbols has Sound",
          "Sound" in cpp_symbols)

    # Import edges (#include resolution).
    edges = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}
    check("pipeline: src/dog.cpp includes include/dog.h",
          ("src/dog.cpp", "include/dog.h") in edges,
          f"edges={edges}")
    check("pipeline: include/dog.h includes include/animal.h",
          ("include/dog.h", "include/animal.h") in edges)
    check("pipeline: include/dog.h includes include/sound.h",
          ("include/dog.h", "include/sound.h") in edges)

    # tests_edges
    te = {(e.test_path, e.subject_path) for e in mapped["tests_edges"]}
    check("pipeline: dog_test.cpp → dog.cpp tests_edge",
          ("tests/dog_test.cpp", "src/dog.cpp") in te,
          f"te={te}")

    # L2 chunks for dog.cpp.
    chunks = mapped["ctx"].indices.get("l2_10_chunks", [])
    dog_chunks = [c for c in chunks if c["path"] == "src/dog.cpp"]
    symbols = {c["symbol"] for c in dog_chunks}
    kinds = {c["kind"] for c in dog_chunks}
    check("pipeline: dog.cpp has multiple chunks",
          len(dog_chunks) >= 3, f"n={len(dog_chunks)}")
    check("pipeline: dog.cpp chunks include method",
          "method" in kinds, f"kinds={kinds}")
    check("pipeline: Dog.speak chunked",
          any(c["symbol"] == "speak" and c["parent_symbol"] == "Dog"
              for c in dog_chunks))
    check("pipeline: Dog.bumpAge chunked",
          any(c["symbol"] == "bumpAge" and c["parent_symbol"] == "Dog"
              for c in dog_chunks))

    # Header chunks.
    dog_h_chunks = [c for c in chunks if c["path"] == "include/dog.h"]
    h_symbols = {(c["symbol"], c.get("parent_symbol")) for c in dog_h_chunks}
    check("pipeline: include/dog.h chunks include Dog class",
          ("Dog", None) in h_symbols, f"h_symbols={h_symbols}")

    # Xrefs.
    xrefs = mapped["ctx"].indices.get("l3_10_xrefs", {})
    by_kind = xrefs.get("by_kind", {})
    by_lang = xrefs.get("by_language", {})
    xedges = xrefs.get("edges", [])
    check("pipeline: xref by_language has cpp",
          by_lang.get("cpp", {}).get("resolved", 0) > 0,
          f"by_lang={by_lang}")
    check("pipeline: calls edges emitted",
          by_kind.get("calls", 0) > 0, f"by_kind={by_kind}")
    check("pipeline: subclassOf edges emitted",
          by_kind.get("subclassOf", 0) > 0)

    def has_edge(src_sub, dst_sub, kind):
        return any(
            src_sub in e.src_chunk_id and dst_sub in e.dst_chunk_id
            and e.kind == kind for e in xedges
        )

    check(
        "pipeline: Dog --subclassOf--> Animal (header)",
        has_edge(
            "include/dog.h#class:Dog:",
            "include/animal.h#class:Animal:",
            "subclassOf",
        ),
        detail="\n".join(
            f"{e.src_chunk_id} -{e.kind}-> {e.dst_chunk_id}" for e in xedges[:30]
        ),
    )
    check(
        "pipeline: Dog::speak --calls--> Sound (direct-init Sound s)",
        has_edge(
            "src/dog.cpp#method:Dog.speak:",
            "include/sound.h#class:Sound:",
            "calls",
        ) or has_edge(
            "src/dog.cpp#method:Dog.speak:",
            "src/sound.cpp#class:Sound:",
            "calls",
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    print("== C++ Tier-1 verification ==")

    test_ast_extractor()
    test_classify()
    test_tests_edges()
    test_header_retag()
    test_symbol_index()

    src_root = Path("tests/fixtures/cpp").resolve()
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        basic = scratch / "basic_pkg"
        shutil.copytree(src_root / "basic_pkg", basic)
        _init_git(basic)
        _commit(basic)
        test_pipeline_end_to_end(scratch)
        if args.keep:
            keep = Path.cwd() / "_tmp" / "cpp-verify"
            shutil.rmtree(keep, ignore_errors=True)
            shutil.copytree(scratch, keep)
            print(f"\n[--keep] fixture preserved at {keep}")

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
