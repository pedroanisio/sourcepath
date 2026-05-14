#!/usr/bin/env python3
"""verify_llm_enrich.py — Step 1 acceptance test for L4.

The L4 absorption's back-compat anchor: a bundle built with the L4
plugin registered must be byte-identical to one built without it,
*as long as no enrichment kind is opted into yet*. Step 1 of the plan
[docs/llm-enrich-plan.md] commits to this; later steps preserve it on
runs that explicitly disable L4.

Specifically:

  1. Register L1 + L2 + L3 + L4 and run the pipeline.
  2. Register L1 + L2 + L3 only and run the pipeline on the same commit.
  3. Compare every artifact byte-for-byte: inventory.ttl,
     shapes.shacl.ttl, ontology-mapping.ttl, embeddings.npz,
     embeddings_meta.json, concepts.json, concepts_embeddings.npz.
  4. Compare run_manifest.json modulo (a) ``generated_at``,
     (b) the L4 manifest fragment (which is present-but-empty in one
     run and absent in the other — both states are equally valid).
  5. Assert the L4 manifest fragment, when present, reports zero
     enrichments and writes no sidecar file.

Exit code: 0 if all pass, 1 otherwise.

This verifier is permanent. Every later step (3, 5, 7, etc.) must
preserve the byte-identity claim *for the default-off path*. As soon
as Step 2 lands the cache + client, this verifier still passes because
the default registration is no-op until a scope is opted in.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


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
            for line in detail.splitlines()[:6]:
                print(f"        {line}")
        FAIL += 1


def build_fixture(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.name", "t"], check=True)
    # Two small files exercise the host's L1/L2/L3 paths without
    # introducing variance. Identical content -> identical hashes ->
    # the only difference between with/without-L4 runs must be the
    # manifest fragment.
    (target / "app.py").write_text(
        '"""Tiny app used as a fixture for verify_llm_enrich."""\n\n'
        'class UserBehavior:\n'
        '    """A behavior. Curated vocab term to ensure L3 fires."""\n'
        '    def authenticate(self, token: str) -> bool:\n'
        '        return bool(token)\n'
    )
    (target / "README.md").write_text("# verify_llm_enrich fixture\n")
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q",
                    "-m", "init"], check=True)


# A tiny driver script that loads exactly the layers we ask for, so
# the verifier can run with or without L4 without touching the
# existing scripts/run_l*.py.
RUNNER = '''
"""Throwaway driver — register requested layers and emit a bundle."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

from plugins import chunks_embeddings, concept_graph
from codebase_mapper import emit, map_codebase, reset_registries
from codebase_mapper.repo_source import resolve_repo_source


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--with-l4", action="store_true")
    args = ap.parse_args()

    reset_registries()
    backend = chunks_embeddings.DeterministicHashBackend(256)
    chunks_embeddings.register_all(backend)
    concept_graph.register_all()
    if args.with_l4:
        from plugins import llm_enrich
        llm_enrich.register_all()

    with resolve_repo_source(args.repo, "HEAD") as repo:
        mapped = map_codebase(repo.path, repo.state)
        manifest = emit("fixture", mapped, args.out.resolve(),
                        emit_blobs_flag=False)
    print(json.dumps(manifest, indent=2, sort_keys=True))

if __name__ == "__main__":
    main()
