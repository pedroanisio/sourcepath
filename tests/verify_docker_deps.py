#!/usr/bin/env python3
"""verify_docker_deps.py — the CLI image must install what the CLI imports.

The root `Dockerfile` installed an explicit, hand-maintained package list and
then ran `pip install --no-deps .`, which suppressed `pyproject.toml` as the
dependency source. The list drifted: `pydantic`, `pyoxigraph`, and the
cpp/java/objc/cfml grammars were never installed. `pydantic` is imported at
CLI import time via `shared_kernel/shacl_spec.py`, so **every** invocation of
the documented `docker run ... codebase-mapper` workflow died with

    ModuleNotFoundError: No module named 'pydantic'

while `docker build` stayed green and no CI job built the image. That is the
failure mode this file exists to prevent.

Asserting "the Dockerfile must list pydantic" would re-create the same drift
one package later, so the contract is derived from the source instead:

  A. the project install must not pass `--no-deps` — pyproject is the
     dependency authority, and suppressing it is what allowed the drift;
  B. the image must install the project itself, so those deps are resolved;
  C. the Dockerfile must not hand-install a package that pyproject already
     declares — a shadow list is exactly what silently went stale;
  D. every third-party module reachable from `codebase_mapper` must be
     covered by a declared dependency, so the declared set is provably
     sufficient rather than merely plausible;
  E. `sentence-transformers` (torch) must stay out of the base dependencies
     and live in the `[sbert]` extra, so the default image stays lean —
     the reason the hand-maintained list was introduced in the first place.

Run from the repo root:  python tests/verify_docker_deps.py
"""
from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE = ROOT / "codebase_mapper"

# Top-level names that resolve inside this repository rather than site-packages.
FIRST_PARTY = {
    "codebase_mapper", "plugins", "decomposer", "recomposer",
    "frontend", "scripts", "tests",
}

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def _canon(name: str) -> str:
    """PEP 503 normalization, so `tree_sitter_c` and `tree-sitter-c` agree."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared(pyproject: dict) -> tuple[set[str], set[str]]:
    """(base dependency names, `[sbert]` extra names), PEP 503 normalized."""
    project = pyproject["project"]
    base = {
        _canon(re.split(r"[<>=!~;\[ ]", spec, 1)[0])
        for spec in project.get("dependencies", [])
    }
    sbert = {
        _canon(re.split(r"[<>=!~;\[ ]", spec, 1)[0])
        for spec in project.get("optional-dependencies", {}).get("sbert", [])
    }
    return base, sbert


def _run_lines(dockerfile: str) -> str:
    """Executable Dockerfile text: comments dropped, continuations folded.

    Comments must go first — this file documents the historical
    `pip install --no-deps .` in prose, and a naive scan matches the
    explanation instead of the instruction.
    """
    executable = "\n".join(
        line for line in dockerfile.splitlines()
        if not line.lstrip().startswith("#")
    )
    return executable.replace("\\\n", " ")


def _third_party_imports(pkg_root: Path) -> set[str]:
    """Top-level names imported anywhere under `pkg_root`, minus stdlib/first-party.

    Walks nested imports too: the historical break was reached through an
    import chain, not a module-level import in the entry point.
    """
    found: set[str] = set()
    for py in sorted(pkg_root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is a relative (first-party) import.
                if node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
    return {
        name for name in found
        if name not in sys.stdlib_module_names and name not in FIRST_PARTY
    }


def _module_to_dists() -> dict[str, set[str]]:
    """Installed module name -> distribution names, PEP 503 normalized."""
    try:
        from importlib.metadata import packages_distributions
    except ImportError:  # pragma: no cover - Python < 3.10
        return {}
    mapping: dict[str, set[str]] = {}
    for module, dists in packages_distributions().items():
        mapping[module] = {_canon(d) for d in dists}
    return mapping


def main() -> int:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    base, sbert = _declared(pyproject)
    folded = _run_lines(dockerfile)

    print("== root Dockerfile resolves dependencies from pyproject ==")

    # A. --no-deps on the project install is what suppressed pyproject.
    project_installs = re.findall(r"pip install\s+([^\n&|;]*\.(?:\[[^\]]*\])?)", folded)
    no_deps_project = [inst for inst in project_installs if "--no-deps" in inst]
    check(
        "project install does not pass --no-deps",
        not no_deps_project,
        f"found: {no_deps_project}",
    )

    # B. the image must actually install the project.
    check(
        "image installs the project from pyproject",
        bool(project_installs),
        "no `pip install .` (or `.[extra]`) found in the Dockerfile",
    )

    # C. a hand-maintained list shadowing declared deps is the drift class.
    installed_tokens = set()
    for match in re.finditer(r"pip install\s+([^\n&|;]*)", folded):
        for token in match.group(1).split():
            if token.startswith("-") or token.startswith("."):
                continue
            if token.startswith('".') or token.startswith("'."):
                continue
            installed_tokens.add(_canon(re.split(r"[<>=!~\[]", token, 1)[0]))
    shadowed = sorted(installed_tokens & base)
    check(
        "no hand-maintained package list shadows the base dependencies",
        not shadowed,
        f"declared in pyproject yet re-listed in the Dockerfile: {shadowed}",
    )

    # D. the declared set must actually cover what the package imports.
    imports = _third_party_imports(PACKAGE)
    module_to_dists = _module_to_dists()
    uncovered: list[str] = []
    unresolved: list[str] = []
    for module in sorted(imports):
        dists = module_to_dists.get(module)
        if dists is None:
            # Not installed here; fall back to the name-shape heuristic so the
            # check still means something in a minimal environment.
            if _canon(module) not in base | sbert:
                unresolved.append(module)
            continue
        if not (dists & (base | sbert)):
            uncovered.append(f"{module} (provided by {sorted(dists)})")
    check(
        "every third-party import of codebase_mapper is a declared dependency",
        not uncovered,
        "; ".join(uncovered),
    )
    if unresolved:
        # Informational: cannot be proven either way without the package present.
        print(f"  note  not installed here, unverifiable: {sorted(unresolved)}")

    # D'. the specific regression, named so the failure reads plainly.
    pydantic_dists = module_to_dists.get("pydantic")
    if pydantic_dists is not None:
        check(
            "pydantic (imported at CLI import time) is a declared dependency",
            bool(pydantic_dists & base),
            "shacl_spec.py imports it on the CLI import path",
        )

    # E. keep the default image lean, which is why the shadow list existed.
    check(
        "sentence-transformers is not a base dependency",
        "sentence-transformers" not in base,
        "torch in the base deps makes the default image enormous",
    )
    check(
        "sentence-transformers is declared in the [sbert] extra",
        "sentence-transformers" in sbert,
        "--backend sbert must remain installable",
    )
    check(
        "Dockerfile offers the sbert opt-in",
        "sbert" in folded.lower(),
        "WITH_SBERT must select the [sbert] extra",
    )

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
