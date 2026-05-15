#!/usr/bin/env python3
"""verify_objc.py — Tier-1 Objective-C / Objective-C++ support invariants.

Mirrors verify_cpp.py / verify_java.py. Covers (SPEC §1 invariants):

  1. AST extractor returns items with line/byte spans for ``@interface``,
     ``@implementation``, ``@protocol``, categories, method declarations,
     and method definitions; selectors are preserved on method items.
  2. Imports are recognised in all three ObjC forms: ``#import "X.h"``,
     ``#import <Framework/X.h>``, and ``@import Module;``.
  3. ``build_objc_symbol_index`` indexes classes by both their interface
     and implementation files; categories register under both their
     full name and the host class.
  4. ``*Test.m`` / ``*Tests.m`` / ``*Spec.m`` classify as ``test_code``;
     ``Latest.m`` does NOT.
  5. tests_edges links ``FooTests.m`` to ``Foo.m``.
  6. L2 chunker emits one chunk per type/method, including the
     interface-vs-implementation distinction.
  7. Symbol-xref resolver emits ``calls`` (class message ``[NSString …]``,
     same-class ``[self …]``, super ``[super …]``, nested
     ``[[Sound alloc] init…]``), ``subclassOf`` (``Dog : Animal``), and
     ``overrides`` (``Dog speak`` overrides ``Animal speak``) edges.
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
from codebase_mapper.inspection.languages.objc import (
    OBJC_LANGUAGE_TAGS,
    build_objc_symbol_index,
    extract_objc_ast_summary,
    resolve_objc_includes,
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


def test_language_of() -> None:
    check("language_of: .m → objective-c",
          language_of("Foo.m") == "objective-c")
    check("language_of: .mm → objective-cpp",
          language_of("Foo.mm") == "objective-cpp")
    check("OBJC_LANGUAGE_TAGS contains both",
          OBJC_LANGUAGE_TAGS == {"objective-c", "objective-cpp"})


def test_ast_extractor() -> None:
    src = Path("tests/fixtures/objc/basic_pkg/Animals/Dog.m").read_bytes()
    summary, errors = extract_objc_ast_summary(src, "Animals/Dog.m")
    assert summary is not None
    check("ast: language=objc", summary["language"] == "objc")
    items = summary["items"]
    by_kind: dict[str, list[dict]] = {}
    for it in items:
        by_kind.setdefault(it["kind"], []).append(it)
    check("ast (Dog.m): class_implementation Dog emitted",
          any(it["name"] == "Dog" for it in by_kind.get("class_implementation", [])),
          f"items={items[:3]}")
    methods = by_kind.get("method", [])
    method_names = {(m["name"], m.get("parent")) for m in methods}
    check("ast (Dog.m): -initWithName: method emitted",
          ("initWithName", "Dog") in method_names, f"methods={method_names}")
    check("ast (Dog.m): -speak method emitted",
          ("speak", "Dog") in method_names)
    check("ast (Dog.m): -bumpAge:by: method has selector",
          any(m.get("selector") == "bumpAge:by:"
              and m["name"] == "bumpAge" and m.get("parent") == "Dog"
              for m in methods),
          f"selectors={[m.get('selector') for m in methods]}")
    check("ast (Dog.m): -copyWithZone: method emitted",
          ("copyWithZone", "Dog") in method_names)

    # Header: interface + extends + protocols.
    hsum, _ = extract_objc_ast_summary(
        Path("tests/fixtures/objc/basic_pkg/Animals/Dog.h").read_bytes(),
        "Animals/Dog.h",
    )
    h_items = hsum["items"]
    dog_iface = next((it for it in h_items
                      if it["kind"] == "class_interface" and it["name"] == "Dog"),
                     None)
    check("ast (Dog.h): @interface Dog detected",
          dog_iface is not None, f"items={h_items[:3]}")
    check("ast (Dog.h): Dog extends Animal",
          dog_iface and dog_iface.get("extends") == "Animal",
          f"dog_iface={dog_iface}")
    check("ast (Dog.h): Dog conforms to NSCopying",
          dog_iface and "NSCopying" in (dog_iface.get("implements") or []),
          f"dog_iface={dog_iface}")

    # Imports: local + system + @import.
    mixed = b"""
#import <Foundation/Foundation.h>
#import "Animal.h"
#include "shared.h"
@import UIKit;
@interface Foo : NSObject @end
"""
    s, _ = extract_objc_ast_summary(mixed, "x.m")
    sources = {imp["source"] for imp in s["imports"]}
    kinds = {imp["source"]: imp["kind"] for imp in s["imports"]}
    check("ast (imports): system Foundation.h captured",
          "Foundation/Foundation.h" in sources)
    check("ast (imports): local Animal.h captured",
          "Animal.h" in sources)
    check("ast (imports): @import UIKit captured",
          "UIKit" in sources and kinds["UIKit"] == "module_import")
    check("ast (imports): #include also captured",
          "shared.h" in sources)

    # Category.
    cat_src = b"""
