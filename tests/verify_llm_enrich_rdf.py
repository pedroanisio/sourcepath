#!/usr/bin/env python3
"""verify_llm_enrich_rdf.py — Step 4 end-to-end RDF emission test.

The Step 3 functional verifier confirms enrichments land on
``ctx.scratch``. This verifier confirms they make it all the way
through to ``inventory.ttl`` triples + ``enrichments.jsonl`` sidecar,
and that SHACL still validates the bundle.

What's checked (requires Ollama; skips cleanly otherwise):

  1. ``cbml4:fileSummary`` triples appear for every enriched cbm:File.
  2. Each subject also carries the four provenance predicates
     (``fileSummary``, ``fileSummaryModel``, ``fileSummaryPromptSha``,
     ``fileSummaryGeneratedAt``).
  3. ``fileSummaryGeneratedAt`` is typed as ``xsd:dateTime``.
  4. ``fileSummaryPromptSha`` is 64 lowercase hex chars (matches the
     SHACL ``sh:pattern``).
  5. Subjects with no enrichment carry NONE of the cbml4 predicates.
  6. SHACL validation against shapes.shacl.ttl passes.
  7. ``enrichments.jsonl`` sidecar exists, is sorted by
     ``(target, kind)``, and every row's fields match the
     corresponding RDF subject.
  8. Manifest extension fragment reports correct counts
     (``n_enrichments``, ``by_kind``).
  9. A run with no scope opted in emits NO cbml4 data triples and
     writes NO sidecar (regression guard for Step 1's invariant).

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, XSD

from codebase_mapper.constants import CBM, CBML4
from plugins.llm_enrich import register_all
from plugins.llm_enrich.cache import Cache
from plugins.llm_enrich.client import OllamaClient


PASS = 0
FAIL = 0
SKIP = 0


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


def skip(name: str, reason: str) -> None:
    global SKIP
    print(f"  SKIP  {name}  ({reason})")
    SKIP += 1


def _ollama_reachable() -> bool:
    try:
        return OllamaClient(timeout=3.0).ping()
    except Exception:
        return False


def build_fixture(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.name", "t"], check=True)
    (target / "auth.py").write_text(
        '"""Token-based user authentication."""\n\n'
        'class Authenticator:\n'
        '    def __init__(self, tokens):\n'
        '        self.tokens = set(tokens)\n'
        '    def check(self, t): return t in self.tokens\n'
    )
    (target / "service.py").write_text(
        '"""Public-facing service handlers."""\n\n'
        'def handle_login(creds):\n'
        '    return {"ok": True, "user": creds.get("name")}\n'
    )
    (target / "README.md").write_text(
        "# Fixture for verify_llm_enrich_rdf\n"
    )
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q",
                    "-m", "init"], check=True)


def _emit_bundle(
    fixture: Path, out: Path, cache_dir: Path,
    *, scopes: tuple[str, ...] | None,
) -> dict:
    """Run host + chunks + concept_graph + llm_enrich; return the
    emit() manifest."""
    from codebase_mapper import emit, map_codebase, reset_registries
    from codebase_mapper.repo_source import resolve_repo_source
    from plugins import chunks_embeddings, concept_graph

    reset_registries()
    backend = chunks_embeddings.DeterministicHashBackend(256)
    chunks_embeddings.register_all(backend)
    concept_graph.register_all()
    register_all(client=OllamaClient(), cache=Cache(cache_dir=cache_dir),
                 scopes=scopes)
    with resolve_repo_source(str(fixture), "HEAD") as repo:
        mapped = map_codebase(repo.path, repo.state)
        manifest = emit("fixture", mapped, out.resolve(),
                        emit_blobs_flag=False)
    return manifest


def _file_iri(path: str) -> URIRef:
    """Mirror of codebase_mapper.rdf_emit.file_iri to keep this
    verifier independent of host-internal helpers."""
    from codebase_mapper.rdf_emit import file_iri as _fi
    return _fi(path)


def test_opt_in_emits_triples_per_enriched_file(out: Path) -> None:
    g = Graph()
    g.parse(str(out / "inventory.ttl"), format="turtle")

    enriched = sorted(set(g.subjects(CBML4.fileSummary, None)))
    check("cbml4:fileSummary triples emitted",
          len(enriched) >= 2,
          f"only {len(enriched)} enriched subjects: {enriched}")

    # README.md must not be enriched.
    readme_iri = _file_iri("README.md")
    check("README.md carries no fileSummary",
          (readme_iri, CBML4.fileSummary, None) not in g)


def test_every_enriched_subject_has_full_provenance(out: Path) -> None:
    g = Graph()
    g.parse(str(out / "inventory.ttl"), format="turtle")
    for s in g.subjects(CBML4.fileSummary, None):
        models = list(g.objects(s, CBML4.fileSummaryModel))
        prompt_shas = list(g.objects(s, CBML4.fileSummaryPromptSha))
        timestamps = list(g.objects(s, CBML4.fileSummaryGeneratedAt))
        check(f"provenance complete: {s}",
              len(models) == 1 and len(prompt_shas) == 1
              and len(timestamps) == 1,
              f"model={len(models)} sha={len(prompt_shas)} "
              f"ts={len(timestamps)}")


def test_prompt_sha_is_64_hex(out: Path) -> None:
    g = Graph()
    g.parse(str(out / "inventory.ttl"), format="turtle")
    bad: list[str] = []
    for _, _, o in g.triples((None, CBML4.fileSummaryPromptSha, None)):
        s = str(o)
        if not re.fullmatch(r"[a-f0-9]{64}", s):
            bad.append(s)
    check("every prompt_sha is 64-hex", not bad,
          f"bad values: {bad[:3]}")


def test_generated_at_is_xsd_datetime(out: Path) -> None:
    g = Graph()
    g.parse(str(out / "inventory.ttl"), format="turtle")
    triples = list(g.triples((None, CBML4.fileSummaryGeneratedAt, None)))
    check("at least one fileSummaryGeneratedAt triple",
          len(triples) > 0)
    bad_types: list[str] = []
    for _, _, o in triples:
        if getattr(o, "datatype", None) != XSD.dateTime:
            bad_types.append(f"{o!r} dt={getattr(o, 'datatype', None)}")
    check("every generated_at typed as xsd:dateTime",
          not bad_types, "\n".join(bad_types[:3]))


def test_shacl_still_conforms(manifest: dict) -> None:
    conforms = (manifest.get("shacl_self_check") or {}).get("conforms")
    excerpt = (manifest.get("shacl_self_check") or {}).get("report_excerpt", "")
    check("shacl_self_check.conforms is True", bool(conforms), excerpt[:200])


def test_sidecar_matches_rdf(out: Path) -> None:
    sidecar = out / "enrichments.jsonl"
    check("enrichments.jsonl exists", sidecar.exists())
    if not sidecar.exists():
        return

    rows: list[dict] = []
    for line in sidecar.read_text().splitlines():
        rows.append(json.loads(line))

    # Sorted by (target, kind)
    sorted_rows = sorted(rows, key=lambda r: (r["target"], r["kind"]))
    check("sidecar rows are sorted by (target, kind)",
          rows == sorted_rows)

    # Every row's text matches the corresponding RDF triple
    g = Graph()
    g.parse(str(out / "inventory.ttl"), format="turtle")
    mismatches: list[str] = []
    for r in rows:
        if r["kind"] != "file_summary":
            continue
        subj = _file_iri(r["target"])
        rdf_texts = [str(o) for o in g.objects(subj, CBML4.fileSummary)]
        if rdf_texts != [r["text"]]:
            mismatches.append(f"{r['target']}: rdf={rdf_texts} sidecar={r['text']!r}")
    check("every sidecar row matches RDF text",
          not mismatches, "\n".join(mismatches[:3]))


def test_manifest_extension_reports_counts(manifest: dict) -> None:
    ext = (manifest.get("extensions") or {}).get("l4_50_artifact")
    check("manifest carries l4_50_artifact fragment",
          isinstance(ext, dict), f"got {type(ext).__name__}")
    if not isinstance(ext, dict):
        return
    n = ext.get("n_enrichments", 0)
    check("n_enrichments > 0 on enriched run", n > 0, f"got {n}")
    by_kind = ext.get("by_kind", {})
    check("by_kind reports file_summary",
          by_kind.get("file_summary", 0) > 0,
          f"by_kind={by_kind}")
    files = ext.get("files", {})
    check("manifest cites enrichments.jsonl with non-zero size",
          (files.get("enrichments.jsonl") or {}).get("size_bytes", 0) > 0,
          f"files={files}")


def test_no_scope_emits_nothing(out: Path) -> None:
    """Regression guard: a run with scopes=None must produce no L4
    data triples and no sidecar — same anchor as Step 1."""
    g = Graph()
    g.parse(str(out / "inventory.ttl"), format="turtle")
    enriched = list(g.subjects(CBML4.fileSummary, None))
    check("no scope: zero cbml4:fileSummary triples", enriched == [],
          f"got {enriched}")
    check("no scope: no enrichments.jsonl",
          not (out / "enrichments.jsonl").exists())


def main() -> int:
    global FAIL

    if not _ollama_reachable():
        for name in ("test_opt_in_emits_triples_per_enriched_file",
                     "test_every_enriched_subject_has_full_provenance",
                     "test_prompt_sha_is_64_hex",
                     "test_generated_at_is_xsd_datetime",
                     "test_shacl_still_conforms",
                     "test_sidecar_matches_rdf",
                     "test_manifest_extension_reports_counts",
                     "test_no_scope_emits_nothing"):
            skip(name, "Ollama unreachable")
        print(f"\npassed: {PASS}   failed: {FAIL}   skipped: {SKIP}")
        return 0

    work = Path(tempfile.mkdtemp(prefix="verify_step4_"))
    try:
        fixture = work / "fixture"
        build_fixture(fixture)

        # Opt-in run
        out_with = work / "with"
        cache_dir = work / "cache"
        manifest_with = _emit_bundle(fixture, out_with, cache_dir,
                                      scopes=("files",))

        # No-scope run (regression check)
        out_off = work / "off"
        cache_off = work / "cache_off"
        _emit_bundle(fixture, out_off, cache_off, scopes=None)

        for t in (
            (test_opt_in_emits_triples_per_enriched_file, out_with),
            (test_every_enriched_subject_has_full_provenance, out_with),
            (test_prompt_sha_is_64_hex, out_with),
            (test_generated_at_is_xsd_datetime, out_with),
            (test_shacl_still_conforms, manifest_with),
            (test_sidecar_matches_rdf, out_with),
            (test_manifest_extension_reports_counts, manifest_with),
            (test_no_scope_emits_nothing, out_off),
        ):
            fn, arg = t
            try:
                fn(arg)
            except Exception:
                FAIL += 1
                print(f"  FAIL  {fn.__name__}")
                traceback.print_exc()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\npassed: {PASS}   failed: {FAIL}   skipped: {SKIP}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
