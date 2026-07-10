#!/usr/bin/env python3
"""verify_report_rs_contract.py — emitter <-> Rust cbm-report key contract.

drift-risk-map C2: `tools/cbm-report` restates the bundle contract as serde
literals across a language boundary (manifest keys, JSON-LD CURIEs, JSONL
field names). Serde deserializes a renamed/missing key to `None`/default with
exit code 0, so a Python-side rename ships a silently wrong PDF; before this
verifier the crate's only tests deserialized hand-written JSON — nothing ever
connected the two sides.

Contract enforced here:

  1. parse the crate's `#[derive(Deserialize)]` items and collect every JSON
     key it expects — `#[serde(rename = "…")]` literals plus un-renamed field
     names;
  2. emit a real fixture bundle (tiny repo with a Rust file -> run_l3, hash
     backend) and build the emitted-key universe per artifact:
     run_manifest.json (recursive keys), inventory.jsonld (node keys),
     rust_items.jsonl (record keys);
  3. every Rust-expected key must be present in the corresponding emitted
     artifact, or provably written by the emitter source under the same
     name — quoted dict key for JSON artifacts, same-namespace rdflib
     attribute for JSON-LD CURIEs (covers keys the tiny fixture legitimately
     lacks: L4 needs Ollama, `cbm:extractionError` needs a broken file);
  4. extraction floors guard the Rust parser against silently matching
     nothing.

Run from the repo root:  uv run python tests/verify_report_rs_contract.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CRATE_SRC = REPO_ROOT / "tools" / "cbm-report" / "src"

# JSON-LD framework keys are contract-free.
JSONLD_BUILTINS = {"@graph", "@id", "@type", "@value", "@language", "@context"}

# CURIE prefix -> emitter-side rdflib Namespace constant name.
CURIE_NS = {"cbm": "CBM", "cbml2": "CBML2", "cbml3": "CBML3",
            "cbml4": "CBML4", "skos": "SKOS", "nif": "NIF"}

# Rust source file -> which emitted universe its keys bind to.
BINDINGS = {
    "ingest/manifest.rs": "manifest",
    "ingest/enrichments.rs": "enrichments",
    "ingest/rust_items.rs": "rust_items",
    "ingest/inventory.rs": "jsonld",
    "stats.rs": "jsonld",
}

MIN_KEYS = {"ingest/manifest.rs": 25, "ingest/enrichments.rs": 3,
            "ingest/rust_items.rs": 3, "stats.rs": 10}

_RENAME = re.compile(r'rename\s*=\s*"([^"]+)"')
_FIELD = re.compile(r"^\s*(?:pub\s+)?([a-z_][a-z0-9_]*)\s*:")
_FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"== {label} == {'ok' if ok else 'FAIL'}"
          + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        _FAILURES.append(label)


def rust_expected_keys(path: Path) -> set[str]:
    """JSON keys a file's Deserialize items expect (rename wins over name)."""
    keys: set[str] = set()
    in_item = False
    depth = 0
    pending_rename: str | None = None
    is_deserialize = False
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not in_item:
            if s.startswith("#[derive(") and "Deserialize" in s:
                is_deserialize = True
                continue
            if is_deserialize and re.match(r"(pub\s+)?(struct|enum)\b", s):
                if "enum" in s.split("{")[0]:  # untagged enums carry no keys
                    is_deserialize = False
                    continue
                in_item = True
                depth = s.count("{") - s.count("}")
                if depth <= 0 and "{" in s:  # one-line struct
                    in_item = False
                    is_deserialize = False
                continue
            if is_deserialize and s.startswith("#["):
                continue  # container attrs between derive and struct
            if is_deserialize and s:
                is_deserialize = False
            continue
        depth += s.count("{") - s.count("}")
        if depth <= 0:
            in_item = False
            is_deserialize = False
            pending_rename = None
            continue
        m = _RENAME.search(s)
        if m and s.startswith("#["):
            pending_rename = m.group(1)
            continue
        if s.startswith("#["):
            continue
        fm = _FIELD.match(line)
        if fm:
            keys.add(pending_rename if pending_rename else fm.group(1))
            pending_rename = None
    return keys


def recursive_keys(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= recursive_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= recursive_keys(v)
    return out


def emit_fixture_bundle(base: Path) -> Path:
    repo = base / "fixture-repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "app.py").write_text(
        "import requests\nfrom pkg import util\n\n"
        "def resolve_user_account():\n"
        "    return util.compute_account_balance()\n")
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "util.py").write_text(
        "def compute_account_balance():\n    return 42\n")
    (repo / "test_app.py").write_text(
        "import app\n\ndef test_resolve():\n"
        "    assert app.resolve_user_account() == 42\n")
    (repo / "lib.rs").write_text(
        "#[derive(Debug, Clone)]\npub struct AccountLedger { pub id: u64 }\n\n"
        "pub async fn settle_ledger() {}\n\n"
        "#[cfg(test)]\nmod tests {\n    #[test]\n    fn ledger() {}\n}\n")
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


def main() -> int:
    emitter_sources = "\n".join(
        p.read_text(encoding="utf-8")
        for root in ("codebase_mapper", "plugins")
        for p in (REPO_ROOT / root).rglob("*.py"))

    def source_writes(key: str) -> bool:
        """Tier 2 — the emitter source demonstrably produces this key."""
        if ":" in key:  # JSON-LD CURIE -> same-namespace rdflib attribute
            prefix, suffix = key.split(":", 1)
            ns = CURIE_NS.get(prefix)
            if ns is None:
                return False
            return re.search(
                rf'\b{ns}\.{suffix}\b|\b{ns}\["{suffix}"\]',
                emitter_sources) is not None
        return f'"{key}"' in emitter_sources

    with tempfile.TemporaryDirectory() as td:
        bundle = emit_fixture_bundle(Path(td))
        manifest_keys = recursive_keys(
            json.loads((bundle / "run_manifest.json").read_text()))
        jsonld_keys = recursive_keys(
            json.loads((bundle / "inventory.jsonld").read_text()))
        rust_item_keys: set[str] = set()
        for line in (bundle / "rust_items.jsonl").read_text().splitlines():
            if line.strip():
                rust_item_keys |= set(json.loads(line))
        check("fixture emitted rust_items.jsonl records",
              bool(rust_item_keys))

        universes = {
            "manifest": manifest_keys,
            "jsonld": jsonld_keys | JSONLD_BUILTINS,
            "rust_items": rust_item_keys,
            "enrichments": set(),  # fixture has no L4 (Ollama) — tier 2 only
        }

        for rel, universe_name in BINDINGS.items():
            expected = rust_expected_keys(CRATE_SRC / rel)
            if rel in MIN_KEYS:
                check(f"{rel}: extraction floor ({len(expected)} keys, "
                      f"need >= {MIN_KEYS[rel]})",
                      len(expected) >= MIN_KEYS[rel])
            universe = universes[universe_name]
            bad = sorted(k for k in expected
                         if k not in universe and not source_writes(k))
            check(f"{rel}: every expected key is emitter-real "
                  f"({universe_name})", not bad,
                  "no emitted or source evidence for: " + ", ".join(bad))

    if _FAILURES:
        print(f"\n{len(_FAILURES)} check(s) failed", file=sys.stderr)
        return 1
    print("\nall Rust-crate contract checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
