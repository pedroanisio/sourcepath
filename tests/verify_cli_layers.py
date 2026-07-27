#!/usr/bin/env python3
"""verify_cli_layers.py — the installed console script must reach its plugins.

`pyproject.toml` ships `plugins*` inside the wheel but excludes `scripts*`,
and plugin registration lived exclusively in `scripts/run_l2.py` /
`run_l3.py` / `run_xrefs.py` / `run_l4.py`. `codebase_mapper/cli.py` contained
no plugin reference at all, so `pip install codebase-mapper` produced a tool
that could emit L1 bundles only: the plugin code was installed and
unreachable, and every documented L2/L3/L4 workflow silently required a git
checkout of the repository.

Contract enforced here:

  1. the CLI exposes layer selection, so plugins are reachable without
     `scripts/`;
  2. each layer actually registers its components with the host registries;
  3. the implication order holds — L3's aggregator reads L2's index entry and
     the xref resolvers read L2's chunks, so both must pull L2 in;
  4. an unknown layer is rejected rather than silently ignored;
  5. the packaging split that caused the bug still holds (`plugins*` shipped,
     `scripts*` not), so the CLI — not a script — has to be the entry point.

Run from the repo root:  python tests/verify_cli_layers.py
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from codebase_mapper.cli import VALID_LAYERS, register_layers  # noqa: E402
from codebase_mapper.shared_kernel.extensions import (  # noqa: E402
    iter_aggregators,
    iter_artifact_emitters,
    iter_record_enrichers,
    reset_registries,
)

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


def _registered_names() -> set[str]:
    names = {c.name for c in iter_record_enrichers()}
    names |= {c.name for c in iter_aggregators()}
    names |= {c.name for c in iter_artifact_emitters()}
    return names


def main() -> int:
    print("== the console script can register plugin layers ==")

    reset_registries()
    baseline = _registered_names()
    enabled = register_layers("l1")
    check("l1 is the default and registers no plugin",
          enabled == ["l1"] and _registered_names() == baseline,
          f"enabled={enabled}")

    reset_registries()
    register_layers("l2", "hash")
    l2_names = _registered_names() - baseline
    check("l2 registers the chunk/embedding components",
          any(n.startswith("l2_") for n in l2_names),
          f"registered: {sorted(l2_names)}")

    reset_registries()
    enabled = register_layers("l3", "hash")
    l3_names = _registered_names() - baseline
    check("l3 implies l2 (its aggregator reads L2's index entry)",
          "l2" in enabled and any(n.startswith("l2_") for n in l3_names),
          f"enabled={enabled}")
    check("l3 registers the concept components",
          any(n.startswith("l3_2") or n.startswith("l3_3") or n.startswith("l3_4")
              for n in l3_names),
          f"registered: {sorted(l3_names)}")

    reset_registries()
    enabled = register_layers("xrefs", "hash")
    check("xrefs implies l2 (its resolvers read L2 chunks)",
          "l2" in enabled, f"enabled={enabled}")

    reset_registries()
    try:
        register_layers("nope")
        rejected = False
    except ValueError:
        rejected = True
    check("an unknown layer is rejected, not silently ignored", rejected)

    reset_registries()

    print("\n== the packaging split that caused the bug still holds ==")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    find = pyproject["tool"]["setuptools"]["packages"]["find"]
    include = find.get("include", [])
    exclude = find.get("exclude", [])
    check("plugins ship in the wheel",
          any(pattern.startswith("plugins") for pattern in include),
          f"include={include}")
    check("scripts do not ship in the wheel — so the CLI must be the entry point",
          any(pattern.startswith("scripts") for pattern in exclude),
          f"exclude={exclude}")

    scripts = pyproject["project"].get("scripts", {})
    check("a console script is declared", bool(scripts), f"scripts={scripts}")

    cli_source = (ROOT / "codebase_mapper" / "cli.py").read_text(encoding="utf-8")
    check("the CLI references the layer surface",
          "--layers" in cli_source and "register_layers" in cli_source)
    check("every declared layer name is documented in the CLI help",
          all(layer in cli_source for layer in VALID_LAYERS))

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
