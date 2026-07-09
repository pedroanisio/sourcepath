#!/usr/bin/env python3
"""
cbm_repair.py — Tier-1 reconstruction of missing bundle sidecars.

An interrupted emit can leave a bundle directory holding only the graph
serializations (inventory.ttl, inventory.jsonld, ontology-mapping.ttl,
shapes.shacl.ttl). The graph embeds everything needed to rebuild several
of the missing sidecars. This tool reconstructs, from inventory.ttl alone:

  - run_manifest.json   : graph-derived counts, sha256 of the artifacts
                          actually present, commit SHA from the graph's
                          cbm:Commit node — and MANDATORY honest labeling
                          ("reconstructed": true, "generated_by", and an
                          explicit list of artifacts that were absent and
                          are NOT reconstructable). A reconstructed
                          manifest must never be mistakable for an
                          original run receipt; fields this tool cannot
                          derive (tool_version, shacl_self_check,
                          ast_coverage, blob counts) are omitted, never
                          fabricated.
  - enrichments.jsonl   : from the graph's cbml4:fileSummary* (and
                          cbml4:conceptDescription* / cbml4:schemaPurpose*)
                          literals, matching the record schema the
                          l4_50_artifact emitter writes.
  - concepts.json       : concept ids/labels/components from the
                          skos:Concept nodes, per_path_concepts from
                          FILE-level cbml3:lexicalizes edges, cooccurrence
                          recounted from per-path co-membership (same
                          MIN_COOCCURRENCE=2 rule as the L3 aggregator),
                          plus a reconstruction-provenance key.

STREAMING — the hard constraint. A kernel-scale inventory.ttl runs to
5 GB+; a full rdflib parse of that costs ~87 GB of RAM. This tool never
parses the whole file. rdflib's turtle serializer emits one subject block
per paragraph with the @prefix header at the top, so we stream the file
line-wise, split it into subject blocks, and feed only the interesting
blocks (file / concept / commit / repo subjects) to rdflib one at a time
— prefix header prepended — so escaped literals are decoded by a real
turtle parser, not by regex. Chunk blocks (the bulk of a big graph) are
skipped on a cheap subject-IRI check without ever touching rdflib.
The splitter is long-string aware: rdflib 7.x serializes literals that
contain newlines as triple-quoted strings which may span blank lines, so
a naive paragraph split would corrupt them. Memory stays bounded by the
largest single subject block plus the per-file/per-concept accumulators.

Known reconstruction limits (disclosed, not hidden):
  - file_summary/schema_purpose target_sha: the original hashes the
    (possibly truncated) prompt input; the graph carries only
    cbm:contentSha256. The two agree for files at or under the prompt
    budget; for longer files the reconstructed value is the content hash,
    which is disclosed in the manifest's reconstruction notes.
  - concept_description target_sha hashes the rendered prompt, which the
    graph does not carry; it is left empty ("") rather than fabricated.

ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs will always produce some
form of error. The enrichment texts recovered here are LLM-authored,
UNVERIFIED content; this tool reproduces them and their provenance
literals verbatim — it does not validate their claims, and downstream
consumers must not treat them as verified facts.

Examples:
  python3 scripts/cbm_repair.py --bundle _tmp/linux-prev-run --out /tmp/linux-repaired
  python3 scripts/cbm_repair.py --bundle _tmp/broken-bundle          # in-place additions
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable, Iterator

TOOL_ID = ("scripts/cbm_repair.py (Tier-1 graph reconstruction; "
           "authored by Claude Fable 5 via Claude Code)")

CBM = "https://codebase-mapper.example.org/cbm#"
C2 = "https://codebase-mapper.example.org/cbml2#"
C3 = "https://codebase-mapper.example.org/cbml3#"
C4 = "https://codebase-mapper.example.org/cbml4#"

# Same co-occurrence floor as plugins/concept_graph/concepts.py.
MIN_COOCCURRENCE = 2

# The artifact names a complete run can leave in a bundle directory
# (mirrors scripts/cbm_report.py's KNOWN list). Used to report, honestly,
# what was absent and NOT reconstructable.
CORE_ARTIFACTS = ("inventory.ttl", "inventory.jsonld",
                  "ontology-mapping.ttl", "shapes.shacl.ttl")
SIDECAR_ARTIFACTS = ("run_manifest.json", "enrichments.jsonl",
                     "embeddings.npz", "embeddings_meta.json",
                     "concepts.json", "concepts_embeddings.npz",
                     "ast_coverage.json", "rust_items.jsonl", "blobs/")
RECONSTRUCT_TARGETS = ("run_manifest.json", "enrichments.jsonl",
                       "concepts.json")


def log(*a):
    print("[cbm-repair]", *a, file=sys.stderr)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Streaming turtle block splitter
# ---------------------------------------------------------------------------


def _scan_line(line: str, in_long: str | None) -> str | None:
    """Advance the long-string state machine across one line.

    ``in_long`` is None (outside any long string) or the active delimiter
    (three quote characters). Escapes (backslash) are honored; short
    strings never span lines in valid turtle, so they only need to be
    skipped over while outside.
    """
    i, n = 0, len(line)
    while i < n:
        if in_long is not None:
            c = line[i]
            if c == "\\":
                i += 2
                continue
            if line.startswith(in_long, i):
                in_long = None
                i += 3
                continue
            i += 1
        else:
            c = line[i]
            if c == '"' or c == "'":
                if line.startswith(c * 3, i):
                    in_long = c * 3
                    i += 3
                else:
                    # short string: skip to its closing quote
                    i += 1
                    while i < n:
                        if line[i] == "\\":
                            i += 2
                            continue
                        if line[i] == c:
                            i += 1
                            break
                        i += 1
            else:
                i += 1
    return in_long


def split_blocks(lines: Iterable[str]) -> Iterator[str]:
    """Yield blank-line-separated turtle blocks, long-string aware.

    A blank line inside a triple-quoted literal does NOT end a block.
    Designed for rdflib-serialized turtle (one subject per paragraph,
    @prefix header paragraph first). Bounded memory: one block at a time.
    """
    buf: list[str] = []
    in_long: str | None = None
    for line in lines:
        if in_long is None and line.strip() == "":
            if buf:
                yield "".join(buf)
                buf = []
            continue
        buf.append(line)
        if in_long is not None:
            if in_long in line:
                in_long = _scan_line(line, in_long)
        elif '"""' in line or "'''" in line:
            in_long = _scan_line(line, None)
    if buf:
        yield "".join(buf)


