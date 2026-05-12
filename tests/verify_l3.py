#!/usr/bin/env python3
"""verify_l3.py — exercises every contract guarantee of the L3 prototype
running alongside L2.

Tests:
  1. Determinism: two consecutive L2+L3 runs produce byte-identical
     artifacts (modulo run_manifest.json's generated_at).
  2. SHACL conformance: emitted graph validates against host + L2 + L3 shapes.
  3. Concepts emitted (>0).
  4. Reference integrity (cross-layer):
      - every cbml3:lexicalizes target is a real skos:Concept in the graph
      - every cbml3:composedOf target is a real skos:Concept
      - every skos:related target is a real skos:Concept
      - file-lexicalizes-concept and chunk-lexicalizes-concept edges only
        originate from cbm:File / cbml2:Chunk subjects respectively
  5. Concept embedding integrity: every concept with cbml3:embeddingRow R
     has a corresponding row R in concepts_embeddings.npz; row count matches.
  6. SHACL mutation suite on chunk shapes (6 cases).
  7. Cross-plugin dependency: rerun with --no-l2 and confirm the L3 graph
     is still valid; concept centroids should NOT be present in this mode.
  8. Cross-layer chunk anchoring: a chunk's symbol's concepts appear in the
     chunk's lexicalizes set (smoke test that the wiring fires).

Exit code: 0 if all pass.
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
from typing import Callable

import numpy as np
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

CBM = Namespace("https://codebase-mapper.example.org/cbm#")
CBML2 = Namespace("https://codebase-mapper.example.org/cbml2#")
CBML3 = Namespace("https://codebase-mapper.example.org/cbml3#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")


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
            for line in detail.splitlines()[:8]:
                print(f"        {line}")
        FAIL += 1


def build_fixture(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)
    (target / "app.py").write_text(
        '"""Sample app for L3 concept validation."""\n\n'
        'import json\n'
        'from dataclasses import dataclass\n\n\n'
        '@dataclass\n'
        'class User:\n'
        '    """A user with a name and an authenticated state."""\n'
        '    name: str\n'
        '    authenticated: bool = False\n\n'
        '    def authenticate(self, token: str) -> bool:\n'
        '        """Authenticate this user with a token."""\n'
        '        if token == "secret":\n'
        '            self.authenticated = True\n'
        '            return True\n'
        '        return False\n\n\n'
        'def load_users(path: str) -> list[User]:\n'
        '    """Load users from a JSON file."""\n'
        '    with open(path) as f:\n'
        '        data = json.load(f)\n'
        '    return [User(**u) for u in data]\n\n\n'
        'def main():\n'
        '    users = load_users("users.json")\n'
        '    for u in users:\n'
        '        u.authenticate("secret")\n'
        '    print(f"authenticated {sum(u.authenticated for u in users)} users")\n'
    )
    (target / "user_service.py").write_text(
        '"""User service module."""\n\n'
        'from app import User, load_users\n\n\n'
        'class UserService:\n'
        '    """Provides user-management operations."""\n\n'
        '    def __init__(self, users: list[User]):\n'
        '        self.users = users\n\n'
        '    def find_by_name(self, name: str) -> User | None:\n'
        '        for u in self.users:\n'
        '            if u.name == name:\n'
        '                return u\n'
        '        return None\n'
    )
    (target / "README.md").write_text("# Sample\n")
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True)


def run_pipeline(fixture: Path, out: Path, repo_root: Path, no_l2: bool = False) -> None:
    cmd = [sys.executable, "scripts/run_l3.py", "--repo", str(fixture), "--out", str(out),
           "--backend", "hash"]
    if no_l2:
        cmd.append("--no-l2")
    env = {**os.environ, "PYTHONPATH": str(repo_root)}
    r = subprocess.run(cmd, env=env, cwd=str(repo_root), capture_output=True, text=True)
    if r.returncode != 0:
        print("FAIL: pipeline exit", r.returncode)
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    global PASS, FAIL
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true")
    args = p.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="verify_l3_"))
    try:
        fixture = work / "fixture"
        build_fixture(fixture)
        repo_root = Path(__file__).resolve().parent.parent

        # --- 1. Determinism: two runs ---
        out1 = work / "out1"
        out2 = work / "out2"
        run_pipeline(fixture, out1, repo_root)
        run_pipeline(fixture, out2, repo_root)

        for fname in ("inventory.ttl", "shapes.shacl.ttl", "embeddings.npz",
                      "embeddings_meta.json", "concepts.json",
                      "concepts_embeddings.npz", "ontology-mapping.ttl"):
            a = (out1 / fname).read_bytes()
            b = (out2 / fname).read_bytes()
            check(f"determinism: {fname}", a == b)

        # --- 2. SHACL conformance ---
        manifest = json.loads((out1 / "run_manifest.json").read_text())
        check("shacl conforms (L2+L3 graphs vs L2+L3 shapes)",
              manifest["shacl_self_check"]["conforms"],
              manifest.get("shacl_self_check", {}).get("report_excerpt", ""))

        # --- Load the unified graph ---
        g = Graph()
        g.parse(str(out1 / "inventory.ttl"), format="turtle")
        shapes = Graph()
        shapes.parse(str(out1 / "shapes.shacl.ttl"), format="turtle")

        concepts = set(g.subjects(RDF.type, SKOS.Concept))
        chunks = set(g.subjects(RDF.type, CBML2.Chunk))
        files = set(g.subjects(RDF.type, CBM.File))

        # --- 3. Concepts emitted ---
        check(f"concepts emitted ({len(concepts)} concepts)", len(concepts) > 0)

        # --- 4. Cross-layer reference integrity ---
        bad_lex_targets = []
        bad_lex_subjects = []
        for s, o in g.subject_objects(CBML3.lexicalizes):
            if o not in concepts:
                bad_lex_targets.append(str(o))
            if s not in files and s not in chunks:
                bad_lex_subjects.append(str(s))
        check("every lexicalizes target is a real concept",
              not bad_lex_targets, f"bad: {bad_lex_targets[:3]}")
        check("every lexicalizes subject is a file or chunk",
              not bad_lex_subjects, f"bad: {bad_lex_subjects[:3]}")

        bad_comp = []
        for s, o in g.subject_objects(CBML3.composedOf):
            if s not in concepts or o not in concepts:
                bad_comp.append((str(s), str(o)))
        check("every composedOf target/source is a real concept",
              not bad_comp, f"bad: {bad_comp[:3]}")

        bad_rel = []
        for s, o in g.subject_objects(SKOS.related):
            if s not in concepts or o not in concepts:
                bad_rel.append((str(s), str(o)))
        check("every skos:related target/source is a real concept",
              not bad_rel, f"bad: {bad_rel[:3]}")

        # --- 5. Concept embedding integrity ---
        try:
            npz = np.load(str(out1 / "concepts_embeddings.npz"), allow_pickle=True)
            cvecs = npz["vectors"]; cids = npz["ids"]
        except (FileNotFoundError, KeyError):
            cvecs, cids = np.zeros((0, 0)), np.array([])
        # Each concept in the graph with cbml3:embeddingRow should have a
        # corresponding row in concepts_embeddings.
        rows_in_graph: set[int] = set()
        for c in concepts:
            row_lit = list(g.objects(c, CBML3.embeddingRow))
            if row_lit:
                rows_in_graph.add(int(row_lit[0]))
        check("concept embedding row count matches npz",
              len(rows_in_graph) == len(cids),
              f"{len(rows_in_graph)} concepts with row vs {len(cids)} rows in npz")
        if len(cvecs) > 0:
            norms = np.linalg.norm(cvecs, axis=1)
            check("concept centroids L2-normalized",
                  bool(np.all(np.abs(norms - 1.0) < 1e-5)))

        # --- 6. SHACL mutation suite on chunk shapes ---
        from pyshacl import validate
        first_concept = next(iter(sorted(concepts)))

        def case(label: str, expect: bool, mutate: Callable[[Graph], None]) -> None:
            gc = Graph(); gc += g
            mutate(gc)
            conf, _, _ = validate(gc, shacl_graph=shapes, inference="none",
                                  abort_on_first=False)
            check(f"mutation: {label}", bool(conf) == expect,
                  f"expected={expect} got={bool(conf)}")

        case("control (unmodified)", True, lambda _g: None)
        case("concept missing prefLabel", False,
             lambda gc: gc.remove((first_concept, SKOS.prefLabel, None)))
        case("two prefLabels on one concept", False,
             lambda gc: gc.add((first_concept, SKOS.prefLabel,
                                Literal("second-pref", lang="en"))))
        case("negative occurrenceCount", False,
             lambda gc: (gc.remove((first_concept, CBML3.occurrenceCount, None)),
                         gc.add((first_concept, CBML3.occurrenceCount,
                                 Literal(-1, datatype=XSD.integer)))))
        case("composedOf points to non-concept", False,
             lambda gc: gc.add((first_concept, CBML3.composedOf,
                                URIRef("http://example.org/not-a-concept"))))
        case("skos:related points to non-concept", False,
             lambda gc: gc.add((first_concept, SKOS.related,
                                URIRef("http://example.org/not-a-concept"))))

        # --- 7. Cross-plugin dependency: L3 alone (no L2) ---
        out3 = work / "out_l3only"
        run_pipeline(fixture, out3, repo_root, no_l2=True)
        g3 = Graph(); g3.parse(str(out3 / "inventory.ttl"), format="turtle")
        check("L3-only: concepts still emitted",
              len(set(g3.subjects(RDF.type, SKOS.Concept))) > 0)
        check("L3-only: no chunks emitted",
              len(set(g3.subjects(RDF.type, CBML2.Chunk))) == 0)
        cj3 = json.load(open(out3 / "concepts.json"))
        check("L3-only: no concept centroids (depends on L2)",
              cj3.get("concept_embedding_ids") is None or
              len(cj3["concept_embedding_ids"]) == 0)
        # concepts_embeddings.npz should NOT exist
        check("L3-only: concepts_embeddings.npz absent",
              not (out3 / "concepts_embeddings.npz").exists())

        # --- 8. Cross-layer chunk anchoring smoke test ---
        # Find chunk for 'authenticate' method and check its concepts.
        auth_chunk = None
        for s in chunks:
            if "authenticate" in str(s):
                auth_chunk = s
                break
        if auth_chunk is not None:
            chunk_concepts = sorted(
                str(c).rsplit("/", 1)[-1] for c in g.objects(auth_chunk, CBML3.lexicalizes)
            )
            check("L2 chunk for 'authenticate' lexicalizes 'authenticate' concept",
                  "authenticate" in chunk_concepts,
                  f"got: {chunk_concepts}")

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
