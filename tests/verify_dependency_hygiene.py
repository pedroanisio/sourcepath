#!/usr/bin/env python3
"""verify_dependency_hygiene.py — regression suite for cbm-inspection-report §3.10.

The §3.10 audit scored "Dependency management" at B because:

  (a) the codebase is mid-refactor — bounded contexts (`inspection/`,
      `emission/`, `serving/`) exist, but no automated guard prevents new
      code from re-introducing cross-context coupling or pulling
      infrastructure libraries into a domain layer;

  (b) the shared-kernel carve completed without legacy compatibility
      shims at `codebase_mapper/constants.py` and `codebase_mapper/
      extensions.py`, so any new `from codebase_mapper.constants import …`
      line silently breaks until import-time;

  (c) the `shared_kernel/__init__.py` star-export surface must stay in
      sync with `constants.py` and `extensions.py` — otherwise consumers
      using `from codebase_mapper.shared_kernel import X` get silent
      `ImportError`s far from the cause.

This script is AST-based and offline (no rdflib, no flask, no network).
It mirrors the convention established by `verify_drift_p{1,2,3}.py`.

Failure modes covered:

  R1  Legacy import paths. Asserts no Python file imports from
      `codebase_mapper.constants` or `codebase_mapper.extensions` —
      every consumer must use `codebase_mapper.shared_kernel.*`.

  R2  Shared-kernel star-export drift. AST-parses
      `shared_kernel/{constants,extensions}.py`, collects every
      module-level public name (anything not starting with `_`), and
      asserts each appears reachable via `from codebase_mapper.
      shared_kernel import …` — i.e. the package's `__init__.py`
      really re-exports the public surface it claims to.

  R3  Cross-context domain coupling. Asserts no file under
      `codebase_mapper/inspection/` imports from `codebase_mapper.
      emission` or from `frontend.backend.serving`; symmetric for
      `emission/`. (Cross-imports between bounded contexts must go
      through `shared_kernel` or an explicit DTO, never directly.)

  R4  Infrastructure libraries in domain layer. Asserts no NEW file
      under `codebase_mapper/emission/application/` imports `rdflib`,
      `flask`, or `click` directly. A baseline of pre-existing
      offenders is allow-listed so the test catches regressions
      (new violations) without blocking on the unfinished
      emission/-port-inversion work tracked by
      task-1778782573999-5f09. When that task completes, shrink the
      allowlist; once empty, the contract is fully closed.

Run:

    python3 tests/verify_dependency_hygiene.py

Exit codes:
    0  all four contracts hold
    1  one or more contracts violated (details on stderr)

This script encodes the bounded-context invariants documented in the
DDD plan (see docs/cbm-inspection-report.md §3.1, §3.2, §3.3). New
violations regress §3.10's score from B → C.
"""
from __future__ import annotations

import ast
import sys
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "codebase_mapper"
SHARED_KERNEL = PKG / "shared_kernel"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def iter_python_files(root: Path) -> Iterable[Path]:
    """Yield every .py file under `root`, skipping caches and vendored trees."""
    for p in root.rglob("*.py"):
        parts = set(p.parts)
        if "__pycache__" in parts or "_tmp" in parts or ".agent-tasks" in parts:
            continue
        yield p


def import_targets(path: Path) -> list[tuple[str, int]]:
    """Return [(dotted-module, line)] for every Import / ImportFrom in `path`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                out.append((node.module, node.lineno))
    return out


def public_module_names(path: Path) -> set[str]:
    """Collect module-level public names from `path` without importing it.

    Picks up: top-level assignments (CONST = ...), function defs, class defs.
    Skips: names starting with '_'.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and not tgt.id.startswith("_"):
                    names.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
    return names


# ---------------------------------------------------------------------------
# contracts
# ---------------------------------------------------------------------------

LEGACY_PATHS = {
    "codebase_mapper.constants",
    "codebase_mapper.extensions",
}


def check_r1_no_legacy_imports() -> list[str]:
    """R1 — no consumer imports the deprecated legacy module paths."""
    violations: list[str] = []
    for py in iter_python_files(REPO_ROOT):
        for mod, line in import_targets(py):
            if mod in LEGACY_PATHS:
                violations.append(
                    f"  {py.relative_to(REPO_ROOT)}:{line} imports legacy '{mod}' "
                    f"(use 'codebase_mapper.shared_kernel.<...>' instead)"
                )
    return violations