# ---------------------------------------------------------------------------
# Reconstruction helpers
# ---------------------------------------------------------------------------


def reconstruct_components(cid: str, comps: set[str]) -> list[str]:
    """Recover the ordered component list of a compound concept.

    The aggregator names a compound ``"_".join(components)`` (plus a
    ``_compound`` suffix on collision with an atomic). RDF edges are
    unordered, so the order is re-derived by segmenting the concept id
    over the composedOf set (components may themselves contain
    underscores). Falls back to sorted order when no exact segmentation
    exists — a disclosed approximation, not silent invention.
    """
    comps = set(comps)
    if not comps:
        return []
    candidates = [cid]
    if cid.endswith("_compound"):
        candidates.append(cid[: -len("_compound")])
    for base in candidates:
        toks = base.split("_")
        if 0 < len(toks) <= 32:
            seg = _segment(toks, comps)
            if seg is not None:
                return seg
    return sorted(comps)


def _segment(toks: list[str], comps: set[str]) -> list[str] | None:
    """Longest-first DFS segmentation of toks whose segment set == comps."""
    n = len(toks)
    found: list[str] | None = None

    def dfs(i: int, acc: list[str]) -> None:
        nonlocal found
        if found is not None:
            return
        if i == n:
            if set(acc) == comps:
                found = list(acc)
            return
        for k in range(n - i, 0, -1):
            cand = "_".join(toks[i:i + k])
            if cand in comps:
                acc.append(cand)
                dfs(i + k, acc)
                acc.pop()
                if found is not None:
                    return

    dfs(0, [])
    return found


