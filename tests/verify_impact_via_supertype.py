"""Interface-mediated dependents in file_impact.

The defect this pins: ``file_impact`` scoped blast radius from the import
graph alone. Nothing imports an implementation class in a DI codebase —
Spring injects by interface — so asking about ``PurchaseServiceImpl.java``
returned ``direct_dependents: []``. That is not a missing answer, it is a
confident wrong one: it reads as "nothing depends on this file" while four
files reach it through ``PurchaseService``.

The bundle already held the edge. ``xrefs.jsonl`` carried 31 ``subclassOf``
and 69 ``overrides`` records resolved by the Java inter-file resolver, and
``impact.py`` already walked the call graph into ``symbol_callers`` /
``symbol_callees`` — which the MCP handler then dropped on the floor while
hand-building its response dict.

Run against a real bundle:

    CBM_BUNDLES_ROOT=<dir> python tests/verify_impact_via_supertype.py

Skips cleanly (exit 0) when no bundle with Java xrefs is present, so it is
safe in a CI that has no fixture.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []

IMPL = "src/main/java/com/gingapay/payment/service/PurchaseServiceImpl.java"
IFACE = "src/main/java/com/gingapay/payment/service/PurchaseService.java"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL {name}: {detail}")


def main() -> int:
    root = os.environ.get("CBM_BUNDLES_ROOT")
    if not root or not Path(root).is_dir():
        print("SKIP: set CBM_BUNDLES_ROOT to a directory of bundles")
        return 0

    from frontend.backend.serving.application.bundle_data import get_bundle
    from frontend.backend.serving.application.impact import (
        dependents_via_supertype,
        get_impact_response,
        supertype_files,
    )

    b = get_bundle(None)
    if IMPL not in b.file_by_path:
        print(f"SKIP: bundle has no {IMPL}")
        return 0
    if not getattr(b, "xrefs", None):
        print("SKIP: bundle carries no xrefs.jsonl")
        return 0

    print("verify_impact_via_supertype")

    print("\n[1] the defect: the import graph alone says nothing depends on it")
    check("direct_dependents is empty for the impl (import-only, unchanged)",
          sorted(b.imports_in.get(IMPL, [])) == [],
          str(sorted(b.imports_in.get(IMPL, []))))
    check("the interface, by contrast, has importers",
          len(b.imports_in.get(IFACE, [])) > 0)

    print("\n[2] supertypes are lifted from the symbol xrefs")
    supers = supertype_files(b, IMPL)
    check("the impl reports its interface as a supertype",
          IFACE in supers, str(supers))
    check("no self-edge", IMPL not in supers, str(supers))

    print("\n[3] dependents through the interface")
    via = dependents_via_supertype(b, IMPL)
    check("keyed by the interface that justified them",
          IFACE in via, str(list(via)))
    callers = via.get(IFACE, [])
    for expected in (
        "src/main/java/com/gingapay/payment/controller/PurchaseController.java",
        "src/main/java/com/gingapay/payment/scheduler/ExpiredPurchaseScheduler.java",
    ):
        check(f"finds {expected.rsplit('/', 1)[-1]}", expected in callers,
              str(callers))
    check("the impl never lists itself", IMPL not in callers)

    print("\n[4] the full response carries it, and keeps the two kinds apart")
    payload = get_impact_response(IMPL, depth=2, limit=100)
    check("direct_dependents still import-only",
          payload["direct_dependents"] == [], str(payload["direct_dependents"]))
    check("supertypes present", IFACE in payload["supertypes"])
    check("dependents_via_supertype present",
          IFACE in payload["dependents_via_supertype"])
    check("symbol_callers computed", "symbol_callers" in payload)
    check("symbol_callees computed", "symbol_callees" in payload)

    print("\n[5] tests reached through the interface are no longer invisible")
    tests = payload["related_tests"]
    check("the scheduler guards test is related",
          any("ExpiredPurchaseSchedulerGuardsTest" in t for t in tests),
          str(tests))

    print("\n[6] the MCP handler stops dropping the xref keys")
    from frontend.mcp_server.handlers import HANDLERS
    out = HANDLERS["file_impact"]({"path": IMPL}, None)
    for key in ("supertypes", "dependents_via_supertype",
                "symbol_callers", "symbol_callees"):
        check(f"{key} survives the handler", key in out, str(sorted(out)))

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
