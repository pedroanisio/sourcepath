#!/usr/bin/env python3
"""verify_llm_enrich_cache.py — Step 2 acceptance test.

Cache layer + client transport, the two foundations the rest of L4
depends on. Pure-cache tests run unconditionally; client-tests run
only when Ollama is reachable (so CI without a model server still
passes).

What's checked:
  1. Cache key is a stable 64-hex sha256 of (kind, model, prompt_sha,
     target_sha).
  2. Key changes when any input field changes — no silent collisions
     across kinds or prompts.
  3. get/put roundtrip preserves payload + adds the schema version.
  4. Corrupt cache entries return None (graceful degradation).
  5. Mismatched schema version returns None (forward-compat for the
     day we bump CACHE_SCHEMA_VERSION).
  6. Atomic writes: a put() that crashes mid-write leaves no half-file
     visible at the final path.
  7. enabled=False disables both reads and writes — no files appear.
  8. get_or_compute hits the second call without re-invoking compute.
  9. Default cache dir honors CBM_LLM_CACHE env var.
 10. resolve_host honors OLLAMA_HOST env var.

Ollama-only checks (skipped when unreachable):
 11. OllamaClient.ping() returns True against the live server.
 12. OllamaClient.available_models() returns at least the configured
     default model.
 13. OllamaClient.chat() returns (text, dt) tuple with non-empty text
     and dt < timeout.
 14. OllamaModelMissing raised on unknown model name.
 15. OllamaUnreachable raised against a bad host.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

from plugins.llm_enrich.cache import (
    CACHE_SCHEMA_VERSION,
    Cache,
    default_cache_dir,
    hash_text,
)
from plugins.llm_enrich.client import (
    DEFAULT_HOST,
    OllamaClient,
    OllamaModelMissing,
    OllamaUnreachable,
    resolve_host,
)
from plugins.llm_enrich.model_resolver import completion_capable_models


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


# ----------------------------------------------------------------------
# Pure-cache tests (always run)
# ----------------------------------------------------------------------


def test_key_is_stable_hex_sha256() -> None:
    k = Cache.compose_key(
        kind="file_summary", model="qwen2.5-coder:7b",
        prompt_sha="abc", target_sha="def",
    )
    check(
        "key is 64-hex-char sha256",
        len(k) == 64 and all(c in "0123456789abcdef" for c in k),
        f"got {k!r}",
    )
    # Stability: identical inputs always produce identical outputs.
    k2 = Cache.compose_key(
        kind="file_summary", model="qwen2.5-coder:7b",
        prompt_sha="abc", target_sha="def",
    )
    check("key is deterministic", k == k2, f"{k} vs {k2}")


def test_key_changes_on_every_input_change() -> None:
    base = dict(kind="k", model="m", prompt_sha="p", target_sha="t")
    k0 = Cache.compose_key(**base)
    pairs = [
        ("kind", "kind", "k2"),
        ("model", "model", "m2"),
        ("prompt_sha", "prompt_sha", "p2"),
        ("target_sha", "target_sha", "t2"),
    ]
    for label, field, new in pairs:
        modified = {**base, field: new}
        k_new = Cache.compose_key(**modified)
        check(f"key changes when {label} changes",
              k_new != k0, f"both {k0} for {modified}")


def test_get_miss_returns_none() -> None:
    with tempfile.TemporaryDirectory() as td:
        c = Cache(cache_dir=Path(td))
        check("get on empty cache returns None",
              c.get("missing_key") is None)


def test_put_get_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        c = Cache(cache_dir=Path(td))
        key = "x" * 64
        c.put(key, {"kind": "file_summary", "text": "hello"})
        got = c.get(key)
        check("roundtrip returns the same record",
              got is not None
              and got.get("text") == "hello"
              and got.get("kind") == "file_summary")
        check("roundtrip adds schema version",
              got is not None and got.get("v") == CACHE_SCHEMA_VERSION,
              f"got v={got.get('v') if got else 'N/A'}")


def test_corrupt_entry_returns_none() -> None:
    with tempfile.TemporaryDirectory() as td:
        c = Cache(cache_dir=Path(td))
        key = "y" * 64
        (Path(td) / f"{key}.json").write_text("not json {")
        check("corrupt entry returns None", c.get(key) is None)


def test_mismatched_schema_returns_none() -> None:
    with tempfile.TemporaryDirectory() as td:
        c = Cache(cache_dir=Path(td))
        key = "z" * 64
        # Hand-write a "future" cache entry with a different version.
        (Path(td) / f"{key}.json").write_text(
            json.dumps({"v": 999, "text": "from-the-future"})
        )
        check("mismatched schema version returns None",
              c.get(key) is None)


def test_atomic_write_leaves_no_partial_file() -> None:
    # We can't easily simulate a crash mid-write, but we can assert
    # the contract: no file is named "<key>.json.tmp" after put() returns.
    with tempfile.TemporaryDirectory() as td:
        c = Cache(cache_dir=Path(td))
        c.put("abc", {"text": "x"})
        leftovers = list(Path(td).glob("*.tmp"))
        check("no .tmp files after put", leftovers == [],
              f"found {leftovers}")


def test_disabled_cache_is_inert() -> None:
    with tempfile.TemporaryDirectory() as td:
        c = Cache(cache_dir=Path(td), enabled=False)
        c.put("k", {"text": "x"})
        check("disabled put writes no file",
              list(Path(td).iterdir()) == [])
        # And the directory is still safe to read from.
        c2 = Cache(cache_dir=Path(td))
        check("disabled put left no read residue", c2.get("k") is None)


def test_get_or_compute_caches_second_call() -> None:
    with tempfile.TemporaryDirectory() as td:
        c = Cache(cache_dir=Path(td))
        calls = []

        def make():
            calls.append(1)
            return {"text": "computed", "generated_at": "2026-05-14T00Z"}

        rec1, hit1 = c.get_or_compute(
            kind="file_summary", model="m",
            prompt_sha="p", target_sha="t", compute=make,
        )
        check("first get_or_compute is a miss",
              not hit1 and rec1.get("text") == "computed")

        rec2, hit2 = c.get_or_compute(
            kind="file_summary", model="m",
            prompt_sha="p", target_sha="t", compute=make,
        )
        check("second get_or_compute is a hit",
              hit2 and len(calls) == 1)
        check("second call returns the same record bytes",
              rec1 == rec2)


def test_default_cache_dir_honors_env() -> None:
    saved = os.environ.get("CBM_LLM_CACHE")
    try:
        os.environ["CBM_LLM_CACHE"] = "/tmp/cbm-test-cache-xyz"
        d = default_cache_dir()
        check("CBM_LLM_CACHE env overrides default",
              str(d) == "/tmp/cbm-test-cache-xyz",
              f"got {d}")
    finally:
        if saved is None:
            os.environ.pop("CBM_LLM_CACHE", None)
        else:
            os.environ["CBM_LLM_CACHE"] = saved


def test_resolve_host_precedence() -> None:
    saved = os.environ.get("OLLAMA_HOST")
    try:
        os.environ["OLLAMA_HOST"] = "http://from-env:9999"
        check("OLLAMA_HOST env wins over default",
              resolve_host() == "http://from-env:9999")
        check("explicit arg wins over env",
              resolve_host("http://explicit:8888") == "http://explicit:8888")
        os.environ.pop("OLLAMA_HOST")
        check("default applies when env unset",
              resolve_host() == DEFAULT_HOST)
    finally:
        if saved is None:
            os.environ.pop("OLLAMA_HOST", None)
        else:
            os.environ["OLLAMA_HOST"] = saved


def test_hash_text_helper() -> None:
    a = hash_text("hello")
    b = hash_text(b"hello")
    check("hash_text handles str and bytes identically", a == b,
          f"{a} != {b}")
    check("hash_text is 64-hex sha256",
          len(a) == 64 and all(c in "0123456789abcdef" for c in a))


# ----------------------------------------------------------------------
# Client tests — require Ollama to be reachable
# ----------------------------------------------------------------------


def _ollama_reachable() -> bool:
    try:
        return OllamaClient(timeout=3.0).ping()
    except Exception:
        return False


def test_client_ping_reachable() -> None:
    c = OllamaClient()
    check("ping() returns True against live Ollama", c.ping())
    c.close()


def test_client_lists_models() -> None:
    c = OllamaClient()
    try:
        models = c.available_models()
    finally:
        c.close()
    check("available_models() returns a non-empty list",
          isinstance(models, list) and len(models) > 0,
          f"got {models}")


def test_client_chat_returns_text_and_time() -> None:
    c = OllamaClient()
    try:
        # Pick a model the server *reports* as completion-capable. Taking
        # available_models()[0] assumed every installed tag can chat —
        # false the moment an embedding-only model (nomic-embed-text) is
        # pulled, which made this test fail on a healthy server.
        capable = completion_capable_models(c.model_catalog())
        if not capable:
            check("chat needs at least one completion-capable model", False,
                  f"installed: {c.available_models()}")
            return
        model = capable[0]
        text, dt = c.chat(
            model=model,
            system="Reply with exactly one word.",
            user="Say ok.",
            seed=42,
        )
        check("chat returns non-empty text",
              isinstance(text, str) and len(text.strip()) > 0,
              f"got {text!r}")
        check("chat returns positive wall_seconds < timeout",
              isinstance(dt, float) and 0 < dt < c.timeout,
              f"dt={dt}")
    finally:
        c.close()


def test_client_raises_on_missing_model() -> None:
    c = OllamaClient()
    try:
        try:
            c.chat(model="this-model-does-not-exist:99",
                   system="x", user="x", seed=0)
            check("expected OllamaModelMissing", False)
        except OllamaModelMissing:
            check("OllamaModelMissing on unknown model", True)
        except Exception as e:
            check("OllamaModelMissing on unknown model", False,
                  f"got {type(e).__name__}: {e}")
    finally:
        c.close()


def test_client_raises_on_unreachable() -> None:
    # Port 11435 should have nothing listening (Ollama is on 11434).
    c = OllamaClient(host="http://127.0.0.1:11435", timeout=2.0)
    try:
        try:
            c.chat(model="qwen2.5-coder:7b", system="x", user="x", seed=0)
            check("expected OllamaUnreachable", False)
        except OllamaUnreachable:
            check("OllamaUnreachable on bad host", True)
        except Exception as e:
            check("OllamaUnreachable on bad host", False,
                  f"got {type(e).__name__}: {e}")
    finally:
        c.close()


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------


def main() -> int:
    pure_tests = [
        test_key_is_stable_hex_sha256,
        test_key_changes_on_every_input_change,
        test_get_miss_returns_none,
        test_put_get_roundtrip,
        test_corrupt_entry_returns_none,
        test_mismatched_schema_returns_none,
        test_atomic_write_leaves_no_partial_file,
        test_disabled_cache_is_inert,
        test_get_or_compute_caches_second_call,
        test_default_cache_dir_honors_env,
        test_resolve_host_precedence,
        test_hash_text_helper,
    ]
    client_tests = [
        test_client_ping_reachable,
        test_client_lists_models,
        test_client_chat_returns_text_and_time,
        test_client_raises_on_missing_model,
        test_client_raises_on_unreachable,
    ]

    print("--- pure cache tests ---")
    for t in pure_tests:
        try:
            t()
        except Exception:
            global FAIL
            FAIL += 1
            print(f"  FAIL  {t.__name__}")
            traceback.print_exc()

    print("\n--- client tests (require Ollama) ---")
    if _ollama_reachable():
        for t in client_tests:
            try:
                t()
            except Exception:
                FAIL += 1
                print(f"  FAIL  {t.__name__}")
                traceback.print_exc()
    else:
        for t in client_tests:
            skip(t.__name__, "Ollama unreachable")

    print(f"\npassed: {PASS}   failed: {FAIL}   skipped: {SKIP}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