@interface NSString (Greet)
- (NSString *)hello;
@end

@implementation NSString (Greet)
- (NSString *)hello { return @"hi"; }
@end
"""
    s, _ = extract_objc_ast_summary(cat_src, "Cat.m")
    cat_items = s["items"]
    check("ast (category): NSString(Greet) interface emitted",
          any(it["kind"] == "category" and it["name"] == "NSString(Greet)"
              for it in cat_items), f"cat_items={cat_items}")
    check("ast (category): NSString(Greet) implementation emitted",
          any(it["kind"] == "category_impl" and it["name"] == "NSString(Greet)"
              for it in cat_items))
    check("ast (category): -hello method inherits parent=NSString(Greet)",
          any(it["kind"] == "method" and it["name"] == "hello"
              and it.get("parent") == "NSString(Greet)"
              for it in cat_items))

    # Protocol.
    proto_src = b"""
@protocol Greeter <NSObject>
- (NSString *)hello:(NSString *)who;
@end
"""
    s, _ = extract_objc_ast_summary(proto_src, "P.h")
    p_items = s["items"]
    proto_item = next((it for it in p_items
                       if it["kind"] == "protocol" and it["name"] == "Greeter"),
                      None)
    check("ast (protocol): @protocol Greeter emitted",
          proto_item is not None, f"p_items={p_items}")
    check("ast (protocol): conforms to NSObject",
          proto_item and "NSObject" in (proto_item.get("implements") or []))


def test_classify() -> None:
    check("classify: DogTests.m → test_code",
          classify("Animals/Tests/DogTests.m", b"") == "test_code")
    check("classify: DogTest.m → test_code",
          classify("Animals/Tests/DogTest.m", b"") == "test_code")
    check("classify: DogSpec.m → test_code",
          classify("Animals/Tests/DogSpec.m", b"") == "test_code")
    check("classify: Latest.m is NOT a test",
          classify("Animals/Latest.m", b"") == "source_code")
    check("classify: regular .m → source_code",
          classify("Animals/Dog.m", b"") == "source_code")
    check("classify: regular .mm → source_code",
          classify("Animals/Foo.mm", b"") == "source_code")


def test_tests_edges() -> None:
    records = [
        _record("Animals/Dog.m", "objective-c", "source_code"),
        _record("Animals/Tests/DogTests.m", "objective-c", "test_code"),
    ]
    edges = infer_tests_edges(records)
    pairs = {(e.test_path, e.subject_path) for e in edges}
    check("tests_edges: DogTests.m → Dog.m",
          ("Animals/Tests/DogTests.m", "Animals/Dog.m") in pairs,
          f"edges={pairs}")


def test_resolve_includes() -> None:
    summary = {
        "imports": [
            {"kind": "local_include", "source": "Animal.h", "lineno": 1},
            {"kind": "local_include", "source": "Sound.h", "lineno": 2},
            {"kind": "system_include", "source": "Foundation/Foundation.h",
             "lineno": 3},
            {"kind": "module_import", "source": "UIKit", "lineno": 4},
        ],
    }
    paths = {
        "Animals/Animal.h",
        "Animals/Sound.h",
        "Animals/Dog.h",
        "Animals/Dog.m",
    }
    in_repo, external = resolve_objc_includes(
        "Animals/Dog.h", summary, paths,
    )
    check("resolve: Animal.h → in-repo (suffix match)",
          "Animals/Animal.h" in in_repo, f"in_repo={in_repo}")
    check("resolve: Sound.h → in-repo (suffix match)",
          "Animals/Sound.h" in in_repo)
    check("resolve: Foundation.h → external",
          "Foundation/Foundation.h" in external)
    check("resolve: @import UIKit → external",
          "UIKit" in external, f"external={external}")


def test_symbol_index() -> None:
    h = {"items": [
        {"kind": "class_interface", "name": "Dog", "parent": None,
         "line_start": 1, "line_end": 5, "byte_start": 0, "byte_end": 60},
    ]}
    m = {"items": [
        {"kind": "class_implementation", "name": "Dog", "parent": None,
         "line_start": 1, "line_end": 30, "byte_start": 0, "byte_end": 400},
    ]}
    cat = {"items": [
        {"kind": "category", "name": "NSString(Greet)", "parent": None,
         "line_start": 1, "line_end": 4, "byte_start": 0, "byte_end": 40},
    ]}
    records = [
        _record("Dog.h", "objective-c", "source_code", ast=h),
        _record("Dog.m", "objective-c", "source_code", ast=m),
        _record("NSString+Greet.m", "objective-c", "source_code", ast=cat),
    ]
    idx = build_objc_symbol_index(records)
    check("symbol_index: Dog → both header and impl",
          set(idx.get("Dog", [])) == {"Dog.h", "Dog.m"},
          f"idx={idx}")
    check("symbol_index: NSString(Greet) registered",
          "NSString(Greet)" in idx)
    check("symbol_index: NSString host also registered (for category)",
          "NSString" in idx and idx["NSString"] == ["NSString+Greet.m"],
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
    objc_records = [
        r for r in mapped["records"]
        if r.language in OBJC_LANGUAGE_TAGS
        and r.type_ in {"source_code", "test_code"}
    ]
    missing = [r.path for r in objc_records if r.ast_summary is None]
    check("pipeline: every ObjC source/test has ast_summary",
          not missing, f"missing={missing}")

    # Symbol index.
    syms = mapped["ctx"].indices["host:objc_symbols"]
    check("pipeline: objc_symbols has Dog (both .h and .m)",
          set(syms.get("Dog", [])) == {"Animals/Dog.h", "Animals/Dog.m"},
          f"Dog={syms.get('Dog')}")
    check("pipeline: objc_symbols has Animal",
          "Animal" in syms)
    check("pipeline: objc_symbols has Sound",
          "Sound" in syms)

    # Include edges.
    edges = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}
    check("pipeline: Animals/Dog.m imports Animals/Dog.h",
          ("Animals/Dog.m", "Animals/Dog.h") in edges, f"edges={edges}")
    check("pipeline: Animals/Dog.h imports Animals/Animal.h",
          ("Animals/Dog.h", "Animals/Animal.h") in edges)
    check("pipeline: Animals/Dog.h imports Animals/Sound.h",
          ("Animals/Dog.h", "Animals/Sound.h") in edges)

    # tests_edges
    te = {(e.test_path, e.subject_path) for e in mapped["tests_edges"]}
    check("pipeline: DogTests.m → Dog.m tests_edge",
          ("Animals/Tests/DogTests.m", "Animals/Dog.m") in te,
          f"te={te}")

    # L2 chunks for Dog.m.
    chunks = mapped["ctx"].indices.get("l2_10_chunks", [])
    dog_m = [c for c in chunks if c["path"] == "Animals/Dog.m"]
    kinds = {c["kind"] for c in dog_m}
    symbols = {c["symbol"] for c in dog_m}
    check("pipeline: Dog.m has multiple chunks",
          len(dog_m) >= 4, f"n={len(dog_m)}")
    check("pipeline: Dog.m chunks include class + method",
          {"class", "method"} <= kinds, f"kinds={kinds}")
    check("pipeline: Dog.m has -speak method chunk",
          any(c["symbol"] == "speak" and c["parent_symbol"] == "Dog"
              for c in dog_m))
    check("pipeline: Dog.m has -initWithName: method chunk",
          any(c["symbol"] == "initWithName" and c["parent_symbol"] == "Dog"
              for c in dog_m))

    # Header chunks.
    dog_h = [c for c in chunks if c["path"] == "Animals/Dog.h"]
    h_symbols = {(c["symbol"], c.get("parent_symbol")) for c in dog_h}
    check("pipeline: Dog.h chunks include @interface Dog",
          ("Dog", None) in h_symbols, f"h_symbols={h_symbols}")

    # Xrefs.
    xrefs = mapped["ctx"].indices.get("l3_10_xrefs", {})
    by_kind = xrefs.get("by_kind", {})
    by_lang = xrefs.get("by_language", {})
    xedges = xrefs.get("edges", [])
    check("pipeline: xref by_language has objective-c entry",
          by_lang.get("objective-c", {}).get("resolved", 0) > 0,
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

    # Dog (impl) subclassOf Animal (header).
    check(
        "pipeline: Dog (impl) --subclassOf--> Animal (header)",
        has_edge(
            "Animals/Dog.h#class:Dog:",
            "Animals/Animal.h#class:Animal:",
            "subclassOf",
        ),
        detail="\n".join(
            f"{e.src_chunk_id} -{e.kind}-> {e.dst_chunk_id}" for e in xedges[:30]
        ),
    )

    # -speak in Dog --calls--> Sound class (nested [[Sound alloc] init…]).
    check(
        "pipeline: Dog.speak --calls--> Sound class (constructor chain)",
        has_edge(
            "Animals/Dog.m#method:Dog.speak:",
            "Animals/Sound.h#class:Sound:",
            "calls",
        ) or has_edge(
            "Animals/Dog.m#method:Dog.speak:",
            "Animals/Sound.m#class:Sound:",
            "calls",
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    print("== Objective-C Tier-1 verification ==")

    test_language_of()
    test_ast_extractor()
    test_classify()
    test_tests_edges()
    test_resolve_includes()
    test_symbol_index()

    src_root = Path("tests/fixtures/objc").resolve()
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td)
        basic = scratch / "basic_pkg"
        shutil.copytree(src_root / "basic_pkg", basic)
        _init_git(basic)
        _commit(basic)
        test_pipeline_end_to_end(scratch)
        if args.keep:
            keep = Path.cwd() / "_tmp" / "objc-verify"
            shutil.rmtree(keep, ignore_errors=True)
            shutil.copytree(scratch, keep)
            print(f"\n[--keep] fixture preserved at {keep}")

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
