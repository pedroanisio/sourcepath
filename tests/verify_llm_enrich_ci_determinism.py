#!/usr/bin/env python3
"""verify_llm_enrich_ci_determinism.py — Step 10: CI-runnable determinism.

The Step 6 determinism verifier (tests/verify_llm_enrich_determinism.py)
requires a live Ollama server because run 1 populates the cache via
real model calls. This verifier does the same thing — assert warm-cache
re-emits are byte-identical — but uses a *pre-seeded* cache committed
under tests/fixtures/llm_cache/. No Ollama needed.

How it works:
  1. Materialize tests/fixtures/llm_cache/repo/ into a fresh temp dir
     and `git init` + commit. The repo contents are identical bytes
     to what was used when the cache was generated, so the
     content-addressed cache keys match.
  2. Point the L4 plugin at tests/fixtures/llm_cache/cache/ via a
     read-only Cache (separate scratch dir for any *writes*, which
     should never happen on a clean fixture).
  3. Inject a stub OllamaClient whose .chat() raises CacheMiss — so
     if any prompt actually goes to the model, the verifier fails
     loudly with a clear "the fixture is stale, regenerate it"
     message instead of trying to call a server that isn't there.
  4. Run the pipeline twice. Assert byte equality on every artifact.
  5. Assert every aggregator/enricher record reports was_cache_hit=True
     (proves we're really cache-driven, not silently degrading).
  6. Assert the manifest's enrichment counts match the fixture's
     manifest.json expectations.

This verifier is the only CI signal for the warm-cache determinism
guarantee — it runs in environments without Ollama (CI, fresh
checkouts, contributors who haven't set up the local stack yet).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "llm_cache"
FIXTURE_CACHE = FIXTURE_DIR / "cache"
FIXTURE_REPO = FIXTURE_DIR / "repo"
FIXTURE_MANIFEST = FIXTURE_DIR / "manifest.json"


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


class CacheMiss(RuntimeError):
    """Raised by the stub OllamaClient when the verifier's pipeline
    would fall through to a real model call. Means the fixture is
    stale — regenerate via tests/fixtures/llm_cache/regenerate.py."""


@dataclass
class StubOllamaClient:
    """Stub that satisfies the OllamaClient interface for L4 but
    refuses every chat. Any cache miss surfaces as a CacheMiss.

    The host field is a placeholder so `_disabled = True` logic and
    the preflight banner work; we never actually open a connection."""

    host: str = "stub://no-ollama"
    timeout: float = 60.0

    def ping(self) -> bool:
        return True  # tell preflight everything is fine

    def chat(self, model: str, system: str, user: str,
             *, seed: int = 0) -> tuple[str, float]:
        # Truncate prompt fragments so the error message stays readable.
        head = system[:60].replace("\n", " ")
        raise CacheMiss(
            f"cache miss against the committed fixture for "
            f"model={model!r}, system={head!r}…  "
            f"Did a prompt file or default model change? "
            f"Regenerate the fixture: "
            f"`python tests/fixtures/llm_cache/regenerate.py`."
        )

    def close(self) -> None:  # pragma: no cover — pipeline doesn't call this
        return None


def _materialize_repo(target: Path) -> None:
    """Copy tests/fixtures/llm_cache/repo/* to target and `git init`
    + commit. The committed repo bytes are identical to what was
    used at fixture-regeneration time so the content-addressed cache
    keys match."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for src in FIXTURE_REPO.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(FIXTURE_REPO)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.email", "fixture@cbm"], check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.name", "cbm-fixture"], check=True)
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q",
                    "-m", "init"], check=True)


def _emit_bundle(
    repo: Path, out: Path, cache_dir: Path, client: Any,
) -> tuple[dict, dict]:
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries
    from codebase_mapper.inspection.repo_source import resolve_repo_source
    from plugins import chunks_embeddings, concept_graph, llm_enrich
    from plugins.llm_enrich.cache import Cache

    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(256))
    concept_graph.register_all()
    llm_enrich.register_all(
        client=client,
        cache=Cache(cache_dir=cache_dir),
        scopes=("files", "concepts", "schemas"),
    )
    with resolve_repo_source(str(repo), "HEAD") as src:
        mapped = map_codebase(src.path, src.state)
        manifest = emit("fixture", mapped, out.resolve(),
                        emit_blobs_flag=False)
    return mapped, manifest


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    global FAIL

    if not FIXTURE_DIR.is_dir() or not FIXTURE_CACHE.is_dir():
        check("fixture directory exists", False,
              f"expected {FIXTURE_DIR}; run "
              f"tests/fixtures/llm_cache/regenerate.py first")
        print(f"\npassed: {PASS}   failed: {FAIL}")
        return 1
    check("fixture directory exists", True)

    cache_files = list(FIXTURE_CACHE.glob("*.json"))
    check(f"fixture cache contains files ({len(cache_files)})",
          len(cache_files) > 0)

    if not FIXTURE_MANIFEST.is_file():
        check("fixture manifest.json exists", False,
              f"missing {FIXTURE_MANIFEST}")
        print(f"\npassed: {PASS}   failed: {FAIL}")
        return 1
    check("fixture manifest.json exists", True)
    fixture_meta = json.loads(FIXTURE_MANIFEST.read_text())

    work = Path(tempfile.mkdtemp(prefix="verify_ci_det_"))
    try:
        # The cache the pipeline reads from. We copy the fixture into
        # a temp dir so any writes (which should not happen — every
        # entry should hit) don't leak back into the committed cache.
        cache_dir = work / "cache"
        shutil.copytree(FIXTURE_CACHE, cache_dir)

        # Materialize the repo from the fixture's source files.
        repo = work / "repo"
        _materialize_repo(repo)

        out1 = work / "out1"
        out2 = work / "out2"

        client = StubOllamaClient()

        # --- Run 1: cache hits only, no model calls ---
        try:
            mapped1, manifest1 = _emit_bundle(repo, out1, cache_dir, client)
        except CacheMiss as e:
            check("run 1 reads entirely from cache (no model calls)",
                  False, str(e))
            return 1
        except Exception:
            check("run 1 completes without unexpected error", False,
                  traceback.format_exc())
            return 1
        check("run 1 reads entirely from cache (no model calls)", True)

        # --- Run 2: same expectation ---
        try:
            mapped2, manifest2 = _emit_bundle(repo, out2, cache_dir, client)
        except CacheMiss as e:
            check("run 2 reads entirely from cache (no model calls)",
                  False, str(e))
            return 1
        except Exception:
            check("run 2 completes without unexpected error", False,
                  traceback.format_exc())
            return 1
        check("run 2 reads entirely from cache (no model calls)", True)

        # --- Every artifact byte-identical between runs ---
        for fname in ("inventory.ttl", "shapes.shacl.ttl",
                      "ontology-mapping.ttl", "embeddings.npz",
                      "embeddings_meta.json", "concepts.json",
                      "concepts_embeddings.npz",
                      "enrichments.jsonl"):
            p1 = out1 / fname
            p2 = out2 / fname
            check(f"both exist: {fname}",
                  p1.exists() and p2.exists(),
                  f"r1 exists={p1.exists()} r2 exists={p2.exists()}")
            if not (p1.exists() and p2.exists()):
                continue
            check(
                f"byte-identical run 1 vs run 2: {fname}",
                p1.read_bytes() == p2.read_bytes(),
                f"sha r1={_sha(p1)[:16]} sha r2={_sha(p2)[:16]}",
            )

        # --- Every enrichment record reports was_cache_hit=True ---
        bucket_keys = ("llm:file_summary", "llm:concept_description",
                       "llm:schema_purpose")
        total = 0
        all_hits = True
        misses: list[str] = []
        for key in bucket_keys:
            bucket = mapped1["ctx"].scratch.get(key) or {}
            for target, rec in bucket.items():
                total += 1
                if not rec.get("was_cache_hit"):
                    all_hits = False
                    misses.append(f"{key}:{target}")
        check(f"every record reports was_cache_hit=True ({total} records)",
              all_hits and total > 0,
              f"misses: {misses[:3]}; total={total}")

        # --- Manifest matches the fixture's expectations ---
        ext = (manifest1.get("extensions") or {}).get("l4_50_artifact") or {}
        expected = fixture_meta.get("expected") or {}
        check(
            f"n_enrichments matches fixture "
            f"(actual={ext.get('n_enrichments')}, "
            f"expected={expected.get('n_enrichments')})",
            ext.get("n_enrichments") == expected.get("n_enrichments"),
        )
        check(
            f"by_kind matches fixture "
            f"(actual={ext.get('by_kind')}, "
            f"expected={expected.get('by_kind')})",
            ext.get("by_kind") == expected.get("by_kind"),
        )
        check(
            "SHACL conforms",
            bool((manifest1.get("shacl_self_check") or {}).get("conforms")),
            ((manifest1.get("shacl_self_check") or {})
             .get("report_excerpt", ""))[:200],
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
