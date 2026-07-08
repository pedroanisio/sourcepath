#!/usr/bin/env python3
"""verify_llm_enrich_file_summary.py — Step 3 end-to-end functional test.

Drives the actual pipeline with the L4 enricher opted in to ``files``,
against a small git fixture, and asserts that ``file_summary``
enrichments end up on ``ctx.scratch["llm:file_summary"]`` with the
expected shape. A second run verifies the cache hits.

Requires Ollama to be reachable (skips cleanly otherwise — same
pattern as ``verify_llm_enrich_cache.py`` for the client tests).

What's checked:

  1. With L4 registered AND ``scopes=("files",)``, every source file
     in the fixture produces a non-empty ``text`` summary in
     ``ctx.scratch["llm:file_summary"]``.
  2. Each record carries the expected fields: ``text``, ``model``,
     ``prompt_sha``, ``target_sha``, ``generated_at``, ``was_cache_hit``,
     and the cache schema version ``v``.
  3. Non-source files (README.md, etc.) are NOT enriched.
  4. With the *same cache* and a second pipeline run, every record
     reports ``was_cache_hit=True`` and the ``text`` field is
     byte-identical to the first run.
  5. With ``scopes=None`` (default), nothing is enriched even when
     a client + cache are configured — preserves the Step 1 back-compat
     anchor on the default registration.
  6. With Ollama unreachable, the enricher disables itself silently
     and leaves ``ctx.scratch["llm:file_summary"]`` either absent or
     empty — no exceptions escape.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

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
    """Model the pipeline will actually use here, or None if it cannot
    enrich. Model-aware guard: a reachable server with no suitable
    qwen2.5-coder tag must skip, not fail (see model_resolver.py)."""
    try:
        from plugins.llm_enrich import resolve_model
        return resolve_model(OllamaClient(timeout=5.0))
    except Exception:
        return None


RESOLVED_MODEL = _resolve_enrich_model()


def build_fixture(target: Path) -> None:
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(target), "config",
                    "user.name", "t"], check=True)
    (target / "auth.py").write_text(
        '"""User authentication helpers."""\n\n'
        'class UserAuthenticator:\n'
        '    """Authenticates users against a token store."""\n'
        '    def __init__(self, tokens):\n'
        '        self.tokens = set(tokens)\n'
        '    def is_valid(self, token: str) -> bool:\n'
        '        return token in self.tokens\n'
    )
    (target / "README.md").write_text(
        "# Fixture for verify_llm_enrich_file_summary\n"
    )
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q",
                    "-m", "init"], check=True)


def _run_pipeline_with_l4(
    fixture: Path, *, cache_dir: Path,
    scopes: tuple[str, ...] | None,
    client_host: str | None = None,
) -> dict:
    """Drive map_codebase() with the L4 enricher registered. Returns
    the ctx so the caller can inspect ``scratch``."""
    # Local imports + reset to avoid touching the parent process's
    # registries (the host's _builtins.py registers analyzers/resolvers
    # at import; we need those *and* L4).
    from codebase_mapper.inspection.pipeline import map_codebase
    from codebase_mapper.shared_kernel.extensions import reset_registries
    from codebase_mapper.inspection.repo_source import resolve_repo_source

    reset_registries()
    client = OllamaClient(host=client_host) if client_host else OllamaClient()
    cache = Cache(cache_dir=cache_dir)
    register_all(client=client, cache=cache, scopes=scopes)
    with resolve_repo_source(str(fixture), "HEAD") as repo:
        mapped = map_codebase(repo.path, repo.state)
    return mapped


def test_opt_in_produces_summaries(work: Path) -> None:
    fixture = work / "fix1"
    build_fixture(fixture)
    cache_dir = work / "cache1"
    mapped = _run_pipeline_with_l4(fixture, cache_dir=cache_dir,
                                   scopes=("files",))
    bucket = mapped["ctx"].scratch.get("llm:file_summary", {})

    check("at least one source file enriched",
          "auth.py" in bucket,
          f"got keys: {sorted(bucket)}")

    if "auth.py" not in bucket:
        return  # downstream checks would crash; bail loud

    rec = bucket["auth.py"]
    expected_keys = {"v", "kind", "model", "prompt_sha", "target_sha",
                     "text", "generated_at", "was_cache_hit"}
    missing = expected_keys - set(rec.keys())
    check("record carries every expected field",
          not missing, f"missing: {missing}")

    check("text is non-empty",
          isinstance(rec.get("text"), str) and len(rec["text"].strip()) > 0,
          f"text={rec.get('text')!r}")
    check("model field matches resolved pipeline model",
          rec.get("model") == RESOLVED_MODEL,
          f"got {rec.get('model')!r}, expected {RESOLVED_MODEL!r}")
    check("first run was a cache miss",
          rec.get("was_cache_hit") is False,
          f"hit={rec.get('was_cache_hit')}")

    check("non-source README.md was NOT enriched",
          "README.md" not in bucket,
          f"README.md unexpectedly enriched: {bucket.get('README.md')}")


def test_second_run_is_all_hits(work: Path) -> None:
    fixture = work / "fix2"
    build_fixture(fixture)
    cache_dir = work / "cache2"
    first = _run_pipeline_with_l4(fixture, cache_dir=cache_dir,
                                  scopes=("files",))
    second = _run_pipeline_with_l4(fixture, cache_dir=cache_dir,
                                   scopes=("files",))
    a = first["ctx"].scratch.get("llm:file_summary", {})
    b = second["ctx"].scratch.get("llm:file_summary", {})

    check("same set of files enriched across runs",
          set(a) == set(b),
          f"first: {sorted(a)}\nsecond: {sorted(b)}")
    if set(a) != set(b):
        return

    all_hits = all(r.get("was_cache_hit") for r in b.values())
    check("every second-run record is a cache hit", all_hits,
          ", ".join(f"{p}:{r.get('was_cache_hit')}" for p, r in b.items()))

    texts_match = all(a[p]["text"] == b[p]["text"] for p in a)
    check("texts byte-identical across cache hit", texts_match)


def test_default_scope_is_noop(work: Path) -> None:
    fixture = work / "fix3"
    build_fixture(fixture)
    cache_dir = work / "cache3"
    # client + cache provided but scopes left at the default (None) →
    # the enricher must not fire.
    mapped = _run_pipeline_with_l4(fixture, cache_dir=cache_dir, scopes=None)
    bucket = mapped["ctx"].scratch.get("llm:file_summary", {})
    check("scopes=None produces no enrichments", bucket == {},
          f"unexpected: {bucket}")
    # And the cache directory shouldn't even exist.
    check("scopes=None writes no cache files",
          not cache_dir.exists() or not any(cache_dir.iterdir()),
          f"cache contents: {list(cache_dir.iterdir()) if cache_dir.exists() else 'absent'}")


def test_unreachable_ollama_degrades_silently(work: Path) -> None:
    fixture = work / "fix4"
    build_fixture(fixture)
    cache_dir = work / "cache4"
    # Point the client at a port nothing listens on (Ollama is on 11434).
    try:
        mapped = _run_pipeline_with_l4(
            fixture, cache_dir=cache_dir, scopes=("files",),
            client_host="http://127.0.0.1:11435",
        )
    except Exception as e:
        check("unreachable Ollama doesn't crash the pipeline", False,
              f"raised: {type(e).__name__}: {e}")
        return
    bucket = mapped["ctx"].scratch.get("llm:file_summary", {})
    check("unreachable Ollama leaves no enrichments behind",
          bucket == {},
          f"unexpected: {list(bucket)}")


def main() -> int:
    global FAIL
    if RESOLVED_MODEL is None:
        for name in ("test_opt_in_produces_summaries",
                     "test_second_run_is_all_hits",
                     "test_default_scope_is_noop",
                     "test_unreachable_ollama_degrades_silently"):
            skip(name, "no suitable qwen2.5-coder model installed")
        print(f"\npassed: {PASS}   failed: {FAIL}   skipped: {SKIP}")
        return 0

    work = Path(tempfile.mkdtemp(prefix="verify_step3_"))
    try:
        for t in (test_opt_in_produces_summaries,
                  test_second_run_is_all_hits,
                  test_default_scope_is_noop,
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
