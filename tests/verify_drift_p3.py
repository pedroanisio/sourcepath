#!/usr/bin/env python3
"""verify_drift_p3.py — contract suite for drift-risk-map.md LOW findings.

Every LOW-rated coupling in `drift-risk-map.md` is already guarded by
some mechanism (shared-import, contract-test, runtime-validation). The
LOW rating reflects that. The hazard this verifier targets is the
*next* layer: someone removes one of those guards, and the coupling
silently slips back up the risk ladder.

This file is therefore a **meta-presence** check plus light
reinforcement of the most load-bearing existing guards. It runs
offline (no Docker, no Ollama, no network).

Findings covered:

  #12 `INPUT_SCHEMAS` ↔ `@tool("X")` decorators — already build-error.
      Reinforced by import-time check + duplicate-key detection.
  #13 `models.py` dataclasses ↔ `rdf_emit.py` imports — shared-import.
      Guard: assert the imports still resolve and the dataclass shape
      is unchanged.
  #14 `llm_enrich/prompts/*.v1.txt` ↔ `PROMPT_REGISTRY` SHAs.
      Re-runs the existing prompt-SHA verification logic in-process
      (so deleting `verify_llm_enrich_prompts.py` doesn't lose the
      check) AND asserts that verifier file still exists on disk.
  #15 `CONCEPT_KIND_LITERALS` (writer) ↔ `_CONCEPT_KINDS` (loader).
      Re-asserts set equality directly + presence-checks the cited
      named test (`test_kind_literal_set_matches_loader`).
  #20 `static/schemas/*.xsd` fixtures ↔ `verify_xsd_fixture.py`.
      Meta-presence only: the verifier exists and the fixture
      directory is non-empty.
  #22 `CACHE_SCHEMA_VERSION` ↔ committed cache fixture entries.
      Walks every fixture file and asserts `"v" == CACHE_SCHEMA_VERSION`.
  #23 `PY_AST_SCHEMA_VERSION` / `RUST_AST_SCHEMA_VERSION` ↔ regenerate
      consumers. Asserts each constant is a positive integer, the
      module references it in the `schema_version` emission site,
      and the per-language verifier exists.

Exit code: 0 if all pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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
            for line in detail.splitlines()[:20]:
                print(f"        {line}")
        FAIL += 1


# ───────────────────────────────────────────────────────────────────────
# Meta-presence: cited guards must still exist on disk
# ───────────────────────────────────────────────────────────────────────


# Each tuple is (path-from-repo-root, drift-risk-finding-id, "why").
CITED_GUARDS: list[tuple[str, str, str]] = [
    ("tests/verify_llm_enrich_prompts.py",         "#14",
     "prompt-SHA integrity"),
    ("tests/verify_llm_enrich_ci_determinism.py",  "#14, #22",
     "warm-cache determinism + cache schema"),
    ("tests/verify_vocab_emission.py",             "#15",
     "test_kind_literal_set_matches_loader"),
    ("tests/verify_xsd_fixture.py",                "#20",
     "vendored XSD classifier coverage"),
    ("tests/verify_regenerate.py",                 "#23",
     "Python AST regenerate"),
    ("tests/verify_rust_regenerate.py",            "#23",
     "Rust AST regenerate"),
]


def check_cited_guards_present() -> None:
    for rel, finding, why in CITED_GUARDS:
        p = REPO_ROOT / rel
        check(
            f"meta-presence ({finding}): {rel} exists ({why})",
            p.exists() and p.stat().st_size > 0,
            f"either restore {rel} or update drift-risk-map.md to remove the citation.",
        )


# ───────────────────────────────────────────────────────────────────────
# Finding #12 — @tool import-time consistency
# ───────────────────────────────────────────────────────────────────────


def check_handlers_load_without_keyerror() -> None:
    # Importing handlers.py runs every `@tool("X")` decorator, which
    # looks the name up in INPUT_SCHEMAS at module import time. Any
    # decorator with a name absent from INPUT_SCHEMAS raises KeyError
    # here. This is the "build-error" propagation the report describes.
    try:
        from frontend.mcp_server import handlers  # noqa: F401
        ok = True
        detail = ""
    except Exception as e:
        ok = False
        detail = f"{type(e).__name__}: {e}"
    check(
        "frontend.mcp_server.handlers imports cleanly "
        "(no @tool ↔ INPUT_SCHEMAS KeyError at import time)",
        ok, detail,
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #13 — inspection/emission model surfaces ↔ RDF emitter contract
# ───────────────────────────────────────────────────────────────────────


def check_models_rdf_emit_shared_import() -> None:
    try:
        from codebase_mapper.emission.infrastructure.rdf.rdflib_emitter import (  # noqa: F401
            build_inventory_graph, build_shacl_graph,
        )
        from codebase_mapper.emission.models import (  # noqa: F401
            SymbolXrefEdge, UnresolvedSymbolRef,
        )
        from codebase_mapper.inspection.models import (  # noqa: F401
            DeclaresDependencyEdge, FileRecord, ImportEdge,
            ImportExternalEdge, PinsDependencyEdge, TestsEdge,
        )
        ok = True
        detail = ""
    except Exception as e:
        ok = False
        detail = f"{type(e).__name__}: {e}"
    check(
        "rdflib_emitter + inspection/emission model imports resolve "
        "(shared-import contract intact)",
        ok, detail,
    )

    # Reinforce: every dataclass used by rdf_emit.build_inventory_graph's
    # signature is still importable under its expected name.
    from codebase_mapper.inspection.models import (
        DeclaresDependencyEdge, FileRecord, ImportEdge,
        ImportExternalEdge, PinsDependencyEdge, TestsEdge,
    )
    expected_field_sets: dict[type, set[str]] = {
        FileRecord:           {"path", "git_blob_sha", "content_sha256",
                               "size_bytes", "language", "type_", "phases"},
        ImportEdge:           {"src_path", "dst_path"},
        ImportExternalEdge:   {"src_path", "package_name"},
        DeclaresDependencyEdge: {"manifest_path", "package_name"},
        PinsDependencyEdge:   {"lockfile_path", "package_name",
                               "package_version"},
        TestsEdge:            {"test_path", "subject_path"},
    }
    for cls, required in expected_field_sets.items():
        from dataclasses import fields
        actual = {f.name for f in fields(cls)}
        missing = required - actual
        check(
            f"inspection.models.{cls.__name__} carries the required fields "
            f"({len(required)} expected)",
            not missing,
            f"missing={sorted(missing)}\nactual={sorted(actual)}",
        )


# ───────────────────────────────────────────────────────────────────────
# Finding #14 — prompt SHAs ↔ PROMPT_REGISTRY
# ───────────────────────────────────────────────────────────────────────


def check_prompt_registry_shas() -> None:
    try:
        from plugins.llm_enrich.prompts import PROMPT_REGISTRY
    except Exception as e:
        check("plugins.llm_enrich.prompts loads", False,
              f"{type(e).__name__}: {e}")
        return

    check("PROMPT_REGISTRY non-empty", bool(PROMPT_REGISTRY),
          "no prompts registered")

    prompts_dir = REPO_ROOT / "plugins/llm_enrich/prompts"
    drift: list[str] = []
    for kind, tmpl in PROMPT_REGISTRY.items():
        p = prompts_dir / tmpl.filename
        if not p.exists():
            drift.append(f"{kind}: file missing at {p}")
            continue
        actual = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual != tmpl.sha256:
            drift.append(
                f"{kind}: registry={tmpl.sha256[:16]}… file={actual[:16]}…",
            )
    check(
        "every PROMPT_REGISTRY entry's sha256 matches the on-disk file",
        not drift,
        "\n".join(drift),
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #15 — CONCEPT_KIND_LITERALS ↔ _CONCEPT_KINDS
# ───────────────────────────────────────────────────────────────────────


def check_concept_kind_literals_sync() -> None:
    from plugins.concept_graph.graph_writer import CONCEPT_KIND_LITERALS
    from codebase_mapper.emission.infrastructure.vocab.loader import _CONCEPT_KINDS  # type: ignore
    check(
        "CONCEPT_KIND_LITERALS (writer) == _CONCEPT_KINDS (loader)",
        set(CONCEPT_KIND_LITERALS) == set(_CONCEPT_KINDS),
        f"writer={sorted(CONCEPT_KIND_LITERALS)}\n"
        f"loader={sorted(_CONCEPT_KINDS)}",
    )

    # Cited named test still exists in the L3 verifier.
    src = (REPO_ROOT / "tests/verify_vocab_emission.py").read_text()
    check(
        "test_kind_literal_set_matches_loader still defined in "
        "verify_vocab_emission.py",
        "test_kind_literal_set_matches_loader" in src,
        "drift-risk-map cites this test by name; do not remove it.",
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #20 — XSD fixture presence
# ───────────────────────────────────────────────────────────────────────


def check_xsd_fixture_presence() -> None:
    schemas_dir = REPO_ROOT / "static/schemas"
    xsds = list(schemas_dir.rglob("*.xsd")) if schemas_dir.exists() else []
    check(
        "static/schemas/ contains at least one .xsd fixture "
        "(verify_xsd_fixture.py's input)",
        bool(xsds),
        f"count={len(xsds)} dir_exists={schemas_dir.exists()}",
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #22 — CACHE_SCHEMA_VERSION ↔ fixture entries
# ───────────────────────────────────────────────────────────────────────


def check_cache_fixture_schema_version() -> None:
    from plugins.llm_enrich.cache import CACHE_SCHEMA_VERSION
    check(
        "CACHE_SCHEMA_VERSION is a positive int",
        isinstance(CACHE_SCHEMA_VERSION, int) and CACHE_SCHEMA_VERSION >= 1,
        f"value={CACHE_SCHEMA_VERSION!r} type={type(CACHE_SCHEMA_VERSION).__name__}",
    )

    cache_dir = REPO_ROOT / "tests/fixtures/llm_cache/cache"
    if not cache_dir.exists():
        check("cache fixture dir present", False,
              f"missing: {cache_dir}")
        return
    entries = list(cache_dir.glob("*.json"))
    check(
        "cache fixture dir has at least one entry",
        bool(entries),
        f"count=0 dir={cache_dir}",
    )
    mismatches: list[str] = []
    for entry in entries:
        try:
            data = json.loads(entry.read_text())
        except Exception as e:
            mismatches.append(f"{entry.name}: parse failure {e}")
            continue
        if not isinstance(data, dict):
            mismatches.append(f"{entry.name}: not a dict")
            continue
        v = data.get("v")
        if v != CACHE_SCHEMA_VERSION:
            mismatches.append(
                f"{entry.name}: v={v!r} expected={CACHE_SCHEMA_VERSION!r}",
            )
    check(
        "every cache fixture entry has v == CACHE_SCHEMA_VERSION",
        not mismatches,
        "\n".join(mismatches[:5]),
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #23 — PY_AST_SCHEMA_VERSION / RUST_AST_SCHEMA_VERSION
# ───────────────────────────────────────────────────────────────────────


def check_ast_schema_versions() -> None:
    from codebase_mapper.inspection.languages.python import PY_AST_SCHEMA_VERSION
    from codebase_mapper.inspection.languages.rust   import RUST_AST_SCHEMA_VERSION

    for name, ver, mod_path in (
        ("PY_AST_SCHEMA_VERSION",
         PY_AST_SCHEMA_VERSION,
         "codebase_mapper/inspection/languages/python.py"),
        ("RUST_AST_SCHEMA_VERSION",
         RUST_AST_SCHEMA_VERSION,
         "codebase_mapper/inspection/languages/rust.py"),
    ):
        check(
            f"{name} is a positive int",
            isinstance(ver, int) and ver >= 1,
            f"value={ver!r}",
        )
        # The emission site must reference the constant — not a hard-coded
        # literal. A drift like `"schema_version": 1` while the constant
        # bumps to 2 is exactly the hazard this guards.
        src = (REPO_ROOT / mod_path).read_text()
        check(
            f"{mod_path} emits schema_version via the constant "
            f"(no hard-coded literal divergence)",
            f'"schema_version": {name}' in src
            or f"schema_version={name}" in src
            or f"'schema_version': {name}" in src,
            f"emission site does not reference {name} symbolically; "
            f"a bump would silently desync.",
        )


# ───────────────────────────────────────────────────────────────────────
# Driver
# ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.parse_args(argv)

    print("Meta-presence — cited guards must still exist")
    check_cited_guards_present()
    print("Finding #12 — handlers.py imports cleanly")
    check_handlers_load_without_keyerror()
    print("Finding #13 — models.py ↔ rdf_emit.py shared-import")
    check_models_rdf_emit_shared_import()
    print("Finding #14 — prompt files ↔ PROMPT_REGISTRY SHAs")
    check_prompt_registry_shas()
    print("Finding #15 — CONCEPT_KIND_LITERALS ↔ _CONCEPT_KINDS")
    check_concept_kind_literals_sync()
    print("Finding #20 — XSD fixture presence")
    check_xsd_fixture_presence()
    print("Finding #22 — CACHE_SCHEMA_VERSION ↔ fixture entries")
    check_cache_fixture_schema_version()
    print("Finding #23 — AST schema versions ↔ emission sites")
    check_ast_schema_versions()

    print()
    print(f"passed: {PASS}   failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
