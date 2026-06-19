#!/usr/bin/env python3
"""verify_go.py — Tier-1 Go support invariants.

Mirrors verify_cpp.py / verify_java.py. Covers:

  1. Classification: ``.go`` -> source_code; ``*_test.go`` -> test_code.
  2. AST extractor returns ``imports`` (parsed package paths) and structured
     ``items`` with line/byte spans for top-level funcs, methods (parent =
     receiver type), structs, and interfaces.
  3. L2 chunker emits one chunk per item (method chunks carry
     ``parent_symbol`` = receiver type).
  4. Import resolution: a module-local import resolves to an in-repo ``.go``
     file; a third-party import is reported unresolved (external).
  5. ``detect_go_module`` reads ``go.mod``; ``go_package_root`` collapses a
     sub-package import to its module root.

Run from the repo root:  uv run python tests/verify_go.py
"""
from __future__ import annotations

import sys

from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codebase_mapper.inspection.classify import classify
from codebase_mapper.inspection.languages.go import (
    detect_go_module,
    extract_go_ast_summary,
    go_package_root,
    resolve_go_imports,
)
from codebase_mapper.ts_setup import TS_AVAILABLE

from plugins.chunks_embeddings.chunker import _chunk_go


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
            for line in str(detail).splitlines()[:20]:
                print(f"        {line}")
        FAIL += 1


GO_SRC = b"""package main

import (
	"fmt"
	"example.com/app/foo"
)

type Greeter struct {
	Name string
}

type Speaker interface {
	Speak() string
}

func (g *Greeter) Hello() string {
	return fmt.Sprintf("hi %s", g.Name)
}

func main() {
	fmt.Println("x")
}
"""


def _items_by_name(items: list[dict]) -> dict[str, dict]:
    return {it["name"]: it for it in items}


def main(argv: list[str] | None = None) -> int:
    print("== Go Tier-1 verification ==")

    # 1. Classification
    check("classify: .go is source_code",
          classify("main.go", b"package main\n") == "source_code")
    check("classify: *_test.go is test_code",
          classify("foo_test.go", b"package main\n") == "test_code")

    if not TS_AVAILABLE:
        print("  SKIP  tree-sitter unavailable — extraction/chunk checks skipped")
        print(f"\nPassed: {PASS}    Failed: {FAIL}")
        return 1 if FAIL else 0

    # 2. Extraction
    summary, errors = extract_go_ast_summary(GO_SRC, "main.go")
    check("extract: returns a summary", summary is not None, str(errors))
    imports = [i["source"] for i in summary.get("imports", [])]
    check("extract: parses imports (fmt + module path)",
          "fmt" in imports and "example.com/app/foo" in imports, str(imports))

    items = summary.get("items", [])
    by_name = _items_by_name(items)
    check("extract: struct item Greeter", by_name.get("Greeter", {}).get("kind") == "struct")
    check("extract: interface item Speaker", by_name.get("Speaker", {}).get("kind") == "interface")
    check("extract: function item main", by_name.get("main", {}).get("kind") == "function")
    hello = by_name.get("Hello", {})
    check("extract: method Hello has parent=Greeter (receiver type)",
          hello.get("kind") == "method" and hello.get("parent") == "Greeter",
          str(hello))
    check("extract: items carry byte+line spans",
          all(all(k in it for k in ("line_start", "line_end", "byte_start", "byte_end"))
              for it in items),
          str(items))

    # 3. Chunking
    rec = SimpleNamespace(ast_summary=summary, path="main.go")
    chunks = _chunk_go(GO_SRC, rec)
    csym = {c["symbol"]: c for c in chunks}
    check("chunk: one chunk per top-level item (>=4)", len(chunks) >= 4, f"{len(chunks)} chunks")
    check("chunk: method Hello chunk carries parent_symbol=Greeter",
          csym.get("Hello", {}).get("parent_symbol") == "Greeter", str(csym.get("Hello")))
    check("chunk: each chunk text is its symbol's source span",
          csym.get("main", {}).get("text", "").startswith("func main()"),
          str(csym.get("main", {}).get("text", ""))[:80])

    # 4 + 5. Resolution + module detection
    files = {
        "go.mod": b"module example.com/app\n\ngo 1.21\n",
        "main.go": GO_SRC,
        "foo/bar.go": b"package foo\n",
    }
    records = [SimpleNamespace(path=p) for p in files]
    module = detect_go_module(records, lambda p: files[p])
    check("module: detect_go_module reads module path",
          module is not None and module["module_path"] == "example.com/app", str(module))

    res_summary = {"imports": [{"source": "example.com/app/foo"},
                               {"source": "github.com/pkg/errors"}]}
    in_repo, external = resolve_go_imports("main.go", res_summary, module, set(files))
    check("resolve: module-local import -> in-repo .go file",
          "foo/bar.go" in in_repo, str(in_repo))
    check("resolve: third-party import -> unresolved (external)",
          "github.com/pkg/errors" in external, str(external))
    check("go_package_root: collapses sub-package to module root",
          go_package_root("github.com/pkg/errors/internal") == "github.com/pkg/errors")

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
