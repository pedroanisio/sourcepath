#!/usr/bin/env python3
"""verify_golden_repo.py — golden end-to-end contract for the extraction pipeline.

Every other verifier checks the pipeline against itself (SHACL conformance,
shape coverage, roundtrips) — internal consistency, not extraction
correctness: a systematically wrong import parser would still produce a
clean, conforming, roundtrippable bundle. This test breaks that
self-referential ceiling.

``tests/fixtures/golden_repo`` is a tiny polyglot repository (Python
package, TypeScript module pair, Go file, markdown, a binary blob, a
dependency manifest). ``tests/fixtures/golden_repo_expected.json`` is the
HAND-WRITTEN expected bundle projection — authored by reading the fixture
sources, never by running the pipeline. The test runs the real host + L2
pipeline over the fixture and diffs the emitted bundle against the human
ground truth.

Tests:
  1. The pipeline runs end-to-end over the fixture and SHACL conforms.
  2. File inventory matches by hand-derived (path, language, type) — exact
     set equality, so both missing and phantom files fail.
  3. Internal import edges match exactly (Python absolute import resolved
     through a package; TS relative import) — the Tier-1 witness that the
     import graph is real, not just well-formed.
  4. Declared external dependencies match.
  5. Chunk inventory matches by (file, symbol, kind, begin/end line) — the
     symbol extractor is checked against human-read line numbers.
  6. Comparator self-test: a tampered projection MUST be reported as a
     mismatch (guards against a vacuously-green comparator).

If you change the fixture repo, re-derive the expected file BY HAND.

Exit code: 0 if all pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF

from codebase_mapper.emission.application.emit_bundle import emit
from codebase_mapper.inspection.pipeline import map_codebase
from codebase_mapper.shared_kernel.constants import CBM_NS
from codebase_mapper.shared_kernel.extensions import reset_registries
from plugins import chunks_embeddings

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "golden_repo"
EXPECTED = REPO_ROOT / "tests" / "fixtures" / "golden_repo_expected.json"

CBM = Namespace(CBM_NS)
CBML2 = Namespace("https://codebase-mapper.example.org/cbml2#")

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
            for line in detail.splitlines()[:40]:
                print(f"        {line}")
        FAIL += 1


def build_fixture_clone(work: Path) -> Path:
    """Copy the checked-in fixture into a temp git repo (HEAD required)."""
    target = work / "golden_repo"
    shutil.copytree(FIXTURE, target)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"],
                   check=True)
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q", "-m", "init"],
                   check=True)
    return target


def project_bundle(out_dir: Path, conforms: bool) -> dict:
    """Reduce inventory.ttl to the hand-checkable golden projection.

    Volatile facts (timestamps, shas, sizes, embedding rows) are deliberately
    excluded — the golden locks WHAT was extracted, not WHEN or into which
    row it landed.
    """
    g = Graph()
    g.parse(str(out_dir / "inventory.ttl"), format="turtle")

    uri_to_path: dict[URIRef, str] = {}
    files: dict[str, dict] = {}
    for f in g.subjects(RDF.type, CBM.File):
        path = str(next(g.objects(f, CBM.path)))
        uri_to_path[f] = path
        lang = next(g.objects(f, CBM.language), None)
        ftype = next(g.objects(f, CBM.type), None)
        files[path] = {
            "language": str(lang) if lang is not None else None,
            "type": str(ftype).rsplit("#", 1)[-1].split("/")[-1]
                    if ftype is not None else None,
        }

    edges = sorted(
        [uri_to_path[s], uri_to_path[o]]
        for s, o in g.subject_objects(CBM.imports)
        if s in uri_to_path and o in uri_to_path
    )

    deps: dict[str, list[str]] = {}
    for s, o in g.subject_objects(CBM.declaresDependency):
        name = next(g.objects(o, CBM.packageName), None)
        if s in uri_to_path and name is not None:
            deps.setdefault(uri_to_path[s], []).append(str(name))
    for k in deps:
        deps[k] = sorted(deps[k])

    chunks: dict[str, list[dict]] = {}
    for c in g.subjects(RDF.type, CBML2.Chunk):
        in_file = next(g.objects(c, CBML2.inFile), None)
        if in_file not in uri_to_path:
            continue
        chunks.setdefault(uri_to_path[in_file], []).append({
            "symbol": str(next(g.objects(c, CBML2.symbol))),
            "kind": str(next(g.objects(c, CBML2.kind))),
            "begin_line": int(next(g.objects(c, CBML2.beginLine)).toPython()),
            "end_line": int(next(g.objects(c, CBML2.endLine)).toPython()),
        })
    for k in chunks:
        chunks[k] = sorted(chunks[k], key=lambda r: (r["symbol"], r["begin_line"]))

    return {
        "files": files,
        "import_edges": edges,
        "declared_dependencies": deps,
        "chunks": chunks,
        "shacl_conforms": conforms,
    }


def diff_sections(expected: dict, actual: dict) -> dict[str, str]:
    """Per-section mismatch report; empty dict == golden match."""
    out: dict[str, str] = {}
    for section in ("files", "import_edges", "declared_dependencies",
                    "chunks", "shacl_conforms"):
        exp, act = expected[section], actual.get(section)
        if exp != act:
            out[section] = (
                f"expected: {json.dumps(exp, indent=2, sort_keys=True)}\n"
                f"actual:   {json.dumps(act, indent=2, sort_keys=True)}"
            )
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true",
                   help="don't delete the workdir on exit")
    args = p.parse_args(argv)

    expected = {
        k: v for k, v in json.loads(EXPECTED.read_text()).items()
        if not k.startswith("_")
    }
    # Normalize the expected chunk ordering to the projection's sort key so
    # the golden file may be written in any human-friendly order.
    for k in expected["chunks"]:
        expected["chunks"][k] = sorted(
            expected["chunks"][k], key=lambda r: (r["symbol"], r["begin_line"]),
        )
    expected["import_edges"] = sorted(expected["import_edges"])

    work = Path(tempfile.mkdtemp(prefix="verify_golden_repo_"))
    try:
        repo = build_fixture_clone(work)
        out = work / "out"

        reset_registries()
        chunks_embeddings.register_all(
            chunks_embeddings.DeterministicHashBackend(dimension=64),
        )
        mapped = map_codebase(repo.resolve(), "HEAD")
        manifest = emit(repo.name, mapped, out.resolve(), emit_blobs_flag=False)

        conforms = bool(manifest["shacl_self_check"]["conforms"])
        check("pipeline runs end-to-end and SHACL conforms", conforms,
              manifest["shacl_self_check"].get("report_excerpt", ""))

        actual = project_bundle(out, conforms)
        mismatches = diff_sections(expected, actual)
        for section in ("files", "import_edges", "declared_dependencies",
                        "chunks", "shacl_conforms"):
            check(
                f"golden match: {section}",
                section not in mismatches,
                mismatches.get(section, ""),
            )

        # Comparator self-test: drop one import edge from the actual
        # projection; the comparator MUST notice, or every check above is
        # potentially vacuous.
        tampered = json.loads(json.dumps(actual))
        if tampered["import_edges"]:
            tampered["import_edges"] = tampered["import_edges"][1:]
        check(
            "comparator self-test: tampered projection is rejected",
            "import_edges" in diff_sections(expected, tampered),
        )

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
