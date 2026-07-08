#!/usr/bin/env python3
"""verify_llm_enrich_determinism.py — Step 6 warm-cache determinism.

The plan's architectural commitment #5: "Deterministic provenance,
not deterministic generation. We do not promise that the model
produces the same string twice. We promise that *given a populated
cache*, two consecutive runs over the same commit produce
byte-identical bundles."

This verifier enforces that promise.

Method:
  Run 1 (cold)  — cache empty, real model calls, populate cache.
  Run 2 (warm)  — cache full, zero model calls, reads from cache.
  Run 3 (warm)  — identical to run 2.

Run 1 vs Run 2 is allowed to differ: the cold call generates a fresh
``generated_at`` timestamp; the warm hit reads it back from the
cache. The cached value is what flows into the bundle. Run 2 vs Run 3
must be byte-identical — that's the warm-cache determinism guarantee.

What's checked:

  1. Every L4-affected artifact is byte-identical between runs 2 and 3:
       - inventory.ttl       (cbml4:* triples + provenance dateTimes)
       - shapes.shacl.ttl    (no run-to-run variation expected anyway)
       - enrichments.jsonl   (sorted, sort_keys=True JSON)
  2. The non-L4 artifacts (embeddings, concepts.json, etc.) are also
     byte-identical between runs 2 and 3 (catches regressions in the
     host pipeline that L4 might inadvertently trigger).
  3. The manifest's L4 fragment between runs 2 and 3:
       - n_enrichments identical
       - by_kind identical
       - sidecar sha256 identical
  4. Every aggregator/enricher record in run 2 reports
     ``was_cache_hit=True`` (proves the cache is doing its job and
     the determinism isn't coincidental).
  5. Same set of (target, kind) pairs in run 2 as in run 3.

Skips cleanly when Ollama is unreachable.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

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


def _resolve_enrich_model() -> str | None:
    """Model the pipeline will use here, or None if it cannot enrich.
    Model-aware guard (see plugins/llm_enrich/model_resolver.py)."""
    try:
        from plugins.llm_enrich import resolve_model
        return resolve_model(OllamaClient(timeout=5.0))
    except Exception:
        return None


RESOLVED_MODEL = _resolve_enrich_model()


def build_fixture(target: Path) -> None:
    """Same shape as verify_llm_enrich_aggregator.py: rich enough for
    L3 typed-concept emission so all three L4 kinds fire."""
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.name", "t"], check=True)
    (target / "a.py").write_text(
        '"""Module A: Behavior + Contract."""\n'
        'class UserBehavior:\n'
        '    def authenticate(self, t): return self.contract(t)\n'
        '    def contract(self, t): return bool(t)\n'
    )
    (target / "b.py").write_text(
        '"""Module B: Intent + Behavior."""\n'
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
    *, scopes: tuple[str, ...],
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
    register_all(client=OllamaClient(), cache=Cache(cache_dir=cache_dir),
                 scopes=scopes)
    with resolve_repo_source(str(fixture), "HEAD") as repo:
        mapped = map_codebase(repo.path, repo.state)
        manifest = emit("fixture", mapped, out.resolve(),
                        emit_blobs_flag=False)
    return mapped, manifest


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strip_generated_at(manifest_path: Path) -> dict:
    """Manifest minus the wall-clock field that legitimately differs
    even on warm runs (the emit() timestamp, not the LLM one)."""
    m = json.loads(manifest_path.read_text())
    m.pop("generated_at", None)
    return m


def main() -> int:
    global FAIL

    if RESOLVED_MODEL is None:
        for name in ("test_warm_cache_byte_identical",
                     "test_run2_all_cache_hits",
                     "test_manifest_l4_fragment_stable"):
            skip(name, "no suitable qwen2.5-coder model installed")
        print(f"\npassed: {PASS}   failed: {FAIL}   skipped: {SKIP}")
        return 0

    work = Path(tempfile.mkdtemp(prefix="verify_step6_det_"))
    try:
        fixture = work / "fixture"
        build_fixture(fixture)
        cache_dir = work / "cache"  # shared across all three runs

        scopes = ("files", "concepts", "schemas")
        out1 = work / "run1"
        out2 = work / "run2"
        out3 = work / "run3"

        # Run 1: populate the cache.
        mapped1, manifest1 = _emit_bundle(fixture, out1, cache_dir,
                                          scopes=scopes)
        # Run 2: warm cache, all hits.
        mapped2, manifest2 = _emit_bundle(fixture, out2, cache_dir,
                                          scopes=scopes)
        # Run 3: warm cache, all hits. Bytes must equal run 2.
        mapped3, manifest3 = _emit_bundle(fixture, out3, cache_dir,
                                          scopes=scopes)

        # --- 1. Every artifact byte-identical between run 2 and run 3 ---
        for fname in ("inventory.ttl", "shapes.shacl.ttl",
                      "ontology-mapping.ttl", "embeddings.npz",
                      "embeddings_meta.json", "concepts.json",
                      "concepts_embeddings.npz",
                      "enrichments.jsonl"):
            p2 = out2 / fname
            p3 = out3 / fname
            check(f"both exist: {fname}",
                  p2.exists() and p3.exists(),
                  f"r2 exists={p2.exists()} r3 exists={p3.exists()}")
            if not (p2.exists() and p3.exists()):
                continue
            a, b = p2.read_bytes(), p3.read_bytes()
            check(
                f"byte-identical run 2 vs run 3: {fname}",
                a == b,
                f"sha r2={_sha(p2)[:16]} sha r3={_sha(p3)[:16]} "
                f"(diff {abs(len(a)-len(b))} bytes)",
            )

        # --- 2. Manifest L4 fragment stable across warm runs ---
        f2 = (manifest2.get("extensions") or {}).get("l4_50_artifact")
        f3 = (manifest3.get("extensions") or {}).get("l4_50_artifact")
        check("L4 fragment present in both warm runs",
              isinstance(f2, dict) and isinstance(f3, dict))
        if isinstance(f2, dict) and isinstance(f3, dict):
            check("n_enrichments identical (run 2 vs run 3)",
                  f2.get("n_enrichments") == f3.get("n_enrichments"),
                  f"r2={f2.get('n_enrichments')} r3={f3.get('n_enrichments')}")
            check("by_kind identical (run 2 vs run 3)",
                  f2.get("by_kind") == f3.get("by_kind"),
                  f"r2={f2.get('by_kind')} r3={f3.get('by_kind')}")
            # Sidecar sha256 reported in the manifest must match the
            # actual file sha (and be identical across the two warm
            # runs). The byte-identity check above already covers the
            # latter, but this catches a manifest-vs-disk drift.
            sha2 = ((f2.get("files") or {}).get("enrichments.jsonl") or {}
                    ).get("sha256")
            sha3 = ((f3.get("files") or {}).get("enrichments.jsonl") or {}
                    ).get("sha256")
            check(
                "manifest's enrichments.jsonl sha matches across warm runs",
                sha2 == sha3 and sha2 == _sha(out2 / "enrichments.jsonl"),
                f"r2_manifest={sha2[:16] if sha2 else None} "
                f"r3_manifest={sha3[:16] if sha3 else None} "
                f"r2_disk={_sha(out2 / 'enrichments.jsonl')[:16]}",
            )

        # --- 3. Run 2 reports every record as a cache hit ---
        # Walk all three ctx.scratch buckets from run 2.
        bucket_keys = ("llm:file_summary", "llm:concept_description",
                       "llm:schema_purpose")
        total = 0
        all_hits = True
        misses: list[str] = []
        for key in bucket_keys:
            bucket = mapped2["ctx"].scratch.get(key) or {}
            for target, rec in bucket.items():
                total += 1
                if not rec.get("was_cache_hit"):
                    all_hits = False
                    misses.append(f"{key}:{target}")
        check(f"run 2 records: every one is a cache hit (total {total})",
              all_hits and total > 0,
              f"misses: {misses[:5]}; total={total}")

        # --- 4. Same (target, kind) set between runs 2 and 3 ---
        def keyset(mapped: dict) -> set:
            out = set()
            for key in bucket_keys:
                bucket = mapped["ctx"].scratch.get(key) or {}
                for target in bucket:
                    out.add((key, target))
            return out

        check("same (kind, target) set in run 2 and run 3",
              keyset(mapped2) == keyset(mapped3))

        # --- 5. Run 1 vs Run 2: same shape (counts) but the cache
        # populated between them ---
        f1 = (manifest1.get("extensions") or {}).get("l4_50_artifact") or {}
        check("cold run reports same n_enrichments as warm run",
              f1.get("n_enrichments") == (f2 or {}).get("n_enrichments"),
              f"cold={f1.get('n_enrichments')} warm={f2.get('n_enrichments')}")

    except Exception:
        FAIL += 1
        print("  FAIL  unexpected exception in main()")
        traceback.print_exc()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\npassed: {PASS}   failed: {FAIL}   skipped: {SKIP}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
