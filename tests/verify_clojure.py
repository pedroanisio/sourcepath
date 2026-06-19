#!/usr/bin/env python3
"""verify_clojure.py — Tier-1 Clojure support invariants.

Clojure has no PyPI tree-sitter grammar, so the analyzer is a self-contained
s-expression reader (like the Python stdlib-``ast`` analyzer). Covers:

  1. Classification: ``.clj`` / ``.cljs`` / ``.cljc`` -> source_code.
  2. AST extractor returns ``namespace``, ``imports`` (``:require`` / ``:use``
     namespaces), and structured ``items`` with line/byte spans for
     defn/defn-/def/defrecord/defprotocol/ns forms.
  3. L2 chunker emits one chunk per def-form (the ``ns`` form is not chunked).
  4. Import resolution: a required namespace maps to an in-repo file
     (dots -> slashes, dashes -> underscores, under any source root); stdlib /
     third-party namespaces are external.
  5. The reader is string/char/comment-aware (delimiters inside ``";"`` /
     ``\\(`` / ``; comment`` never miscount).

Run from the repo root:  uv run python tests/verify_clojure.py
"""
from __future__ import annotations

import sys

from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codebase_mapper.inspection.classify import classify
from codebase_mapper.inspection.languages.clojure import (
    extract_clojure_ast_summary,
    resolve_clojure_imports,
)

from plugins.chunks_embeddings.chunker import _chunk_clojure


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


CLJ_SRC = b"""(ns my.app.core
  \"Core namespace.\"
  (:require [my.app.util :as util]
            [my.app.string-utils :as su :refer [trim]]
            [clojure.string :as str]
            my.app.db))

;; a comment with parens ( ) and a semicolon ;
(def config {:port 8080 :sep \\;})

(defn greet
  \"Say hello.\"
  [name]
  (str \"hi (\" name \")\"))

(defn- private-helper [x] (inc x))

(defrecord Point [x y])

(defprotocol Shape
  (area [this]))
"""


def _by_name(items: list[dict]) -> dict[str, dict]:
    return {it["name"]: it for it in items}


def main(argv: list[str] | None = None) -> int:
    print("== Clojure Tier-1 verification ==")

    # 1. Classification
    check("classify: .clj is source_code", classify("core.clj", b"(ns x)") == "source_code")
    check("classify: .cljs is source_code", classify("core.cljs", b"(ns x)") == "source_code")
    check("classify: .cljc is source_code", classify("core.cljc", b"(ns x)") == "source_code")

    # 2. Extraction
    summary, errors = extract_clojure_ast_summary(CLJ_SRC, "core.clj")
    check("extract: returns a summary", summary is not None, str(errors))
    check("extract: namespace is my.app.core", summary.get("namespace") == "my.app.core",
          str(summary.get("namespace")))

    imports = [i["source"] for i in summary.get("imports", [])]
    for ns in ("my.app.util", "my.app.string-utils", "clojure.string", "my.app.db"):
        check(f"extract: require {ns}", ns in imports, str(imports))
    check("extract: :refer'd symbol 'trim' is NOT captured as a namespace",
          "trim" not in imports, str(imports))

    items = summary.get("items", [])
    by = _by_name(items)
    check("extract: defn greet -> function", by.get("greet", {}).get("kind") == "function")
    check("extract: defn- private-helper -> function",
          by.get("private-helper", {}).get("kind") == "function")
    check("extract: def config -> var", by.get("config", {}).get("kind") == "var")
    check("extract: defrecord Point -> record", by.get("Point", {}).get("kind") == "record")
    check("extract: defprotocol Shape -> protocol", by.get("Shape", {}).get("kind") == "protocol")
    check("extract: ns form -> namespace item",
          by.get("my.app.core", {}).get("kind") == "namespace")
    check("extract: items carry byte+line spans",
          all(all(k in it for k in ("line_start", "line_end", "byte_start", "byte_end"))
              for it in items), str(items))
    check("extract: comment/char-literal parens do not corrupt the def scan "
          "(config + greet both found)",
          "config" in by and "greet" in by, str(sorted(by)))
    check("extract: top_level_functions", set(summary.get("top_level_functions", [])) ==
          {"greet", "private-helper"}, str(summary.get("top_level_functions")))
    check("extract: top_level_classes", set(summary.get("top_level_classes", [])) ==
          {"Point", "Shape"}, str(summary.get("top_level_classes")))

    # 3. Chunking
    rec = SimpleNamespace(ast_summary=summary, path="core.clj")
    chunks = _chunk_clojure(CLJ_SRC, rec)
    csym = {c["symbol"]: c for c in chunks}
    check("chunk: per-def chunks for greet/config/Point/Shape",
          {"greet", "config", "Point", "Shape"} <= set(csym), str(sorted(csym)))
    check("chunk: ns form is not chunked", "my.app.core" not in csym, str(sorted(csym)))
    check("chunk: greet chunk text is its own form",
          csym.get("greet", {}).get("text", "").startswith("(defn greet"),
          str(csym.get("greet", {}).get("text", ""))[:60])

    # 4. Resolution (dots->slashes, dashes->underscores, under a source root)
    paths = {
        "src/my/app/core.clj",
        "src/my/app/util.clj",
        "src/my/app/string_utils.clj",
        "src/my/app/db.cljc",
    }
    in_repo, external = resolve_clojure_imports("src/my/app/core.clj", summary, paths)
    check("resolve: my.app.util -> src/my/app/util.clj",
          "src/my/app/util.clj" in in_repo, str(in_repo))
    check("resolve: dashed ns my.app.string-utils -> string_utils.clj",
          "src/my/app/string_utils.clj" in in_repo, str(in_repo))
    check("resolve: my.app.db -> .cljc file (ext-agnostic)",
          "src/my/app/db.cljc" in in_repo, str(in_repo))
    check("resolve: clojure.string is external (stdlib)",
          "clojure.string" in external, str(external))

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
