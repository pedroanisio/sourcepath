#!/usr/bin/env python3
"""verify_cobol.py — Tier-1 COBOL support invariants.

COBOL has no PyPI tree-sitter grammar, so the analyzer is a self-contained,
column-aware reader (like the Clojure s-expr reader and the Python stdlib-``ast``
analyzer). This exercises the on-disk fixture under tests/fixtures/cobol/animals/
end to end. Covers:

  1. Classification: ``.cbl`` / ``.cob`` / ``.cpy`` -> source_code.
  2. AST extraction, FIXED format: programs -> top_level_classes, PROCEDURE
     DIVISION paragraphs/sections -> top_level_functions, with line/byte spans.
     Area-A gating: statements in Area B are NOT mistaken for paragraph headers.
  3. AST extraction, FREE format: ``>> SOURCE FORMAT FREE`` is detected and the
     same surface is recovered without column rules; ``*>`` inline comments and
     lone reserved words (``GOBACK.``) do not corrupt the paragraph scan.
  4. COPY resolution: ``COPY ANIMAL`` -> in-repo copybooks/ANIMAL.cpy; an
     unknown copybook surfaces as external (never dropped).
  5. L2 chunker: one chunk per program (class) + one per paragraph/section
     (method, parent = program); a data-only copybook falls back to whole-file.
  6. Symbol xrefs: ``PERFORM`` -> intra-program calls (incl. THRU),
     ``CALL 'DOG'`` -> inter-program call, ``CALL identifier`` -> unresolved
     ``dynamic_dispatch``.

Run from the repo root:  uv run python tests/verify_cobol.py
"""
from __future__ import annotations

import sys

from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codebase_mapper.inspection.classify import classify
from codebase_mapper.inspection.languages.cobol import (
    extract_cobol_ast_summary,
    resolve_cobol_imports,
)
from plugins.chunks_embeddings.chunker import _chunk_cobol
from plugins.symbol_xrefs.cobol_resolver import resolve_cobol_calls


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cobol" / "animals"

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


def _by_name(items: list[dict]) -> dict[str, dict]:
    return {it["name"]: it for it in items}


def _rec(path: str, summary: dict) -> SimpleNamespace:
    return SimpleNamespace(path=path, language="cobol", ast_summary=summary)


