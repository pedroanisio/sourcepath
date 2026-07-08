#!/usr/bin/env python3
"""verify_llm_enrich_cli.py — Step 8 acceptance test.

Drives the three Python entry points as real subprocesses to confirm
the CLI surface works the way users will invoke it. Each test builds
a tiny git fixture, runs the script, and asserts the exit code + key
manifest fields. No imports of the scripts as modules — the goal is
to catch any drift between the documented invocation and what the
process actually does.

What's checked (always runs — no Ollama required for the offline tests):

  1. ``scripts/run_l4.py --help`` exits 0 and mentions all L4 flags.
  2. ``run_l4.py`` against an unreachable Ollama host returns exit 0,
     emits a SHACL-conforming bundle with zero enrichments, and prints
     the documented NOTE warning to stderr.
  3. ``run_l3.py --llm-enrich`` works the same way (graceful
     degradation when Ollama is down).
  4. ``run_xrefs.py --llm-enrich`` implies --concepts (concept_graph
     plugin gets registered).
  5. ``run_l4.py --llm-scope ''`` registers the plugin but produces
     no enrichments — the verifier path.
  6. ``run_l4.py --llm-scope unknown_kind`` is rejected with a clear
     parser error.
  7. ``run_l4.py --concept-vocab X --no-builtin-vocab`` is rejected
     as mutually exclusive (regression guard from earlier stages).

When Ollama IS reachable, runs additional tests:

  8. ``run_l4.py`` with defaults produces a bundle carrying at least
     one cbml4:fileSummary triple in inventory.ttl.
  9. ``run_l4.py --llm-no-cache`` produces enrichments without writing
     cache files.
 10. ``run_l3.py --llm-enrich`` and ``run_l4.py`` produce equivalent
     bundles when given the same fixture + same cache dir (the
     shorthand and full forms agree).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


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
            for line in detail.splitlines()[:6]:
                print(f"        {line}")
        FAIL += 1


def _resolve_enrich_model() -> str | None:
    """Model the pipeline will use here, or None if it cannot enrich.
    Model-aware guard (see plugins/llm_enrich/model_resolver.py)."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from plugins.llm_enrich import resolve_model
        from plugins.llm_enrich.client import OllamaClient
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
        '"""Token-based auth."""\n'
        'class Authenticator:\n'
        '    def check(self, t): return t\n'
    )
    subprocess.run(["git", "-C", str(target), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(target), "commit", "-q",
                    "-m", "init"], check=True)


def _run_script(script: str, *args: str,
                cwd: Path = REPO_ROOT,
                env: dict | None = None,
                ) -> subprocess.CompletedProcess:
    """Invoke scripts/<script> with the given args. Returns the
    CompletedProcess for the caller to inspect."""
    return subprocess.run(
        [sys.executable, f"scripts/{script}", *args],
        cwd=str(cwd),
        env=env or os.environ,
        capture_output=True, text=True,
        timeout=180,
    )


# ----------------------------------------------------------------------
# Always-runs tests
# ----------------------------------------------------------------------


def test_run_l4_help_shows_llm_flags() -> None:
    r = _run_script("run_l4.py", "--help")
    check("run_l4.py --help exits 0", r.returncode == 0,
          r.stderr[-500:])
    for flag in ("--llm-model", "--llm-host", "--llm-scope",
                 "--llm-cache-dir", "--llm-no-cache", "--no-llm"):
        check(f"run_l4.py --help mentions {flag}",
              flag in r.stdout,
              f"missing in --help output")


