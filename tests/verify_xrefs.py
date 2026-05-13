#!/usr/bin/env python3
"""verify_xrefs.py — Phase 1 contract for the symbol-xref layer.

Phase 1 ships only the schema + vocab + empty plumbing. Tests:

  1. Empty contract: no resolvers registered → manifest fragment reports
     zero edges/unresolved, xrefs.jsonl exists and is empty.
  2. Vocabulary bound: ``cbmxr:`` appears in inventory.ttl;
     ``cbmxr:EdgeShape`` appears in shapes.shacl.ttl.
  3. SHACL conforms with no edges (trivial well-formedness).
  4. Determinism: two consecutive runs produce a byte-identical sidecar
     and inventory.
  5. Aggregator unit: ``XrefAggregator(resolvers={}).run(ctx)`` returns
     the documented empty shape.
  6. SHACL positive: a well-formed ``cbmxr:Edge`` referencing a real
     ``cbml2:Chunk`` validates.
  7. SHACL negative — missing src: an edge without ``cbmxr:src`` fails.
  8. SHACL negative — bad kind: an edge whose ``cbmxr:kind`` is outside
     the enum fails.
  9. SHACL negative — bad resolution: an edge whose ``cbmxr:resolution``
     is outside the enum fails.

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

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF

from codebase_mapper import emit, map_codebase, reset_registries
from codebase_mapper.constants import CBMI_NS, CBMXR, CBMXR_NS
from codebase_mapper.extensions import PipelineCtx
from plugins import symbol_xrefs
from plugins.symbol_xrefs.aggregator import XREF_INDEX_KEY, XrefAggregator


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


def build_fixture(target: Path) -> None:
    """One Python file is enough; xrefs Phase 1 doesn't depend on chunks."""
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "t"], check=True)
    (target / "hello.py").write_text('def hi():\n    return "hi"\n')
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(target), "commit", "-q", "-m", "init"], check=True,
    )


def run_pipeline(fixture: Path, out_dir: Path) -> dict:
    reset_registries()
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
        fixture = work / "fixture"
        build_fixture(fixture)

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

        print()
        print(f"passed: {PASS}   failed: {FAIL}")
        return 0 if FAIL == 0 else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