def main(argv: list[str] | None = None) -> int:
    print("== COBOL Tier-1 verification ==")

    # 1. Classification
    check("classify: .cbl is source_code",
          classify("ANIMALS.cbl", b"       PROGRAM-ID. X.") == "source_code")
    check("classify: .cob is source_code",
          classify("prog.cob", b"       PROGRAM-ID. X.") == "source_code")
    check("classify: .cpy is source_code",
          classify("book.cpy", b"       01 R.") == "source_code")

    # Load fixture bytes.
    animals_b = (FIXTURE / "ANIMALS.cbl").read_bytes()
    dog_b = (FIXTURE / "DOG.cbl").read_bytes()
    sound_b = (FIXTURE / "SOUND.cbl").read_bytes()
    copybook_b = (FIXTURE / "copybooks" / "ANIMAL.cpy").read_bytes()

    # 2. Extraction — FIXED format (ANIMALS.cbl)
    a_sum, a_err = extract_cobol_ast_summary(animals_b, "ANIMALS.cbl")
    check("extract: ANIMALS returns a summary", a_sum is not None, str(a_err))
    check("extract: source_format detected as fixed",
          a_sum.get("source_format") == "fixed", str(a_sum.get("source_format")))
    check("extract: extraction_method is regex",
          a_sum.get("extraction_method") == "regex")
    check("extract: program ANIMALS -> top_level_classes",
          a_sum.get("top_level_classes") == ["ANIMALS"],
          str(a_sum.get("top_level_classes")))
    funcs = set(a_sum.get("top_level_functions", []))
    check("extract: paragraphs -> top_level_functions",
          {"MAIN-PARA", "GREET-DOG", "PLAY-SOUND-PARA", "PLAY-SOUND-END"} == funcs,
          str(sorted(funcs)))
    a_by = _by_name(a_sum.get("items", []))
    check("extract: ANIMALS program item kind",
          a_by.get("ANIMALS", {}).get("kind") == "program")
    check("extract: MAIN-PARA paragraph item kind + parent",
          a_by.get("MAIN-PARA", {}).get("kind") == "paragraph"
          and a_by.get("MAIN-PARA", {}).get("parent") == "ANIMALS")
    check("extract: items carry byte+line spans",
          all(all(k in it for k in ("line_start", "line_end", "byte_start", "byte_end"))
              for it in a_sum.get("items", [])), str(a_sum.get("items")))
    check("extract: Area-B statement 'STOP RUN.' is NOT a paragraph",
          "STOP" not in funcs and "RUN" not in funcs, str(sorted(funcs)))
    check("extract: WORKING-STORAGE SECTION (DATA div) is not a procedure",
          "WORKING-STORAGE" not in funcs, str(sorted(funcs)))
    a_imports = [i["source"] for i in a_sum.get("imports", [])]
    check("extract: COPY ANIMAL captured as import", a_imports == ["ANIMAL"],
          str(a_sum.get("imports")))

    # 3. Extraction — FREE format (SOUND.cbl)
    s_sum, s_err = extract_cobol_ast_summary(sound_b, "SOUND.cbl")
    check("extract: SOUND returns a summary", s_sum is not None, str(s_err))
    check("extract: source_format detected as free",
          s_sum.get("source_format") == "free", str(s_sum.get("source_format")))
    check("extract: free-format program SOUND",
          s_sum.get("top_level_classes") == ["SOUND"],
          str(s_sum.get("top_level_classes")))
    check("extract: free-format paragraph PLAY-SOUND-PARA",
          s_sum.get("top_level_functions") == ["PLAY-SOUND-PARA"],
          str(s_sum.get("top_level_functions")))
    check("extract: lone reserved word GOBACK. is NOT a paragraph",
          "GOBACK" not in s_sum.get("top_level_functions", []),
          str(s_sum.get("top_level_functions")))

    # DOG.cbl
    d_sum, _ = extract_cobol_ast_summary(dog_b, "DOG.cbl")
    check("extract: DOG program + SPEAK/MAKE-SOUND paragraphs",
          d_sum.get("top_level_classes") == ["DOG"]
          and set(d_sum.get("top_level_functions", [])) == {"SPEAK", "MAKE-SOUND"},
          str(d_sum))

    # Data-only copybook: valid summary, no programs/procedures.
    c_sum, c_err = extract_cobol_ast_summary(copybook_b, "copybooks/ANIMAL.cpy")
    check("extract: data-only copybook yields a non-null summary",
          c_sum is not None, str(c_err))
    check("extract: copybook has no programs/procedures",
          not c_sum.get("top_level_classes") and not c_sum.get("top_level_functions"),
          str(c_sum))

    # 4. COPY resolution
    paths = {
        "ANIMALS.cbl", "DOG.cbl", "SOUND.cbl", "copybooks/ANIMAL.cpy",
    }
    in_repo, external = resolve_cobol_imports("ANIMALS.cbl", a_sum, paths)
    check("resolve: COPY ANIMAL -> copybooks/ANIMAL.cpy",
          in_repo == ["copybooks/ANIMAL.cpy"], str(in_repo))
    check("resolve: no external for a resolved copybook", external == [], str(external))
    missing_sum = {"imports": [{"kind": "copy", "source": "VENDOR", "lineno": 1}]}
    mi, me = resolve_cobol_imports("ANIMALS.cbl", missing_sum, paths)
    check("resolve: unknown copybook surfaces as external (not dropped)",
          me == ["VENDOR"] and mi == [], f"in={mi} ext={me}")

    # 5. Chunking
    a_chunks = _chunk_cobol(animals_b, _rec("ANIMALS.cbl", a_sum))
    kinds = {(c["symbol"], c["kind"], c["parent_symbol"]) for c in a_chunks}
    check("chunk: program ANIMALS -> class chunk",
          ("ANIMALS", "class", None) in kinds, str(sorted(kinds)))
    check("chunk: paragraph GREET-DOG -> method chunk parented on ANIMALS",
          ("GREET-DOG", "method", "ANIMALS") in kinds, str(sorted(kinds)))
    check("chunk: GREET-DOG chunk text contains its PERFORM body",
          any(c["symbol"] == "GREET-DOG" and "PERFORM PLAY-SOUND-PARA" in c["text"]
              for c in a_chunks))
    cpy_chunks = _chunk_cobol(copybook_b, _rec("copybooks/ANIMAL.cpy", c_sum))
    check("chunk: data-only copybook falls back to a whole-file chunk",
          len(cpy_chunks) == 1 and cpy_chunks[0]["kind"] == "file",
          str(cpy_chunks))

    # 6. Symbol xrefs — build a minimal ctx with an l2_10_chunks index.
    d_chunks = _chunk_cobol(dog_b, _rec("DOG.cbl", d_sum))
    s_chunks = _chunk_cobol(sound_b, _rec("SOUND.cbl", s_sum))
    all_chunks: list[dict] = []
    for path, chs in (("ANIMALS.cbl", a_chunks), ("DOG.cbl", d_chunks),
                      ("SOUND.cbl", s_chunks)):
        for i, c in enumerate(chs):
            c = dict(c)
            c["path"] = path
            c["chunk_id"] = f"{path}#{i}"
            all_chunks.append(c)

    records = [
        _rec("ANIMALS.cbl", a_sum), _rec("DOG.cbl", d_sum), _rec("SOUND.cbl", s_sum),
    ]
    ctx = SimpleNamespace(
        indices={"l2_10_chunks": all_chunks},
        scratch={},
        paths_set=paths,
        records=records,
    )
    cid = {(c["path"], c["symbol"]): c["chunk_id"] for c in all_chunks}

    a_edges, a_unres = resolve_cobol_calls(records[0], ctx)
    edge_set = {(e.src_chunk_id, e.dst_chunk_id, e.kind, e.resolver) for e in a_edges}
    check("xref: PERFORM GREET-DOG -> intra-program calls edge",
          (cid[("ANIMALS.cbl", "MAIN-PARA")], cid[("ANIMALS.cbl", "GREET-DOG")],
           "calls", "cobol_intra_file") in edge_set, str(sorted(edge_set)))
    check("xref: PERFORM ... THRU emits edges to both endpoints",
          (cid[("ANIMALS.cbl", "GREET-DOG")], cid[("ANIMALS.cbl", "PLAY-SOUND-PARA")],
           "calls", "cobol_intra_file") in edge_set
          and (cid[("ANIMALS.cbl", "GREET-DOG")], cid[("ANIMALS.cbl", "PLAY-SOUND-END")],
               "calls", "cobol_intra_file") in edge_set, str(sorted(edge_set)))
    check("xref: CALL 'DOG' -> inter-program calls edge to DOG program",
          (cid[("ANIMALS.cbl", "MAIN-PARA")], cid[("DOG.cbl", "DOG")],
           "calls", "cobol_inter_file") in edge_set, str(sorted(edge_set)))

    d_edges, _ = resolve_cobol_calls(records[1], ctx)
    d_edge_set = {(e.src_chunk_id, e.dst_chunk_id, e.kind) for e in d_edges}
    check("xref: DOG MAKE-SOUND PERFORM SPEAK -> intra calls edge",
          (cid[("DOG.cbl", "MAKE-SOUND")], cid[("DOG.cbl", "SPEAK")], "calls")
          in d_edge_set, str(sorted(d_edge_set)))

    s_edges, s_unres = resolve_cobol_calls(records[2], ctx)
    reasons = {(u.raw_target, u.reason) for u in s_unres}
    check("xref: CALL identifier (dynamic) -> dynamic_dispatch unresolved",
          ("SOUND-NAME", "dynamic_dispatch") in reasons, str(sorted(reasons)))
    check("xref: no 'language_unsupported' emitted for COBOL",
          all(u.reason != "language_unsupported"
              for u in (a_unres + s_unres)), str(reasons))

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