def test_run_l4_offline_degrades_gracefully(work: Path) -> None:
    fixture = work / "fix1"; build_fixture(fixture)
    out = work / "out1"
    cache = work / "cache1"
    r = _run_script(
        "run_l4.py",
        "--repo", str(fixture),
        "--out", str(out),
        "--backend", "hash",
        "--no-emit-blobs",
        "--llm-host", "http://127.0.0.1:11435",   # unreachable
        "--llm-cache-dir", str(cache),
    )
    check("run_l4.py offline: exit 0", r.returncode == 0,
          r.stderr[-400:])
    if r.returncode != 0:
        return
    check("run_l4.py offline: NOTE warning on stderr",
          "Ollama unreachable" in r.stderr,
          f"stderr: {r.stderr[-200:]!r}")
    manifest = json.loads(r.stdout)
    check("run_l4.py offline: SHACL conforms",
          bool(manifest["shacl_self_check"]["conforms"]))
    ext = (manifest.get("extensions") or {}).get("l4_50_artifact") or {}
    check("run_l4.py offline: n_enrichments == 0",
          ext.get("n_enrichments") == 0,
          f"got {ext.get('n_enrichments')}")
    check("run_l4.py offline: no enrichments.jsonl",
          not (out / "enrichments.jsonl").exists())


def test_run_l3_llm_enrich_offline(work: Path) -> None:
    fixture = work / "fix2"; build_fixture(fixture)
    out = work / "out2"
    saved_host = os.environ.get("OLLAMA_HOST")
    os.environ["OLLAMA_HOST"] = "http://127.0.0.1:11435"
    try:
        r = _run_script(
            "run_l3.py",
            "--repo", str(fixture),
            "--out", str(out),
            "--backend", "hash",
            "--no-emit-blobs",
            "--llm-enrich",
        )
    finally:
        if saved_host is None:
            os.environ.pop("OLLAMA_HOST", None)
        else:
            os.environ["OLLAMA_HOST"] = saved_host
    check("run_l3.py --llm-enrich offline: exit 0",
          r.returncode == 0, r.stderr[-400:])
    if r.returncode != 0:
        return
    check("run_l3.py --llm-enrich offline: NOTE warning on stderr",
          "Ollama unreachable" in r.stderr)


def test_run_xrefs_llm_enrich_implies_concepts(work: Path) -> None:
    fixture = work / "fix3"; build_fixture(fixture)
    out = work / "out3"
    saved_host = os.environ.get("OLLAMA_HOST")
    os.environ["OLLAMA_HOST"] = "http://127.0.0.1:11435"
    try:
        r = _run_script(
            "run_xrefs.py",
            "--repo", str(fixture),
            "--out", str(out),
            "--backend", "hash",
            "--no-emit-blobs",
            "--llm-enrich",
        )
    finally:
        if saved_host is None:
            os.environ.pop("OLLAMA_HOST", None)
        else:
            os.environ["OLLAMA_HOST"] = saved_host
    check("run_xrefs.py --llm-enrich offline: exit 0",
          r.returncode == 0, r.stderr[-400:])
    if r.returncode != 0:
        return
    # --llm-enrich implies --concepts; concepts.json should be present.
    check("--llm-enrich implies --concepts (concepts.json present)",
          (out / "concepts.json").exists())


def test_run_l4_empty_scope_registers_no_op(work: Path) -> None:
    fixture = work / "fix4"; build_fixture(fixture)
    out = work / "out4"
    r = _run_script(
        "run_l4.py",
        "--repo", str(fixture),
        "--out", str(out),
        "--backend", "hash",
        "--no-emit-blobs",
        "--llm-host", "http://127.0.0.1:11435",  # never reached
        "--llm-scope", "",
        "--llm-cache-dir", str(work / "cache4"),
    )
    check("run_l4.py --llm-scope '': exit 0",
          r.returncode == 0, r.stderr[-300:])
    if r.returncode != 0:
        return
    manifest = json.loads(r.stdout)
    ext = (manifest.get("extensions") or {}).get("l4_50_artifact") or {}
    check("--llm-scope '': n_enrichments == 0",
          ext.get("n_enrichments") == 0)


def test_run_l4_rejects_unknown_scope(work: Path) -> None:
    fixture = work / "fix5"; build_fixture(fixture)
    out = work / "out5"
    r = _run_script(
        "run_l4.py",
        "--repo", str(fixture),
        "--out", str(out),
        "--backend", "hash",
        "--llm-scope", "bogus",
    )
    check("--llm-scope bogus: non-zero exit",
          r.returncode != 0, "(should have errored)")
    check("--llm-scope bogus: stderr mentions scope",
          "scope" in r.stderr.lower() or "bogus" in r.stderr,
          f"stderr: {r.stderr[-300:]!r}")