def check_r2_shared_kernel_reexports() -> list[str]:
    """R2 — every public name in shared_kernel/{constants,extensions}.py
    must be reachable via `codebase_mapper.shared_kernel`."""
    violations: list[str] = []
    init = SHARED_KERNEL / "__init__.py"
    if not init.exists():
        return [f"  missing {init.relative_to(REPO_ROOT)}"]

    # parse __init__.py — collect everything brought into the namespace
    init_tree = ast.parse(init.read_text(encoding="utf-8"))
    star_modules: set[str] = set()
    explicit_names: set[str] = set()
    for node in init_tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    star_modules.add(mod)
                else:
                    explicit_names.add(alias.asname or alias.name)

    for stem in ("constants", "extensions"):
        src = SHARED_KERNEL / f"{stem}.py"
        if not src.exists():
            violations.append(f"  missing {src.relative_to(REPO_ROOT)}")
            continue
        publics = public_module_names(src)
        # if `from .<stem> import *` is in __init__, every public name is reachable
        if stem in star_modules:
            continue
        # otherwise, each must appear explicitly
        missing = publics - explicit_names
        if missing:
            violations.append(
                f"  shared_kernel/__init__.py does not re-export from "
                f"'{stem}.py': {sorted(missing)[:5]}{'…' if len(missing) > 5 else ''}"
            )
    return violations


def check_r3_no_cross_context_imports() -> list[str]:
    """R3 — bounded contexts may not import each other's modules directly."""
    violations: list[str] = []
    matrix = {
        "codebase_mapper/inspection": ["codebase_mapper.emission",
                                       "frontend.backend.serving"],
        "codebase_mapper/emission":   ["codebase_mapper.inspection",
                                       "frontend.backend.serving"],
    }
    for ctx_dir, forbidden_prefixes in matrix.items():
        ctx_path = REPO_ROOT / ctx_dir
        if not ctx_path.exists():
            continue
        for py in iter_python_files(ctx_path):
            for mod, line in import_targets(py):
                for forbidden in forbidden_prefixes:
                    if mod == forbidden or mod.startswith(forbidden + "."):
                        violations.append(
                            f"  {py.relative_to(REPO_ROOT)}:{line} crosses "
                            f"bounded-context boundary: '{mod}' "
                            f"(from {ctx_dir})"
                        )
    return violations


INFRA_LIBS = {"rdflib", "flask", "click"}

# Baseline of known pre-existing offenders. Captured on 2026-05-14 against
# bundle 31d3c2aa…, before task-1778782573999-5f09 (emission port inversion)
# completed. Each entry is a bundle-relative path. NEW files outside this
# allowlist that import an infra library from emission/application/ FAIL R4.
# SHRINK this set as task-1778782573999-5f09 lands; do not GROW it.
R4_BASELINE_ALLOWLIST: frozenset[str] = frozenset({
    "codebase_mapper/emission/application/regenerate.py",
    "codebase_mapper/emission/application/emit_bundle.py",
    "codebase_mapper/emission/application/reconstruct.py",
})


def check_r4_no_infra_in_domain() -> list[str]:
    """R4 — no NEW emission/application/ file imports rdflib/flask/click.

    Pre-existing offenders are allow-listed; any file outside the allowlist
    that violates this rule fails the contract.
    """
    violations: list[str] = []
    domain_root = PKG / "emission" / "application"
    if not domain_root.exists():
        # emission split not yet landed; skip silently
        return []
    for py in iter_python_files(domain_root):
        rel = str(py.relative_to(REPO_ROOT))
        if rel in R4_BASELINE_ALLOWLIST:
            continue
        for mod, line in import_targets(py):
            root = mod.split(".", 1)[0]
            if root in INFRA_LIBS:
                violations.append(
                    f"  {py.relative_to(REPO_ROOT)}:{line} imports infra "
                    f"library '{mod}' from emission/application/ "
                    f"(new violation — belongs in emission/infrastructure/)"
                )
    return violations


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------

CONTRACTS = [
    ("R1", "no legacy 'codebase_mapper.constants' / '.extensions' imports",
     check_r1_no_legacy_imports),
    ("R2", "shared_kernel/__init__.py re-exports the full public surface",
     check_r2_shared_kernel_reexports),
    ("R3", "bounded contexts (inspection/emission) do not cross-import",
     check_r3_no_cross_context_imports),
    ("R4", "emission/application/ does not import rdflib/flask/click",
     check_r4_no_infra_in_domain),
]


def main() -> int:
    overall_fail = False
    for tag, desc, fn in CONTRACTS:
        violations = fn()
        if violations:
            overall_fail = True
            print(f"FAIL  {tag}  {desc}", file=sys.stderr)
            for v in violations:
                print(v, file=sys.stderr)
        else:
            print(f"PASS  {tag}  {desc}")
    return 1 if overall_fail else 0


if __name__ == "__main__":
    sys.exit(main())