def _z_datetime(lexical: str) -> str:
    """Normalize an xsd:dateTime lexical form to the emitter's Z format."""
    s = str(lexical).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return str(lexical)  # pass the lexical through rather than guess
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ast_full_body(lit: str, key: str) -> bool:
    """True iff the astSummary JSON carries a non-null full body under
    ``key``. Fast path: the literal is a sort_keys dump, so the body key
    ("ast_json" / "cst_json") is its first key when present."""
    prefix = f'{{"{key}": '
    if lit.startswith(prefix):
        return not lit.startswith(prefix + "null")
    try:
        return json.loads(lit).get(key) is not None
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# Graph streaming pass
# ---------------------------------------------------------------------------

# (content, model, prompt_sha, generated_at) predicate tails per kind,
# mirroring plugins/llm_enrich/graph_writer.py.
_ENRICH_KINDS = {
    "file_summary": ("fileSummary", "fileSummaryModel",
                     "fileSummaryPromptSha", "fileSummaryGeneratedAt"),
    "schema_purpose": ("schemaPurpose", "schemaPurposeModel",
                       "schemaPurposePromptSha", "schemaPurposeGeneratedAt"),
    "concept_description": ("conceptDescription", "conceptDescriptionModel",
                            "conceptDescriptionPromptSha",
                            "conceptDescriptionGeneratedAt"),
}

_EDGE_COUNT_PREDICATES = {
    "import_edges": CBM + "imports",
    "import_external_edges": CBM + "importsExternal",
    "declares_dependency_edges": CBM + "declaresDependency",
    "pins_dependency_edges": CBM + "pinsDependency",
    "tests_edges": CBM + "tests",
}


class Recon:
    """Accumulators for everything the sidecars need. Size is bounded by
    the number of files + concepts, never by the number of triples."""

    def __init__(self) -> None:
        self.files: dict[str, dict] = {}          # path -> facts
        self.concepts: dict[str, dict] = {}       # cid -> record
        self.concept_descs: dict[str, dict] = {}  # cid -> enrichment bundle
        self.edge_counts: Counter[str] = Counter()
        self.commit_sha: str | None = None
        self.repo_name: str | None = None
        self.embedding_artifact: str | None = None
        self.n_chunks_skipped = 0
        self.n_blocks_parsed = 0


def _subject_iri_of(block: str) -> str | None:
    first = block.lstrip()
    if first.startswith("<"):
        end = first.find(">")
        if end > 0:
            return first[1:end]
    return None


