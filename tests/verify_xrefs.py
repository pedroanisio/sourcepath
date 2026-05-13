#!/usr/bin/env python3
"""verify_xrefs.py — contract suite for the symbol-xref layer.

Phase 1 — schema, vocab, empty plumbing (no L2 registered, no resolvers fire):
  1. Empty contract: manifest fragment reports zero edges/unresolved,
     xrefs.jsonl exists and is empty.
  2. Vocabulary bound: ``cbmxr:EdgeShape`` in shapes.shacl.ttl; the
     ``cbmxr:`` prefix round-trips when an edge is mutated in.
  3. SHACL conforms with no edges.
  4. Determinism: two consecutive runs are byte-identical.
  5. Aggregator unit: ``XrefAggregator(resolvers={}).run(ctx)`` returns
     the documented empty shape.
  6-9. SHACL mutation suite: a well-formed edge validates; missing
     src / bad kind / bad resolution are flagged.

Phase 2 — Python intra-file ``calls`` resolver (L2 + symbol_xrefs registered):
  10. The expected (src_symbol, dst_symbol, kind, resolution) triples land
      in xrefs.jsonl. Negative cases (Attribute calls, builtins,
      module-level calls, nested-function calls) do not.
  11. Each emitted edge's src/dst chunk_id corresponds to a real
      ``cbml2:Chunk`` in inventory.ttl (reference integrity).
  12. SHACL conforms with real edges + real chunks.
  13. xrefs.jsonl round-trips: each line parses to a dict matching the
      in-graph triples; lines are sorted (deterministic).
  14. Determinism with L2 + resolver: two runs byte-identical.
  15. Pure resolver unit: ``resolve_python_intra_file`` called directly
      on a record + chunks index returns the same edge set.

Phase 3 — persistence contract (TTL + JSON-LD + sidecar + CLI):
  16. Sidecar lines reconstruct as ``SymbolXrefEdge`` dataclasses and
      equal the aggregator's in-memory edge set (the actual downstream
      consumer contract).
  17. JSON-LD round-trip: ``inventory.jsonld`` survives the host's
      canonicalization step and still contains every ``cbmxr:Edge``
      and its src/dst/kind/resolution/resolver predicates.
  18. CLI parity: ``scripts/run_xrefs.py`` over the same fixture
      produces a byte-identical ``xrefs.jsonl`` and the same edge count
      as in-process registration.

Exit code: 0 if all pass, 1 otherwise.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF

from codebase_mapper import emit, map_codebase, reset_registries
from codebase_mapper.constants import CBMI_NS, CBMXR, CBMXR_NS
from codebase_mapper.extensions import PipelineCtx
from codebase_mapper.models import FileRecord
from plugins import chunks_embeddings, symbol_xrefs
from plugins.symbol_xrefs.aggregator import XREF_INDEX_KEY, XrefAggregator
from plugins.symbol_xrefs.python_resolver import (
    RESOLVER_NAME as PYTHON_INTRA_FILE,
    resolve_python_intra_file,
)


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
            for line in detail.splitlines()[:12]:
                print(f"        {line}")
        FAIL += 1


def build_minimal_fixture(target: Path) -> None:
    """Phase 1 fixture: enough to exercise the empty contract."""
    _init_git(target)
    (target / "hello.py").write_text('def hi():\n    return "hi"\n')
    _commit(target)


# Phase 2 fixture — every call pattern Phase 2 must handle (positive + negative).
# Line numbers are not asserted; the test matches edges by symbol name only.
PHASE2_SRC = '''\
def helper():
    return 1


def main():
    helper()  # → edge: main → helper
    helper()  # dedup; same edge


def recursive(n):
    if n > 0:
        recursive(n - 1)  # → edge: recursive → recursive (self-call)


class User:
    def greet(self):
        helper()  # → edge: User.greet → helper

    def chained(self):
        self.greet()  # Attribute call; Phase 2 skips


def top_call():
    print("hi")  # builtin / not in this file; skipped


top_call()  # module-level call; no enclosing chunk; skipped
'''


def build_phase2_fixture(target: Path) -> None:
    _init_git(target)
    (target / "app.py").write_text(PHASE2_SRC)
    _commit(target)


def _init_git(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)


def _commit(target: Path) -> None:
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True,
    )


def run_pipeline(fixture: Path, out_dir: Path, *, with_l2: bool = False) -> dict:
    reset_registries()
    if with_l2:
        chunks_embeddings.register_all(
            chunks_embeddings.DeterministicHashBackend(dimension=64),
        )
    symbol_xrefs.register_all()
    mapped = map_codebase(fixture.resolve(), "HEAD")
    return emit(fixture.name, mapped, out_dir.resolve(), emit_blobs_flag=False)


def _add_chunk(g: Graph, chunk_id: str = "demo#hello.py#hi") -> URIRef:
    """Inject a minimal ``cbml2:Chunk`` so SHACL src/dst-class checks fire."""
    import urllib.parse
    safe = urllib.parse.quote(chunk_id, safe="")
    ciri = URIRef(f"{CBMI_NS}chunk/{safe}")
    g.add((ciri, RDF.type, CBML2.Chunk))
    return ciri


def _symbol_from_chunk_id(chunk_id: str) -> str:
    """Parse the L2 chunk_id ``path#kind:symbol:L<a>-L<b>`` → ``symbol``.

    Method chunks encode the parent as ``parent.symbol``; returned as-is.
    """
    _path, _hash, rest = chunk_id.partition("#")
    _kind, _colon, rest = rest.partition(":")
    symbol, _colon, _lines = rest.rpartition(":")
    return symbol


def _add_edge(g: Graph, *, src: URIRef, dst: URIRef,
              kind: str = "calls", resolution: str = "exact",
              resolver: str = "test", omit: str | None = None) -> URIRef:
    import hashlib
    key = f"{src}|{dst}|{kind}|{resolver}|{omit}"
    eiri = URIRef(f"{CBMI_NS}xref/{hashlib.sha1(key.encode()).hexdigest()[:16]}")
    g.add((eiri, RDF.type, CBMXR.Edge))
    if omit != "src":
        g.add((eiri, CBMXR.src, src))
    if omit != "dst":
        g.add((eiri, CBMXR.dst, dst))
    if omit != "kind":
        g.add((eiri, CBMXR.kind, Literal(kind)))
    if omit != "resolution":
        g.add((eiri, CBMXR.resolution, Literal(resolution)))
    if omit != "resolver":
        g.add((eiri, CBMXR.resolver, Literal(resolver)))
    return eiri


def main(argv: list[str] | None = None) -> int:
    global PASS, FAIL
    p = argparse.ArgumentParser()
    p.add_argument("--keep", action="store_true", help="don't delete the workdir on exit")
    args = p.parse_args(argv)

    work = Path(tempfile.mkdtemp(prefix="verify_xrefs_"))
    try:
        # =====================================================
        # Phase 1: empty plumbing (no L2 registered)
        # =====================================================
        fixture = work / "fixture"
        build_minimal_fixture(fixture)

        out1 = work / "out1"
        out2 = work / "out2"
        manifest1 = run_pipeline(fixture, out1)
        manifest2 = run_pipeline(fixture, out2)

        # --- 1. Empty contract: manifest fragment reports zeros ---
        frag = manifest1.get("extensions", {}).get("l3_50_xrefs_artifact")
        check(
            "manifest fragment present under extensions.l3_50_xrefs_artifact",
            frag is not None,
            detail=json.dumps(manifest1.get("extensions", {}), indent=2),
        )
        if frag is not None:
            check(
                "fragment.n_edges == 0",
                frag.get("n_edges") == 0,
                detail=str(frag),
            )
            check(
                "fragment.n_unresolved == 0",
                frag.get("n_unresolved") == 0,
                detail=str(frag),
            )
            for k in ("by_kind", "by_resolution", "by_language"):
                check(
                    f"fragment.{k} is empty dict",
                    frag.get(k) == {},
                    detail=str(frag),
                )
            check(
                "fragment.files lists xrefs.jsonl with sha256",
                "xrefs.jsonl" in frag.get("files", {})
                and frag["files"]["xrefs.jsonl"].get("size_bytes") == 0
                and len(frag["files"]["xrefs.jsonl"].get("sha256", "")) == 64,
                detail=str(frag.get("files")),
            )

        # --- 1b. Sidecar exists and is empty ---
        sidecar = out1 / "xrefs.jsonl"
        check("xrefs.jsonl written", sidecar.exists())
        check(
            "xrefs.jsonl is empty (0 bytes)",
            sidecar.exists() and sidecar.stat().st_size == 0,
            detail=f"size={sidecar.stat().st_size if sidecar.exists() else 'missing'}",
        )

        # --- 2. Vocabulary registered ---
        # rdflib only emits a namespace binding when some triple uses it,
        # so with no edges `cbmxr:` is correctly absent from inventory.ttl.
        # The contract we want here is: shapes carry the vocabulary, AND
        # adding an edge round-trips through the `cbmxr:` prefix.
        shapes_text = (out1 / "shapes.shacl.ttl").read_text()
        check("EdgeShape declared in shapes.shacl.ttl",
              "EdgeShape" in shapes_text and "cbmxr" in shapes_text)

        inv_with_edge = Graph()
        inv_with_edge.parse(str(out1 / "inventory.ttl"), format="turtle")
        c_a = _add_chunk(inv_with_edge, "demo#a#f")
        c_b = _add_chunk(inv_with_edge, "demo#b#g")
        _add_edge(inv_with_edge, src=c_a, dst=c_b)
        inv_with_edge.bind("cbmxr", CBMXR)
        serialized = inv_with_edge.serialize(format="turtle")
        check(
            "cbmxr namespace binding round-trips when an edge exists",
            "cbmxr:" in serialized,
            detail=serialized[:400],
        )

        # --- 3. SHACL conforms with no edges ---
        check("shacl conforms (empty edges)",
              manifest1["shacl_self_check"]["conforms"],
              detail=manifest1["shacl_self_check"].get("report_excerpt", ""))

        # --- 4. Determinism ---
        for fname in ("inventory.ttl", "shapes.shacl.ttl", "xrefs.jsonl"):
            a = (out1 / fname).read_bytes()
            b = (out2 / fname).read_bytes()
            check(
                f"determinism: {fname} byte-identical across runs",
                a == b,
                detail=f"len {len(a)} vs {len(b)}",
            )

        # --- 5. Aggregator unit: empty resolver dict yields empty shape ---
        ctx = PipelineCtx(
            repo=fixture, commit="HEAD", records=[],
            blob_by_path={}, mode_by_path={}, paths_set=set(),
            read_path=lambda p: b"",
        )
        agg = XrefAggregator(resolvers={})
        result = agg.run(ctx)
        check(
            "XrefAggregator(empty).run() returns documented empty shape",
            result == {"edges": [], "unresolved": [], "by_language": {},
                       "by_kind": {}, "by_resolution": {}},
            detail=json.dumps(result, default=repr),
        )

        # --- 6/7/8/9. SHACL mutation suite ---
        from pyshacl import validate

        shapes_graph = Graph()
        shapes_graph.parse(str(out1 / "shapes.shacl.ttl"), format="turtle")

        def mutated(mutate) -> bool:
            data = Graph()
            data.parse(str(out1 / "inventory.ttl"), format="turtle")
            mutate(data)
            conforms, _, _ = validate(
                data, shacl_graph=shapes_graph, inference="none",
                abort_on_first=False, meta_shacl=False,
            )
            return bool(conforms)

        def case_well_formed(g: Graph) -> None:
            c1 = _add_chunk(g, "demo#a#f")
            c2 = _add_chunk(g, "demo#b#g")
            _add_edge(g, src=c1, dst=c2)

        check("SHACL accepts a well-formed cbmxr:Edge",
              mutated(case_well_formed),
              "should conform")

        def case_missing_src(g: Graph) -> None:
            c1 = _add_chunk(g, "demo#a#f")
            c2 = _add_chunk(g, "demo#b#g")
            _add_edge(g, src=c1, dst=c2, omit="src")

        check("SHACL rejects edge missing cbmxr:src",
              not mutated(case_missing_src),
              "should fail")

        def case_bad_kind(g: Graph) -> None:
            c1 = _add_chunk(g, "demo#a#f")
            c2 = _add_chunk(g, "demo#b#g")
            _add_edge(g, src=c1, dst=c2, kind="totally_made_up")

        check("SHACL rejects edge with kind outside enum",
              not mutated(case_bad_kind),
              "should fail")

        def case_bad_resolution(g: Graph) -> None:
            c1 = _add_chunk(g, "demo#a#f")
            c2 = _add_chunk(g, "demo#b#g")
            _add_edge(g, src=c1, dst=c2, resolution="bogus")

        check("SHACL rejects edge with resolution outside enum",
              not mutated(case_bad_resolution),
              "should fail")

        # =====================================================
        # Phase 2: Python intra-file `calls` resolver
        # =====================================================
        p2_fixture = work / "p2_fixture"
        build_phase2_fixture(p2_fixture)
        p2_out1 = work / "p2_out1"
        p2_out2 = work / "p2_out2"
        p2_manifest = run_pipeline(p2_fixture, p2_out1, with_l2=True)
        run_pipeline(p2_fixture, p2_out2, with_l2=True)

        # --- 10. Edges match the expected (src, dst, kind, resolution) set ---
        sidecar_lines = (p2_out1 / "xrefs.jsonl").read_text().splitlines()
        emitted = [json.loads(line) for line in sidecar_lines]
        actual_triples = {
            (
                _symbol_from_chunk_id(e["src_chunk_id"]),
                _symbol_from_chunk_id(e["dst_chunk_id"]),
                e["kind"],
                e["resolution"],
            )
            for e in emitted
        }
        expected_triples = {
            ("main", "helper", "calls", "exact"),
            ("recursive", "recursive", "calls", "exact"),
            ("User.greet", "helper", "calls", "exact"),
        }
        check(
            "Phase 2: emitted call edges match expected (src, dst, kind, resolution)",
            actual_triples == expected_triples,
            f"expected={sorted(expected_triples)}\nactual={sorted(actual_triples)}",
        )
        check(
            "Phase 2: every edge resolver is python_intra_file",
            all(e["resolver"] == PYTHON_INTRA_FILE for e in emitted),
            f"resolvers={set(e['resolver'] for e in emitted)}",
        )
        check(
            "Phase 2: manifest fragment counts agree with sidecar",
            (
                p2_manifest["extensions"]["l3_50_xrefs_artifact"]["n_edges"]
                == len(expected_triples)
                and p2_manifest["extensions"]["l3_50_xrefs_artifact"]["by_kind"]
                == {"calls": len(expected_triples)}
                and p2_manifest["extensions"]["l3_50_xrefs_artifact"]["by_resolution"]
                == {"exact": len(expected_triples)}
            ),
            json.dumps(p2_manifest["extensions"]["l3_50_xrefs_artifact"], indent=2),
        )
        check(
            "Phase 2: manifest by_language reports python resolved count",
            p2_manifest["extensions"]["l3_50_xrefs_artifact"]["by_language"].get("python", {}).get("resolved")
            == len(expected_triples)
            and p2_manifest["extensions"]["l3_50_xrefs_artifact"]["by_language"].get("python", {}).get("unresolved") == 0,
            str(p2_manifest["extensions"]["l3_50_xrefs_artifact"].get("by_language")),
        )

        # --- 11. Reference integrity: every edge's src/dst exists as cbml2:Chunk ---
        g_p2 = Graph()
        g_p2.parse(str(p2_out1 / "inventory.ttl"), format="turtle")
        chunk_subjects = set(g_p2.subjects(RDF.type, CBML2.Chunk))
        edge_subjects = list(g_p2.subjects(RDF.type, CBMXR.Edge))
        check(
            "Phase 2: cbmxr:Edge count in inventory.ttl matches sidecar",
            len(edge_subjects) == len(emitted),
            f"ttl_edges={len(edge_subjects)} sidecar_edges={len(emitted)}",
        )
        bad_refs = []
        for e_iri in edge_subjects:
            for pred in (CBMXR.src, CBMXR.dst):
                tgt = next(iter(g_p2.objects(e_iri, pred)), None)
                if tgt is None or tgt not in chunk_subjects:
                    bad_refs.append((str(e_iri), str(pred), str(tgt)))
        check(
            "Phase 2: every cbmxr:src/dst resolves to a real cbml2:Chunk",
            not bad_refs,
            f"bad={bad_refs[:3]}",
        )

        # --- 12. SHACL conforms with real edges ---
        check(
            "Phase 2: SHACL conforms with real edges + chunks",
            p2_manifest["shacl_self_check"]["conforms"],
            p2_manifest["shacl_self_check"].get("report_excerpt", ""),
        )

        # --- 13. Sidecar lines are ordered by edge tuple (deterministic) ---
        # The contract is "edges sorted by (src, dst, kind, resolution, resolver)",
        # not "lines sorted lexicographically as strings" (the JSON keys are
        # alphabetical, so dst_chunk_id leads each line — sorting by string
        # would shuffle the intended edge order).
        edge_tuples = [
            (e["src_chunk_id"], e["dst_chunk_id"], e["kind"], e["resolution"], e["resolver"])
            for e in emitted
        ]
        check(
            "Phase 2: xrefs.jsonl edges sorted by (src, dst, kind, resolution, resolver)",
            edge_tuples == sorted(edge_tuples),
            f"first divergence at line {next((i for i, (a, b) in enumerate(zip(edge_tuples, sorted(edge_tuples))) if a != b), -1)}",
        )

        # --- 14. Determinism under L2 + resolver ---
        for fname in ("inventory.ttl", "shapes.shacl.ttl", "xrefs.jsonl"):
            a = (p2_out1 / fname).read_bytes()
            b = (p2_out2 / fname).read_bytes()
            check(
                f"Phase 2 determinism: {fname} byte-identical",
                a == b,
                f"len {len(a)} vs {len(b)}",
            )

        # --- 15. Pure resolver unit: same edges from direct call ---
        # Rebuild the pipeline state in-process and call the resolver directly
        # to prove it doesn't depend on emit / graph_writer.
        reset_registries()
        chunks_embeddings.register_all(
            chunks_embeddings.DeterministicHashBackend(dimension=64),
        )
        symbol_xrefs.register_all()
        from codebase_mapper.pipeline import map_codebase as _map
        mapped = _map(p2_fixture.resolve(), "HEAD")
        unit_ctx = mapped["ctx"]
        py_records = [r for r in mapped["records"] if r.language == "python"]
        unit_edges = []
        for rec in py_records:
            e, u = resolve_python_intra_file(rec, unit_ctx)
            unit_edges.extend(e)
            check(
                f"Phase 2: resolver returned empty unresolved for {rec.path}",
                u == [],
                f"got {u}",
            )
        unit_triples = {
            (
                _symbol_from_chunk_id(e.src_chunk_id),
                _symbol_from_chunk_id(e.dst_chunk_id),
                e.kind,
                e.resolution,
            )
            for e in unit_edges
        }
        check(
            "Phase 2: pure resolver unit produces the same edge set",
            unit_triples == expected_triples,
            f"unit={sorted(unit_triples)}\nexpected={sorted(expected_triples)}",
        )

        # =====================================================
        # Phase 3: persistence contract
        # =====================================================
        from codebase_mapper.models import SymbolXrefEdge

        # --- 16. Sidecar → SymbolXrefEdge round-trip ---
        unit_edge_set = set(unit_edges)
        reconstructed = {
            SymbolXrefEdge(**json.loads(line)) for line in sidecar_lines
        }
        check(
            "Phase 3: xrefs.jsonl reconstructs as SymbolXrefEdge dataclasses "
            "equal to the resolver's in-memory edge set",
            reconstructed == unit_edge_set,
            f"sidecar={len(reconstructed)} unit={len(unit_edge_set)} "
            f"diff_sidecar_only={list(reconstructed - unit_edge_set)[:2]} "
            f"diff_unit_only={list(unit_edge_set - reconstructed)[:2]}",
        )

        # --- 17. JSON-LD round-trip ---
        jsonld_path = p2_out1 / "inventory.jsonld"
        jsonld_text = jsonld_path.read_text()
        # The custom canonicalization in emit_bundle.py expands/compacts —
        # the cleanest check is to re-parse as RDF and confirm the same
        # edge/predicate counts as inventory.ttl.
        g_jsonld = Graph()
        g_jsonld.parse(str(jsonld_path), format="json-ld")
        jsonld_edges = list(g_jsonld.subjects(RDF.type, CBMXR.Edge))
        check(
            "Phase 3: inventory.jsonld carries the same cbmxr:Edge count "
            "as inventory.ttl",
            len(jsonld_edges) == len(edge_subjects),
            f"jsonld={len(jsonld_edges)} ttl={len(edge_subjects)}",
        )
        # And every required predicate is reachable from each edge.
        missing_preds = []
        for e_iri in jsonld_edges:
            for pred in (CBMXR.src, CBMXR.dst, CBMXR.kind,
                         CBMXR.resolution, CBMXR.resolver):
                if not list(g_jsonld.objects(e_iri, pred)):
                    missing_preds.append((str(e_iri), str(pred)))
        check(
            "Phase 3: every cbmxr:Edge in JSON-LD has src/dst/kind/resolution/resolver",
            not missing_preds,
            f"missing={missing_preds[:3]}",
        )

        # --- 18. CLI parity: scripts/run_xrefs.py matches in-process bundle ---
        cli_out = work / "p2_cli"
        repo_root = Path(__file__).resolve().parent.parent
        env = {**os.environ, "PYTHONPATH": str(repo_root)}
        cli = subprocess.run(
            [
                sys.executable, "scripts/run_xrefs.py",
                "--repo", str(p2_fixture),
                "--out", str(cli_out),
                "--backend", "hash",
                "--hash-dim", "64",
                "--no-emit-blobs",
            ],
            env=env, cwd=str(repo_root), capture_output=True, text=True,
        )
        check(
            "Phase 3: scripts/run_xrefs.py exits 0",
            cli.returncode == 0,
            f"stderr_tail={cli.stderr[-600:]}",
        )
        if cli.returncode == 0:
            check(
                "Phase 3: CLI xrefs.jsonl is byte-identical to in-process bundle",
                (cli_out / "xrefs.jsonl").read_bytes()
                == (p2_out1 / "xrefs.jsonl").read_bytes(),
                "byte mismatch",
            )
            cli_manifest = json.loads((cli_out / "run_manifest.json").read_text())
            check(
                "Phase 3: CLI manifest n_edges matches in-process",
                cli_manifest["extensions"]["l3_50_xrefs_artifact"]["n_edges"]
                == p2_manifest["extensions"]["l3_50_xrefs_artifact"]["n_edges"],
                f"cli={cli_manifest['extensions']['l3_50_xrefs_artifact']['n_edges']} "
                f"inproc={p2_manifest['extensions']['l3_50_xrefs_artifact']['n_edges']}",
            )

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
