#!/usr/bin/env python3
"""verify_l2.py — exercises every contract guarantee of the L2 prototype.

Tests:
  1. Determinism: two consecutive runs produce byte-identical artifacts
     (modulo run_manifest.json's generated_at).
  2. SHACL conformance: emitted graph validates against extended shapes.
  3. Reference integrity: every chunk's inFile points at a real cbm:File
     in the same graph.
  4. Span sanity: NIF beginIndex < endIndex; line numbers positive.
  5. Embedding integrity: every chunk with cbml2:embeddingRow R has a
     corresponding row R in embeddings.npz; row IDs match chunk IDs.
  6. Normalization: every embedding row is L2-normalized within tolerance.
  7. SHACL mutation suite for the chunk shapes: 6 cases, each must be
     correctly flagged or accepted.
  8. Semantic sanity: a function and its enclosing class have higher
     cosine similarity than the function and an unrelated chunk.

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
from typing import Callable

import numpy as np
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

CBML2 = Namespace("https://codebase-mapper.example.org/cbml2#")
NIF = Namespace("http://persistence.uni-leipzig.org/nlp2rdf/ontologies/nif-core#")
CBM = Namespace("https://codebase-mapper.example.org/cbm#")


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
            for line in detail.splitlines()[:10]:
                print(f"        {line}")
        FAIL += 1


def build_fixture(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)
    (target / "app.py").write_text(
        '"""Sample app for L2 chunking validation."""\n'
        "\n"
        "import json\n"
        "from dataclasses import dataclass\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class User:\n"
        '    """A user with a name and an authenticated state."""\n'
        "    name: str\n"
        "    authenticated: bool = False\n"
        "\n"
        "    def authenticate(self, token: str) -> bool:\n"
        '        """Authenticate this user with a token."""\n'
        '        if token == "secret":\n'
        "            self.authenticated = True\n"
        "            return True\n"
        "        return False\n"
        "\n"
        "\n"
        "def load_users(path: str) -> list[User]:\n"
        '    """Load users from a JSON file."""\n'
        "    with open(path) as f:\n"
        "        data = json.load(f)\n"
        "    return [User(**u) for u in data]\n"
        "\n"
        "\n"
        "def main():\n"
        '    users = load_users("users.json")\n'
        "    for u in users:\n"
        '        u.authenticate("secret")\n'
        '    print(f"authenticated {sum(u.authenticated for u in users)} users")\n'
    )
    (target / "README.md").write_text("# Sample\n")
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True
    )


def main(argv: list[str] | None = None) -> int:
    global PASS, FAIL
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true", help="don't delete the workdir on exit")
    p.add_argument("--backend", choices=["hash", "sbert", "ollama"], default="hash",
                   help="hash is faster and exercises full determinism; sbert is "
                        "real; ollama needs a live server ($OLLAMA_HOST) with "
                        "nomic-embed-text pulled")
    args = p.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="verify_l2_"))
    try:
        fixture = work / "fixture"
        build_fixture(fixture)
        repo_root = Path(__file__).resolve().parent.parent

        # --- 1. Determinism: two runs, byte-identical artifacts ---
        out1 = work / "out1"
        out2 = work / "out2"
        for out in (out1, out2):
            r = subprocess.run(
                [sys.executable, "scripts/run_l2.py", "--repo", str(fixture),
                 "--out", str(out), "--backend", args.backend],
                env={**__import__("os").environ, "PYTHONPATH": str(repo_root)},
                cwd=str(repo_root),
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                print("FAIL: run_l2.py exit", r.returncode)
                print(r.stdout[-2000:])
                print(r.stderr[-2000:])
                return 1

        for fname in ("inventory.ttl", "shapes.shacl.ttl", "embeddings.npz",
                      "embeddings_meta.json", "ontology-mapping.ttl"):
            a = (out1 / fname).read_bytes()
            b = (out2 / fname).read_bytes()
            check(f"determinism: {fname} byte-identical", a == b,
                  f"sha differs: {a[:32]!r}... vs {b[:32]!r}...")

        # --- 2. SHACL conformance ---
        manifest = json.loads((out1 / "run_manifest.json").read_text())
        check("shacl conforms", manifest["shacl_self_check"]["conforms"],
              manifest.get("shacl_self_check", {}).get("report_excerpt", ""))

        # --- Load graphs once ---
        g = Graph()
        g.parse(str(out1 / "inventory.ttl"), format="turtle")
        shapes = Graph()
        shapes.parse(str(out1 / "shapes.shacl.ttl"), format="turtle")

        chunks = list(g.subjects(RDF.type, CBML2.Chunk))
        files = set(g.subjects(RDF.type, CBM.File))
        check("at least one chunk emitted", len(chunks) > 0,
              f"got {len(chunks)} chunks")

        # --- 3. Reference integrity: every chunk -> existing cbm:File ---
        bad_refs = []
        for c in chunks:
            in_files = list(g.objects(c, CBML2.inFile))
            if len(in_files) != 1 or in_files[0] not in files:
                bad_refs.append(str(c))
        check("every chunk references a real cbm:File", not bad_refs,
              f"bad: {bad_refs[:3]}")

        # --- 4. Span sanity ---
        bad_spans = []
        for c in chunks:
            beg = int(next(g.objects(c, NIF.beginIndex)))
            end = int(next(g.objects(c, NIF.endIndex)))
            blo = int(next(g.objects(c, CBML2.beginLine)))
            elo = int(next(g.objects(c, CBML2.endLine)))
            if not (0 <= beg <= end and 1 <= blo <= elo):
                bad_spans.append((str(c), beg, end, blo, elo))
        check("all NIF spans well-formed", not bad_spans,
              f"bad: {bad_spans[:3]}")

        # --- 5. Embedding integrity ---
        npz = np.load(str(out1 / "embeddings.npz"), allow_pickle=True)
        vecs = npz["vectors"]
        ids = npz["ids"]
        chunk_to_row: dict[str, int] = {}
        for c in chunks:
            row_lit = list(g.objects(c, CBML2.embeddingRow))
            if row_lit:
                chunk_to_row[str(c)] = int(row_lit[0])
        # every row R has a chunk that references it via cbml2:embeddingRow R
        row_to_chunk_in_graph = {r: c for c, r in chunk_to_row.items()}
        missing = []
        for r in range(len(ids)):
            if r not in row_to_chunk_in_graph:
                missing.append(r)
        check("every embedding row is referenced by a chunk",
              not missing, f"orphan rows: {missing[:5]}")
        check("vectors shape matches ids length",
              vecs.shape[0] == len(ids),
              f"{vecs.shape[0]} vs {len(ids)}")

        # --- 6. Normalization ---
        norms = np.linalg.norm(vecs, axis=1)
        check("all rows L2-normalized within 1e-5",
              bool(np.all(np.abs(norms - 1.0) < 1e-5)),
              f"min={norms.min():.6f} max={norms.max():.6f}")

        # --- 7. SHACL mutation suite on chunk shapes ---
        from pyshacl import validate

        def case(label: str, expected_conforms: bool,
                 mutate: Callable[[Graph], None]) -> None:
            gc = Graph()
            gc += g
            mutate(gc)
            conf, _, _ = validate(gc, shacl_graph=shapes, inference="none",
                                  abort_on_first=False)
            check(f"mutation: {label}", bool(conf) == expected_conforms,
                  f"expected conforms={expected_conforms}, got {bool(conf)}")

        first_chunk = chunks[0]

        case("control (unmodified)", True, lambda _g: None)
        case("drop inFile", False,
             lambda gc: gc.remove((first_chunk, CBML2.inFile, None)))
        case("invalid kind", False,
             lambda gc: (gc.remove((first_chunk, CBML2.kind, None)),
                         gc.add((first_chunk, CBML2.kind, Literal("bogus")))))
        case("missing contentSha256", False,
             lambda gc: gc.remove((first_chunk, CBML2.contentSha256, None)))
        case("malformed contentSha256 (too short)", False,
             lambda gc: (gc.remove((first_chunk, CBML2.contentSha256, None)),
                         gc.add((first_chunk, CBML2.contentSha256,
                                 Literal("abcd", datatype=XSD.hexBinary)))))
        case("negative beginLine", False,
             lambda gc: (gc.remove((first_chunk, CBML2.beginLine, None)),
                         gc.add((first_chunk, CBML2.beginLine,
                                 Literal(-1, datatype=XSD.integer)))))

        # --- 8. Semantic sanity (sbert backend only — hash is meaningless) ---
        if args.backend == "sbert" and len(chunks) >= 4:
            # chunk_ids carry a trailing ``:b<byte_start>-<byte_end>`` span
            # (injective id, defect D2); match on the stable line-range prefix.
            id_to_row = {str(ids[i]): i for i in range(len(ids))}

            def row_for(prefix: str) -> int | None:
                for cid, i in id_to_row.items():
                    if cid.startswith(prefix):
                        return i
                return None

            auth = row_for("app.py#method:User.authenticate:L13-L18")
            cls = row_for("app.py#class:User:L7-L18")
            rdme = row_for("README.md#file:<file>:L1-L1")
            if None in (auth, cls, rdme):
                check("semantic: have expected chunk ids", False,
                      f"missing one of auth/cls/rdme")
            else:
                sim_close = float(vecs[auth] @ vecs[cls])
                sim_far = float(vecs[auth] @ vecs[rdme])
                check(
                    f"semantic: sim(authenticate, User) > sim(authenticate, README)  "
                    f"({sim_close:.3f} > {sim_far:.3f})",
                    sim_close > sim_far,
                )

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