def stream_inventory(inv_path: str) -> Recon:
    import rdflib
    from rdflib import URIRef
    from rdflib.namespace import RDF, SKOS

    U = URIRef
    rec = Recon()
    header_parts: list[str] = []
    t0 = time.time()
    last_log = t0

    def tail_of(iri: str, marker: str) -> str:
        return urllib.parse.unquote(str(iri).split(marker, 1)[-1])

    def handle(g: "rdflib.Graph", subj: "URIRef") -> None:
        types = set(g.objects(subj, RDF.type))
        for label, pred in _EDGE_COUNT_PREDICATES.items():
            rec.edge_counts[label] += sum(1 for _ in g.objects(subj, U(pred)))

        if U(CBM + "File") in types:
            path_lit = next(g.objects(subj, U(CBM + "path")), None)
            path = (str(path_lit) if path_lit is not None
                    else tail_of(str(subj), "#file/"))
            f: dict = {
                "sha": "", "language": None, "type": None,
                "concepts": set(), "ast_len": 0, "ast_full_key": None,
                "enrich": {},
            }
            sha = next(g.objects(subj, U(CBM + "contentSha256")), None)
            if sha is not None:
                f["sha"] = str(sha)
            lang = next(g.objects(subj, U(CBM + "language")), None)
            if lang is not None:
                f["language"] = str(lang)
            ftype = next(g.objects(subj, U(CBM + "type")), None)
            if ftype is not None:
                f["type"] = str(ftype).split("#")[-1]
            for o in g.objects(subj, U(C3 + "lexicalizes")):
                f["concepts"].add(tail_of(str(o), "#concept/"))
            ast = next(g.objects(subj, U(CBM + "astSummary")), None)
            if ast is not None:
                lit = str(ast)
                f["ast_len"] = len(lit)
                if f["language"] == "python":
                    f["ast_full_key"] = ("python"
                                         if _ast_full_body(lit, "ast_json")
                                         else None)
                elif f["language"] in ("typescript", "javascript"):
                    f["ast_full_key"] = ("tsjs"
                                         if _ast_full_body(lit, "cst_json")
                                         else None)
                elif f["language"] == "rust":
                    f["ast_full_key"] = ("rust"
                                         if _ast_full_body(lit, "cst_json")
                                         else None)
            for kind in ("file_summary", "schema_purpose"):
                bundle = _read_enrich(g, subj, U, kind)
                if bundle:
                    f["enrich"][kind] = bundle
            rec.files[path] = f
            return

        if SKOS.Concept in types:
            cid = tail_of(str(subj), "#concept/")
            c: dict = {"label": cid, "alt_labels": [], "components": [],
                       "frequency": 0, "file_count": 0, "embedding_row": None}
            comps: set[str] = set()
            for p, o in g.predicate_objects(subj):
                pn = str(p)
                if pn == str(SKOS.prefLabel):
                    c["label"] = str(o)
                elif pn == str(SKOS.altLabel):
                    c["alt_labels"].append(str(o))
                elif pn == C3 + "composedOf":
                    comps.add(tail_of(str(o), "#concept/"))
                elif pn == C3 + "occurrenceCount":
                    c["frequency"] = int(o)
                elif pn == C3 + "fileCount":
                    c["file_count"] = int(o)
                elif pn == C3 + "embeddingRow":
                    c["embedding_row"] = int(o)
                elif pn == C3 + "embeddingArtifact":
                    rec.embedding_artifact = str(o)
                elif pn == C3 + "conceptKind":
                    c["kind"] = str(o)
                elif pn == C3 + "broaderCollection":
                    c["broader"] = tail_of(str(o), "#collection/")
            c["alt_labels"] = sorted(c["alt_labels"])
            c["components"] = reconstruct_components(cid, comps)
            rec.concepts[cid] = c
            bundle = _read_enrich(g, subj, U, "concept_description")
            if bundle:
                rec.concept_descs[cid] = bundle
            return

        if U(CBM + "Commit") in types:
            sha = next(g.objects(subj, U(CBM + "commitSha")), None)
            if sha is not None:
                rec.commit_sha = str(sha)
            return

        if U(CBM + "Repository") in types:
            rec.repo_name = tail_of(str(subj), "#repo/")
            if rec.commit_sha is None:
                at = next(g.objects(subj, U(CBM + "atCommit")), None)
                if at is not None:
                    rec.commit_sha = tail_of(str(at), "#commit/")
            return

    def lines(path: str) -> Iterator[str]:
        with open(path, "r", encoding="utf-8") as fh:
            yield from fh

    for block in split_blocks(lines(inv_path)):
        stripped = block.lstrip()
        if stripped.startswith("@prefix") or stripped.startswith("@base") \
                or stripped.lower().startswith("prefix ") \
                or stripped.lower().startswith("base "):
            header_parts.append(block)
            continue
        iri = _subject_iri_of(block)
        if iri is not None and "#chunk/" in iri:
            rec.n_chunks_skipped += 1
            continue
        g = rdflib.Graph()
        try:
            g.parse(data="".join(header_parts) + "\n" + block,
                    format="turtle")
        except Exception as e:  # a malformed block is a hard fact worth surfacing
            raise SystemExit(
                f"[cbm-repair] failed to parse subject block "
                f"({(iri or block.splitlines()[0])[:120]}): {e}")
        rec.n_blocks_parsed += 1
        for subj in set(g.subjects()):
            handle(g, subj)
        now = time.time()
        if now - last_log > 15:
            last_log = now
            log(f"…streaming: {rec.n_blocks_parsed:,} blocks parsed, "
                f"{rec.n_chunks_skipped:,} chunk blocks skipped, "
                f"{len(rec.files):,} files, {len(rec.concepts):,} concepts "
                f"({now - t0:.0f}s)")

    log(f"stream done: {rec.n_blocks_parsed:,} blocks parsed, "
        f"{rec.n_chunks_skipped:,} chunk blocks skipped, "
        f"{len(rec.files):,} files, {len(rec.concepts):,} concepts "
        f"({time.time() - t0:.1f}s)")
    return rec


