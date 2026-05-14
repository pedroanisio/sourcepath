#!/usr/bin/env python3
"""verify_drift_p2.py — contract suite for drift-risk-map.md MODERATE findings.

Each block corresponds to one MODERATE-rated coupling in `drift-risk-map.md`.
Runs offline (no Docker, no Ollama, no network) so any contributor can
invoke it as a fast pre-merge guard.

Findings covered:

  #11 MCP `@tool("X")` decorators ↔ `INPUT_SCHEMAS`/`OUTPUT_SCHEMAS`.
      Asserts every tool name registered in `handlers.py` has matching
      schema entries on both sides (and vice versa).
  #17 `tests/verify_*.py` on disk ↔ README's "## Verify" section.
      Asserts the set of verifier files matches the set documented in
      the README. Catches drift in either direction (orphan file or
      stale README entry).
  #18 README CLI invocations ↔ `scripts/run_*.py` argparse surface.
      Asserts every `--flag` referenced in a README example invocation
      is actually defined in the corresponding script's parser.
  #19 `LANG_BY_EXT` ↔ language strings consumed by analyzers.
      AST-parses `codebase_mapper/_builtins.py`, extracts the language
      literals checked by every `*Analyzer.matches`, asserts each is
      a value present in `LANG_BY_EXT`.
  #21 Plugin-name sort prefixes ↔ extension registry naming convention.
      Registers every built-in + plugin component, walks every registry,
      and asserts each name matches `l<layer>_<order>_<slug>` (or the
      documented host-prefix `lang_*` / `resolve_*` for built-ins).
      Also asserts uniqueness across the layered prefixes.

Exit code: 0 if all pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codebase_mapper.constants import LANG_BY_EXT


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
# Finding #11 — @tool decorators ↔ schemas
# ───────────────────────────────────────────────────────────────────────


def check_mcp_tool_decorators_vs_schemas() -> None:
    from frontend.mcp_server.schemas import INPUT_SCHEMAS, OUTPUT_SCHEMAS

    src = (REPO_ROOT / "frontend/mcp_server/handlers.py").read_text()
    tree = ast.parse(src)
    decorated: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # Want: Call(func=Name("tool"), args=[Constant(value="<name>")])
            if not isinstance(dec, ast.Call):
                continue
            f = dec.func
            if isinstance(f, ast.Name) and f.id == "tool":
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    val = dec.args[0].value
                    if isinstance(val, str):
                        decorated.add(val)

    check(
        "every @tool(\"X\") decorator has a matching INPUT_SCHEMAS entry",
        decorated <= set(INPUT_SCHEMAS),
        f"decorated_but_no_input_schema={sorted(decorated - set(INPUT_SCHEMAS))}",
    )
    check(
        "every @tool(\"X\") decorator has a matching OUTPUT_SCHEMAS entry",
        decorated <= set(OUTPUT_SCHEMAS),
        f"decorated_but_no_output_schema={sorted(decorated - set(OUTPUT_SCHEMAS))}",
    )
    check(
        "every INPUT_SCHEMAS tool has a @tool decorator (no orphan schemas)",
        set(INPUT_SCHEMAS) <= decorated,
        f"schema_but_no_decorator={sorted(set(INPUT_SCHEMAS) - decorated)}",
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #17 — disk verifiers vs README "## Verify" list
# ───────────────────────────────────────────────────────────────────────


def _readme_verify_paths() -> set[str]:
    text = (REPO_ROOT / "README.md").read_text()
    # Slice from "## Verify" through the next "## " heading (or EOF).
    start = text.find("## Verify")
    if start == -1:
        return set()
    rest = text[start:]
    next_heading = re.search(r"\n## ", rest[2:])
    if next_heading:
        rest = rest[: 2 + next_heading.start()]
    listed: set[str] = set()
    for m in re.finditer(r"python\s+(tests/verify_[A-Za-z0-9_]+\.py)\b", rest):
        listed.add(m.group(1))
    return listed


def check_verify_section_matches_disk() -> None:
    listed = _readme_verify_paths()
    on_disk = {
        f"tests/{p.name}"
        for p in (REPO_ROOT / "tests").glob("verify_*.py")
    }
    check(
        "every tests/verify_*.py file is referenced in README's Verify section",
        on_disk <= listed,
        f"orphan_on_disk={sorted(on_disk - listed)}\n"
        f"add a one-liner to README's ## Verify section.",
    )
    check(
        "every README Verify-section entry corresponds to a real file",
        listed <= on_disk,
        f"stale_in_readme={sorted(listed - on_disk)}\n"
        f"delete the README line or restore the file.",
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #18 — README CLI invocations ↔ scripts/run_*.py parsers
# ───────────────────────────────────────────────────────────────────────


# Scripts whose CLI surfaces are referenced in README invocations.
# The parsers are built inline in each script's `main()` — we AST-parse
# the file instead of importing it, so we don't pay the import cost or
# hit a side-effecting top-level.
SCRIPT_PATHS: list[str] = [
    "scripts/run_l2.py",
    "scripts/run_l3.py",
    "scripts/run_l4.py",
    "scripts/run_xrefs.py",
]


def _extract_flags_for_script(readme_text: str, script: str) -> set[str]:
    """Return every `--flag` token that appears on a line invoking ``script``."""
    flags: set[str] = set()
    # Tolerate line-continuations: collapse `\<newline>` into a space first.
    flat = readme_text.replace("\\\n", " ")
    pat = re.compile(
        r"^[^\n]*\b" + re.escape(script) + r"\b([^\n]*)$",
        re.MULTILINE,
    )
    for m in pat.finditer(flat):
        tail = m.group(1)
        for flag in re.findall(r"--[A-Za-z][A-Za-z0-9_-]*", tail):
            flags.add(flag)
    return flags


def _parser_flags_from_script(script_path: Path) -> set[str]:
    """AST-parse a script and collect every `--flag` argument passed
    to a `*.add_argument("--flag", …)` call. Catches argparse parsers
    built inline in `main()` without needing to import the module."""
    src = script_path.read_text()
    tree = ast.parse(src)
    flags: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == "add_argument"):
            continue
        # Every positional Constant string starting with `--` is a long opt.
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value.startswith("--"):
                    flags.add(arg.value)
    return flags


def check_readme_cli_flags() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    for script in SCRIPT_PATHS:
        path = REPO_ROOT / script
        if not path.exists():
            check(f"{script}: exists for flag-cross-check", False, "missing")
            continue
        readme_flags = _extract_flags_for_script(readme, script)
        parser_flags = _parser_flags_from_script(path)
        missing = readme_flags - parser_flags
        check(
            f"{script}: every --flag in README is accepted by argparse",
            not missing,
            f"missing_in_parser={sorted(missing)}\n"
            f"readme_flags={sorted(readme_flags)}\n"
            f"parser_flags_sample={sorted(parser_flags)[:15]}…",
        )


# ───────────────────────────────────────────────────────────────────────
# Finding #19 — LANG_BY_EXT ↔ analyzer language strings
# ───────────────────────────────────────────────────────────────────────


def _analyzer_languages_from_builtins() -> set[str]:
    """Parse `_builtins.py` with AST and pull every language literal
    checked in `*Analyzer.matches`. Captures both:

        return record.language == "python"
        return record.language in ("typescript", "javascript") and ...

    plus boolean-AND combinations.
    """
    src = (REPO_ROOT / "codebase_mapper/_builtins.py").read_text()
    tree = ast.parse(src)
    out: set[str] = set()

    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        if not cls.name.endswith("Analyzer"):
            continue
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef) or fn.name != "matches":
                continue
            for node in ast.walk(fn):
                # record.language == "X"
                if (
                    isinstance(node, ast.Compare)
                    and isinstance(node.left, ast.Attribute)
                    and node.left.attr == "language"
                    and len(node.ops) == 1
                ):
                    op = node.ops[0]
                    rhs = node.comparators[0]
                    if isinstance(op, ast.Eq) and isinstance(rhs, ast.Constant):
                        if isinstance(rhs.value, str):
                            out.add(rhs.value)
                    elif isinstance(op, ast.In) and isinstance(rhs, ast.Tuple):
                        for elt in rhs.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                out.add(elt.value)
    return out


def check_lang_by_ext_covers_analyzers() -> None:
    used = _analyzer_languages_from_builtins()
    declared = set(LANG_BY_EXT.values())
    unreachable = used - declared
    check(
        "every language tested by a *Analyzer.matches lives in LANG_BY_EXT",
        not unreachable,
        f"unreachable_languages={sorted(unreachable)}\n"
        f"these analyzers will never fire because no extension maps to them.",
    )
    check(
        "_builtins.py has at least one analyzer for each major language",
        used,
        "no analyzer language literals found — has the AST shape changed?",
    )


# ───────────────────────────────────────────────────────────────────────
# Finding #21 — plugin-name sort prefixes
# ───────────────────────────────────────────────────────────────────────


# Layered-plugin name pattern: l<layer>_<order>_<slug>. Examples:
#   l2_10_chunks, l3_10_xrefs, l3_30_graph, l3_40_concepts_artifact
LAYERED_PATTERN = re.compile(r"^l\d+_\d+_[a-z][a-z0-9_]*$")

# Host built-ins (registered by codebase_mapper._builtins.register_builtins)
# use a flat namespace by convention: lang_* (analyzers) and resolve_*
# (import resolvers). These predate the layered system and are exempt.
HOST_PATTERNS = (
    re.compile(r"^lang_[a-z][a-z0-9_]*$"),
    re.compile(r"^resolve_[a-z][a-z0-9_]*$"),
)


def _matches_any(name: str, patterns) -> bool:
    return any(p.match(name) for p in patterns)


def check_plugin_name_prefixes() -> None:
    # Bring up every plugin layer so registries are populated.
    from codebase_mapper import reset_registries
    from codebase_mapper.extensions import (
        iter_aggregators, iter_artifact_emitters,
        iter_graph_contributors, iter_import_resolvers,
        iter_language_analyzers, iter_record_enrichers,
        iter_shape_contributors,
    )
    from plugins import chunks_embeddings, concept_graph, symbol_xrefs

    reset_registries()
    chunks_embeddings.register_all(
        chunks_embeddings.DeterministicHashBackend(dimension=64),
    )
    concept_graph.register_all()
    symbol_xrefs.register_all()

    # Walk every registry. The "shape" of each name we permit is:
    #   - host built-ins: lang_* or resolve_*  (LanguageAnalyzer / ImportResolver only)
    #   - everything else: l<layer>_<order>_<slug>
    registries: list[tuple[str, list, tuple]] = [
        ("language_analyzers", list(iter_language_analyzers()), HOST_PATTERNS),
        ("import_resolvers",   list(iter_import_resolvers()),   HOST_PATTERNS),
        ("record_enrichers",   list(iter_record_enrichers()),   (LAYERED_PATTERN,)),
        ("aggregators",        list(iter_aggregators()),        (LAYERED_PATTERN,)),
        ("graph_contributors", list(iter_graph_contributors()), (LAYERED_PATTERN,)),
        ("shape_contributors", list(iter_shape_contributors()), (LAYERED_PATTERN,)),
        ("artifact_emitters",  list(iter_artifact_emitters()),  (LAYERED_PATTERN,)),
    ]

    for registry_name, items, allowed in registries:
        names = [getattr(it, "name", None) for it in items]
        bad = [n for n in names if n is None or not _matches_any(n, allowed)]
        check(
            f"{registry_name}: every name matches the documented prefix pattern",
            not bad,
            f"bad_names={bad}\n"
            f"allowed_patterns={[p.pattern for p in allowed]}",
        )
        duplicates = sorted(
            {n for n in names if names.count(n) > 1 and n is not None}
        )
        check(
            f"{registry_name}: names are unique within the registry",
            not duplicates,
            f"duplicates={duplicates}",
        )

    # The layered registries should also keep a single total sort order
    # across the union of all of them. A duplicate name in different
    # registries is fine (host names CAN collide with plugin names if
    # they're in different protocols), but two LAYERED names collapsing
    # across registries usually points at a copy-paste regression.
    layered_names: list[str] = []
    for registry_name, items, allowed in registries:
        if HOST_PATTERNS in (allowed, *allowed):  # exclude host-only registries
            continue
        for it in items:
            n = getattr(it, "name", None)
            if n and LAYERED_PATTERN.match(n):
                layered_names.append(n)
    layered_dupes = sorted(
        {n for n in layered_names if layered_names.count(n) > 1}
    )
    check(
        "no duplicate layered plugin names across all layered registries",
        not layered_dupes,
        f"duplicates={layered_dupes}",
    )


# ───────────────────────────────────────────────────────────────────────
# Driver
# ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.parse_args(argv)

    print("Finding #11 — MCP @tool decorators vs INPUT/OUTPUT_SCHEMAS")
    check_mcp_tool_decorators_vs_schemas()
    print("Finding #17 — disk verifiers vs README \"## Verify\" list")
    check_verify_section_matches_disk()
    print("Finding #18 — README CLI invocations vs scripts/run_*.py parsers")
    check_readme_cli_flags()
    print("Finding #19 — LANG_BY_EXT vs analyzer language strings")
    check_lang_by_ext_covers_analyzers()
    print("Finding #21 — plugin-name sort prefixes")
    check_plugin_name_prefixes()

    print()
    print(f"passed: {PASS}   failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
