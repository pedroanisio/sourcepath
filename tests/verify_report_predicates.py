#!/usr/bin/env python3
"""verify_report_predicates.py — reverse predicate-coverage for the reports.

drift-risk-map C3: `scripts/cbm_report.py` and `scripts/cbm_dossier.py` build
RDF predicate IRIs by string concatenation (`CBM + "imports"`,
`CR.C3 + "lexicalizes"`, …) with no guard that the emitter actually writes
those predicates — a namespace or suffix mismatch silently queries nothing.
That exact failure shipped once: the dossier queried `cbm#lexicalizes` while
the emitter has only ever written `cbml3#lexicalizes`, so a whole link class
rendered as absent in every dossier.

This verifier closes the loop, mirroring `verify_shape_coverage.py` in the
opposite direction:

  1. extract every `<NS> + "<suffix>"` predicate reference from both report
     scripts (regex over the four namespace aliases CBM/C2/C3/C4);
  2. emit a real fixture bundle (tiny repo -> run_l3, hash backend) and
     collect the predicate IRIs actually present in inventory.ttl;
  3. each referenced predicate must be (a) present in the emitted graph, or
     (b) written by the emitter source under the SAME namespace constant
     (`CBM.x` / `CBM["x"]` in codebase_mapper/ or plugins/) — covering
     predicates the tiny fixture legitimately does not exercise (e.g. L4
     enrichment needs Ollama);
  4. sanity floors on extraction counts guard the regexes themselves.

Run from the repo root:  uv run python tests/verify_report_predicates.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Report-side alias -> emitter-side rdflib Namespace constant name.
NS_ALIAS_TO_EMITTER = {"CBM": "CBM", "C2": "CBML2", "C3": "CBML3", "C4": "CBML4"}

# `CBM + "imports"` in cbm_report.py; `CR.CBM + "imports"` in cbm_dossier.py.
_CONCAT = re.compile(r'(?:CR\.)?\b(CBM|C2|C3|C4)\s*\+\s*"([A-Za-z0-9_]+)"')

# Extraction floors: if a refactor changes the concatenation idiom, the regex
# silently finding ~nothing must fail loudly, not pass vacuously.
MIN_REFS = {"cbm_report.py": 12, "cbm_dossier.py": 6}

_FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"== {label} == {'ok' if ok else 'FAIL'}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        _FAILURES.append(label)


def emit_fixture_bundle(base: Path) -> Path:
    """Map a tiny real repo through L1+L2(hash)+L3 and return the bundle dir."""
    repo = base / "fixture-repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "app.py").write_text(
        "import requests\n"
        "from pkg import util\n\n"
        "def resolve_user_account():\n"
        "    return util.compute_account_balance()\n"
    )
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "util.py").write_text(
        "def compute_account_balance():\n    return 42\n")
    (repo / "test_app.py").write_text(
        "import app\n\ndef test_resolve():\n"
        "    assert app.resolve_user_account() == 42\n")
    (repo / "lib.rs").write_text(
        "#[derive(Debug, Clone)]\npub struct AccountLedger { pub id: u64 }\n\n"
        "#[cfg(test)]\nmod tests {\n    #[test]\n    fn ledger() {}\n}\n")
    (repo / "requirements.txt").write_text("requests==2.31.0\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "fixture"], cwd=repo, check=True)

    out = base / "bundle"
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_l3.py"),
         "--repo", str(repo), "--out", str(out), "--backend", "hash"],
        cwd=REPO_ROOT, check=True, capture_output=True)
    return out


def emitter_writes(ns_const: str, suffix: str, emitter_sources: str) -> bool:
    """Tier 2: the emitter source uses the same namespace constant + suffix."""
    return re.search(
        rf'\b{ns_const}\.{suffix}\b|\b{ns_const}\["{suffix}"\]',
        emitter_sources) is not None


def main() -> int:
    import cbm_report as CR  # namespace IRI strings

    ns_iri = {"CBM": CR.CBM, "C2": CR.C2, "C3": CR.C3, "C4": CR.C4}

    refs: dict[str, set[tuple[str, str]]] = {}
    for script in MIN_REFS:
        text = (REPO_ROOT / "scripts" / script).read_text(encoding="utf-8")
        found = set(_CONCAT.findall(text))
        refs[script] = found
        check(f"{script}: extraction floor ({len(found)} refs, "
              f"need >= {MIN_REFS[script]})", len(found) >= MIN_REFS[script])

    emitter_sources = "\n".join(
        p.read_text(encoding="utf-8")
        for root in ("codebase_mapper", "plugins")
        for p in (REPO_ROOT / root).rglob("*.py"))

    with tempfile.TemporaryDirectory() as td:
        bundle = emit_fixture_bundle(Path(td))
        import rdflib
        g = rdflib.Graph()
        g.parse(bundle / "inventory.ttl", format="turtle")
        emitted = {str(p) for p in g.predicates()}

        for script, pairs in refs.items():
            bad: list[str] = []
            for alias, suffix in sorted(pairs):
                iri = ns_iri[alias] + suffix
                if iri in emitted:
                    continue
                if emitter_writes(NS_ALIAS_TO_EMITTER[alias], suffix,
                                  emitter_sources):
                    continue
                bad.append(f"{alias}+{suffix!r} -> <{iri}>")
            check(f"{script}: every queried predicate is emitter-real",
                  not bad,
                  "never emitted under that namespace: " + "; ".join(bad))

        # --- drift-risk H6: the decomposer's hand-rolled JSON-LD reader ----
        # decomposer/evidence.py re-parses inventory.jsonld beside the shared
        # loader, restating CURIE keys/types as string literals. Pin each
        # against the fixture bundle's actual JSON-LD keys and @type values.
        import json as _json

        def jsonld_universe(obj, acc: set[str]) -> set[str]:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    acc.add(k)
                    if k == "@type":
                        for t in (v if isinstance(v, list) else [v]):
                            if isinstance(t, str):
                                acc.add(t)
                    jsonld_universe(v, acc)
            elif isinstance(obj, list):
                for v in obj:
                    jsonld_universe(v, acc)
            return acc

        universe = jsonld_universe(
            _json.loads((bundle / "inventory.jsonld").read_text()), set())
        evidence_src = (REPO_ROOT / "decomposer" / "evidence.py"
                        ).read_text(encoding="utf-8")
        curies = set(re.findall(
            r'"((?:cbm|cbml2|cbml3|cbml4|skos|nif):[A-Za-z0-9_]+)"',
            evidence_src))
        check(f"decomposer/evidence.py: extraction floor "
              f"({len(curies)} CURIEs, need >= 3)", len(curies) >= 3)
        missing = sorted(c for c in curies if c not in universe)
        check("decomposer/evidence.py: every restated JSON-LD key is "
              "emitter-real", not missing,
              f"absent from emitted inventory.jsonld: {missing}")

    if _FAILURES:
        print(f"\n{len(_FAILURES)} check(s) failed", file=sys.stderr)
        return 1
    print("\nall report-predicate coverage checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
