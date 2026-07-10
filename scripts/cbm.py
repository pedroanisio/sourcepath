#!/usr/bin/env python3
"""cbm.py — one front door for the codebase-mapper reporting tools.

Every tool remains runnable on its own (``python scripts/<tool>.py``);
this dispatcher only routes, so each tool's argparse stays the single
source of truth for its flags.

Usage:
    python scripts/cbm.py <command> [options]
    python scripts/cbm.py <command> --help

Commands:
    report     Structural report (HTML / MD / JSON) from a bundle
    report-rs  Rust-rendered PDF report (streams multi-GB inventories)
    dossier    A4 PDF dossier, typeset with the Measured Ink design system
    pdf        Render an authored Markdown report to a themed PDF
    site       Generate the static bundle-browser site
    repair     Apply post-hoc data-quality fixes to an emitted bundle

Commands import lazily: a missing optional dependency (e.g. reportlab
for ``dossier``, weasyprint for ``pdf``) breaks only that command, with
an install hint instead of a traceback.
"""
from __future__ import annotations

import importlib
import os
import sys

from codebase_mapper.shared_kernel.settings import load_env

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# command → (module under scripts/, one-line description shown in usage)
COMMANDS: dict[str, tuple[str, str]] = {
    "report": ("cbm_report",
               "Structural report (HTML / MD / JSON) from a bundle"),
    "report-rs": ("cbm_report_rs",
                  "Rust-rendered PDF report (streams multi-GB inventories)"),
    "dossier": ("cbm_dossier",
                "A4 PDF dossier, typeset with the Measured Ink design system"),
    "pdf": ("report_to_pdf",
            "Render an authored Markdown report to a themed PDF"),
    "site": ("generate_static_site",
             "Generate the static bundle-browser site"),
    "repair": ("cbm_repair",
               "Apply post-hoc data-quality fixes to an emitted bundle"),
}


def _usage() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = [f"  {name:<{width}}   {desc}"
             for name, (_module, desc) in COMMANDS.items()]
    return (
        "usage: python scripts/cbm.py <command> [options]\n\n"
        "commands:\n" + "\n".join(lines) + "\n\n"
        "Run `python scripts/cbm.py <command> --help` for a command's flags.\n"
    )


def _load_command(name: str):
    """Import seam (patched in tests). Returns the command module."""
    return importlib.import_module(COMMANDS[name][0])


def main(argv: list[str] | None = None) -> int:
    load_env()  # .env (repo-scoped) fills gaps; real environment always wins
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(_usage(), end="")
        return 0
    if not argv:
        print(_usage(), end="", file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    if command not in COMMANDS:
        print(f"error: unknown command {command!r}\n\n" + _usage(),
              end="", file=sys.stderr)
        return 2
    try:
        module = _load_command(command)
    except ImportError as e:
        print(f"error: command {command!r} is missing a dependency: {e}\n"
              f"       install it (see pyproject optional-dependencies) "
              "and retry", file=sys.stderr)
        return 1
    rc = module.main(rest)
    return 0 if rc is None else int(rc)


if __name__ == "__main__":
    sys.exit(main())
