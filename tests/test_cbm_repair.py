#!/usr/bin/env python3
"""test_cbm_repair.py — TDD suite for scripts/cbm_repair.py.

Tier-1 bundle reconstruction: given a bundle directory that carries only
the graph serializations (interrupted emit), rebuild run_manifest.json,
enrichments.jsonl, and concepts.json from the facts embedded in
inventory.ttl — streaming, never loading the full graph into rdflib.

Two layers:

  1. Unit tests against a small synthetic bundle authored here with the
     project's own vocabulary/IRI helpers, serialized by rdflib exactly
     like the production emitter. Includes a file summary containing
     escaped quotes, a newline, AND a blank line inside the literal —
     rdflib 7.x serializes that as a triple-quoted long string spanning
     a paragraph break, which is the hard case for a streaming splitter.

  2. Integration tests against the complete reference bundle at
     _tmp/fastapi (skipped when absent) comparing the reconstruction to
     the original sidecars.

Provenance note (PALS's LAW): enrichment texts recovered from the graph
are LLM-authored, UNVERIFIED content; these tests verify only that the
tool reproduces the graph's literals and their provenance fields
faithfully — not that the texts themselves are true.

Run: .venv/bin/python -m pytest tests/test_cbm_repair.py -v
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.cbm_repair import (  # noqa: E402
    main,
    reconstruct_components,
    split_blocks,
)

# ---------------------------------------------------------------------------
# Synthetic fixture
# ---------------------------------------------------------------------------

# The hard literal: escaped quotes, a newline, a blank line, a backslash.
APP_SUMMARY = (
    'Parses "quoted" args with """care""".\n'
    "\n"
    "Second paragraph after a blank line, plus a \\ backslash."
)
UTIL_SUMMARY = "Utility helpers."
HANDLER_DESC = 'Describes the "handler" concept.\nUsed across modules.'

MODEL = "test-model:1b"
FS_PROMPT_SHA = "b" * 64
CD_PROMPT_SHA = "c" * 64
APP_SHA = "a1" * 32
UTIL_SHA = "d2" * 32
TEST_SHA = "e3" * 32
COMMIT_SHA = "ab12" * 10

APP_AST = '{"ast_json": {"_type": "Module"}, "imports": []}'
UTIL_AST = '{"ast_json": null, "imports": []}'


def _build_synthetic_ttl() -> str:
    """Author a miniature inventory.ttl through the production vocabulary
    and rdflib's own turtle serializer (same path as the real emitter)."""
    from rdflib import Graph, Literal, Namespace, URIRef
    from rdflib.namespace import RDF, SKOS, XSD

    from codebase_mapper.shared_kernel.constants import CBM, CBMI_NS, CBML4
    from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import (
        file_iri,
    )

    CBML2 = Namespace("https://codebase-mapper.example.org/cbml2#")
    CBML3 = Namespace("https://codebase-mapper.example.org/cbml3#")

    def concept(cid: str) -> URIRef:
        import urllib.parse
        return URIRef(f"{CBMI_NS}concept/{urllib.parse.quote(cid, safe='')}")

    CBMT_NS = "https://codebase-mapper.example.org/cbm/type#"

    g = Graph()
    g.bind("cbm", CBM)
    g.bind("cbml2", CBML2)
    g.bind("cbml3", CBML3)
    g.bind("cbml4", CBML4)
    g.bind("skos", SKOS)
    g.bind("cbmt", CBMT_NS)

    app = file_iri("src/app.py")
    util = file_iri("src/util.py")
    tst = file_iri("tests/test_app.py")

    def add_file(iri, path, sha, ftype, lang, ast=None):
        g.add((iri, RDF.type, CBM.File))
        g.add((iri, CBM.path, Literal(path)))
        g.add((iri, CBM.contentSha256, Literal(sha, datatype=XSD.hexBinary)))
        g.add((iri, CBM.type, URIRef(CBMT_NS + ftype)))
        g.add((iri, CBM.language, Literal(lang)))
        if ast is not None:
            g.add((iri, CBM.astSummary, Literal(ast)))

    add_file(app, "src/app.py", APP_SHA, "source_code", "python", APP_AST)
    add_file(util, "src/util.py", UTIL_SHA, "source_code", "python", UTIL_AST)
    add_file(tst, "tests/test_app.py", TEST_SHA, "test_code", "python")

    # edges for manifest counts
    g.add((app, CBM.imports, util))
    g.add((tst, CBM.imports, app))
    g.add((app, CBM.importsExternal, URIRef(f"{CBMI_NS}ext/os")))
    g.add((tst, CBM.tests, app))
    g.add((app, CBM.pinsDependency, URIRef(f"{CBMI_NS}pkg/fastapi/0.1")))
    g.add((app, CBM.declaresDependency, URIRef(f"{CBMI_NS}pkg/fastapi")))

    # L4 file summaries (exactly like plugins/llm_enrich/graph_writer.py)
    def add_summary(iri, text, generated_at):
        g.add((iri, CBML4.fileSummary, Literal(text)))
        g.add((iri, CBML4.fileSummaryModel, Literal(MODEL)))
        g.add((iri, CBML4.fileSummaryPromptSha, Literal(FS_PROMPT_SHA)))
        g.add((iri, CBML4.fileSummaryGeneratedAt,
               Literal(generated_at, datatype=XSD.dateTime)))

    add_summary(app, APP_SUMMARY, "2026-07-01T10:00:00Z")
    add_summary(util, UTIL_SUMMARY, "2026-07-01T10:00:05Z")

    # Concepts (exactly like plugins/concept_graph/graph_writer.py)
    def add_concept(cid, label, alts=(), comps=(), freq=1, fc=1, row=None,
                    kind=None, broader=None):
        ciri = concept(cid)
        g.add((ciri, RDF.type, SKOS.Concept))
        g.add((ciri, SKOS.prefLabel, Literal(label, lang="en")))
        for a in alts:
            g.add((ciri, SKOS.altLabel, Literal(a, lang="en")))
        for comp in comps:
            g.add((ciri, CBML3.composedOf, concept(comp)))
        g.add((ciri, CBML3.occurrenceCount, Literal(freq, datatype=XSD.integer)))
        g.add((ciri, CBML3.fileCount, Literal(fc, datatype=XSD.integer)))
        if row is not None:
            g.add((ciri, CBML3.embeddingRow, Literal(row, datatype=XSD.integer)))
            g.add((ciri, CBML3.embeddingArtifact,
                   Literal("concepts_embeddings.npz")))
        if kind is not None:
            g.add((ciri, CBML3.conceptKind, Literal(kind)))
        if broader is not None:
            g.add((ciri, CBML3.broaderCollection,
                   URIRef(f"{CBMI_NS}collection/{broader}")))
        return ciri

    # embedding rows follow the sorted-id order the aggregator uses
    add_concept("app", "app", alts=["App"], freq=5, fc=2, row=0)
    add_concept("app_main", "app main", alts=["AppMain"],
                comps=["app", "main"], freq=2, fc=1, row=1)
    add_concept("main", "main", freq=3, fc=2, row=2)
    add_concept("util", "util", freq=1, fc=1)
    handler = add_concept("handler", "handler", freq=4, fc=1,
                          kind="domain-primitive",
                          broader="intent_first_ontology")
    add_concept("test", "test", freq=2, fc=1)
    add_concept("import_statement", "import_statement", freq=2, fc=1)
    add_concept("test_import_statement", "test import_statement",
                comps=["test", "import_statement"], freq=1, fc=1)
    add_concept("chunkonly", "chunkonly", freq=1, fc=1)

    # concept_description literals on the typed concept
    g.add((handler, CBML4.conceptDescription, Literal(HANDLER_DESC)))
    g.add((handler, CBML4.conceptDescriptionModel, Literal(MODEL)))
    g.add((handler, CBML4.conceptDescriptionPromptSha, Literal(CD_PROMPT_SHA)))
    g.add((handler, CBML4.conceptDescriptionGeneratedAt,
           Literal("2026-07-01T09:00:00Z", datatype=XSD.dateTime)))

    # FILE-level lexicalizes edges (these define per_path_concepts)
    for cid in ("app", "app_main", "handler", "main"):
        g.add((app, CBML3.lexicalizes, concept(cid)))
    for cid in ("app", "main", "util"):
        g.add((util, CBML3.lexicalizes, concept(cid)))
    for cid in ("import_statement", "test", "test_import_statement"):
        g.add((tst, CBML3.lexicalizes, concept(cid)))

    # A chunk with lexicalizes edges — must NOT leak into per_path_concepts.
    import urllib.parse
    chunk = URIRef(
        f"{CBMI_NS}chunk/"
        + urllib.parse.quote("src/app.py#function:main:L1-L5:b0-99", safe="")
    )
    g.add((chunk, RDF.type, CBML2.Chunk))
    g.add((chunk, CBML2.inFile, app))
    g.add((chunk, CBML2.embeddingRow, Literal(0, datatype=XSD.integer)))
    g.add((chunk, CBML3.lexicalizes, concept("chunkonly")))
    g.add((chunk, CBML3.lexicalizes, concept("main")))

    # repo + commit
    repo = URIRef(f"{CBMI_NS}repo/synthrepo")
    commit = URIRef(f"{CBMI_NS}commit/{COMMIT_SHA}")
    g.add((repo, RDF.type, CBM.Repository))
    g.add((repo, CBM.atCommit, commit))
    for f in (app, util, tst):
        g.add((repo, CBM.hasFile, f))
    g.add((commit, RDF.type, CBM.Commit))
    g.add((commit, CBM.commitSha, Literal(COMMIT_SHA)))

    return g.serialize(format="turtle")


