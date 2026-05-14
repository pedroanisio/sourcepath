#!/usr/bin/env python3
"""verify_llm_enrich_prompts.py — Step 3 prompt-integrity verifier.

The cache key for every L4 enrichment includes the active prompt
file's SHA-256. If the file on disk drifts from the SHA registered in
``PROMPT_REGISTRY`` (because someone edited the file without bumping
its version), every cache entry built against the old SHA becomes
silently unreachable. The system still produces correct output (the
miss triggers a fresh model call) but the cache is effectively
flushed without anyone noticing.

This verifier catches the drift directly:

  1. Every entry in PROMPT_REGISTRY has a real file on disk.
  2. That file's SHA matches the registered ``sha256`` field.
  3. Both ``SYSTEM:`` and ``USER:`` markers are present (parse round-trip).
  4. The registered ``version`` matches the integer in the filename
     (``<kind>.v<N>.txt``).
  5. ``render()`` substitutes every ``{placeholder}`` token in the
     active prompts without leaving residual ``{...}`` strings.

Plus negative tests:

  6. Hand-tampering a prompt file in a temp dir and re-loading raises
     ``PromptVersionMismatch``.
  7. A prompt missing the ``SYSTEM:`` header raises ``ValueError``.

Exit code: 0 on full pass, 1 on any failure.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

from plugins.llm_enrich.cache import hash_text
from plugins.llm_enrich.prompts import (
    PROMPT_REGISTRY,
    PROMPTS_DIR,
    PromptVersionMismatch,
    _load,
    _parse_prompt_text,
    verify_registry,
)


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


def test_registry_is_non_empty() -> None:
    check("PROMPT_REGISTRY has at least one kind",
          len(PROMPT_REGISTRY) > 0,
          "expected file_summary at minimum after Step 3")


def test_every_prompt_file_exists() -> None:
    for kind, tmpl in PROMPT_REGISTRY.items():
        check(f"file exists: {tmpl.filename}",
              tmpl.path.is_file(),
              f"not found at {tmpl.path}")


def test_every_sha_matches_file_bytes() -> None:
    for kind, tmpl in PROMPT_REGISTRY.items():
        raw = tmpl.path.read_bytes()
        actual = hash_text(raw)
        check(f"sha matches: {tmpl.filename}",
              actual == tmpl.sha256,
              f"registry={tmpl.sha256[:16]} file={actual[:16]}")


def test_verify_registry_passes_on_clean_tree() -> None:
    try:
        verify_registry()
    except PromptVersionMismatch as e:
        check("verify_registry() passes on clean repo", False, str(e))
    else:
        check("verify_registry() passes on clean repo", True)


def test_filename_version_matches_registered_version() -> None:
    for kind, tmpl in PROMPT_REGISTRY.items():
        m = re.match(rf"^{re.escape(kind)}\.v(\d+)\.txt$", tmpl.filename)
        if not m:
            check(f"filename pattern: {tmpl.filename}", False,
                  f"expected '{kind}.vN.txt'")
            continue
        check(
            f"version in filename matches registered: {tmpl.filename}",
            int(m.group(1)) == tmpl.version,
            f"filename={m.group(1)} registered={tmpl.version}",
        )


def test_sections_parse() -> None:
    for kind, tmpl in PROMPT_REGISTRY.items():
        raw = tmpl.path.read_text(encoding="utf-8")
        try:
            system, user = _parse_prompt_text(raw)
        except ValueError as e:
            check(f"sections parse: {tmpl.filename}", False, str(e))
            continue
        check(f"sections parse: {tmpl.filename}",
              bool(system) and bool(user),
              f"empty system={not system} user={not user}")


def test_render_leaves_no_placeholders() -> None:
    """Each active template's ``render()`` should produce strings with
    no leftover ``{placeholder}`` tokens when called with the documented
    placeholder set."""
    # Document the expected placeholders per kind here. Adding a new
    # kind in Step 5 means adding a row.
    placeholders = {
        "file_summary": dict(path="x.py", language="python",
                             content="def f(): pass"),
    }
    for kind, fields in placeholders.items():
        tmpl = PROMPT_REGISTRY[kind]
        system, user = tmpl.render(**fields)
        leftover = re.search(r"\{[a-z_][a-z0-9_]*\}", system + user)
        check(
            f"render() substitutes every placeholder: {kind}",
            leftover is None,
            f"leftover token: {leftover.group(0) if leftover else '?'}",
        )


# --- negative tests ---------------------------------------------------


def test_tampering_detected() -> None:
    """Copy a prompt file to a temp dir, tamper it, force a reload —
    the registry's verify_registry() should refuse."""
    # We mutate PROMPT_REGISTRY in-place under a try/finally so the
    # subsequent tests see the original state. _load reads from
    # PROMPTS_DIR directly; we need to bypass it for this scenario by
    # tampering with the on-disk file and re-running verify_registry.
    kind, tmpl = next(iter(PROMPT_REGISTRY.items()))
    original = tmpl.path.read_bytes()
    try:
        tmpl.path.write_bytes(original + b"# tampered\n")
        try:
            verify_registry()
        except PromptVersionMismatch:
            check("verify_registry detects tampered file", True)
        else:
            check("verify_registry detects tampered file", False,
                  "no exception raised")
    finally:
        tmpl.path.write_bytes(original)


def test_missing_section_raises() -> None:
    """A prompt file missing SYSTEM: or USER: should fail to parse."""
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "broken.v1.txt"
        bad.write_text("no markers here\njust prose\n")
        try:
            _parse_prompt_text(bad.read_text())
        except ValueError:
            check("missing SYSTEM/USER raises ValueError", True)
        else:
            check("missing SYSTEM/USER raises ValueError", False)


def main() -> int:
    tests = [
        test_registry_is_non_empty,
        test_every_prompt_file_exists,
        test_every_sha_matches_file_bytes,
        test_verify_registry_passes_on_clean_tree,
        test_filename_version_matches_registered_version,
        test_sections_parse,
        test_render_leaves_no_placeholders,
        test_tampering_detected,
        test_missing_section_raises,
    ]
    for t in tests:
        try:
            t()
        except Exception:
            global FAIL
            FAIL += 1
            print(f"  FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\npassed: {PASS}   failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
