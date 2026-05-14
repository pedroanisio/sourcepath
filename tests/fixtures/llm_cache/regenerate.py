#!/usr/bin/env python3
"""regenerate.py — produce the committed L4 cache fixture for CI determinism.

Run this script once when:
  - A prompt file's SHA changes (e.g., file_summary.v1.txt edit, or a
    v1 → v2 bump).
  - The default model changes (qwen2.5-coder:7b → something else).
  - The cache schema version bumps (CACHE_SCHEMA_VERSION 1 → 2).
  - A new enrichment kind is added that the fixture should cover.

Otherwise, the fixture is stable and committed; CI uses it as-is.

Usage:
  ollama serve &
  ollama pull qwen2.5-coder:7b
  .venv/bin/python tests/fixtures/llm_cache/regenerate.py

What it does:
  1. Build a tiny git repo on disk (REPO_DIR) matching ``repo_files``.
  2. Run the L4 pipeline against it with a fresh temp cache.
  3. Copy the cache files into ``tests/fixtures/llm_cache/cache/``.
  4. Write ``manifest.json`` recording the model, prompt SHAs, and
     enrichment-count expectations the CI verifier asserts.

The committed fixture is small (~3-5 KB per cache file; ~30 KB total).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# Resolve cbm so the script works regardless of cwd.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "llm_cache"
CACHE_DIR = FIXTURE_DIR / "cache"
REPO_DIR = FIXTURE_DIR / "repo"


# The tiny repo whose enrichments live in the committed cache. Kept
# minimal so the cache stays small. Three Python files exercise the
# file_summary kind; cross-imports give the L3 cooccurrence threshold
# enough material to produce typed concepts (for concept_description);
# the static/schemas/event.xsd exercises schema_purpose.
REPO_FILES: dict[str, str] = {
    "a.py": (
        '"""Module A: Behavior + Contract."""\n'
        'from b import LoginIntent\n'
        'class UserBehavior:\n'
        '    def authenticate(self, t): return self.contract(t)\n'
        '    def contract(self, t): return bool(t)\n'
    ),
    "b.py": (
        '"""Module B: Behavior + Intent."""\n'
        'from c import AdminBehavior\n'
        'class LoginIntent:\n'
        '    def behavior(self): return "login"\n'
        '    def contract(self): return True\n'
    ),
    "c.py": (
        '"""Module C: Behavior + Contract."""\n'
        'class AdminBehavior:\n'
        '    def authenticate(self): pass\n'
        '    def contract(self): pass\n'
    ),
    "static/schemas/event.xsd": (
        '<?xml version="1.0"?>\n'
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">\n'
        '  <xs:element name="event"/>\n'
        '</xs:schema>\n'
    ),
}

# Files inside ``repo/`` that the CI verifier writes on disk before
# running the pipeline. The fixture commits the *contents* — we
# materialize them at verifier time so the verifier doesn't need to
# touch the network or build a git repo each run.


def _build_repo(target: Path) -> None:
    """Write the fixture's source files to ``target`` and commit them
    as a single-commit git repo. The committed cache key includes
    the file content sha; rebuilding the repo at runtime with the
    same content produces the same SHAs."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for rel, content in REPO_FILES.items():
        p = target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.email", "fixture@cbm"], check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.name", "cbm-fixture"], check=True)
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q",
                    "-m", "init"], check=True)


def main() -> int:
    from plugins import chunks_embeddings, concept_graph, llm_enrich
    from plugins.llm_enrich.cache import Cache
    from plugins.llm_enrich.client import OllamaClient
    from codebase_mapper.emission.application.emit_bundle import emit
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries
    from codebase_mapper.inspection.repo_source import resolve_repo_source

    # Preflight: Ollama must be reachable.
    client = OllamaClient()
    if not client.ping():
        print(
            f"ERROR: Ollama unreachable at {client.host}.\n"
            f"This script needs a live Ollama server to populate the "
            f"cache fixture. Start ollama, pull qwen2.5-coder:7b, and "
            f"re-run.",
            file=sys.stderr,
        )
        return 1

    # Build the fixture repo in a temp location, then move into place.
    # The temp scratch is for the bundle output; the cache lands in a
    # *separate* dir we copy from after the run.
    workdir = Path(tempfile.mkdtemp(prefix="llm_cache_fixture_"))
    try:
        fixture_repo = workdir / "repo"
        _build_repo(fixture_repo)
        scratch_cache = workdir / "scratch_cache"
        out = workdir / "out"

        reset_registries()
        chunks_embeddings.register_all(
            chunks_embeddings.DeterministicHashBackend(256))
        concept_graph.register_all()
        llm_enrich.register_all(
            client=client,
            cache=Cache(cache_dir=scratch_cache),
            scopes=("files", "concepts", "schemas"),
        )

        with resolve_repo_source(str(fixture_repo), "HEAD") as repo:
            mapped = map_codebase(repo.path, repo.state)
            manifest = emit("fixture", mapped, out.resolve(),
                            emit_blobs_flag=False)

        # Sanity-check before committing the fixture.
        ext = (manifest.get("extensions") or {}).get("l4_50_artifact") or {}
        if not ext.get("n_enrichments"):
            print("ERROR: zero enrichments produced — Ollama may have failed silently.",
                  file=sys.stderr)
            return 1
        if not manifest["shacl_self_check"]["conforms"]:
            print("ERROR: SHACL did not conform on the fresh bundle.",
                  file=sys.stderr)
            return 1

        # Replace the committed cache + repo with the freshly produced ones.
        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR)
        CACHE_DIR.mkdir(parents=True)
        for f in scratch_cache.iterdir():
            shutil.copy2(f, CACHE_DIR / f.name)

        # The repo source files get committed too — the CI verifier
        # writes them out at runtime to a fresh git repo, ensuring
        # identical content hashes.
        if REPO_DIR.exists():
            shutil.rmtree(REPO_DIR)
        REPO_DIR.mkdir(parents=True)
        for rel, content in REPO_FILES.items():
            p = REPO_DIR / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)

        # Manifest the CI verifier uses to assert expectations.
        meta = {
            "model": "qwen2.5-coder:7b",
            "scopes": ["files", "concepts", "schemas"],
            "expected": {
                "n_enrichments": ext.get("n_enrichments"),
                "by_kind": ext.get("by_kind", {}),
                "shacl_conforms": True,
            },
            "regenerated_with": {
                "tool_version": manifest.get("tool_version"),
                "vocabulary_version": manifest.get("vocabulary_version"),
            },
            "instructions": (
                "Regenerate with `python tests/fixtures/llm_cache/"
                "regenerate.py` when a prompt SHA, default model, or "
                "cache schema version changes. See verify_llm_enrich_"
                "ci_determinism.py for the consumer-side contract."
            ),
        }
        (FIXTURE_DIR / "manifest.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n"
        )

        n_cache_files = len(list(CACHE_DIR.iterdir()))
        print(f"OK: regenerated {n_cache_files} cache files "
              f"covering {ext.get('n_enrichments')} enrichments "
              f"({ext.get('by_kind', {})}).")
        print(f"     fixture: {FIXTURE_DIR}")
        print(f"     cache:   {CACHE_DIR}  ({n_cache_files} files)")
        print(f"     repo:    {REPO_DIR}   ({len(REPO_FILES)} files)")
        print()
        print("Commit the changes under tests/fixtures/llm_cache/")
        print("and run `python tests/verify_llm_enrich_ci_determinism.py`")
        print("to confirm the verifier passes against the new fixture.")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