def _read_enrich(g, subj, U, kind: str) -> dict | None:
    p_text, p_model, p_sha, p_dt = (U(C4 + t) for t in _ENRICH_KINDS[kind])
    text = next(g.objects(subj, p_text), None)
    if text is None:
        return None
    bundle = {"text": str(text), "model": "", "prompt_sha": "",
              "generated_at": ""}
    model = next(g.objects(subj, p_model), None)
    if model is not None:
        bundle["model"] = str(model)
    sha = next(g.objects(subj, p_sha), None)
    if sha is not None:
        bundle["prompt_sha"] = str(sha)
    dt = next(g.objects(subj, p_dt), None)
    if dt is not None:
        bundle["generated_at"] = _z_datetime(str(dt))
    return bundle


# ---------------------------------------------------------------------------
# Sidecar builders
# ---------------------------------------------------------------------------


def build_enrichment_rows(rec: Recon) -> list[dict]:
    """Flatten to the exact sidecar row shape of the l4_50_artifact
    emitter (plugins/llm_enrich/artifact.py): sorted by (kind, target),
    seven fixed keys."""
    rows: list[dict] = []
    for path, f in rec.files.items():
        for kind in ("file_summary", "schema_purpose"):
            b = f["enrich"].get(kind)
            if not b or not b["text"]:
                continue
            rows.append({
                "target": path,
                "kind": kind,
                "text": b["text"],
                "model": b["model"],
                "prompt_sha": b["prompt_sha"],
                # The graph does not carry the original prompt-input hash;
                # cbm:contentSha256 is the file's honest content identity.
                "target_sha": f["sha"],
                "generated_at": b["generated_at"],
            })
    for cid, b in rec.concept_descs.items():
        if not b["text"]:
            continue
        rows.append({
            "target": cid,
            "kind": "concept_description",
            "text": b["text"],
            "model": b["model"],
            "prompt_sha": b["prompt_sha"],
            # Original hashes the rendered prompt — unrecoverable; left
            # empty rather than fabricated.
            "target_sha": "",
            "generated_at": b["generated_at"],
        })
    rows.sort(key=lambda r: (r["kind"], r["target"]))
    return rows


def serialize_enrichments(rows: list[dict]) -> str:
    return "".join(
        json.dumps(r, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False) + "\n"
        for r in rows)


def build_concepts_payload(rec: Recon, generated_at: str) -> dict:
    per_path = {path: sorted(f["concepts"])
                for path, f in rec.files.items() if f["concepts"]}

    pair_counts: Counter[tuple[str, str]] = Counter()
    for names in per_path.values():
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pair_counts[(a, b)] += 1
    cooccurrence = [[a, b, int(c)]
                    for (a, b), c in sorted(pair_counts.items())
                    if c >= MIN_COOCCURRENCE]

    with_rows = sorted(
        ((cid, c["embedding_row"]) for cid, c in rec.concepts.items()
         if c["embedding_row"] is not None),
        key=lambda x: x[1])
    embedding_ids = [cid for cid, _ in with_rows] or None

    return {
        "concepts": {cid: dict(c) for cid, c in sorted(rec.concepts.items())},
        "per_path_concepts": per_path,
        "cooccurrence": cooccurrence,
        "concept_embeddings_artifact": (
            rec.embedding_artifact if embedding_ids else None),
        "concept_embedding_ids": embedding_ids,
        "reconstruction": {
            "reconstructed": True,
            "generated_by": TOOL_ID,
            "generated_at": generated_at,
            "source_artifact": "inventory.ttl",
            "notes": [
                "Reconstructed from skos:Concept nodes and file-level "
                "cbml3:lexicalizes edges of inventory.ttl.",
                "cooccurrence recounted from per-path co-membership with "
                f"MIN_COOCCURRENCE={MIN_COOCCURRENCE} (the L3 aggregator's "
                "rule).",
                "Compound component order re-derived by segmenting the "
                "concept id over its composedOf set; sorted fallback when "
                "no exact segmentation exists.",
            ],
        },
    }


