#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""setup_and_validate_ontology.py — idempotent installer, configurer, and SHACL
validator for the Scope-A software-architecture ontology (TBox + SHACL shapes).

Purpose
-------
The ontology file(s) embed *both* the data (the 20 core dimensions + the quality
overlay) and the SHACL shapes that constrain them. This script makes the
toolchain required to check that pairing reproducible and self-verifying:

  1. INSTALL   — ensure ``rdflib`` and ``pyshacl`` are importable, preferring
                 ``uv`` (``uv sync``) and falling back to ``pip``.
  2. CONFIGURE — resolve the project virtual environment and, when the current
                 interpreter lacks the dependencies, re-exec inside it exactly
                 once.
  3. VALIDATE  — run pyshacl over each ontology file (data graph == shapes
                 graph) with SHACL-SPARQL constraints enabled (e.g. the
                 position-uniqueness check), and report conformance.

Idempotency
-----------
Every step is safe to repeat, and re-running converges to the same state:
  * dependency install is skipped when already satisfied; ``uv sync`` / ``pip``
    are themselves idempotent when they do run;
  * a re-exec sentinel env var prevents interpreter re-exec loops;
  * validation is strictly read-only — it never mutates the ontology, the
    virtual environment contents beyond install, or the repository.

Usage
-----
    uv run scripts/setup_and_validate_ontology.py                 # full run
    python scripts/setup_and_validate_ontology.py --check-only    # validate only
    python scripts/setup_and_validate_ontology.py --reinstall     # force install
    python scripts/setup_and_validate_ontology.py path/to/file.ttl [more.ttl ...]
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

__file_meta__ = {
    "role": "tooling",
    "status": "active",
    "rules": [],
    "summary": "Idempotent install + SHACL validation harness for the ontology TTL files.",
}

REEXEC_SENTINEL = "_CBM_ONTOLOGY_SETUP_REEXEC"
REQUIRED_PACKAGES = ("rdflib", "pyshacl")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Files are discovered by content, not by name, so the harness survives renames:
# a default target is any repo-root .ttl that embeds at least one SHACL shape.
SHACL_MARKER = "sh:NodeShape"


def log(msg: str) -> None:
    print(f"[ontology-setup] {msg}", flush=True)


def deps_importable() -> bool:
    """True when every required package can be imported by this interpreter."""
    return all(importlib.util.find_spec(pkg) is not None for pkg in REQUIRED_PACKAGES)


def venv_python(root: Path) -> Path | None:
    subdir, exe = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    candidate = root / ".venv" / subdir / exe
    return candidate if candidate.exists() else None


def run(cmd: list[str]) -> None:
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=PROJECT_ROOT)


def ensure_dependencies(reinstall: bool) -> None:
    """Install rdflib + pyshacl into the project environment. Idempotent."""
    if deps_importable() and not reinstall:
        log("dependencies already importable — skipping install")
        return

    uv = shutil.which("uv")
    if uv:
        # `uv sync` installs the project + locked deps into .venv. It is a no-op
        # when the environment already matches the lockfile.
        run([uv, "sync"])
        return

    # Fallback: editable install into the current interpreter via pip.
    run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"])
    run([sys.executable, "-m", "pip", "install", "-e", "."])


def maybe_reexec_in_venv() -> None:
    """If the deps are still not importable here, re-exec inside the project
    venv exactly once (guarded by a sentinel to prevent loops)."""
    if deps_importable():
        return
    if os.environ.get(REEXEC_SENTINEL):
        raise SystemExit(
            "Dependencies still not importable after install and re-exec. "
            "Inspect the project virtual environment (.venv)."
        )
    py = venv_python(PROJECT_ROOT)
    if py is None:
        raise SystemExit(
            "No .venv interpreter found after install. Run `uv sync` manually, "
            "then re-run this script."
        )
    log(f"re-exec inside project venv: {py}")
    env = dict(os.environ, **{REEXEC_SENTINEL: "1"})
    os.execve(str(py), [str(py), *sys.argv], env)


