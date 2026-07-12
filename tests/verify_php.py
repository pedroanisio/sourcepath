#!/usr/bin/env python3
"""verify_php.py — first-class PHP support invariants.

PHP is first-class per the TIOBE-50 goal ledger: detection + analyzer + import
resolver + symbol chunker + L4 summary gate + this suite. The analyzer is a
single-pass state-machine neutralizer (blanks //, #, /* */ comments, single/
double strings, and heredoc/nowdoc bodies — length-preserving) followed by a
brace-matched declaration scan.

Covered:
  1. Declaration AST: namespace, class / interface / trait / enum, top-level
     functions, and methods (with their parent), including bodyless interface
     methods that end at ';' rather than a block.
  2. The PHP hazards: a '}' inside a comment or a string does not end a body;
     a heredoc containing '}' and 'function ghost() {' produces no phantom
     function and does not corrupt brace matching; and `use Loggable;` INSIDE a
     class body is trait composition, NOT a namespace import — only depth-0
     `use` statements are import edges.
  3. resolve_php_imports: `require`/`include` (incl. the `__DIR__ . '/…'`
     idiom) resolve to files, and `use App\\Models\\User;` resolves through the
     composer.json PSR-4 autoload map.
  4. classify: *.php → source_code.
  5. L2 chunker emits class/method/function chunks.
  6. Pipeline end-to-end: ast_summary, require/include/use edges, chunks.
  7. First-class facets.
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
from codebase_mapper.inspection.languages.php import (
    extract_php_ast_summary,
    parse_composer_psr4,
    resolve_php_imports,
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
FIX = Path("tests/fixtures/php/app")


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        if detail:
            for line in detail.splitlines()[:20]:
                print(f"        {line}")


def _init_git(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)


def _commit(target: Path) -> None:
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True)


def _summary():
    src = (FIX / "src/Service/UserService.php").read_bytes()
    return extract_php_ast_summary(src, "src/Service/UserService.php")


def test_ast_extractor() -> None:
    summary, errors = _summary()
    assert summary is not None
    check("ast: no errors", errors == [], f"errors={errors}")
    check("ast: language=php", summary["language"] == "php")
    check("ast: extraction_method=regex", summary["extraction_method"] == "regex")
    check("ast: namespace captured", summary.get("namespace") == "App\\Service",
          f"namespace={summary.get('namespace')}")

    by_kind: dict[str, set[str]] = {}
    for it in summary["items"]:
        by_kind.setdefault(it["kind"], set()).add(it["name"])
    check("ast: interface Greeter", "Greeter" in by_kind.get("interface", set()), f"{by_kind}")
    check("ast: trait Loggable", "Loggable" in by_kind.get("trait", set()), f"{by_kind}")
    check("ast: enum Status", "Status" in by_kind.get("enum", set()), f"{by_kind}")
    check("ast: class UserService", "UserService" in by_kind.get("class", set()), f"{by_kind}")
    check("ast: top-level function make_service",
          "make_service" in by_kind.get("function", set()), f"{by_kind}")

    methods = {(it["name"], it.get("parent")) for it in summary["items"] if it["kind"] == "method"}
    check("ast: method greet(parent=UserService)", ("greet", "UserService") in methods, f"{methods}")
    check("ast: method log(parent=Loggable)", ("log", "Loggable") in methods, f"{methods}")
    check("ast: bodyless interface method greet(parent=Greeter)",
          ("greet", "Greeter") in methods, f"{methods}")

    # Hazards
    all_names = {it["name"] for it in summary["items"]}
    check("ast: heredoc yields NO phantom function 'ghost'", "ghost" not in all_names,
          f"names={sorted(all_names)}")
    svc = next((it for it in summary["items"]
                if it["kind"] == "class" and it["name"] == "UserService"), None)
    greet = next((it for it in summary["items"]
                  if it["kind"] == "method" and it.get("parent") == "UserService"), None)
    check("ast: UserService.greet span survives its heredoc",
          greet is not None and (greet["line_end"] - greet["line_start"]) >= 5, f"greet={greet}")
    check("ast: class UserService span is well-formed",
          svc is not None and svc["line_end"] > svc["line_start"], f"svc={svc}")
    ok_spans = all(it["line_end"] >= it["line_start"] and it["byte_end"] > it["byte_start"]
                   for it in summary["items"])
    check("ast: spans are non-degenerate", ok_spans)


def test_imports_and_use_depth() -> None:
    summary, _ = _summary()
    srcs = {imp["source"] for imp in summary["imports"]}
    check("imports: depth-0 `use App\\Models\\User` captured",
          "App\\Models\\User" in srcs, f"srcs={srcs}")
    check("imports: trait `use Loggable;` inside a class is NOT an import",
          not any(s.endswith("Loggable") for s in srcs), f"srcs={srcs}")
    check("imports: require __DIR__ . '/../../bootstrap.php' captured",
          any("bootstrap.php" in s for s in srcs), f"srcs={srcs}")
    check("imports: include 'helpers.php' captured",
          any("helpers.php" in s for s in srcs), f"srcs={srcs}")


def test_psr4_and_resolution() -> None:
    psr4 = parse_composer_psr4((FIX / "composer.json").read_bytes())
    check("psr4: composer autoload map parsed", psr4.get("App\\") == "src/", f"psr4={psr4}")

    summary, _ = _summary()
    paths = {"src/Models/User.php", "bootstrap.php",
             "src/Service/helpers.php", "src/Service/UserService.php"}
    in_repo, external = resolve_php_imports(
        "src/Service/UserService.php", summary, paths, psr4)
    check("resolve: use App\\Models\\User -> src/Models/User.php (PSR-4)",
          "src/Models/User.php" in in_repo, f"in_repo={in_repo}")
    check("resolve: require __DIR__ idiom -> bootstrap.php",
          "bootstrap.php" in in_repo, f"in_repo={in_repo}")
    check("resolve: include 'helpers.php' -> src/Service/helpers.php",
          "src/Service/helpers.php" in in_repo, f"in_repo={in_repo}")


def test_classify() -> None:
    check("classify: src/Models/User.php -> source_code",
          classify("src/Models/User.php", b"<?php") == "source_code")


def test_first_class_facets() -> None:
    reset_registries()
    chunks_embeddings.register_all(chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()
    rec = FileRecord(path="x.php", git_blob_sha="0" * 40, content_sha256="0" * 64,
                     size_bytes=1, language="php", type_="source_code", phases=["runtime"])
    rec.ast_summary = {"items": []}
    ctx = PipelineCtx(repo=None, commit="", records=[rec], blob_by_path={},
                      mode_by_path={}, paths_set={rec.path}, read_path=lambda p: b"")
    check("facet: analyzer matches php",
          any(a.matches(rec, ctx) for a in iter_language_analyzers()))
    check("facet: resolver matches php",
          any(r.matches(rec, ctx) for r in iter_import_resolvers()))
    check("facet: php extension-detected", "php" in set(LANG_BY_EXT.values()))
    import re as _re
    src = Path("plugins/chunks_embeddings/chunker.py").read_text()
    check("facet: L2 chunker dispatches on php",
          "php" in set(_re.findall(r'record\.language\s*==\s*"([a-z-]+)"', src)))
    from plugins.llm_enrich.enricher import SUPPORTED_LANGUAGES
    check("facet: php is L4-supported", "php" in SUPPORTED_LANGUAGES)


def test_pipeline_end_to_end(repo: Path) -> None:
    reset_registries()
    chunks_embeddings.register_all(chunks_embeddings.DeterministicHashBackend(dimension=64))
    symbol_xrefs.register_all()
    mapped = map_codebase(repo.resolve(), "HEAD")

    php = [r for r in mapped["records"] if r.language == "php"]
    missing = [r.path for r in php if r.ast_summary is None]
    check("pipeline: every .php has ast_summary", not missing, f"missing={missing}")

    edges = {(e.src_path, e.dst_path) for e in mapped["import_edges"]}
    svc = "src/Service/UserService.php"
    check("pipeline: use App\\Models\\User -> src/Models/User.php (PSR-4 via composer.json)",
          (svc, "src/Models/User.php") in edges, f"edges={edges}")
    check("pipeline: require -> bootstrap.php", (svc, "bootstrap.php") in edges, f"edges={edges}")
    check("pipeline: include -> src/Service/helpers.php",
          (svc, "src/Service/helpers.php") in edges, f"edges={edges}")

    chunks = mapped["ctx"].indices.get("l2_10_chunks", [])
    sc = [c for c in chunks if c["path"] == svc]
    kinds = {c["kind"] for c in sc}
    symbols = {c["symbol"] for c in sc}
    check("pipeline: multiple per-declaration chunks", len(sc) >= 5, f"n={len(sc)}")
    check("pipeline: chunk kinds include class + method + function",
          kinds >= {"class", "method", "function"}, f"kinds={kinds}")
    check("pipeline: UserService + make_service chunked",
          {"UserService", "make_service"} <= symbols, f"symbols={symbols}")
    check("pipeline: chunk kinds are the allowed set",
          kinds <= {"class", "function", "method", "file"}, f"kinds={kinds}")


def test_bases_extraction() -> None:
    """BL-037 — PHP was the only class-bearing language emitting no inheritance."""
    print("\n-- inheritance: extends / implements (BL-037) --")
    path = "src/Models/Admin.php"
    summary, errors = extract_php_ast_summary((FIX / path).read_bytes(), path)
    check("bases: Admin.php extracts", summary is not None, f"errors={errors}")
    if summary is None:
        return
    by = {i["name"]: i for i in summary["items"] if i["kind"] in
          ("class", "interface", "trait", "enum")}

    check("bases: class captures extends AND implements, in order",
          by["Admin"].get("bases") == ["User", "Identifiable", "App\\Contracts\\Auditable"],
          f"bases={by['Admin'].get('bases')}")
    check("bases: interface extends several parents",
          by["Identifiable"].get("bases") == ["Countable", "Stringable"],
          f"bases={by['Identifiable'].get('bases')}")
    check("bases: backed enum keeps the interface and drops the ': string' backing type",
          by["Status"].get("bases") == ["Identifiable"],
          f"bases={by['Status'].get('bases')}")
    check("bases: a trait has no bases key",
          "bases" not in by["Timestamped"], f"item={by['Timestamped']}")
    check("bases: a class with no clause has no bases key (peer contract)",
          "bases" not in by["Bare"], f"item={by['Bare']}")

    # Hazards: neutralization must hold for the header and the bodies.
    flat = [b for i in by.values() for b in i.get("bases", [])]
    check("bases: a comment inside the header creates no base",
          not {"GhostBase", "GhostFace"} & set(flat), f"bases={flat}")
    check("bases: a string in a body creates no phantom class",
          "Phantom" not in by, f"names={sorted(by)}")
    check("bases: a string in a body creates no base",
          not {"Spectre", "Wraith"} & set(flat), f"bases={flat}")
    check("bases: `use T;` in a class body is composition, not inheritance",
          "Timestamped" not in by["Admin"].get("bases", []),
          f"bases={by['Admin'].get('bases')}")


def test_composer_degradation() -> None:
    """BL-038 — an unreadable composer manifest must disclose, not vanish."""
    print("\n-- composer manifest failure is disclosed (BL-038) --")
    from codebase_mapper.inspection._builtins import _php_psr4_index
    from codebase_mapper.inspection.languages.php import ComposerManifestError

    # Library contract: a corrupt manifest and a manifest with no psr-4 section
    # are different facts and must not collapse to the same return value.
    try:
        parse_composer_psr4(b"{ this is not json ,,, ")
        raised = False
    except ComposerManifestError:
        raised = True
    check("composer: an unparseable manifest raises a typed error", raised)
    check("composer: a valid manifest with no psr-4 section is an empty map, not an error",
          parse_composer_psr4(b'{"name": "acme/app"}') == {})

    def broken(_path: str) -> bytes:
        return b"{ this is not json ,,, "

    rec = FileRecord(path="src/A.php", git_blob_sha="0" * 40, content_sha256="0" * 64,
                     size_bytes=1, language="php", type_="source_code", phases=["runtime"])
    ctx = PipelineCtx(repo=None, commit="", records=[rec], blob_by_path={},
                      mode_by_path={}, paths_set={"composer.json", rec.path},
                      read_path=broken)

    index = _php_psr4_index(ctx)
    check("composer: a malformed manifest yields no PSR-4 prefixes",
          index == {}, f"index={index}")

    degradations = ctx.scratch.get("degradations", [])
    mine = [d for d in degradations if d.get("component") == "php_psr4"]
    check("composer: the failure registers a degradation entry",
          len(mine) == 1, f"degradations={degradations}")
    if mine:
        d = mine[0]
        check("composer: the entry names the offending file",
              d.get("path") == "composer.json", f"entry={d}")
        check("composer: the entry carries a reason and an error excerpt",
              bool(d.get("reason")) and bool(d.get("error")), f"entry={d}")

    # A healthy manifest must stay silent — no false degradations.
    good = (FIX / "composer.json").read_bytes()
    rec2 = FileRecord(path="src/A.php", git_blob_sha="0" * 40, content_sha256="0" * 64,
                      size_bytes=1, language="php", type_="source_code", phases=["runtime"])
    ctx2 = PipelineCtx(repo=None, commit="", records=[rec2], blob_by_path={},
                       mode_by_path={}, paths_set={"composer.json", rec2.path},
                       read_path=lambda p: good)
    idx2 = _php_psr4_index(ctx2)
    check("composer: a healthy manifest still resolves prefixes",
          idx2.get("App\\") == "src/", f"index={idx2}")
    check("composer: a healthy manifest registers no degradation",
          not ctx2.scratch.get("degradations"), f"scratch={ctx2.scratch.get('degradations')}")


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--keep", action="store_true"); ap.parse_args()
    print("== PHP first-class verification ==")
    test_ast_extractor(); test_imports_and_use_depth(); test_psr4_and_resolution()
    test_classify(); test_first_class_facets()
    test_bases_extraction(); test_composer_degradation()
    with tempfile.TemporaryDirectory() as td:
        scratch = Path(td) / "app"
        shutil.copytree(FIX.resolve(), scratch)
        _init_git(scratch); _commit(scratch)
        test_pipeline_end_to_end(scratch)
    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
