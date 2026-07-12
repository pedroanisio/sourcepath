#!/usr/bin/env python3
"""verify_backend_image.py — the backend image must contain what the app imports.

BL-029. `frontend/backend/Dockerfile` copied `app.py` into the image and never
copied `serving/` — the package `app.py` imports on nearly every line. The
built image raised `ModuleNotFoundError: No module named 'serving'` at import
time and died before serving a request. No CI job builds this image, so nothing
observed it. That contradicts the project guideline that every commit must be
deployable.

Rather than assert the one missing line ("the Dockerfile must COPY serving"),
this verifier derives the requirement from the source: **every first-party
module `app.py` imports must be copied into the image.** A future module added
under `frontend/backend/` and imported by the app fails this check on the
commit that introduces it, not in production.

First-party = the import's top-level name resolves to a file or package inside
`frontend/backend/`. Third-party (`fastapi`, `pydantic`) comes from
requirements.txt; `codebase_mapper` is installed as a package and is checked
separately, since it is copied and pip-installed rather than vendored.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "frontend" / "backend"
APP = BACKEND / "app.py"
DOCKERFILE = BACKEND / "Dockerfile"

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


def _toplevel_imports(source: str) -> set[str]:
    """Top-level module name of every import in the file, at any nesting depth.

    `app.py` imports some modules inside functions and at the file's tail, so a
    module-level-only walk would miss them — exactly the kind of blind spot that
    let this ship.
    """
    names: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:      # relative import — first-party by construction
                if node.module:
                    names.add(node.module.split(".")[0])
            elif node.module:
                names.add(node.module.split(".")[0])
    return names


def _first_party(name: str) -> bool:
    """True when the import resolves to something inside frontend/backend/."""
    return (BACKEND / name).is_dir() or (BACKEND / f"{name}.py").is_file()


def _copied_paths(dockerfile: str) -> list[str]:
    """Every source path named on a COPY instruction (handles line continuations)."""
    text = re.sub(r"\\\s*\n", " ", dockerfile)     # join continuations
    copied: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY "):
            continue
        args = line[len("COPY "):].strip()
        args = re.sub(r"^--\S+\s+", "", args)      # drop --from=, --chown=, ...
        parts = args.split()
        if len(parts) >= 2:
            copied.extend(parts[:-1])              # last token is the destination
    return copied


def main() -> int:
    argparse.ArgumentParser().parse_args()
    print("== backend image contents (BL-029) ==")

    app_src = APP.read_text()
    dockerfile = DOCKERFILE.read_text()
    copied = _copied_paths(dockerfile)
    copied_norm = {c.strip("./").rstrip("/") for c in copied}

    imports = _toplevel_imports(app_src)
    first_party = sorted(n for n in imports if _first_party(n))

    check("app.py has first-party imports to satisfy",
          bool(first_party), f"imports={sorted(imports)}")

    for name in first_party:
        # The Dockerfile's build context is the repo root (docker-compose sets
        # `context: ..`), so paths are repo-relative.
        expected = f"frontend/backend/{name}"
        ok = any(c == expected or c.startswith(expected + "/") for c in copied_norm)
        check(f"image copies first-party module '{name}'",
              ok,
              f"app.py imports '{name}' but no COPY provides {expected!r}; "
              f"the container raises ModuleNotFoundError at boot. COPY={copied}")

    # The app entrypoint itself must obviously be present.
    check("image copies the app entrypoint",
          any(c == "frontend/backend/app.py" for c in copied_norm),
          f"COPY={copied}")

    # codebase_mapper is installed as a package (COPY + pip install), not vendored.
    if "codebase_mapper" in imports:
        check("image installs the codebase_mapper package",
              "codebase_mapper" in copied_norm
              and re.search(r"pip install[^\n]*\.", dockerfile) is not None,
              "app.py imports codebase_mapper but the image neither copies nor "
              "installs it")

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
