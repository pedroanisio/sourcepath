#!/usr/bin/env python3
"""verify_resolver_coverage.py — an analyzer that finds imports must resolve them.

`asm`, `devicetree`, `kconfig`, and `make` shipped with `LanguageAnalyzer`s but
no `ImportResolver`. Their extractors do emit import specs — `asm_include`,
`kconfig_source`, `dts_include`, `make_include` — so the data was found,
written into `ast_summary`, and then went nowhere: with no resolver registered
for the language, `map_codebase` never turned those specs into `cbm:imports`
edges.

Nothing reported it. The manifest counted the files as analyzed, the coverage
sidecar saw symbols, and no degradation entry was written, so a bundle whose
build graph was substantially incomplete looked healthy. On a kernel-scale
repository these four formats carry a large share of that graph, which makes
this precisely the "silent zero" class the project's honesty machinery exists
to surface — but one layer up, in edges rather than symbols.

Contract enforced here, derived from the registries rather than a hand-written
list, so a new analyzer is covered on the commit that adds it:

  1. every registered LanguageAnalyzer's language has a registered
     ImportResolver — an extractor that can find imports must have something
     that consumes them;
  2. the four historically-missing languages resolve a concrete include to a
     real in-repo edge, so the wiring is proven to work rather than merely to
     exist;
  3. unresolvable specs (unexpanded variables, absolute paths) are surfaced
     as external rather than dropped.

Run from the repo root:  python tests/verify_resolver_coverage.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codebase_mapper  # noqa: F401,E402  (registers built-ins at import)
from codebase_mapper.inspection.languages.lightweight import (  # noqa: E402
    resolve_lightweight_imports,
)
from codebase_mapper.shared_kernel.extensions import (  # noqa: E402
    iter_import_resolvers,
    iter_language_analyzers,
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


def main() -> int:
    print("== every analyzer language has an import resolver ==")

    analyzer_langs = {a.name.replace("lang_", "") for a in iter_language_analyzers()}
    resolver_langs = {r.name.replace("resolve_", "") for r in iter_import_resolvers()}
    missing = sorted(analyzer_langs - resolver_langs)
    check(
        "no analyzer language lacks a resolver",
        not missing,
        f"analyzers with no ImportResolver: {missing}",
    )

    for lang in ("asm", "devicetree", "kconfig", "make"):
        check(
            f"{lang} has a registered resolver",
            lang in resolver_langs,
            "its extractor emits import specs that would otherwise be dropped",
        )

    print("\n== lightweight includes resolve to real in-repo edges ==")

    paths = {"arch/x86/boot.S", "arch/x86/common.inc", "Makefile", "mk/rules.mk"}

    in_repo, external = resolve_lightweight_imports(
        "arch/x86/boot.S",
        {"imports": [{"kind": "asm_include", "source": "common.inc", "lineno": 1}]},
        paths,
    )
    check(
        "a sibling include resolves relative to the including file",
        in_repo == ["arch/x86/common.inc"],
        f"got in_repo={in_repo} external={external}",
    )

    in_repo, external = resolve_lightweight_imports(
        "Makefile",
        {"imports": [{"kind": "make_include", "source": "mk/rules.mk", "lineno": 3}]},
        paths,
    )
    check(
        "a repo-root-relative include resolves",
        in_repo == ["mk/rules.mk"],
        f"got in_repo={in_repo} external={external}",
    )

    in_repo, external = resolve_lightweight_imports(
        "Makefile",
        {"imports": [
            {"kind": "make_include", "source": "$(SRCTREE)/gen.mk", "lineno": 4},
            {"kind": "make_include", "source": "/etc/global.mk", "lineno": 5},
        ]},
        paths,
    )
    check(
        "unresolvable specs are surfaced as external, never dropped",
        in_repo == [] and len(external) == 2,
        f"got in_repo={in_repo} external={external}",
    )

    in_repo, _ = resolve_lightweight_imports(
        "arch/x86/boot.S",
        {"imports": [{"kind": "dts_include", "source": "<common.inc>", "lineno": 1}]},
        paths,
    )
    check(
        "angle-bracketed includes are unwrapped before resolution",
        in_repo == ["arch/x86/common.inc"],
        f"got in_repo={in_repo}",
    )

    in_repo, _ = resolve_lightweight_imports(
        "Makefile",
        {"imports": [{"kind": "make_include", "source": "Makefile", "lineno": 1}]},
        paths,
    )
    check("a file never imports itself", in_repo == [], f"got {in_repo}")

    print(f"\nPassed: {PASS}    Failed: {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