@pytest.fixture(scope="module")
def synthetic_bundle(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("synthetic_bundle")
    (d / "inventory.ttl").write_text(_build_synthetic_ttl(), encoding="utf-8")
    (d / "ontology-mapping.ttl").write_text(
        "# placeholder mapping\n", encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def repaired(synthetic_bundle, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("repaired")
    rc = main(["--bundle", str(synthetic_bundle), "--out", str(out)])
    assert rc == 0
    return out


# ---------------------------------------------------------------------------
# Streaming splitter
# ---------------------------------------------------------------------------


class TestSplitBlocks:
    def test_blank_line_inside_long_literal_does_not_split(self):
        ttl = _build_synthetic_ttl()
        assert '"""' in ttl, (
            "fixture must exercise rdflib's triple-quoted long-string form"
        )
        lines = ttl.splitlines(keepends=True)
        header = "".join(l for l in lines if l.startswith("@prefix"))
        blocks = list(split_blocks(iter(lines)))
        subject_blocks = [b for b in blocks if not b.lstrip().startswith("@prefix")]

        # One block per subject: 3 files + 9 concepts + 1 chunk + repo + commit
        assert len(subject_blocks) == 15

        # Every block must be independently parseable and, in total, must
        # round-trip the exact literal set of the original graph.
        from rdflib import Graph
        merged = Graph()
        for b in subject_blocks:
            merged.parse(data=header + b, format="turtle")
        reparsed = Graph()
        reparsed.parse(data=ttl, format="turtle")
        assert set(merged) == set(reparsed)

    def test_literal_roundtrip_of_blank_line_summary(self):
        from rdflib import Graph, URIRef
        ttl = _build_synthetic_ttl()
        lines = ttl.splitlines(keepends=True)
        header = "".join(l for l in lines if l.startswith("@prefix"))
        target = None
        for b in split_blocks(iter(lines)):
            if "src%2Fapp.py>" in b.split("\n", 1)[0]:
                target = b
                break
        assert target is not None
        g = Graph()
        g.parse(data=header + target, format="turtle")
        C4 = "https://codebase-mapper.example.org/cbml4#"
        text = str(next(g.objects(None, URIRef(C4 + "fileSummary"))))
        assert text == APP_SUMMARY


# ---------------------------------------------------------------------------
# components order reconstruction
# ---------------------------------------------------------------------------


class TestReconstructComponents:
    def test_simple_order(self):
        assert reconstruct_components("app_main", {"app", "main"}) == [
            "app", "main"]

    def test_component_containing_underscore(self):
        assert reconstruct_components(
            "test_import_statement", {"test", "import_statement"},
        ) == ["test", "import_statement"]

    def test_compound_suffix(self):
        assert reconstruct_components(
            "app_main_compound", {"app", "main"}) == ["app", "main"]

    def test_duplicate_component(self):
        assert reconstruct_components("test_test", {"test"}) == [
            "test", "test"]

    def test_unresolvable_falls_back_sorted(self):
        assert reconstruct_components("weird", {"y", "x"}) == ["x", "y"]


# ---------------------------------------------------------------------------
# enrichments.jsonl reconstruction (synthetic)
# ---------------------------------------------------------------------------


class TestEnrichmentsSynthetic:
    def _records(self, repaired):
        p = repaired / "enrichments.jsonl"
        assert p.exists()
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def test_record_schema_and_sort(self, repaired):
        recs = self._records(repaired)
        assert len(recs) == 3
        for r in recs:
            assert sorted(r.keys()) == [
                "generated_at", "kind", "model", "prompt_sha",
                "target", "target_sha", "text",
            ]
        # sorted by (kind, target): concept_description first
        assert [(r["kind"], r["target"]) for r in recs] == [
            ("concept_description", "handler"),
            ("file_summary", "src/app.py"),
            ("file_summary", "src/util.py"),
        ]

    def test_file_summary_hard_literal(self, repaired):
        recs = {(r["kind"], r["target"]): r for r in self._records(repaired)}
        app = recs[("file_summary", "src/app.py")]
        assert app["text"] == APP_SUMMARY          # quotes + \n + blank line
        assert app["model"] == MODEL
        assert app["prompt_sha"] == FS_PROMPT_SHA
        assert app["target_sha"] == APP_SHA        # cbm:contentSha256
        assert app["generated_at"] == "2026-07-01T10:00:00Z"  # Z-normalized

    def test_second_summary(self, repaired):
        recs = {(r["kind"], r["target"]): r for r in self._records(repaired)}
        util = recs[("file_summary", "src/util.py")]
        assert util["text"] == UTIL_SUMMARY
        assert util["target_sha"] == UTIL_SHA
        assert util["generated_at"] == "2026-07-01T10:00:05Z"

    def test_concept_description(self, repaired):
        recs = {(r["kind"], r["target"]): r for r in self._records(repaired)}
        cd = recs[("concept_description", "handler")]
        assert cd["text"] == HANDLER_DESC
        assert cd["model"] == MODEL
        assert cd["prompt_sha"] == CD_PROMPT_SHA
        assert cd["generated_at"] == "2026-07-01T09:00:00Z"
        # The original target_sha hashes the rendered prompt, which the
        # graph does not carry — an honest reconstruction leaves it empty.
        assert cd["target_sha"] == ""


# ---------------------------------------------------------------------------
# concepts.json reconstruction (synthetic)
# ---------------------------------------------------------------------------


class TestConceptsSynthetic:
    def _payload(self, repaired):
        p = repaired / "concepts.json"
        assert p.exists()
        return json.loads(p.read_text())

    def test_concept_ids(self, repaired):
        c = self._payload(repaired)
        assert set(c["concepts"]) == {
            "app", "app_main", "main", "util", "handler",
            "test", "import_statement", "test_import_statement", "chunkonly",
        }

    def test_atomic_record(self, repaired):
        c = self._payload(repaired)["concepts"]["app"]
        assert c == {
            "label": "app", "alt_labels": ["App"], "components": [],
            "frequency": 5, "file_count": 2, "embedding_row": 0,
        }

    def test_compound_component_order(self, repaired):
        cs = self._payload(repaired)["concepts"]
        assert cs["app_main"]["components"] == ["app", "main"]
        assert cs["test_import_statement"]["components"] == [
            "test", "import_statement"]

    def test_untyped_has_null_row(self, repaired):
        assert self._payload(repaired)["concepts"]["util"][
            "embedding_row"] is None

    def test_typed_concept_kind_and_broader(self, repaired):
        h = self._payload(repaired)["concepts"]["handler"]
        assert h["kind"] == "domain-primitive"
        assert h["broader"] == "intent_first_ontology"

    def test_per_path_from_file_level_edges_only(self, repaired):
        pp = self._payload(repaired)["per_path_concepts"]
        assert pp == {
            "src/app.py": ["app", "app_main", "handler", "main"],
            "src/util.py": ["app", "main", "util"],
            "tests/test_app.py": [
                "import_statement", "test", "test_import_statement"],
        }
        # chunk-level lexicalizes must not leak in
        assert "chunkonly" not in pp["src/app.py"]

    def test_cooccurrence_recounted_min2(self, repaired):
        c = self._payload(repaired)
        assert c["cooccurrence"] == [["app", "main", 2]]

    def test_embedding_ids_ordered_by_row(self, repaired):
        c = self._payload(repaired)
        assert c["concept_embedding_ids"] == ["app", "app_main", "main"]
        assert c["concept_embeddings_artifact"] == "concepts_embeddings.npz"

    def test_reconstruction_provenance_key(self, repaired):
        c = self._payload(repaired)
        rec = c["reconstruction"]
        assert rec["reconstructed"] is True
        assert "cbm_repair" in rec["generated_by"]
        assert rec["source_artifact"] == "inventory.ttl"


# ---------------------------------------------------------------------------
# run_manifest.json reconstruction (synthetic)
# ---------------------------------------------------------------------------


class TestManifestSynthetic:
    def _man(self, repaired):
        p = repaired / "run_manifest.json"
        assert p.exists()
        return json.loads(p.read_text())

    def test_honest_labeling(self, repaired):
        m = self._man(repaired)
        assert m["reconstructed"] is True
        assert "cbm_repair" in m["generated_by"]
        # never mistakable for an original run receipt
        assert "tool_version" not in m
        assert "shacl_self_check" not in m
        rec = m["reconstruction"]
        assert set(rec["reconstructed_artifacts"]) == {
            "run_manifest.json", "enrichments.jsonl", "concepts.json"}

    def test_absent_artifacts_listed(self, repaired):
        absent = self._man(repaired)["reconstruction"][
            "absent_not_reconstructable"]
        for name in ("embeddings.npz", "embeddings_meta.json",
                     "concepts_embeddings.npz", "ast_coverage.json",
                     "inventory.jsonld", "blobs/"):
            assert name in absent, name

    def test_counts_from_graph(self, repaired):
        counts = self._man(repaired)["counts"]
        assert counts["files"] == 3
        assert counts["import_edges"] == 2
        assert counts["import_external_edges"] == 1
        assert counts["declares_dependency_edges"] == 1
        assert counts["pins_dependency_edges"] == 1
        assert counts["tests_edges"] == 1
        assert counts["ast_full_bodies_python"] == 1
        assert counts["ast_summary_total_bytes"] == len(APP_AST) + len(UTIL_AST)

    def test_identity_and_census(self, repaired):
        m = self._man(repaired)
        assert m["commit_sha"] == COMMIT_SHA
        assert m["repo_name"] == "synthrepo"
        assert m["files_by_language"] == {"python": 3}
        assert m["files_by_type"] == {"source_code": 2, "test_code": 1}

    def test_artifact_hashes_of_present_files(self, repaired, synthetic_bundle):
        m = self._man(repaired)
        inv = synthetic_bundle / "inventory.ttl"
        assert m["artifacts"]["inventory.ttl"]["sha256"] == hashlib.sha256(
            inv.read_bytes()).hexdigest()
        assert m["artifacts"]["inventory.ttl"]["size_bytes"] == inv.stat().st_size
        assert "ontology-mapping.ttl" in m["artifacts"]
        assert "inventory.jsonld" not in m["artifacts"]

    def test_extension_fragments(self, repaired):
        m = self._man(repaired)
        l3 = m["extensions"]["l3_40_concepts_artifact"]
        assert l3["n_concepts"] == 9
        assert l3["n_cooccurrence"] == 1
        assert l3["concept_centroids_available"] is True
        l4 = m["extensions"]["l4_50_artifact"]
        assert l4["n_enrichments"] == 3
        assert l4["by_kind"] == {"concept_description": 1, "file_summary": 2}
        # hashes of the files this tool actually wrote
        ej = repaired / "enrichments.jsonl"
        assert l4["files"]["enrichments.jsonl"]["sha256"] == hashlib.sha256(
            ej.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# CLI conventions
# ---------------------------------------------------------------------------


class TestCli:
    def test_refuses_to_overwrite(self, synthetic_bundle, tmp_path, capsys):
        out = tmp_path / "out"
        out.mkdir()
        (out / "concepts.json").write_text("{}")
        rc = main(["--bundle", str(synthetic_bundle), "--out", str(out)])
        assert rc != 0
        # refused wholesale: nothing else written either
        assert not (out / "enrichments.jsonl").exists()
        assert not (out / "run_manifest.json").exists()
        assert (out / "concepts.json").read_text() == "{}"
        err = capsys.readouterr().err
        assert "concepts.json" in err

    def test_in_place_default_and_second_run_refusal(
            self, synthetic_bundle, tmp_path):
        import shutil
        d = tmp_path / "bundle"
        shutil.copytree(synthetic_bundle, d)
        assert main(["--bundle", str(d)]) == 0
        for name in ("run_manifest.json", "enrichments.jsonl",
                     "concepts.json"):
            assert (d / name).exists(), name
        before = (d / "run_manifest.json").read_bytes()
        assert main(["--bundle", str(d)]) != 0
        assert (d / "run_manifest.json").read_bytes() == before

    def test_missing_inventory_is_an_error(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        rc = main(["--bundle", str(empty), "--out", str(tmp_path / "o")])
        assert rc != 0


# ---------------------------------------------------------------------------
# Integration: the complete fastapi reference bundle
# ---------------------------------------------------------------------------

FASTAPI = Path("/home/pals/github-mirror/code-base-mapper/_tmp/fastapi")

fastapi_only = pytest.mark.skipif(
    not (FASTAPI / "inventory.ttl").exists(),
    reason="fastapi reference bundle not present",
)


@pytest.fixture(scope="module")
def fastapi_repaired(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("fastapi_repaired")
    rc = main(["--bundle", str(FASTAPI), "--out", str(out)])
    assert rc == 0
    return out


@fastapi_only
class TestFastapiIntegration:
    def _orig_enrichments(self):
        return [json.loads(l)
                for l in (FASTAPI / "enrichments.jsonl").read_text()
                .splitlines() if l.strip()]

    def _recon_enrichments(self, out):
        return [json.loads(l)
                for l in (out / "enrichments.jsonl").read_text()
                .splitlines() if l.strip()]

    def test_enrichment_recoverable_fields_equal_as_set(self, fastapi_repaired):
        key = lambda r: (r["target"], r["kind"], r["text"], r["model"],
                         r["prompt_sha"], r["generated_at"])
        orig = self._orig_enrichments()
        recon = self._recon_enrichments(fastapi_repaired)
        assert len(recon) == len(orig)
        assert {key(r) for r in recon} == {key(r) for r in orig}

    def test_enrichment_full_tuple_including_target_sha(self, fastapi_repaired):
        """Full 7-tuple equality wherever target_sha is recoverable.

        The original target_sha hashes the (possibly truncated) prompt
        input; the graph carries only cbm:contentSha256. The two agree
        for every file at or under the 4000-char prompt budget. Records
        that deviate must deviate in target_sha ONLY, and the vast
        majority must match on the full 7-tuple.
        """
        full = lambda r: (r["target"], r["kind"], r["text"], r["model"],
                          r["prompt_sha"], r["generated_at"], r["target_sha"])
        orig_fs = {r["target"]: r for r in self._orig_enrichments()
                   if r["kind"] == "file_summary"}
        recon_fs = {r["target"]: r for r in
                    self._recon_enrichments(fastapi_repaired)
                    if r["kind"] == "file_summary"}
        assert set(orig_fs) == set(recon_fs)
        exact = 0
        for tgt, o in orig_fs.items():
            r = recon_fs[tgt]
            if full(o) == full(r):
                exact += 1
            else:
                # only the prompt-hash field may differ
                assert (o["text"], o["model"], o["prompt_sha"],
                        o["generated_at"]) == (
                    r["text"], r["model"], r["prompt_sha"], r["generated_at"])
                assert o["target_sha"] != r["target_sha"]
        assert exact >= 0.9 * len(orig_fs), (exact, len(orig_fs))

    def test_enrichment_counts_per_kind(self, fastapi_repaired):
        from collections import Counter
        orig = Counter(r["kind"] for r in self._orig_enrichments())
        recon = Counter(r["kind"]
                        for r in self._recon_enrichments(fastapi_repaired))
        assert recon == orig  # 532 file_summary + 13 concept_description

    def test_concept_ids_match(self, fastapi_repaired):
        orig = json.loads((FASTAPI / "concepts.json").read_text())
        recon = json.loads((fastapi_repaired / "concepts.json").read_text())
        assert set(recon["concepts"]) == set(orig["concepts"])

    def test_per_path_concepts_match(self, fastapi_repaired):
        orig = json.loads((FASTAPI / "concepts.json").read_text())
        recon = json.loads((fastapi_repaired / "concepts.json").read_text())
        assert recon["per_path_concepts"] == orig["per_path_concepts"]

    def test_cooccurrence_and_embedding_ids_match(self, fastapi_repaired):
        orig = json.loads((FASTAPI / "concepts.json").read_text())
        recon = json.loads((fastapi_repaired / "concepts.json").read_text())
        assert recon["cooccurrence"] == orig["cooccurrence"]
        assert recon["concept_embedding_ids"] == orig["concept_embedding_ids"]

    def test_manifest_counts_match_original(self, fastapi_repaired):
        orig = json.loads((FASTAPI / "run_manifest.json").read_text())
        recon = json.loads((fastapi_repaired / "run_manifest.json").read_text())
        for k, v in recon["counts"].items():
            assert v == orig["counts"][k], k
        assert recon["files_by_language"] == orig["files_by_language"]
        assert recon["files_by_type"] == orig["files_by_type"]
        assert recon["commit_sha"] == orig["commit_sha"]
        assert recon["repo_name"] == orig["repo_name"]
        l3o = orig["extensions"]["l3_40_concepts_artifact"]
        l3r = recon["extensions"]["l3_40_concepts_artifact"]
        assert l3r["n_concepts"] == l3o["n_concepts"]
        assert l3r["n_cooccurrence"] == l3o["n_cooccurrence"]
        l4o = orig["extensions"]["l4_50_artifact"]
        l4r = recon["extensions"]["l4_50_artifact"]
        assert l4r["n_enrichments"] == l4o["n_enrichments"]
        assert l4r["by_kind"] == l4o["by_kind"]

    def test_manifest_is_labeled_reconstructed(self, fastapi_repaired):
        recon = json.loads((fastapi_repaired / "run_manifest.json").read_text())
        assert recon["reconstructed"] is True
        assert "cbm_repair" in recon["generated_by"]