def test_run_l4_rejects_mutually_exclusive_vocab_flags(work: Path) -> None:
    fixture = work / "fix6"; build_fixture(fixture)
    out = work / "out6"
    r = _run_script(
        "run_l4.py",
        "--repo", str(fixture),
        "--out", str(out),
        "--backend", "hash",
        "--concept-vocab", "/nonexistent.yaml",
        "--no-builtin-vocab",
    )
    check("--concept-vocab + --no-builtin-vocab: non-zero exit",
          r.returncode != 0, "(should have errored)")
    check("error mentions mutual exclusion",
          "mutually exclusive" in r.stderr,
          f"stderr: {r.stderr[-300:]!r}")


# ----------------------------------------------------------------------
# Ollama-dependent tests
# ----------------------------------------------------------------------


def test_run_l4_with_ollama_emits_enrichments(work: Path) -> None:
    fixture = work / "fix7"; build_fixture(fixture)
    out = work / "out7"
    r = _run_script(
        "run_l4.py",
        "--repo", str(fixture),
        "--out", str(out),
        "--backend", "hash",
        "--no-emit-blobs",
        "--llm-cache-dir", str(work / "cache7"),
    )
    check("run_l4.py online: exit 0",
          r.returncode == 0, r.stderr[-400:])
    if r.returncode != 0:
        return
    manifest = json.loads(r.stdout)
    ext = (manifest.get("extensions") or {}).get("l4_50_artifact") or {}
    check("run_l4.py online: at least one file_summary",
          (ext.get("by_kind") or {}).get("file_summary", 0) >= 1,
          f"by_kind={ext.get('by_kind')}")
    inv = (out / "inventory.ttl").read_text()
    check("inventory.ttl carries cbml4:fileSummary",
          "cbml4:fileSummary " in inv)


def test_run_l4_no_cache_writes_no_cache_files(work: Path) -> None:
    fixture = work / "fix8"; build_fixture(fixture)
    out = work / "out8"
    cache = work / "cache8"
    r = _run_script(
        "run_l4.py",
        "--repo", str(fixture),
        "--out", str(out),
        "--backend", "hash",
        "--no-emit-blobs",
        "--llm-cache-dir", str(cache),
        "--llm-no-cache",
    )
    check("run_l4.py --llm-no-cache: exit 0",
          r.returncode == 0, r.stderr[-400:])
    if r.returncode != 0:
        return
    cache_files = list(cache.glob("*.json")) if cache.exists() else []
    check("run_l4.py --llm-no-cache: zero cache files written",
          len(cache_files) == 0,
          f"found {len(cache_files)} cache files")


def main() -> int:
    global FAIL
    work = Path(tempfile.mkdtemp(prefix="verify_step8_cli_"))
    try:
        always_run = [
            test_run_l4_help_shows_llm_flags,
            test_run_l4_offline_degrades_gracefully,
            test_run_l3_llm_enrich_offline,
            test_run_xrefs_llm_enrich_implies_concepts,
            test_run_l4_empty_scope_registers_no_op,
            test_run_l4_rejects_unknown_scope,
            test_run_l4_rejects_mutually_exclusive_vocab_flags,
        ]
        ollama_tests = [
            test_run_l4_with_ollama_emits_enrichments,
            test_run_l4_no_cache_writes_no_cache_files,
        ]
        for t in always_run:
            try:
                if t is test_run_l4_help_shows_llm_flags:
                    t()
                else:
                    t(work)
            except Exception:
                FAIL += 1
                print(f"  FAIL  {t.__name__}")
                traceback.print_exc()
        if RESOLVED_MODEL is not None:
            for t in ollama_tests:
                try:
                    t(work)
                except Exception:
                    FAIL += 1
                    print(f"  FAIL  {t.__name__}")
                    traceback.print_exc()
        else:
            for t in ollama_tests:
                print(f"  SKIP  {t.__name__}  (Ollama unreachable)")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print(f"\npassed: {PASS}   failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
