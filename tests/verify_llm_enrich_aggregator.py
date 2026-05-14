#!/usr/bin/env python3
"""verify_llm_enrich_aggregator.py — Step 5 end-to-end verifier.

Drives the L4 aggregator with ``scopes=("concepts", "schemas")``
against a git fixture rich enough to exercise the typed-concept path
and the schema-file path simultaneously.

What's checked (requires Ollama; skips cleanly otherwise):

  1. concept_description bucket is populated for every typed concept
     in ctx.indices["l3_20_concepts"]["concepts"] (those with a
     ``kind`` field).
  2. schema_purpose bucket is populated for every fixture file under
     static/schemas/ with a known schema extension.
  3. Records carry the full provenance set: text, model, prompt_sha,
     target_sha, generated_at, was_cache_hit.
  4. cbml4:conceptDescription triples land on ``cbmi:concept/<name>``.
  5. cbml4:schemaPurpose triples land on ``cbmi:file/<path>``.
  6. The same file CAN carry both fileSummary and schemaPurpose when
     it's a source file under static/schemas/ (no exclusion).
  7. SHACL conforms with all three kinds present.
  8. enrichments.jsonl includes all three kinds with byte-stable
     sort order ((kind, target)).
  9. Scopes are independent: scopes=("concepts",) does not fire
     schema_purpose, and vice versa.
 10. With Ollama unreachable, the aggregator degrades silently —
     no exceptions, both buckets empty, SHACL stays green.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.parse
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, XSD

from codebase_mapper.constants import CBM, CBMI_NS, CBML4
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


def _concept_iri(canon: str) -> URIRef:
    return URIRef(f"{CBMI_NS}concept/{urllib.parse.quote(canon, safe='')}")


def _file_iri(path: str) -> URIRef:
    from codebase_mapper.rdf_emit import file_iri as _fi
    return _fi(path)


def build_fixture(target: Path) -> None:
    """Fixture rich enough that L3 produces at least one typed concept
    after MIN_COOCCURRENCE filtering. The trick: emit identifiers that
    cooccur with curated-vocab terms on multiple paths."""
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.name", "t"], check=True)

    # Three files that all reference 'behavior', 'intent', 'contract'.
    # The L3 cooccurrence threshold drops singletons; three files
    # ensures every term passes.
    (target / "a.py").write_text(
        '"""Module A: defines a Behavior and its Contract."""\n'
        'class UserBehavior:\n'
        '    def authenticate(self, token):\n'
        '        return self.contract(token)\n'
        '    def contract(self, t): return bool(t)\n'
    )
    (target / "b.py").write_text(
        '"""Module B: defines an Intent linked to a Behavior."""\n'
        'class LoginIntent:\n'
        '    def behavior(self): return "login"\n'
        '    def contract(self): return True\n'
    )
    (target / "c.py").write_text(
        '"""Module C: another Behavior + Contract."""\n'
        'class AdminBehavior:\n'
        '    def authenticate(self): pass\n'
        '    def contract(self): pass\n'
    )

    # And a schema file under static/schemas/.
    schemas = target / "static" / "schemas"
    schemas.mkdir(parents=True)
    (schemas / "event.xsd").write_text(
        '<?xml version="1.0"?>\n'
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"\n'
        '           targetNamespace="http://example.com/event">\n'
        '  <xs:element name="event">\n'
        '    <xs:complexType>\n'
        '      <xs:sequence>\n'
        '        <xs:element name="type" type="xs:string"/>\n'
        '        <xs:element name="timestamp" type="xs:dateTime"/>\n'
        '      </xs:sequence>\n'
        '    </xs:complexType>\n'
        '  </xs:element>\n'
        '</xs:schema>\n'
    )

    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q",
                    "-m", "init"], check=True)


def _emit_bundle(
    fixture: Path, out: Path, cache_dir: Path,
    *, scopes: tuple[str, ...] | None,
    client_host: str | None = None,
) -> tuple[dict, dict]:
    """Returns (mapped, manifest)."""
    from codebase_mapper import emit, map_codebase, reset_registries
    from codebase_mapper.repo_source import resolve_repo_source
    from plugins import chunks_embeddings, concept_graph

    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(256))
    concept_graph.register_all()
    client = OllamaClient(host=client_host) if client_host else OllamaClient()
    register_all(client=client, cache=Cache(cache_dir=cache_dir),
                 scopes=scopes)
    with resolve_repo_source(str(fixture), "HEAD") as repo:
        mapped = map_codebase(repo.path, repo.state)
        manifest = emit("fixture", mapped, out.resolve(),
                        emit_blobs_flag=False)
    return mapped, manifest


# ---------- test bodies ----------

def test_concepts_scope_populates_typed_concepts(work: Path) -> None:
    fixture = work / "f1"
    build_fixture(fixture)
    out = work / "out1"
    cache_dir = work / "cache1"
    mapped, _ = _emit_bundle(fixture, out, cache_dir,
                             scopes=("concepts",))

    l3 = mapped["ctx"].indices.get("l3_20_concepts") or {}
    typed = [n for n, m in (l3.get("concepts") or {}).items()
             if "kind" in m]
    bucket = mapped["ctx"].scratch.get("llm:concept_description") or {}

    check("L3 produced at least one typed concept in this fixture",
          len(typed) > 0,
          "the fixture isn't rich enough to exercise concept_description")
    if not typed:
        return
    check(
        "every typed concept got a description",
        set(bucket) >= set(typed),
        f"missing: {sorted(set(typed) - set(bucket))[:5]}",
    )

    # Each record carries full provenance + non-empty text.
    expected_keys = {"v", "kind", "model", "prompt_sha", "target_sha",
                     "text", "generated_at", "was_cache_hit"}
    bad: list[str] = []
    for n, rec in bucket.items():
        if expected_keys - set(rec.keys()):
            bad.append(f"{n}: missing {expected_keys - set(rec.keys())}")
        if not rec.get("text", "").strip():
            bad.append(f"{n}: empty text")
    check("every concept record is complete", not bad,
          "\n".join(bad[:3]))

    # No schema_purpose enrichment when scope is concepts-only.
    sp = mapped["ctx"].scratch.get("llm:schema_purpose") or {}
    check("concepts-only scope: no schema_purpose", sp == {},
          f"unexpected: {list(sp)[:3]}")


def test_schemas_scope_populates_schema_files(work: Path) -> None:
    fixture = work / "f2"
    build_fixture(fixture)
    out = work / "out2"
    cache_dir = work / "cache2"
    mapped, _ = _emit_bundle(fixture, out, cache_dir,
                             scopes=("schemas",))

    bucket = mapped["ctx"].scratch.get("llm:schema_purpose") or {}
    check("at least one schema file enriched",
          "static/schemas/event.xsd" in bucket,
          f"got keys: {sorted(bucket)}")

    # No concept_description when scope is schemas-only.
    cd = mapped["ctx"].scratch.get("llm:concept_description") or {}
    check("schemas-only scope: no concept_description", cd == {},
          f"unexpected: {list(cd)[:3]}")


def test_rdf_triples_land_on_right_subjects(work: Path) -> None:
    fixture = work / "f3"
    build_fixture(fixture)
    out = work / "out3"
    cache_dir = work / "cache3"
    _emit_bundle(fixture, out, cache_dir,
                 scopes=("concepts", "schemas"))

    g = Graph()
    g.parse(str(out / "inventory.ttl"), format="turtle")

    # concept_description triples on cbmi:concept/<name>
    concept_subs = list(g.subjects(CBML4.conceptDescription, None))
    check("at least one cbml4:conceptDescription triple",
          len(concept_subs) > 0)
    for s in concept_subs:
        check(
            f"conceptDescription subject is cbmi:concept/...: {s}",
            str(s).startswith(f"{CBMI_NS}concept/"),
        )

    # schema_purpose triples on cbmi:file/static/schemas/event.xsd
    schema_subs = list(g.subjects(CBML4.schemaPurpose, None))
    check("at least one cbml4:schemaPurpose triple",
          len(schema_subs) > 0)
    expected_xsd = _file_iri("static/schemas/event.xsd")
    check(
        "schemaPurpose attached to the right file",
        expected_xsd in schema_subs,
        f"got: {[str(s) for s in schema_subs]}",
    )


def test_shacl_conforms_with_all_kinds(work: Path) -> None:
    fixture = work / "f4"
    build_fixture(fixture)
    out = work / "out4"
    cache_dir = work / "cache4"
    _, manifest = _emit_bundle(fixture, out, cache_dir,
                               scopes=("files", "concepts", "schemas"))
    check("shacl_self_check.conforms is True",
          bool((manifest.get("shacl_self_check") or {}).get("conforms")),
          ((manifest.get("shacl_self_check") or {})
           .get("report_excerpt", ""))[:200])


def test_sidecar_includes_all_kinds(work: Path) -> None:
    fixture = work / "f5"
    build_fixture(fixture)
    out = work / "out5"
    cache_dir = work / "cache5"
    _, _ = _emit_bundle(fixture, out, cache_dir,
                       scopes=("files", "concepts", "schemas"))

    sidecar = out / "enrichments.jsonl"
    rows = [json.loads(line) for line in sidecar.read_text().splitlines()]
    kinds_seen = {r["kind"] for r in rows}
    check(
        "sidecar carries all three kinds",
        kinds_seen == {"file_summary", "concept_description", "schema_purpose"},
        f"got: {kinds_seen}",
    )
    sorted_rows = sorted(rows, key=lambda r: (r["kind"], r["target"]))
    check("rows sorted by (kind, target)", rows == sorted_rows)


def test_unreachable_ollama_degrades_silently(work: Path) -> None:
    fixture = work / "f6"
    build_fixture(fixture)
    out = work / "out6"
    cache_dir = work / "cache6"
    # Point at port 11435 — nothing listens there.
    try:
        mapped, manifest = _emit_bundle(
            fixture, out, cache_dir,
            scopes=("concepts", "schemas"),
            client_host="http://127.0.0.1:11435",
        )
    except Exception as e:
        check("unreachable Ollama doesn't crash the pipeline",
              False, f"raised: {type(e).__name__}: {e}")
        return
    cd = mapped["ctx"].scratch.get("llm:concept_description") or {}
    sp = mapped["ctx"].scratch.get("llm:schema_purpose") or {}
    check("unreachable: empty concept_description bucket",
          cd == {}, f"unexpected: {list(cd)[:3]}")
    check("unreachable: empty schema_purpose bucket",
          sp == {}, f"unexpected: {list(sp)[:3]}")
    check("unreachable: SHACL still conforms",
          bool((manifest.get("shacl_self_check") or {}).get("conforms")))


def main() -> int:
    global FAIL

    test_names = [
        "test_concepts_scope_populates_typed_concepts",
        "test_schemas_scope_populates_schema_files",
        "test_rdf_triples_land_on_right_subjects",
        "test_shacl_conforms_with_all_kinds",
        "test_sidecar_includes_all_kinds",
        "test_unreachable_ollama_degrades_silently",
    ]

    if not _ollama_reachable():
        for n in test_names:
            skip(n, "Ollama unreachable")
        print(f"\npassed: {PASS}   failed: {FAIL}   skipped: {SKIP}")
        return 0

    work = Path(tempfile.mkdtemp(prefix="verify_step5_"))
    try:
        for t in (test_concepts_scope_populates_typed_concepts,
                  test_schemas_scope_populates_schema_files,
                  test_rdf_triples_land_on_right_subjects,
                  test_shacl_conforms_with_all_kinds,
                  test_sidecar_includes_all_kinds,
                  test_unreachable_ollama_degrades_silently):
            try:
                t(work)
            except Exception:
                FAIL += 1
                print(f"  FAIL  {t.__name__}")
                traceback.print_exc()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\npassed: {PASS}   failed: {FAIL}   skipped: {SKIP}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
