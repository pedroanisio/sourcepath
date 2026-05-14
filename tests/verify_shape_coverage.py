#!/usr/bin/env python3
"""verify_shape_coverage.py — contract suite for drift-risk findings #4 / #5.

Findings:

  #4. Several host CBM predicates are emitted into the inventory graph
      but have no SHACL shape declaring them: a typo or rename of these
      predicates would propagate to every bundle silently. The original
      offenders were `cbm:astSummary`, `cbm:commitSha`,
      `cbm:declaresDependency`, `cbm:extractionError`, `cbm:hasFile`,
      `cbm:pinsDependency`.

  #5. The L3 concept plugin emits `cbml3:lexicalizes` (file→concept and
      chunk→concept edges) and `cbml3:embeddingArtifact` (concept→matrix
      filename) into the inventory graph, but neither predicate had a
      corresponding shape in `ConceptShapes`.

This script:
  1. Builds a real bundle from a small fixture (host + chunks + concepts
     so every L1 and L3 surface fires).
  2. Walks `inventory.ttl` and collects every distinct predicate IRI
     under `CBM_NS` and `CBML3_NS`.
  3. Walks `shapes.shacl.ttl` and collects every predicate that appears
     under `sh:path` (i.e. shape-covered).
  4. Asserts that `emitted - INTENTIONALLY_UNSHAPED ⊆ shape-covered`.
  5. Asserts that SHACL conforms on the live bundle (sanity).

Adding a new emitted predicate without a corresponding shape will fail
this verifier. If a predicate is *legitimately* outside SHACL coverage
(e.g. provisional or non-load-bearing), add it to `INTENTIONALLY_UNSHAPED`
below with a comment explaining why.

Exit code: 0 if all pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from codebase_mapper.emission.application.emit_bundle import emit
from codebase_mapper.inspection.pipeline import map_codebase
from codebase_mapper.shared_kernel.extensions import reset_registries
from codebase_mapper.shared_kernel.constants import CBM_NS
from plugins import chunks_embeddings, concept_graph


CBML3_NS = "https://codebase-mapper.example.org/cbml3#"
SH = URIRef("http://www.w3.org/ns/shacl#")
SH_PATH = URIRef("http://www.w3.org/ns/shacl#path")

# Predicates that are deliberately not under SHACL coverage. Empty by
# default; adding a key here is a conscious decision that needs a comment.
INTENTIONALLY_UNSHAPED: dict[str, str] = {
    # Example (placeholder; remove or fill as needed):
    # f"{CBM_NS}provisional": "experimental field; promoted to shape in vX.Y",
}


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
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(
        ["git", "-C", str(target), "config", "user.email", "t@t"], check=True,
    )
    subprocess.run(
        ["git", "-C", str(target), "config", "user.name", "t"], check=True,
    )


def _commit(target: Path) -> None:
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True,
    )


# Fixture: enough surface to exercise the predicates we care about.
#   - app.py: a Python source file (cbm:File, cbm:astSummary, cbm:imports,
#             possibly cbm:extractionError if the analyzer hiccups)
#   - pyproject.toml: dependency_manifest → cbm:declaresDependency
#   - uv.lock: lockfile → cbm:pinsDependency
#   - lib.py: importable from app, exercises cbm:imports
APP_SRC = """\
from lib import helper


def main():
    return helper()
"""

LIB_SRC = """\
def helper():
    return 1
"""

PYPROJECT_SRC = """\
[project]
name = "fixture"
version = "0.0.1"
dependencies = ["rich>=13.0"]
"""

UV_LOCK_SRC = """\
version = 1
revision = 1
requires-python = ">=3.10"

[[package]]
name = "rich"
version = "13.0.0"
source = { registry = "https://pypi.org/simple" }
"""


def build_fixture(target: Path) -> None:
    _init_git(target)
    (target / "app.py").write_text(APP_SRC)
    (target / "lib.py").write_text(LIB_SRC)
    (target / "pyproject.toml").write_text(PYPROJECT_SRC)
    (target / "uv.lock").write_text(UV_LOCK_SRC)
    _commit(target)


def main(argv: list[str] | None = None) -> int:
    global PASS, FAIL
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true",
                   help="don't delete the workdir on exit")
    args = p.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="verify_shape_coverage_"))
    try:
        fixture = work / "fixture"
        build_fixture(fixture)
        out = work / "out"

        # Run host + L2 + L3 so every shape surface is loaded.
        reset_registries()
        chunks_embeddings.register_all(
            chunks_embeddings.DeterministicHashBackend(dimension=64),
        )
        concept_graph.register_all()
        mapped = map_codebase(fixture.resolve(), "HEAD")
        manifest = emit(
            fixture.name, mapped, out.resolve(), emit_blobs_flag=False,
        )

        # --- 1. SHACL conforms on the live bundle ---
        check(
            "SHACL conforms on live host+L2+L3 bundle",
            manifest["shacl_self_check"]["conforms"],
            manifest["shacl_self_check"].get("report_excerpt", ""),
        )

        # --- 2. Collect emitted CBM_NS + CBML3_NS predicates ---
        inv = Graph()
        inv.parse(str(out / "inventory.ttl"), format="turtle")
        emitted: set[str] = set()
        for _s, p, _o in inv:
            ps = str(p)
            if ps.startswith(CBM_NS) or ps.startswith(CBML3_NS):
                emitted.add(ps)

        # --- 3. Collect shape-covered predicates from shapes.shacl.ttl ---
        shapes = Graph()
        shapes.parse(str(out / "shapes.shacl.ttl"), format="turtle")
        covered: set[str] = set()
        for _s, _p, o in shapes.triples((None, SH_PATH, None)):
            covered.add(str(o))
        # Predicates listed in cbm:targetSubjectsOf are also "covered":
        # a node-shape that targets every subject of a predicate is itself
        # an enforcement of that predicate's range.
        for _s, _p, o in shapes.triples(
            (None, URIRef(str(SH) + "targetSubjectsOf"), None),
        ):
            covered.add(str(o))

        # --- 4. The coverage assertion ---
        unshaped = (
            emitted - covered - set(INTENTIONALLY_UNSHAPED)
        )
        check(
            "every emitted CBM_NS / CBML3_NS predicate has a SHACL shape",
            not unshaped,
            "unshaped predicates (add a shape, or list in "
            "INTENTIONALLY_UNSHAPED with reason):\n"
            + "\n".join(sorted(unshaped)),
        )

        # --- 5. Witness assertions for the specific drift-risk findings ---
        # These guarantee a regression-test would notice the historical
        # offenders coming back as unshaped.
        required_present: set[str] = {
            f"{CBM_NS}astSummary",
            f"{CBM_NS}commitSha",
            f"{CBM_NS}declaresDependency",
            f"{CBM_NS}extractionError",
            f"{CBM_NS}hasFile",
            f"{CBM_NS}pinsDependency",
            f"{CBML3_NS}lexicalizes",
            f"{CBML3_NS}embeddingArtifact",
        }
        for pred in sorted(required_present):
            # Some predicates are emitted only when relevant rows exist
            # (e.g. embeddingArtifact requires a computed row). Skip the
            # presence check when the predicate is genuinely absent in
            # this fixture; the coverage assertion above still catches a
            # regression that emits but doesn't shape.
            if pred in emitted:
                check(
                    f"drift-risk witness: {pred} is shape-covered",
                    pred in covered,
                    f"emitted={pred in emitted} covered={pred in covered}",
                )

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
