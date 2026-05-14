#!/usr/bin/env python3
"""verify_llm_enrich_offline.py — Step 6 offline degradation.

The plan's architectural commitment #7: "Failure mode is degradation,
not breakage. Ollama unreachable → log + skip → no cbml4:* triples
emitted → SHACL stays green. The bundle is still useful; it just
lacks the enrichment layer."

This verifier enforces that promise. Unlike the other L4 verifiers,
this one *always runs* (no Ollama skip) — pointing the client at an
unreachable port is itself a test of the degradation path.

What's checked:

  1. The pipeline exits successfully (exit 0) even with all scopes
     opted in.
  2. No cbml4:fileSummary / conceptDescription / schemaPurpose
     triples appear in inventory.ttl.
  3. No enrichments.jsonl is written on disk.
  4. SHACL self-check reports conforms=True.
  5. The L4 manifest fragment is still present, reporting zero work.
  6. The L4 SHACL shapes (LlmFileShape + LlmConceptShape) are still
     declared — the shapes are "fields are optional" and remain
     valid on an empty graph. Removing them would break consumers
     who SPARQL-query the shape file to discover the L4 contract.
  7. The non-L4 artifacts (inventory data triples, embeddings,
     concepts) are byte-identical to a no-L4-plugin run — the
     pipeline rolls back to "indistinguishable from L1+L2+L3 alone".
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF

from codebase_mapper.shared_kernel.constants import CBM, CBML4, CBML4_NS
from plugins.llm_enrich import register_all
from plugins.llm_enrich.cache import Cache
from plugins.llm_enrich.client import OllamaClient


PASS = 0
FAIL = 0


# Port 11435: Ollama listens on 11434 by default; 11435 should have
# nothing listening. Using an explicit unreachable host means this
# verifier doesn't depend on Ollama being absent — it tests the
# degradation path regardless of whether the real server is up.
UNREACHABLE_HOST = "http://127.0.0.1:11435"


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
    (target / "auth.py").write_text(
        '"""Token-based auth."""\n'
        'class Authenticator:\n'
        '    def __init__(self, tokens): self.tokens = set(tokens)\n'
        '    def check(self, t): return t in self.tokens\n'
    )
    (target / "service.py").write_text(
        '"""Login handlers."""\n'
        'def handle_login(creds): return {"ok": True}\n'
    )
    schemas = target / "static" / "schemas"
    schemas.mkdir(parents=True)
    (schemas / "event.xsd").write_text(
        '<?xml version="1.0"?>\n'
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">\n'
        '  <xs:element name="event"/>\n'
        '</xs:schema>\n'
    )
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q",
                    "-m", "init"], check=True)


def _emit_bundle(
    fixture: Path, out: Path, cache_dir: Path,
    *, with_l4: bool, scopes: tuple[str, ...] = (),
    client_host: str | None = None,
) -> tuple[dict, dict]:
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries
    from codebase_mapper.inspection.repo_source import resolve_repo_source
    from plugins import chunks_embeddings, concept_graph

    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(256))
    concept_graph.register_all()
    if with_l4:
        client = (OllamaClient(host=client_host)
                  if client_host else OllamaClient())
        register_all(client=client, cache=Cache(cache_dir=cache_dir),
                     scopes=scopes)
    with resolve_repo_source(str(fixture), "HEAD") as repo:
        mapped = map_codebase(repo.path, repo.state)
        manifest = emit("fixture", mapped, out.resolve(),
                        emit_blobs_flag=False)
    return mapped, manifest


def main() -> int:
    global FAIL
    work = Path(tempfile.mkdtemp(prefix="verify_step6_off_"))
    try:
        fixture = work / "fixture"
        build_fixture(fixture)

        # Run with L4 registered + scopes opted in + unreachable Ollama.
        # If degradation works correctly, the pipeline returns success
        # and the bundle is L4-free.
        out_off = work / "out_offline"
        cache_off = work / "cache_off"
        try:
            mapped, manifest = _emit_bundle(
                fixture, out_off, cache_off,
                with_l4=True,
                scopes=("files", "concepts", "schemas"),
                client_host=UNREACHABLE_HOST,
            )
        except Exception as e:
            check("pipeline completes with unreachable Ollama",
                  False,
                  f"raised: {type(e).__name__}: {e}")
            print(f"\npassed: {PASS}   failed: {FAIL}")
            return 1
        check("pipeline completes with unreachable Ollama", True)

        # --- 2. No cbml4:* data triples in inventory.ttl ---
        g = Graph()
        g.parse(str(out_off / "inventory.ttl"), format="turtle")
        for predicate, label in (
            (CBML4.fileSummary, "fileSummary"),
            (CBML4.conceptDescription, "conceptDescription"),
            (CBML4.schemaPurpose, "schemaPurpose"),
        ):
            n = sum(1 for _ in g.triples((None, predicate, None)))
            check(f"no cbml4:{label} triples", n == 0, f"got {n}")

        # --- 3. No sidecar on disk ---
        check("no enrichments.jsonl written",
              not (out_off / "enrichments.jsonl").exists())

        # --- 4. SHACL still conforms ---
        conforms = (manifest.get("shacl_self_check") or {}).get("conforms")
        excerpt = (manifest.get("shacl_self_check") or {}
                   ).get("report_excerpt", "")
        check("shacl_self_check.conforms is True on offline run",
              bool(conforms), excerpt[:200])

        # --- 5. L4 manifest fragment is present, reports zero work ---
        ext = (manifest.get("extensions") or {}).get("l4_50_artifact")
        check("L4 manifest fragment present", isinstance(ext, dict))
        if isinstance(ext, dict):
            check("L4 fragment: n_enrichments == 0",
                  ext.get("n_enrichments") == 0,
                  f"got {ext.get('n_enrichments')}")
            check("L4 fragment: by_kind is empty",
                  ext.get("by_kind") == {},
                  f"got {ext.get('by_kind')}")
            check("L4 fragment: files is empty",
                  ext.get("files") == {},
                  f"got {ext.get('files')}")

        # --- 6. SHACL shapes still declare the L4 contract ---
        shapes = Graph()
        shapes.parse(str(out_off / "shapes.shacl.ttl"), format="turtle")
        SH = Namespace("http://www.w3.org/ns/shacl#")
        SKOS_CONCEPT = URIRef("http://www.w3.org/2004/02/skos/core#Concept")

        file_shape = URIRef(f"{CBML4_NS}LlmFileShape")
        concept_shape = URIRef(f"{CBML4_NS}LlmConceptShape")
        check(
            "LlmFileShape still declared on offline run",
            (file_shape, RDF.type, SH.NodeShape) in shapes,
        )
        check(
            "LlmFileShape still targets cbm:File",
            (file_shape, SH.targetClass, CBM.File) in shapes,
        )
        check(
            "LlmConceptShape still declared on offline run",
            (concept_shape, RDF.type, SH.NodeShape) in shapes,
        )
        check(
            "LlmConceptShape still targets skos:Concept",
            (concept_shape, SH.targetClass, SKOS_CONCEPT) in shapes,
        )

        # --- 7. Data-graph artifacts match a no-L4 run ---
        # Build a control bundle with no L4 plugin at all; the offline
        # L4 run should produce byte-identical data-graph artifacts.
        out_no = work / "out_no_l4"
        cache_no = work / "cache_no_l4"
        _emit_bundle(fixture, out_no, cache_no, with_l4=False)

        for fname in ("inventory.ttl", "ontology-mapping.ttl",
                      "embeddings.npz", "embeddings_meta.json",
                      "concepts.json", "concepts_embeddings.npz"):
            a = (out_no / fname).read_bytes()
            b = (out_off / fname).read_bytes()
            check(
                f"offline L4 run matches no-L4 control: {fname}",
                a == b,
                f"diverged by {abs(len(a)-len(b))} bytes",
            )

    except Exception:
        FAIL += 1
        print("  FAIL  unexpected exception in main()")
        traceback.print_exc()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\npassed: {PASS}   failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