def _has_shacl_shapes(path: Path) -> bool:
    try:
        return SHACL_MARKER in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def discover_ontology_files(explicit: list[str]) -> list[Path]:
    if explicit:
        paths = [Path(p).resolve() for p in explicit]
        missing = [p for p in paths if not p.exists()]
        if missing:
            raise SystemExit("Ontology file(s) not found: " + ", ".join(map(str, missing)))
        return paths
    skip = {".venv", ".git", "node_modules", "__pycache__", "_tmp", "site-packages"}
    paths = sorted(
        p
        for p in PROJECT_ROOT.rglob("*.ttl")
        if not (set(p.relative_to(PROJECT_ROOT).parts) & skip) and _has_shacl_shapes(p)
    )
    if not paths:
        raise SystemExit(
            f"No SHACL-bearing .ttl files (containing {SHACL_MARKER!r}) found under {PROJECT_ROOT}"
        )
    return paths


def validate_file(path: Path, inference: str, tbox: Path | None = None) -> tuple[bool, str]:
    """Validate one TTL file. Read-only. Returns (conforms, report).

    Two modes:
      * self-contained (tbox is None): the file embeds its own SHACL shapes
        alongside its data — used for the TBox itself.
      * ABox mode (tbox given): the file is instance data (a generated ABox);
        its shapes live in the TBox. The data graph is TBox+ABox merged so that
        sh:class checks resolve the dimension/value/system types, and the shapes
        graph is the TBox.
    """
    import rdflib
    from pyshacl import validate

    data = rdflib.Graph()
    if tbox is not None:
        data.parse(tbox.as_posix(), format="turtle")
    data.parse(path.as_posix(), format="turtle")

    if tbox is not None:
        shapes = rdflib.Graph()
        shapes.parse(tbox.as_posix(), format="turtle")
    else:
        shapes = data  # SHACL shapes live in the same file as the data

    conforms, _results_graph, results_text = validate(
        data_graph=data,
        shacl_graph=shapes,
        ont_graph=None,
        inference=inference,
        advanced=True,       # enable SHACL-SPARQL / advanced constraint handling
        meta_shacl=False,
        debug=False,
    )
    return conforms, results_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Idempotently install the RDF/SHACL toolchain and validate the ontology."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="ontology TTL files to validate (default: every repo-root .ttl that embeds SHACL shapes)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="skip the install step and only validate (fails if deps are absent)",
    )
    parser.add_argument(
        "--reinstall",
        action="store_true",
        help="force dependency (re)install even when already importable",
    )
    parser.add_argument(
        "--inference",
        default="none",
        choices=["none", "rdfs", "owlrl", "both"],
        help="pyshacl pre-validation inference expansion (default: none)",
    )
    parser.add_argument(
        "--tbox",
        default=None,
        metavar="PATH",
        help="validate the given files as ABoxes (instance data) against this TBox's "
        "SHACL shapes, instead of expecting each file to embed its own shapes",
    )
    args = parser.parse_args(argv)

    tbox_path = None
    if args.tbox:
        tbox_path = Path(args.tbox).resolve()
        if not tbox_path.exists():
            raise SystemExit(f"TBox file not found: {tbox_path}")

    if args.check_only:
        if not deps_importable():
            raise SystemExit(
                "--check-only was given but rdflib/pyshacl are not importable. "
                "Run once without --check-only to install them."
            )
    else:
        ensure_dependencies(reinstall=args.reinstall)
        maybe_reexec_in_venv()

    if tbox_path is not None:
        if not args.files:
            raise SystemExit("--tbox requires one or more ABox files to validate.")
        files = [Path(p).resolve() for p in args.files]
        missing = [p for p in files if not p.exists()]
        if missing:
            raise SystemExit("ABox file(s) not found: " + ", ".join(map(str, missing)))
        log(f"validating {len(files)} ABox file(s) against TBox {tbox_path.name}")
    else:
        files = discover_ontology_files(args.files)
        log(f"validating {len(files)} ontology file(s) with inference={args.inference!r}")

    all_ok = True
    for path in files:
        conforms, report = validate_file(path, args.inference, tbox=tbox_path)
        rel = path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path
        if conforms:
            log(f"PASS  {rel}")
        else:
            all_ok = False
            log(f"FAIL  {rel}")
            print(report.rstrip(), file=sys.stderr)

    if all_ok:
        log("All ontology files conform to their SHACL shapes.")
        return 0
    log("SHACL violations found — see the report above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