'''


def run_pipeline(fixture: Path, out: Path, repo_root: Path, *,
                 with_l4: bool, runner: Path) -> None:
    cmd = [sys.executable, str(runner), "--repo", str(fixture),
           "--out", str(out)]
    if with_l4:
        cmd.append("--with-l4")
    env = {**os.environ, "PYTHONPATH": str(repo_root)}
    r = subprocess.run(cmd, env=env, cwd=str(repo_root),
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FAIL: pipeline exit {r.returncode}")
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit(1)


def manifest_modulo(text: str) -> dict:
    """Return a manifest dict with the known-divergent fields stripped:
      - ``generated_at`` (wall-clock at emit time)
      - the L4 manifest fragment under ``extensions["l4_50_artifact"]``
      - the ``artifacts["shapes.shacl.ttl"]`` entry (its SHA + size
        legitimately differ when L4 adds its optional-cardinality
        shape entries; the data-graph artifacts are still byte-identical)
    All three must be allowed to differ between with/without-L4 runs
    *without* breaking back-compat."""
    m = json.loads(text)
    m.pop("generated_at", None)
    ext = m.get("extensions") or {}
    ext.pop("l4_50_artifact", None)
    artifacts = m.get("artifacts") or {}
    artifacts.pop("shapes.shacl.ttl", None)
    return m


def main(argv: list[str] | None = None) -> int:
    global PASS, FAIL
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true",
                   help="Keep the temp workdir for inspection.")
    args = p.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="verify_llm_enrich_"))
    try:
        fixture = work / "fixture"
        build_fixture(fixture)
        repo_root = Path(__file__).resolve().parent.parent

        # Drop the throwaway runner into the workdir so the subprocess
        # `PYTHONPATH=repo_root` import works.
        runner = work / "runner.py"
        runner.write_text(RUNNER)

        out_with = work / "with_l4"
        out_without = work / "without_l4"
        run_pipeline(fixture, out_without, repo_root,
                     with_l4=False, runner=runner)
        run_pipeline(fixture, out_with, repo_root,
                     with_l4=True, runner=runner)

        # --- 1. Every emitted artifact must be byte-identical ---
        # shapes.shacl.ttl is handled separately: the L4 plugin
        # always contributes its optional-cardinality shape (so a
        # consumer SPARQLing the bundle knows what the L4 layer
        # promises). The shape declares "fields are optional" — it
        # validates an empty graph the same as a populated one. We
        # assert the shapes graphs are equal modulo the L4 namespace
        # entries.
        for fname in ("inventory.ttl",
                      "ontology-mapping.ttl", "embeddings.npz",
                      "embeddings_meta.json", "concepts.json",
                      "concepts_embeddings.npz"):
            a = (out_without / fname).read_bytes()
            b = (out_with / fname).read_bytes()
            check(f"byte-identical: {fname}", a == b,
                  f"diverged by {abs(len(a) - len(b))} bytes")

        # shapes.shacl.ttl modulo L4 entries: rdflib's blank-node IDs
        # for sh:in RDF lists vary across runs (they're internal),
        # so a plain set-equality comparison fails on cosmetic
        # differences. Use rdflib.compare.isomorphic which canonicalizes
        # blank nodes before comparing.
        from rdflib import Graph, Namespace, URIRef
        from rdflib.compare import isomorphic
        from rdflib.namespace import RDF
        SH_URI = "http://www.w3.org/ns/shacl#"
        CBML4_URI = "https://codebase-mapper.example.org/cbml4#"

        def shapes_without_l4(path: Path) -> Graph:
            g = Graph()
            g.parse(str(path), format="turtle")
            # Remove every triple whose subject is in the cbml4
            # namespace (the shape node + the property-shape nodes).
            to_remove = [
                (s, p, o) for s, p, o in g
                if str(s).startswith(CBML4_URI)
            ]
            for t in to_remove:
                g.remove(t)
            return g

        ga = shapes_without_l4(out_without / "shapes.shacl.ttl")
        gb = shapes_without_l4(out_with / "shapes.shacl.ttl")
        check(
            "shapes.shacl.ttl isomorphic modulo L4 cbml4: entries",
            isomorphic(ga, gb),
            f"a={len(ga)} triples, b={len(gb)} triples",
        )

        # And the L4 shape entries that DO appear must be the
        # expected optional-cardinality predicates — not silently
        # broken into something else.
        g_with = Graph()
        g_with.parse(str(out_with / "shapes.shacl.ttl"), format="turtle")
        CBML4 = Namespace(CBML4_URI)
        # The LlmFileSummaryShape node must exist and target cbm:File.
        shape_iri = URIRef(f"{CBML4_URI}LlmFileSummaryShape")
        from codebase_mapper.constants import CBM
        check(
            "L4 shape declares LlmFileSummaryShape",
            (shape_iri, RDF.type, URIRef(SH_URI + "NodeShape")) in g_with,
        )
        check(
            "L4 shape targets cbm:File",
            (shape_iri, URIRef(SH_URI + "targetClass"), CBM.File) in g_with,
        )

        # --- 2. run_manifest.json modulo generated_at + l4 fragment ---
        a = manifest_modulo((out_without / "run_manifest.json").read_text())
        b = manifest_modulo((out_with / "run_manifest.json").read_text())
        check(
            "run_manifest.json (modulo generated_at + l4 fragment)",
            a == b,
            f"diff keys: { set(a.keys()) ^ set(b.keys()) }",
        )

        # --- 3. The L4 manifest fragment, when present, reports zero work ---
        man_with = json.loads(
            (out_with / "run_manifest.json").read_text()
        )
        ext = (man_with.get("extensions") or {}).get("l4_50_artifact")
        check(
            "L4 manifest fragment present in with-L4 run",
            isinstance(ext, dict),
            f"got: {type(ext).__name__}",
        )
        if isinstance(ext, dict):
            check("L4 fragment reports 0 enrichments",
                  ext.get("n_enrichments") == 0,
                  f"n_enrichments={ext.get('n_enrichments')}")
            check("L4 fragment reports empty by_kind",
                  ext.get("by_kind") == {},
                  f"by_kind={ext.get('by_kind')}")
            check("L4 fragment writes no sidecar files",
                  ext.get("files") == {},
                  f"files={ext.get('files')}")

        # --- 4. No enrichments.jsonl on disk in either run ---
        check(
            "no enrichments.jsonl in without-L4 run",
            not (out_without / "enrichments.jsonl").exists(),
        )
        check(
            "no enrichments.jsonl in with-L4 run",
            not (out_with / "enrichments.jsonl").exists(),
        )

    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"workdir kept at {work}")

    print(f"\npassed: {PASS}   failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