def build_manifest(rec: Recon, bundle: str, generated_at: str,
                   written: dict[str, dict], will_not_write: list[str],
                   inv_sha: str) -> dict:
    counts: dict[str, int] = {
        "files": len(rec.files),
        **{k: int(rec.edge_counts.get(k, 0))
           for k in _EDGE_COUNT_PREDICATES},
        "ast_summary_total_bytes": sum(
            f["ast_len"] for f in rec.files.values()),
        "ast_full_bodies_python": sum(
            1 for f in rec.files.values() if f["ast_full_key"] == "python"),
        "ast_full_bodies_tsjs": sum(
            1 for f in rec.files.values() if f["ast_full_key"] == "tsjs"),
        "ast_full_bodies_rust": sum(
            1 for f in rec.files.values() if f["ast_full_key"] == "rust"),
    }

    by_lang = Counter((f["language"] or "(none)")
                      for f in rec.files.values())
    by_type = Counter(f["type"] for f in rec.files.values()
                      if f["type"] is not None)

    artifacts = {}
    for name in CORE_ARTIFACTS:
        p = os.path.join(bundle, name)
        if os.path.exists(p):
            artifacts[name] = {
                "path": name,
                "sha256": inv_sha if name == "inventory.ttl"
                else sha256_file(p),
                "size_bytes": os.path.getsize(p),
            }

    reconstructed_names = ["run_manifest.json"] + sorted(written)
    absent = []
    for name in CORE_ARTIFACTS + SIDECAR_ARTIFACTS:
        if name in reconstructed_names:
            continue
        p = os.path.join(bundle, name.rstrip("/"))
        present = os.path.isdir(p) if name.endswith("/") else os.path.exists(p)
        if not present:
            absent.append(name)
    absent += sorted(will_not_write)

    manifest: dict = {
        "reconstructed": True,
        "generated_by": TOOL_ID,
        "generated_at": generated_at,
        "notice": (
            "RECONSTRUCTED ARTIFACT — this is NOT an original run receipt. "
            "All values were re-derived from inventory.ttl; fields the "
            "graph cannot support (tool_version, shacl_self_check, "
            "ast_coverage, blob counts) are omitted, not fabricated."),
        "reconstruction": {
            "tool": "cbm_repair.py",
            "tier": 1,
            "source_artifact": "inventory.ttl",
            "source_sha256": inv_sha,
            "reconstructed_artifacts": sorted(reconstructed_names),
            "absent_not_reconstructable": sorted(set(absent)),
            "notes": [
                "counts are re-derived from graph triples, not from the "
                "original pipeline run.",
                "enrichments.jsonl target_sha for file-targeted kinds is "
                "the file's cbm:contentSha256; the original hashed the "
                "(possibly truncated) prompt input, which the graph does "
                "not carry. concept_description target_sha is left empty "
                "for the same reason.",
                "Enrichment texts are LLM-authored, UNVERIFIED content "
                "reproduced verbatim from the graph (PALS's LAW: absence "
                "of output verification is a design defect; treat as "
                "untrusted).",
            ],
        },
        "repo_name": rec.repo_name,
        "commit_sha": rec.commit_sha,
        "counts": counts,
        "files_by_language": dict(sorted(by_lang.items(),
                                         key=lambda x: (-x[1], x[0]))),
        "files_by_type": dict(sorted(by_type.items(),
                                     key=lambda x: (-x[1], x[0]))),
        "artifacts": artifacts,
    }

    extensions: dict = {}
    if "concepts.json" in written:
        n_co = written["concepts.json"].pop("_n_cooccurrence")
        extensions["l3_40_concepts_artifact"] = {
            "reconstructed": True,
            "n_concepts": len(rec.concepts),
            "n_cooccurrence": n_co,
            "concept_centroids_available": rec.embedding_artifact is not None,
            "files": {"concepts.json": written["concepts.json"]},
        }
    if "enrichments.jsonl" in written:
        by_kind = written["enrichments.jsonl"].pop("_by_kind")
        n_enrich = written["enrichments.jsonl"].pop("_n")
        extensions["l4_50_artifact"] = {
            "reconstructed": True,
            "n_enrichments": n_enrich,
            "by_kind": by_kind,
            "files": {"enrichments.jsonl": written["enrichments.jsonl"]},
        }
    if extensions:
        manifest["extensions"] = extensions
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cbm_repair.py",
        description=("Tier-1 reconstruction of run_manifest.json, "
                     "enrichments.jsonl, and concepts.json from a bundle's "
                     "inventory.ttl (streaming; never loads the full graph)."))
    p.add_argument("--bundle", required=True,
                   help="bundle directory holding inventory.ttl")
    p.add_argument("--out", default=None,
                   help=("output directory (default: the bundle directory, "
                         "in-place additions). Existing artifacts are NEVER "
                         "overwritten."))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    bundle = args.bundle.rstrip("/")
    out_dir = args.out or bundle

    inv = os.path.join(bundle, "inventory.ttl")
    if not os.path.isfile(inv):
        log(f"ERROR: {inv} not found — nothing to reconstruct from.")
        return 2

    existing = [n for n in RECONSTRUCT_TARGETS
                if os.path.exists(os.path.join(out_dir, n))]
    if existing:
        log("ERROR: refusing to overwrite existing artifact(s) in "
            f"{out_dir}: {', '.join(existing)}. "
            "Point --out at an empty directory instead.")
        return 2

    os.makedirs(out_dir, exist_ok=True)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    log(f"bundle: {bundle}")
    log(f"out:    {out_dir}")

    log("hashing inventory.ttl …")
    inv_sha = sha256_file(inv)
    rec = stream_inventory(inv)

    if rec.commit_sha is None:
        log("WARNING: no cbm:Commit node found in the graph; "
            "commit_sha will be null in the reconstructed manifest.")

    written: dict[str, dict] = {}
    will_not_write: list[str] = []

    # --- enrichments.jsonl ---
    rows = build_enrichment_rows(rec)
    if rows:
        path = os.path.join(out_dir, "enrichments.jsonl")
        data = serialize_enrichments(rows).encode("utf-8")
        with open(path, "xb") as f:   # 'x': hard guarantee against overwrite
            f.write(data)
        by_kind = Counter(r["kind"] for r in rows)
        written["enrichments.jsonl"] = {
            "path": "enrichments.jsonl",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "_by_kind": dict(sorted(by_kind.items())),
            "_n": len(rows),
        }
        log(f"enrichments.jsonl: {len(rows):,} records "
            f"({dict(sorted(by_kind.items()))})")
    else:
        will_not_write.append("enrichments.jsonl")
        log("no cbml4 enrichment literals in the graph — "
            "enrichments.jsonl not written.")

    # --- concepts.json ---
    if rec.concepts:
        payload = build_concepts_payload(rec, generated_at)
        path = os.path.join(out_dir, "concepts.json")
        data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(
            "utf-8")
        with open(path, "xb") as f:
            f.write(data)
        written["concepts.json"] = {
            "path": "concepts.json",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "_n_cooccurrence": len(payload["cooccurrence"]),
        }
        log(f"concepts.json: {len(rec.concepts):,} concepts, "
            f"{len(payload['per_path_concepts']):,} paths, "
            f"{len(payload['cooccurrence']):,} cooccurrence entries")
    else:
        will_not_write.append("concepts.json")
        log("no skos:Concept nodes in the graph — concepts.json not written.")

    # --- run_manifest.json ---
    manifest = build_manifest(rec, bundle, generated_at, written,
                              will_not_write, inv_sha)
    path = os.path.join(out_dir, "run_manifest.json")
    with open(path, "x", encoding="utf-8") as f:
        f.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    log(f"run_manifest.json: files={manifest['counts']['files']:,} "
        f"commit={str(manifest['commit_sha'])[:12]} "
        f"repo={manifest['repo_name']}")
    log("done — reconstructed artifacts are labeled reconstructed=true "
        "and are not original run receipts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
